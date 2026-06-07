"""
agents/runner.py — Stage-3 RunnerAgent (Stage 7.2 + 7.3 + 7.5).

Drives the full experiment execution lifecycle through ExperimentStateMachine
substages:

  WRITING_CODE → AWAITING_APPROVAL → SETTING_UP_DATA
  → PREPROCESSING / TRAINING / EVALUATING
  → INTERPRETING → AWAITING_NEXT → (loop back or done)

Sub-step 7.2: code generation, approval gate, execution, metric/phase/plot
              parsing, live log streaming, post-run git commit, interpretation.
Sub-step 7.3: error-fix loop (auto-apply, no gate).
  - ModuleNotFoundError  → conda/pip install + retry
  - Generic errors       → fixer role (+ ddgs search on attempt ≥ 1)
  - Configurable fix cap (config.max_fix_attempts, default 3); when exhausted
    the user is offered an approval card to extend the cap for the current run
  - Mistakes recorded in the memory store
Sub-step 7.5: next-iteration loop + versioning.
  - _propose_next_iteration() → collaborator role generates JSON proposal
  - machine.transition(AWAITING_NEXT) blocks until user approves/rejects
  - On approval: create new Experiment row (iteration N+1), loop back
  - _reset_run_state() clears per-run state between iterations
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, select

from alfred.agents.base import Role, make_client
from alfred.db import get_engine
from alfred.models.db_models import (
    Experiment,
    ExperimentStatus,
    Message,
    MessageKind,
    MessageRole,
    Metric,
    Project,
    RunLog,
    RunPhase,
    VersionMode,
)
from alfred.services.conda import CondaExecutor
from alfred.services.dataset_cache import DatasetCache
from alfred.services.git_service import GitService
from alfred.services.ollama import keepalive_model
from alfred.services.plotting import emit_plot_event, get_preamble
from alfred.state_machine.machine import (
    ExperimentStateMachine,
    S3Sub,
    Stage,
    get_machine,
    register_machine,
    unregister_machine,
)

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_DEFAULT_MAX_FIX_ATTEMPTS = 3  # overridden per-instance from config.max_fix_attempts
LOG_FLUSH_EVERY = 50
TRACEBACK_WINDOW = 60   # lines kept for error diagnosis

_METRIC_RE = re.compile(
    r"ALFRED_METRIC:\s+([\w./-]+)\s*=\s*([0-9.eE+\-]+)\s+step=(\d+)"
)
_PHASE_RE = re.compile(r"ALFRED_PHASE:\s+(preprocess|train|eval)")
_PLOT_RE = re.compile(r"ALFRED_PLOT:\s+(.+)")
_PROGRESS_RE = re.compile(r"(?:Epoch|Step)\s+(\d+)/(\d+)", re.IGNORECASE)

_PHASE_TO_SUB = {
    "preprocess": (RunPhase.preprocess, S3Sub.PREPROCESSING),
    "train":      (RunPhase.train,      S3Sub.TRAINING),
    "eval":       (RunPhase.eval,       S3Sub.EVALUATING),
}

# Error classifier patterns — checked in order; first match wins
_CLASSIFY_PATTERNS: list[tuple[str, re.Pattern, int]] = [
    # (error_type, pattern, capture_group_for_extra; 0 means no group)
    ("ModuleNotFoundError", re.compile(r"ModuleNotFoundError: No module named '([^']+)'"), 1),
    ("CUDA_OOM",            re.compile(r"CUDA out of memory"),                              0),
    ("FileNotFoundError",   re.compile(r"FileNotFoundError.*'([^']+)'"),                   1),
]


class RunnerAgent:
    """
    Orchestrates experiment iterations end-to-end, looping via _propose_next_iteration.

    Created per chat turn in main._handle_chat_run.  State persists to the DB
    (Experiment rows + machine snapshot) so the instance does not need to be
    long-lived.
    """

    def __init__(
        self,
        project_id: int,
        model: str,
        ws_manager: object,
        db_session: Session,
        auto_approve: bool = False,
    ) -> None:
        self.project_id = project_id
        self.pid_str = str(project_id)
        self.model = model
        self.ws = ws_manager
        self.session = db_session
        self.auto_approve = auto_approve

        self.client = make_client(model, project_id=self.pid_str, ws_manager=ws_manager)

        # Load max_fix_attempts from config; falls back to default if config unavailable
        try:
            from alfred.config import get_config
            self.max_fix_attempts: int = get_config().max_fix_attempts
        except Exception:
            self.max_fix_attempts = _DEFAULT_MAX_FIX_ATTEMPTS

        # Run-time state — populated during _run_pipeline; reset between iterations
        self._machine: ExperimentStateMachine | None = None
        self._executor: CondaExecutor | None = None
        self._git: GitService | None = None
        self._plan: dict = {}
        self._current_phase: RunPhase = RunPhase.preprocess
        self._pending_logs: list[tuple[str, str, RunPhase]] = []
        self._pending_metrics: list[tuple[str, float, int]] = []
        self._collected_ascii: list[str] = []
        self._script_code: str = ""       # last written script (preamble + code)
        self._script_path: Path | None = None
        self._active_exp_id: int | None = None
        self._recent_lines: list[str] = []  # sliding window for traceback capture

    # ── Public API ─────────────────────────────────────────────────────────────

    async def run(self, user_content: str, asst_msg_id: int | None = None) -> None:
        """
        Entry point.  Drops the message if an experiment is already executing.
        """
        existing = get_machine(self.project_id)
        if existing is not None and existing.current_substage not in (
            S3Sub.AWAITING_NEXT,
        ):
            await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
                "message": "Experiment is already in progress — please wait.",
                "phase": "run",
            })
            return

        try:
            await self._run_pipeline(user_content, asst_msg_id)
        except asyncio.CancelledError:
            # Reset any in-flight experiment back to "planned" so the user can re-run
            if self._active_exp_id is not None:
                try:
                    with Session(get_engine()) as _s:
                        _exp = _s.get(Experiment, self._active_exp_id)
                        if _exp is not None and _exp.status == ExperimentStatus.running:
                            _exp.status = ExperimentStatus.planned
                            _exp.started_at = None
                            _s.add(_exp)
                            _s.commit()
                except Exception as _exc:
                    logger.warning("Could not reset experiment on cancel: %s", _exc)
            await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
                "message": "⏹ Stopped by user.", "phase": "run",
            })
            if self._machine is not None:
                await self._machine.report_done("Stopped")
            unregister_machine(self.project_id)
            await self.ws.send(self.pid_str, "stopped", {"summary": "Stopped by user"})  # type: ignore[attr-defined]
        except Exception as exc:
            logger.exception("RunnerAgent pipeline failed: %s", exc)
            await self.ws.broadcast_error(  # type: ignore[attr-defined]
                self.pid_str,
                human_message=f"Runner error: {exc}",
                remediation="Check the backend terminal for the full traceback.",
            )
            if self._machine is not None:
                await self._machine.report_error(str(exc))
            unregister_machine(self.project_id)

    # ── Pipeline (iterates via AWAITING_NEXT loop) ─────────────────────────────

    async def _run_pipeline(
        self, user_content: str, asst_msg_id: int | None
    ) -> None:
        project = self.session.get(Project, self.project_id)
        if project is None:
            raise RuntimeError(f"Project {self.project_id} not found")

        exp_folder = Path(project.experiment_folder)
        conda_env = project.conda_env

        while True:
            # Expire cache so we pick up any newly created Experiment rows
            self.session.expire_all()

            exp = self.session.exec(
                select(Experiment)
                .where(Experiment.project_id == self.project_id)
                .where(Experiment.status == ExperimentStatus.planned)
                .order_by(Experiment.iteration.desc())  # type: ignore[arg-type]
            ).first()

            if exp is None:
                await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
                    "message": (
                        "No planned experiment found. "
                        "Complete Stage 6 (plan approval) first."
                    ),
                    "phase": "run",
                })
                await self.ws.broadcast_done(self.pid_str, summary="No experiment to run")  # type: ignore[attr-defined]
                return

            self._active_exp_id = exp.id
            self._plan = json.loads(exp.plan_json or "{}")
            iteration = exp.iteration

            # Init machine + services — stored on self for reuse in the fix loop
            self._machine = self._get_or_create_machine()
            self._git = GitService(exp_folder)
            self._git.init()
            self._executor = CondaExecutor(conda_env=conda_env, experiment_folder=exp_folder)

            # ── 1. Generate code ────────────────────────────────────────────────
            await self._machine.transition(
                S3Sub.WRITING_CODE, label="Writing experiment code…", stage=Stage.RUN
            )
            await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
                "message": (
                    f"━━ Iteration {iteration} — code generation ━━\n"
                    f"Model: {self.model}\n"
                    f"Objective: {str(self._plan.get('objective', '—'))[:120]}"
                ),
                "phase": "generate",
            })
            # Keep Ollama model loaded during long pipeline runs
            await keepalive_model(self.model, keep_alive="30m")

            prev_code = _load_prev_script(exp_folder, iteration)
            code = await self._generate_code(self._plan, iteration, exp_folder, prev_code=prev_code)
            self._script_code = get_preamble() + "\n" + code

            script_path = _script_path_for(exp_folder, iteration, self._plan)
            script_path.parent.mkdir(parents=True, exist_ok=True)
            script_path.write_text(self._script_code)
            self._script_path = script_path

            await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
                "message": f"Code written → {script_path}  ({len(self._script_code)} chars)",
                "phase": "generate",
            })

            diff_text = _compute_diff(prev_code, self._script_code, script_path.name)

            # ── 2. Approval gate ────────────────────────────────────────────────
            response = await self._machine.transition(
                S3Sub.AWAITING_APPROVAL,
                plan={
                    "diff": diff_text,
                    "code_path": str(script_path),
                    "iteration": iteration,
                    "experiment_id": exp.id,
                    "summary": str(self._plan.get("objective", f"Iteration {iteration}"))[:120],
                },
                label="Awaiting code approval",
            )

            if response is not None and not response.approved:
                await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
                    "message": "Code rejected — update the plan and try again.",
                    "phase": "run",
                })
                await self._machine.report_done("Code rejected")
                unregister_machine(self.project_id)
                return

            # ── 3. Mark running ─────────────────────────────────────────────────
            exp = self.session.get(Experiment, self._active_exp_id)
            if exp is None:
                raise RuntimeError("Experiment row disappeared after approval")
            exp.status = ExperimentStatus.running
            exp.started_at = datetime.utcnow()
            exp.code_path = str(script_path)
            self.session.add(exp)
            self.session.commit()

            # ── 4. Dataset setup ────────────────────────────────────────────────
            await self._machine.transition(S3Sub.SETTING_UP_DATA, label="Setting up datasets")
            cfg = _load_config_safe()
            if cfg is not None:
                cache = DatasetCache(Path(cfg.workspace_path))
                for uri in _extract_dataset_uris(self._plan):
                    try:
                        await cache.get_or_download(uri, exp_folder, self.session)
                    except Exception as exc:
                        logger.warning("Dataset fetch failed for %s: %s", uri, exc)
                        await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
                            "message": f"⚠️  Dataset download failed: {uri} — {exc}",
                            "phase": "setup",
                        })

            # ── 5. Execute (with error-fix loop on failure) ─────────────────────
            await self._machine.transition(S3Sub.PREPROCESSING, label="Preprocessing")
            await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
                "message": (
                    f"━━ Executing script ━━\n"
                    f"  conda env: {conda_env}\n"
                    f"  script:    {script_path.name}"
                ),
                "phase": "run",
            })
            exit_code = await self._executor.run_script(script_path, self._on_log_line)
            await self._flush_logs(self._active_exp_id)
            await self._flush_metrics(self._active_exp_id)

            done_exp_id = self._active_exp_id

            if exit_code == 0:
                interp = await self._post_run_success()
            else:
                traceback = "\n".join(self._recent_lines)
                success, interp = await self._error_fix_loop(traceback, attempt=0)
                if not success:
                    return  # error-fix loop already handled termination

            # ── 6. Propose next iteration ────────────────────────────────────────
            with Session(get_engine()) as s:
                done_exp = s.get(Experiment, done_exp_id)

            if done_exp is None:
                await self._machine.report_done("Experiment complete")
                unregister_machine(self.project_id)
                return

            should_continue = await self._propose_next_iteration(done_exp, interp)
            if not should_continue:
                return

            # Reset per-run state; loop back to pick up the new planned experiment
            self._reset_run_state()

    # ── Code generation ────────────────────────────────────────────────────────

    async def _generate_code(
        self, plan: dict, iteration: int, exp_folder: Path, prev_code: str = ""
    ) -> str:
        """Generate experiment code using the coder role."""
        dataset_uris = _extract_dataset_uris(plan)
        dataset_note = (
            "\nDataset linked at: data/ (relative to script directory)"
            if dataset_uris else ""
        )

        prev_code_section = ""
        if prev_code and iteration > 1:
            prev_code_section = f"""
PREVIOUS ITERATION CODE (iteration {iteration - 1}) — build on this, don't start from scratch:
```python
{prev_code[:4000]}
```
Apply the changes from the plan above. Preserve working patterns from the previous code.
"""

        prompt = f"""\
You are writing iteration {iteration} of this ML experiment.

PLAN:
{json.dumps(plan, indent=2)}

PREAMBLE ALREADY INJECTED — do NOT re-import or redefine these:
- log_metric(name, value, step)  call for every metric (loss, accuracy, etc.)
- plt.savefig(path)              emits ALFRED_PLOT automatically
- logging.basicConfig(...)       already configured; use logging.debug() freely
- matplotlib backend set to "Agg"
{dataset_note}

REQUIRED — emit ALFRED_PHASE at the start of each phase:
  print("ALFRED_PHASE: preprocess", flush=True)
  print("ALFRED_PHASE: train", flush=True)
  print("ALFRED_PHASE: eval", flush=True)

REQUIRED — call log_metric() for every scalar:
  log_metric("train_loss", loss.item(), step=epoch)
  log_metric("val_accuracy", acc, step=epoch)

REQUIRED — set random seed: seed = {plan.get("seed", 42)}

REQUIRED — use GPU if available (always include this at the top of training code):
  device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
  print(f"Using device: {{device}}", flush=True)
  # Move models and tensors: model.to(device), tensor.to(device)

PLOTTING PATTERN — always collect losses in a list, then plot AFTER the training loop:
  import matplotlib.pyplot as plt

  train_losses = []
  for epoch in range(epochs):
      loss = train_one_epoch(...)
      train_losses.append(float(loss))
      log_metric("train_loss", float(loss), step=epoch)

  # Plot AFTER training — not before, not during
  plt.figure(figsize=(8, 5))
  plt.plot(train_losses, label="Training Loss")
  plt.xlabel("Epoch"); plt.ylabel("Loss")
  plt.title("Training Loss Curve"); plt.legend(); plt.tight_layout()
  plt.savefig(os.path.join(os.path.dirname(os.path.abspath(__file__)), "loss_curve.png"))
  log_metric("final_loss", train_losses[-1], step=epochs - 1)
{prev_code_section}
Write ONLY the Python code, no markdown fences, no explanatory prose.
"""
        messages = [{"role": "user", "content": prompt}]
        response = await self.client.chat_silent(Role.CODER, messages)
        return _strip_code_fences(response)

    # ── Log line callback ──────────────────────────────────────────────────────

    async def _on_log_line(self, line: str) -> None:
        exp_id = self._active_exp_id
        if exp_id is None:
            return

        # Sliding window for traceback capture (TRACEBACK_WINDOW most recent lines)
        self._recent_lines.append(line)
        if len(self._recent_lines) > TRACEBACK_WINDOW:
            self._recent_lines.pop(0)

        # Live WS log event for thinking tab
        await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
            "message": line,
            "phase": self._current_phase.value,
            "experiment_id": exp_id,
        })

        # Parse ALFRED_METRIC
        mm = _METRIC_RE.search(line)
        if mm:
            name, val_str, step_str = mm.groups()
            try:
                self._pending_metrics.append((name, float(val_str), int(step_str)))
                if self._machine is not None:
                    await self._machine.report_progress(
                        int(step_str), 0,
                        f"{name} = {float(val_str):.4f}",
                        status="running",
                    )
            except ValueError:
                pass

        # Parse ALFRED_PHASE — transition machine substage
        pm = _PHASE_RE.search(line)
        if pm and self._machine is not None:
            phase_str = pm.group(1)
            if phase_str in _PHASE_TO_SUB:
                run_phase, sub = _PHASE_TO_SUB[phase_str]
                self._current_phase = run_phase
                await self._machine.transition(sub, label=phase_str.capitalize())

        # Parse ALFRED_PLOT
        plm = _PLOT_RE.search(line)
        if plm and exp_id is not None:
            png_path = Path(plm.group(1).strip())
            if png_path.exists():
                ascii_art = await emit_plot_event(
                    self.ws, self.pid_str, png_path, exp_id
                )
                self._collected_ascii.append(ascii_art)

        # Parse Epoch/Step progress
        prm = _PROGRESS_RE.search(line)
        if prm and self._machine is not None:
            cur, tot = int(prm.group(1)), int(prm.group(2))
            await self._machine.report_progress(cur, tot, f"Step {cur}/{tot}")

        # Buffer for batch DB write
        level = "DEBUG" if "DEBUG" in line else "INFO"
        self._pending_logs.append((level, line, self._current_phase))
        if len(self._pending_logs) >= LOG_FLUSH_EVERY:
            await self._flush_logs(exp_id)

    # ── Error-fix loop (Sub-step 7.3) ─────────────────────────────────────────

    async def _error_fix_loop(
        self, traceback: str, *, attempt: int, user_guidance: str = ""
    ) -> tuple[bool, str]:
        """
        Auto-apply error fixes without an approval gate.
        Diff is shown in the thinking tab for transparency.
        Caps at self.max_fix_attempts (config.max_fix_attempts, default 3).
        When exhausted, shows an approval card offering to extend the cap.

        Returns (True, interpretation_text) if ultimately successful,
        (False, "") if all attempts exhausted and user declines extension.
        """
        assert self._machine is not None
        assert self._executor is not None
        assert self._script_path is not None

        if attempt >= self.max_fix_attempts:
            # Offer the user a chance to extend the fix cap for this run
            traceback_short = next(
                (l for l in traceback.splitlines() if "Error" in l or "Exception" in l),
                traceback[:200],
            )
            response = await self._machine.transition(
                S3Sub.AWAITING_APPROVAL,
                plan={
                    "kind": "fix_exhausted",
                    "attempts_used": attempt,
                    "traceback_summary": traceback_short,
                    "experiment_id": self._active_exp_id,
                },
                label=f"Fix cap reached ({attempt} attempts) — awaiting extension",
            )
            if response and response.approved:
                extra = int((response.edited_plan or {}).get("extra_attempts", 3))
                new_guidance = str((response.edited_plan or {}).get("user_guidance", "")).strip()
                self.max_fix_attempts = attempt + max(extra, 1)
                msg = f"Fix cap extended to {self.max_fix_attempts} attempts"
                if new_guidance:
                    msg += f" — with guidance: {new_guidance[:100]}"
                await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
                    "message": msg + " — retrying…",
                    "phase": "fix",
                })
                return await self._error_fix_loop(
                    traceback, attempt=attempt, user_guidance=new_guidance
                )
            # User declined — give up
            await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
                "message": (
                    f"⛔  Fix loop stopped after {attempt} attempts.\n"
                    "Review the traceback in the thinking tab and fix manually."
                ),
                "phase": "fix",
            })
            await self._mark_experiment_failed()
            await self._machine.report_done("Fix attempts exhausted")
            unregister_machine(self.project_id)
            return False, ""

        await self._machine.transition(
            S3Sub.DIAGNOSING_ERROR,
            label=f"Diagnosing error (attempt {attempt + 1}/{self.max_fix_attempts})",
        )

        error_type, extra = _classify_error(traceback)
        traceback_first_line = next(
            (l for l in traceback.splitlines() if ("Error" in l or "Exception" in l)),
            traceback[:200],
        )

        await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
            "message": f"[DIAGNOSE] {error_type}: {extra or traceback_first_line}",
            "phase": "error",
        })

        if error_type == "ModuleNotFoundError" and extra:
            return await self._fix_missing_module(extra, traceback, attempt)
        else:
            return await self._fix_with_llm(
                traceback, traceback_first_line, error_type, attempt,
                user_guidance=user_guidance,
            )

    async def _fix_missing_module(
        self, package: str, traceback: str, attempt: int
    ) -> tuple[bool, str]:
        """Install a missing package and retry execution."""
        assert self._machine is not None
        assert self._executor is not None

        await self._machine.transition(
            S3Sub.FIXING, label=f"Installing {package}"
        )
        await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
            "message": f"[FIX] Installing missing package: {package}",
            "phase": "fix",
        })

        install_code = await self._executor.install_package(package)
        if install_code != 0:
            logger.warning("Package install failed for %s", package)

        _capture_mistake(
            self.project_id,
            f"Missing package: {package} — installed via conda/pip (attempt {attempt + 1})",
        )

        # Retry execution with the same script
        self._recent_lines.clear()
        exit_code = await self._executor.run_script(
            self._script_path, self._on_log_line  # type: ignore[arg-type]
        )
        await self._flush_logs(self._active_exp_id)
        await self._flush_metrics(self._active_exp_id)

        if exit_code == 0:
            interp = await self._post_run_success()
            return True, interp
        else:
            new_traceback = "\n".join(self._recent_lines)
            return await self._error_fix_loop(new_traceback, attempt=attempt + 1)

    async def _fix_with_llm(
        self,
        traceback: str,
        traceback_first_line: str,
        error_type: str,
        attempt: int,
        user_guidance: str = "",
    ) -> tuple[bool, str]:
        """
        Agentic error-fix loop: LLM is told it has web search capability and
        decides autonomously whether to use it.

        Flow per attempt:
          1. Load past mistakes from memory store (explicit, not relying on system prompt).
          2. ASSESSMENT call (chat_silent): LLM sees error + past mistakes + search option.
             LLM responds with EITHER a fix (SEARCH/REPLACE or full script)
             OR "NEED_SEARCH: <query>" if it wants web results first.
          3. If NEED_SEARCH: run the search, then FIXER call (chat_log_stream) with results.
             Otherwise: use the assessment response directly as the fix.
        """
        assert self._machine is not None
        assert self._executor is not None
        assert self._script_path is not None

        await self._machine.transition(S3Sub.FIXING, label="Fixing script")

        await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
            "message": (
                f"[FIX] Attempt {attempt + 1}/{self.max_fix_attempts}\n"
                f"{'─' * 60}\n"
                f"{traceback[-3000:]}"
            ),
            "phase": "fix",
            "message_id": f"fix-tb-{attempt}",
        })

        # ── Load past mistakes from memory (last 5, most recent first) ────────
        past_mistakes_block = _load_past_mistakes(self.project_id)

        old_code = self._script_code
        # Strip the injected preamble so the LLM only sees (and rewrites) the
        # experiment body.  This prevents double-preamble corruption on full rewrites.
        preamble = get_preamble()
        experiment_body = (
            old_code[len(preamble):].lstrip("\n")
            if old_code.startswith(preamble)
            else old_code
        )
        last_tb_lines = "\n".join(traceback.splitlines()[-30:])

        # ── ASSESSMENT CALL — LLM decides: fix directly OR request search ─────
        assessment_prompt = f"""\
You are debugging a Python ML experiment that crashed. You have access to a web search tool.

NOTE: The standard preamble (sys/os/logging/matplotlib setup, log_metric, plt.savefig patch)
is already injected automatically — do NOT include it in any fix.

ERROR:
{traceback_first_line}

LAST 30 LINES OF OUTPUT:
{last_tb_lines}

EXPERIMENT BODY (first 4000 chars — preamble already stripped):
```python
{experiment_body[:4000]}
```
{past_mistakes_block}{f"USER GUIDANCE (from researcher — follow this precisely):{chr(10)}{user_guidance}{chr(10)}{chr(10)}" if user_guidance else ""}DECISION:
- If you can fix this confidently from your training knowledge (common Python/ML errors,
  standard library issues, typical shape mismatches, etc.) → provide the fix immediately
  using SEARCH/REPLACE blocks or a full rewrite of the experiment body.
- If this error likely requires current information (specific package version bugs,
  undocumented behavior, community workarounds, recent API changes) → output EXACTLY:
  NEED_SEARCH: <one targeted search query>
  ...and nothing else on that line.

Respond now.
"""
        await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
            "message": "[FIX] Assessing error — LLM deciding whether to search…",
            "phase": "fix",
        })
        assessment = await self.client.chat_silent(
            Role.FIXER, [{"role": "user", "content": assessment_prompt}]
        )

        # ── Parse NEED_SEARCH directive ────────────────────────────────────────
        search_context = ""
        search_query = _extract_search_directive(assessment)

        if search_query:
            await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
                "message": f"[SEARCH] LLM requested search: {search_query}",
                "phase": "fix",
            })
            search_context = await _ddgs_search(search_query)
            if search_context:
                await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
                    "message": f"[SEARCH] Got {len(search_context)} chars of results",
                    "phase": "fix",
                })
            else:
                await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
                    "message": "[SEARCH] No results — proceeding with LLM knowledge only",
                    "phase": "fix",
                })

        # ── FIX CALL (streaming, visible in Show Work) ─────────────────────────
        fix_prompt = f"""\
Diagnose and fix this Python ML experiment script that crashed.

IMPORTANT: The standard preamble (sys/os/logging/matplotlib/log_metric setup) is injected
automatically BEFORE your code. Do NOT include preamble lines in your response.

ERROR (most recent {len(traceback.splitlines())} lines):
```
{traceback[-2500:]}
```
EXPERIMENT BODY (preamble already stripped):
```python
{experiment_body[:6000]}
```
{f"{chr(10)}WEB SEARCH RESULTS (query: {search_query}):{chr(10)}{search_context[:2000]}" if search_context else ""}
{past_mistakes_block}{f"USER GUIDANCE (researcher instruction — follow this precisely):{chr(10)}{user_guidance}{chr(10)}" if user_guidance else ""}
Respond with EXACTLY this structure — root cause on line 1, then the fix:

REASON: <one sentence — root cause>
<<<SEARCH>>>
<exact lines to replace — include 1-2 lines of surrounding context to be unique>
<<<REPLACE>>>
<corrected replacement — same indentation>
<<<END>>>

Use one block per change. For large rewrites (>10 lines changed) write the complete
corrected experiment body instead (starting from your imports, NOT the preamble).
"""
        await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
            "message": f"\n[FIXER LLM — generating fix…]\n{'─' * 60}\n",
            "phase": "fix",
            "message_id": f"fix-hdr-{attempt}",
        })
        messages = [{"role": "user", "content": fix_prompt}]
        fix_response = await self.client.chat_log_stream(
            Role.FIXER, messages,
            log_phase="fix",
            log_msg_id=f"fix-stream-{attempt}",
        )

        # ── Apply the fix ──────────────────────────────────────────────────────
        # Patches are applied to experiment_body (preamble-stripped) only.
        # Full rewrites are also expected to be preamble-free; preamble is prepended after.
        new_full_script: str
        try:
            patched_body, had_blocks = _apply_patch(experiment_body, fix_response)
            if had_blocks:
                new_full_script = preamble + "\n" + patched_body
            else:
                # No valid patch blocks — treat LLM output as full experiment body rewrite.
                # Strip any stray REASON: prefix and code fences before accepting.
                candidate = _strip_llm_prefix(fix_response)
                new_full_script = preamble + "\n" + candidate
        except ValueError as patch_err:
            logger.warning("Patch application failed (%s); requesting full rewrite", patch_err)
            await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
                "message": "[FIX] Patch mismatch — requesting explicit full rewrite",
                "phase": "fix",
            })
            fallback_prompt = f"""\
The previous patch could not be applied. Write ONLY the complete corrected experiment
body — no preamble (no sys/os/logging/matplotlib imports), no markdown fences.

EXPERIMENT BODY (current):
```python
{experiment_body}
```

ERROR:
```
{traceback[-2000:]}
```
"""
            fb_messages = [{"role": "user", "content": fallback_prompt}]
            fb_response = await self.client.chat_silent(Role.FIXER, fb_messages)
            candidate = _strip_llm_prefix(fb_response)
            new_full_script = preamble + "\n" + candidate

        # ── Sanity checks before writing ───────────────────────────────────────
        # 1. Reject if nothing actually changed (no-op fix wastes an attempt).
        if new_full_script.strip() == old_code.strip():
            await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
                "message": "[FIX] Fix produced no changes — skipping write, treating as failed attempt",
                "phase": "fix",
            })
            return await self._error_fix_loop(traceback, attempt=attempt + 1, user_guidance=user_guidance)

        # 2. Validate Python syntax before writing; corrupt scripts cause infinite loops.
        try:
            import ast as _ast
            _ast.parse(new_full_script)
        except SyntaxError as se:
            await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
                "message": (
                    f"[FIX] Generated script has syntax error ({se}) — "
                    "requesting clean rewrite"
                ),
                "phase": "fix",
            })
            clean_prompt = f"""\
Write ONLY the corrected Python experiment body. No preamble, no markdown.
The previous fix attempt introduced a syntax error: {se}

EXPERIMENT BODY (original):
```python
{experiment_body}
```

ERROR:
```
{traceback[-1500:]}
```
"""
            clean_resp = await self.client.chat_silent(
                Role.FIXER, [{"role": "user", "content": clean_prompt}]
            )
            candidate = _strip_llm_prefix(clean_resp)
            new_full_script = preamble + "\n" + candidate
            # If still invalid after the clean rewrite, give up this attempt
            try:
                _ast.parse(new_full_script)
            except SyntaxError as se2:
                await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
                    "message": f"[FIX] Second rewrite still invalid ({se2}) — attempt failed",
                    "phase": "fix",
                })
                return await self._error_fix_loop(traceback, attempt=attempt + 1, user_guidance=user_guidance)

        diff_text = _compute_diff(old_code, new_full_script, self._script_path.name)

        # Emit diff to log tab for transparency
        await self.ws.send(self.pid_str, "log", {  # type: ignore[attr-defined]
            "message": (
                f"[FIX DIFF — attempt {attempt + 1}]\n"
                f"{'─' * 60}\n"
                f"{diff_text[:4000]}"
            ),
            "phase": "fix",
        })

        # Write patched script
        self._script_path.write_text(new_full_script)
        self._script_code = new_full_script

        _capture_mistake(
            self.project_id,
            (
                f"Error (attempt {attempt + 1}): {traceback_first_line}\n"
                f"Fix applied: {fix_response[:300]}"
            ),
        )

        # Retry
        self._recent_lines.clear()
        exit_code = await self._executor.run_script(self._script_path, self._on_log_line)
        await self._flush_logs(self._active_exp_id)
        await self._flush_metrics(self._active_exp_id)

        if exit_code == 0:
            interp = await self._post_run_success()
            return True, interp
        else:
            new_traceback = "\n".join(self._recent_lines)
            return await self._error_fix_loop(new_traceback, attempt=attempt + 1, user_guidance=user_guidance)

    # ── DB flush helpers ───────────────────────────────────────────────────────

    async def _flush_logs(self, exp_id: int | None) -> None:
        if not self._pending_logs or exp_id is None:
            return
        entries = list(self._pending_logs)
        self._pending_logs.clear()
        try:
            with Session(get_engine()) as s:
                for level, msg, phase in entries:
                    s.add(RunLog(
                        experiment_id=exp_id,
                        level=level,
                        message=msg[:4096],
                        phase=phase,
                    ))
                s.commit()
        except Exception as exc:
            logger.warning("RunLog flush failed: %s", exc)

    async def _flush_metrics(self, exp_id: int | None) -> None:
        if not self._pending_metrics or exp_id is None:
            return
        entries = list(self._pending_metrics)
        self._pending_metrics.clear()
        try:
            with Session(get_engine()) as s:
                for name, value, step in entries:
                    s.add(Metric(
                        experiment_id=exp_id,
                        name=name,
                        step=step,
                        value=value,
                    ))
                s.commit()
        except Exception as exc:
            logger.warning("Metric flush failed: %s", exc)

    # ── Post-run success ───────────────────────────────────────────────────────

    async def _post_run_success(self) -> str:
        """
        Commit to git, update experiment status to done, stream interpretation.
        Returns the interpretation text (caller decides whether to loop or stop).
        """
        exp_id = self._active_exp_id
        assert exp_id is not None
        assert self._git is not None
        assert self._machine is not None

        metrics_summary = _format_metrics_summary(self._pending_metrics)
        commit_msg = (
            f"exp {self._plan.get('iteration', 1)}: "
            f"{str(self._plan.get('objective', 'run'))[:60]}"
            f" | {metrics_summary}"
            f" | seed={self._plan.get('seed', 42)}"
        )
        commit_hash = ""
        try:
            commit_hash = self._git.commit(commit_msg)
        except Exception as exc:
            logger.warning("Git commit failed: %s", exc)

        try:
            with Session(get_engine()) as s:
                exp = s.get(Experiment, exp_id)
                if exp is not None:
                    exp.status = ExperimentStatus.done
                    exp.finished_at = datetime.utcnow()
                    if exp.started_at:
                        exp.runtime_seconds = (
                            exp.finished_at - exp.started_at
                        ).total_seconds()
                    if commit_hash:
                        exp.git_commit = commit_hash
                    s.add(exp)
                    s.commit()
        except Exception as exc:
            logger.warning("Experiment done-update failed: %s", exc)

        await self._machine.transition(S3Sub.INTERPRETING, label="Interpreting results")
        interpretation = await self._interpret(exp_id, self._plan)
        return interpretation

    async def _mark_experiment_failed(self) -> None:
        exp_id = self._active_exp_id
        if exp_id is None:
            return
        try:
            with Session(get_engine()) as s:
                exp = s.get(Experiment, exp_id)
                if exp is not None:
                    exp.status = ExperimentStatus.failed
                    exp.finished_at = datetime.utcnow()
                    s.add(exp)
                    s.commit()
        except Exception as exc:
            logger.warning("Experiment failed-update failed: %s", exc)

    # ── Interpretation ─────────────────────────────────────────────────────────

    async def _interpret(self, exp_id: int, plan: dict) -> str:
        """Stream a plain-language interpretation as a new assistant message.
        Returns the full response text."""
        metrics_table = ""
        try:
            with Session(get_engine()) as s:
                metrics = s.exec(
                    select(Metric)
                    .where(Metric.experiment_id == exp_id)
                    .order_by(Metric.step.asc())  # type: ignore[arg-type]
                ).all()
            if metrics:
                by_name: dict[str, list[tuple[int, float]]] = {}
                for m in metrics:
                    by_name.setdefault(m.name, []).append((m.step, m.value))
                rows = [
                    f"  {name}: last={pts[-1][1]:.4f} ({len(pts)} points)"
                    for name, pts in by_name.items()
                ]
                metrics_table = "METRICS:\n" + "\n".join(rows)
        except Exception as exc:
            logger.debug("Metrics load for interpreter failed: %s", exc)
            metrics_table = "(metrics unavailable)"

        ascii_section = ""
        if self._collected_ascii:
            ascii_section = "PLOTS (ASCII):\n" + "\n\n".join(self._collected_ascii)

        asst_id: int | None = None
        engine = get_engine()
        try:
            with Session(engine) as s:
                row = Message(
                    project_id=self.project_id,
                    role=MessageRole.assistant,
                    content="",
                    kind=MessageKind.chat,
                    metadata_json="{}",
                )
                s.add(row)
                s.commit()
                s.refresh(row)
                asst_id = row.id
        except Exception as exc:
            logger.warning("Interpreter placeholder failed: %s", exc)

        if asst_id is not None:
            await self.ws.send(self.pid_str, "msg_start", {"msg_id": asst_id})  # type: ignore[attr-defined]

        prompt = f"""\
Experiment completed. Here are the outputs:

{metrics_table}

{ascii_section}

Plan objective: {plan.get("objective", "(no objective)")}

Write a concise plain-language interpretation (3–4 short paragraphs):
1. What the metrics show (is the model learning / converged / diverging?)
2. Whether the approach is working as expected
3. The most important observation or anomaly from the data
4. A concrete recommendation for the next iteration
"""
        messages = [{"role": "user", "content": prompt}]
        response = await self.client.chat(
            Role.INTERPRETER, messages, message_id=str(asst_id or "")
        )

        if asst_id is not None:
            try:
                with Session(engine) as s:
                    row = s.get(Message, asst_id)
                    if row is not None:
                        row.content = response
                        row.metadata_json = json.dumps({"model": self.model})
                        s.add(row)
                        s.commit()
            except Exception as exc:
                logger.warning("Interpreter persist failed: %s", exc)

        await self.ws.send(self.pid_str, "done", {  # type: ignore[attr-defined]
            "msg_id": asst_id,
            "summary": "Interpretation complete",
        })

        return response

    # ── Next-iteration proposal (Sub-step 7.5) ────────────────────────────────

    async def _propose_next_iteration(
        self, exp: Experiment, interpretation: str
    ) -> bool:
        """
        Generate a next-iteration proposal using the collaborator role, then
        transition to AWAITING_NEXT.

        Returns True if the user approved and a new Experiment row was created
        (caller should reset state and loop). Returns False if rejected or if
        an error occurred (machine already cleaned up).
        """
        assert self._machine is not None

        plan = json.loads(exp.plan_json or "{}")

        # Collect metrics summary from DB
        metrics_summary = "(no metrics)"
        try:
            with Session(get_engine()) as s:
                metrics = s.exec(
                    select(Metric).where(Metric.experiment_id == exp.id)
                ).all()
            if metrics:
                by_name: dict[str, float] = {}
                for m in metrics:
                    by_name[m.name] = m.value  # keep last value per metric
                metrics_summary = ", ".join(
                    f"{k}={v:.4f}" for k, v in list(by_name.items())[:8]
                )
        except Exception as exc:
            logger.debug("Metrics load for next-iter proposal failed: %s", exc)

        proposal_prompt = f"""\
You are an ML research assistant reviewing a completed experiment.

PREVIOUS PLAN:
{json.dumps(plan, indent=2)}

KEY METRICS (final values):
{metrics_summary}

INTERPRETATION:
{interpretation[:1500]}

Based on these results, propose the next iteration as a JSON object with these exact fields:
{{
  "changes": ["change 1", "change 2"],
  "objective": "one-sentence objective for the next run",
  "architecture": "model architecture description",
  "hyperparams": {{"lr": 0.001, "batch_size": 32, "epochs": 20}},
  "dataset": "dataset URI or name (same as before unless you have a specific reason to change)",
  "seed": 43,
  "version_mode": "modify",
  "rationale": "2-3 sentences explaining why these changes will improve results"
}}

RULES:
- "version_mode": use "modify" for hyperparameter tweaks, "branch" for architectural changes
- Increment seed by 1 from the previous run
- "changes" should be a list of 2-4 concrete, actionable changes vs the previous run
- Return ONLY the JSON object. No markdown fences, no prose before or after.
"""

        proposal: dict = {}
        try:
            raw = await self.client.chat_silent(
                Role.COLLABORATOR,
                [{"role": "user", "content": proposal_prompt}],
            )
            proposal = json.loads(_strip_code_fences(raw))
        except Exception as exc:
            logger.warning("Next-iteration proposal generation failed: %s", exc)
            proposal = {
                "changes": ["Continue from previous iteration with minor adjustments"],
                "objective": str(plan.get("objective", "Continue experiment")),
                "architecture": str(plan.get("architecture", "")),
                "hyperparams": plan.get("hyperparams", {}),
                "dataset": str(plan.get("dataset", "")),
                "seed": int(plan.get("seed", 42)) + 1,
                "version_mode": "modify",
                "rationale": "Previous run completed. Proposing to continue with minor adjustments.",
            }

        proposal["kind"] = "next_iteration"
        proposal["experiment_id"] = exp.id
        proposal["iteration"] = exp.iteration + 1

        MAX_REPROPOSALS = 3
        reproposal_count = 0
        feedback_history = ""

        while True:
            # Transition to AWAITING_NEXT — blocks until user approves or rejects
            gate_response = await self._machine.transition(
                S3Sub.AWAITING_NEXT,
                plan=proposal,
                label=f"Iteration {exp.iteration + 1} proposal",
            )

            if gate_response is not None and gate_response.approved:
                break  # user approved — continue below

            # User rejected
            feedback = gate_response.feedback if gate_response else ""
            if not feedback or reproposal_count >= MAX_REPROPOSALS:
                # No feedback = "stop here", or reproposal limit reached
                await self._machine.report_done("No more iterations requested")
                unregister_machine(self.project_id)
                return False

            # User provided feedback — regenerate proposal incorporating it
            reproposal_count += 1
            feedback_history += f"\nFeedback round {reproposal_count}: {feedback}"
            await self.ws.broadcast_progress(
                self.pid_str, stage=3, substage="proposing",
                label=f"Revising iteration proposal…", current=0, total=0, status="running",
            )
            revised_prompt = proposal_prompt + f"""

USER FEEDBACK ON PREVIOUS PROPOSAL:
{feedback_history}

Revise the proposal to address this feedback. Return the same JSON format.
"""
            try:
                raw = await self.client.chat_silent(
                    Role.COLLABORATOR,
                    [{"role": "user", "content": revised_prompt}],
                )
                proposal = json.loads(_strip_code_fences(raw))
            except Exception as exc:
                logger.warning("Re-proposal generation failed: %s", exc)
                # Keep the previous proposal if revision fails
            proposal["kind"] = "next_iteration"
            proposal["experiment_id"] = exp.id
            proposal["iteration"] = exp.iteration + 1

        # Merge user edits (e.g. version_mode change) into the proposal
        edited = gate_response.edited_plan if gate_response.edited_plan is not None else {}
        vm_str = str(edited.get("version_mode", proposal.get("version_mode", "modify"))).lower()
        version_mode = VersionMode.branch if vm_str == "branch" else VersionMode.modify

        next_plan = {k: v for k, v in proposal.items()
                     if k not in ("kind", "experiment_id", "iteration")}
        # Apply any user edits to the plan
        for k, v in edited.items():
            if k not in ("kind", "experiment_id", "iteration"):
                next_plan[k] = v
        next_plan["version_mode"] = vm_str

        seed_val = int(next_plan.get("seed", int(plan.get("seed", 42)) + 1))

        try:
            with Session(get_engine()) as s:
                new_exp = Experiment(
                    project_id=self.project_id,
                    iteration=exp.iteration + 1,
                    status=ExperimentStatus.planned,
                    plan_json=json.dumps(next_plan),
                    version_mode=version_mode,
                    seed=seed_val,
                )
                s.add(new_exp)
                s.commit()
        except Exception as exc:
            logger.error("Failed to create next experiment row: %s", exc)
            await self._machine.report_done("Failed to create next iteration")
            unregister_machine(self.project_id)
            return False

        return True

    # ── State reset between iterations ────────────────────────────────────────

    def _reset_run_state(self) -> None:
        """Reset per-run state so the next iteration starts clean."""
        self._current_phase = RunPhase.preprocess
        self._pending_logs.clear()
        self._pending_metrics.clear()
        self._collected_ascii.clear()
        self._script_code = ""
        self._script_path = None
        self._active_exp_id = None
        self._recent_lines.clear()

    # ── Machine management ─────────────────────────────────────────────────────

    def _get_or_create_machine(self) -> ExperimentStateMachine:
        existing = get_machine(self.project_id)
        if existing is not None:
            return existing
        machine = ExperimentStateMachine(
            project_id=self.project_id,
            ws_manager=self.ws,
            db_session=self.session,
            auto_approve=self.auto_approve,
        )
        machine.current_stage = Stage.RUN
        register_machine(self.project_id, machine)
        return machine


# ── Module-level helpers ───────────────────────────────────────────────────────

def _script_path_for(exp_folder: Path, iteration: int, plan: dict) -> Path:
    vm = plan.get("version_mode", "modify")
    if vm == VersionMode.branch or vm == "branch":
        return exp_folder / "runs" / f"iter_{iteration}" / "run.py"
    return exp_folder / f"run_{iteration}.py"


def _load_prev_script(exp_folder: Path, iteration: int) -> str:
    if iteration <= 1:
        return ""
    prev = exp_folder / f"run_{iteration - 1}.py"
    return prev.read_text() if prev.exists() else ""


def _compute_diff(old_code: str, new_code: str, filename: str) -> str:
    old_lines = old_code.splitlines(keepends=True)
    new_lines = new_code.splitlines(keepends=True)
    return "".join(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=3,
    ))


def _apply_patch(original: str, patch_text: str) -> tuple[str, bool]:
    """
    Apply <<<SEARCH>>> / <<<REPLACE>>> / <<<END>>> blocks from patch_text to original.
    <<<END>>> is optional: the parser also accepts blocks terminated by the next
    <<<SEARCH>>> or end-of-string.

    Returns (patched_code, True)  — all non-trivial blocks matched and applied.
    Returns (original, False)     — no non-trivial blocks found; caller should treat
                                    LLM output as a full-script rewrite instead.
    Raises ValueError              — blocks were found but at least one SEARCH string
                                    could not be located in original.
    """
    # Accept <<<END>>> as delimiter OR next <<<SEARCH>>> OR end-of-string.
    blocks = re.findall(
        r'<<<SEARCH>>>\n(.*?)\n?<<<REPLACE>>>\n(.*?)(?:\n?<<<END>>>|(?=\n<<<SEARCH>>>)|\Z)',
        patch_text,
        re.DOTALL,
    )
    if not blocks:
        return original, False

    result = original
    applied_any = False
    for search_raw, replace_raw in blocks:
        # Skip no-op blocks (SEARCH == REPLACE — LLM hallucinated an identical change)
        if search_raw.strip() == replace_raw.strip():
            logger.debug("_apply_patch: skipping no-op block")
            continue

        applied = False
        # Try progressively looser matches: exact → strip trailing NL → strip all edge WS
        for s, r in [
            (search_raw, replace_raw),
            (search_raw.rstrip('\n'), replace_raw.rstrip('\n')),
            (search_raw.strip(), replace_raw.strip()),
        ]:
            if s and s in result:
                result = result.replace(s, r, 1)
                applied = True
                applied_any = True
                break
        if not applied:
            raise ValueError(
                f"SEARCH block not found in script — patch cannot be applied.\n"
                f"Block (first 200 chars): {search_raw[:200]!r}"
            )

    return (result, True) if applied_any else (original, False)


def _extract_dataset_uris(plan: dict) -> list[str]:
    uris: list[str] = []
    for key in ("dataset", "datasets"):
        val = plan.get(key)
        if isinstance(val, str) and val:
            uris.append(val)
        elif isinstance(val, list):
            uris.extend(str(v) for v in val if v)
    return uris


def _format_metrics_summary(pending: list[tuple[str, float, int]]) -> str:
    if not pending:
        return "no metrics"
    by_name: dict[str, float] = {}
    for name, value, _ in pending:
        by_name[name] = value
    return " | ".join(f"{k}={v:.4f}" for k, v in list(by_name.items())[:4])


def _strip_code_fences(text: str) -> str:
    """Strip leading/trailing markdown code fences from LLM output."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]   # drop ``` or ```python
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _classify_error(traceback: str) -> tuple[str, str]:
    """
    Classify the error from a traceback string.
    Returns (error_type, extra_info).  extra_info is the captured group
    (e.g. module name) or "" if the pattern has no capture group.
    """
    for error_type, pattern, group_idx in _CLASSIFY_PATTERNS:
        m = pattern.search(traceback)
        if m:
            extra = m.group(group_idx) if group_idx and m.lastindex else ""
            return error_type, extra
    return "generic", ""


async def _ddgs_search(query: str, max_results: int = 5) -> str:
    """
    Quick DuckDuckGo search for error context.
    Returns formatted results string, or "" on failure.
    """
    try:
        from duckduckgo_search import DDGS  # type: ignore  # noqa: PLC0415
        results: list[str] = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                title = r.get("title", "")
                snippet = r.get("body", "")[:300]
                url = r.get("href", "")
                results.append(f"[{title}] {snippet}\n{url}")
        return "\n\n".join(results)
    except ImportError:
        logger.debug("duckduckgo-search not installed; skipping error search")
        return ""
    except Exception as exc:
        logger.debug("DDGS search failed for '%s': %s", query, exc)
        return ""


def _strip_llm_prefix(text: str) -> str:
    """
    Strip LLM preamble from a full-rewrite response:
    - Remove a leading "REASON: ..." line
    - Strip markdown code fences (``` / ```python)
    - If the response accidentally starts with preamble imports (import sys / import os /
      logging.basicConfig), skip lines until we hit the actual experiment code
    """
    text = text.strip()

    # Strip REASON: prefix line
    lines = text.splitlines()
    if lines and lines[0].strip().upper().startswith("REASON:"):
        lines = lines[1:]
    text = "\n".join(lines).strip()

    # Strip code fences
    text = _strip_code_fences(text)

    # Strip any leftover preamble block: if the text starts looking like our
    # standard preamble, skip to the first line after the preamble sentinel comment
    # ("# ── Phase markers" or the blank line after logging setup).
    _PREAMBLE_SENTINELS = (
        "# ── Phase markers",
        "# ── Logging",
        "# ── ALFRED protocol helpers",
        "matplotlib.use(",
        "logging.basicConfig(",
    )
    preamble_lines = text.splitlines()
    start_idx = 0
    if preamble_lines and preamble_lines[0].strip() in ("import sys", "import os"):
        for i, line in enumerate(preamble_lines):
            if any(line.strip().startswith(s) for s in _PREAMBLE_SENTINELS):
                # Find next non-comment, non-blank line after the preamble block
                for j in range(i + 1, len(preamble_lines)):
                    stripped = preamble_lines[j].strip()
                    if stripped and not stripped.startswith("#"):
                        start_idx = j
                        break
                break
    return "\n".join(preamble_lines[start_idx:]).strip()


def _load_past_mistakes(project_id: int, limit: int = 5) -> str:
    """
    Load the most recent mistake records for this project from the memory store.
    Returns a formatted block for injection into fix prompts, or "".
    """
    try:
        from alfred.memory.store import list_items  # noqa: PLC0415
        from alfred.models.db_models import MemoryType  # noqa: PLC0415
        with Session(get_engine()) as s:
            items = list_items(
                s,
                project_id=project_id,
                memory_type=MemoryType.mistake,
                active_only=True,
            )
        if not items:
            return ""
        recent = list(items)[:limit]
        lines = [f"- {item.content[:300]}" for item in recent]
        return "\nPAST MISTAKES (avoid repeating these):\n" + "\n".join(lines) + "\n"
    except Exception as exc:
        logger.debug("_load_past_mistakes failed (non-fatal): %s", exc)
        return ""


def _extract_search_directive(text: str) -> str:
    """
    Check if the LLM responded with a NEED_SEARCH directive.
    Returns the search query string, or "" if the LLM wants to fix directly.
    """
    for line in text.strip().splitlines()[:5]:
        stripped = line.strip()
        if stripped.upper().startswith("NEED_SEARCH:"):
            query = stripped[len("NEED_SEARCH:"):].strip().strip('"').strip("'")
            return query[:200]
    return ""


def _capture_mistake(project_id: int, content: str) -> None:
    """Record an error-fix event in the project's memory store."""
    try:
        from alfred.memory.store import capture_mistake  # noqa: PLC0415
        with Session(get_engine()) as s:
            capture_mistake(s, project_id=project_id, content=content)
    except Exception as exc:
        logger.debug("Memory capture_mistake failed (non-fatal): %s", exc)


def _load_config_safe():
    try:
        from alfred.config import load_config  # noqa: PLC0415
        return load_config()
    except Exception:
        return None
