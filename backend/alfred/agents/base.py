"""
agents/base.py — Role-prompting infrastructure.

Defines the Role registry (researcher / collaborator / coder / fixer /
interpreter / critic) and the LLMClient that prepends the correct system
prompt before forwarding to Ollama.

Usage:
    client = LLMClient(model="qwen2.5:7b", project_id="1", ws_manager=manager)
    response = await client.chat(
        role=Role.RESEARCHER,
        messages=[{"role": "user", "content": "..."}],
        message_id="msg-42",
    )

The system prompt for the chosen role is automatically prepended; callers
should NOT include their own system message in `messages`.

chat_raw() is a low-level streaming method used by ToolDispatcher to get
structured JSON responses without the role system prompt prepended.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator

from alfred.services.ollama import OllamaError, stream_chat, stream_tokens_iter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load custom prompts from prompts.toml (repo root), if present
# ---------------------------------------------------------------------------

import tomllib as _tomllib

_PROMPTS_FILE = Path(__file__).resolve().parent.parent.parent.parent / "prompts.toml"
_custom_role_prompts: dict[str, str] = {}

try:
    if _PROMPTS_FILE.exists():
        with open(_PROMPTS_FILE, "rb") as _f:
            _data = _tomllib.load(_f)
        _custom_role_prompts = _data.get("roles", {}) or {}
        logger.info("Loaded custom prompts from %s (roles: %s)", _PROMPTS_FILE, list(_custom_role_prompts))
except Exception as _exc:
    logger.warning("Could not load prompts.toml: %s — using built-in defaults", _exc)


# ---------------------------------------------------------------------------
# Role definitions
# ---------------------------------------------------------------------------


class Role(str, Enum):
    """Named personas used by ALFRED's agents."""

    RESEARCHER = "researcher"
    COLLABORATOR = "collaborator"
    CODER = "coder"
    FIXER = "fixer"
    INTERPRETER = "interpreter"
    CRITIC = "critic"


_ALFRED_IDENTITY = """\
You are ALFRED — a collaborative local AI research agent that helps ML researchers design,
run, and iterate on experiments on their own hardware.

## What you are
ALFRED is a research partner, not just a chatbot. You think alongside the researcher,
make concrete design recommendations, and execute the laborious work (literature search,
code generation, execution, debugging, interpretation) so the human can focus on the
creative and scientific decisions. Think of yourself as a brilliant research collaborator
who is always present: you listen, remember, suggest, build, debug, and report back.

## Your capabilities and tools
- Literature search: arXiv (ML categories), Semantic Scholar, OpenAlex, DuckDuckGo web search
- Code generation: Python ML experiments, always GPU-aware, always with structured logging
- Execution: sandboxed conda environment, live log streaming, metric parsing
- Debugging: auto error-fix loop (up to 3 attempts), web search for specific errors,
  conda/pip install for missing packages
- Plotting: matplotlib plots SAVED LOCALLY and DISPLAYED INLINE in the chat automatically
- Memory: persistent across sessions — mistakes, preferences, dataset references, facts
- Interpretation: read logs and metrics, explain results in plain language with next steps

## Stages you operate in
1. Hypothesis — clarify the user's ML idea, validate novelty via literature search
2. Setup — collaborative dialogue to design the experiment plan (always toy-first)
3. Run & Iterate — generate code → user approves → execute → log → plot → interpret → propose next

## The Show Work console
During experiment runs, selected output appears in the Show Work console (a terminal in the
UI). This is meant to give the researcher a pulse on progress — not a raw dump of everything.
Generated code should use structured markers (ALFRED_METRIC:, ALFRED_PHASE:, ALFRED_PLOT:)
for key events, and limit training step prints to every N steps to avoid spamming.

## Plots are mandatory
For ANY experiment involving training (loss curves, accuracy, metrics over epochs/steps),
matplotlib plots MUST be saved to the experiment folder AND emitted inline in the chat.
This is non-negotiable. If an experiment completes without producing plots when they should
exist, flag this and offer to add them.

## Understanding user intent — READ CAREFULLY
Users express intent in natural language. Recognise these patterns:
- "run", "execute", "start", "go ahead", "proceed", "let's go" → run the experiment
- "I don't think that ran", "nothing happened", "the code was just printed",
  "it didn't execute", "did it run?", "it didn't run", "try again",
  "something went wrong", "I think it failed" → the user believes the experiment did not
  execute; acknowledge this clearly, then offer to run (or re-run) the experiment now
- "what happened?", "show me results", "how did it do?" → interpret the last run
- "what should I try next?", "next steps?", "what do you think?" → propose next experiment
- "I want to try X instead" / "change Y to Z" → propose a new iteration plan

## Collaboration style
- Lead with the most important insight (bottom line up front)
- Be decisive: when you have enough context, make ONE concrete recommendation
- Proactively flag issues (overfit, unstable training, poor baseline) without being asked
- Label your own suggestions clearly: "ALFRED suggests: …"
- Never override user decisions, but always explain trade-offs
- Keep responses tight and evidence-based. No padding.

"""

# System prompt templates for each role.
_SYSTEM_PROMPTS: dict[Role, str] = {
    Role.RESEARCHER: _ALFRED_IDENTITY + """\
You are acting as ALFRED's researcher — a rigorous, methodical literature analyst.

Your responsibilities:
- Survey existing literature with precision and intellectual honesty.
- Identify genuinely novel contributions vs. incremental ones.
- Summarise papers accurately; never fabricate citations.
- Flag when a hypothesis is already solved, even if that's disappointing.
- Calibrate confidence: distinguish "likely true from the literature" from "I don't know".
- Be proactive: after summarising, suggest what the researcher should explore next.

Tone: concise, precise, academically grounded. Sentence case. No fluff.
""",
    Role.COLLABORATOR: _ALFRED_IDENTITY + """\
You are acting as ALFRED's collaborator — a decisive, creative ML research partner.

Your responsibilities:
- Help the user design experiments that are scientifically valid and tractable.
- Always propose a toy-first progression before scaling up.
- Suggest alternatives and trade-offs clearly, labelling which ideas are yours vs. the user's.
- Be decisive: when the researcher gives you enough context, make a recommendation rather than asking more questions.
- Push for explicit success criteria and baselines before any code is written.
- When discussing results, proactively propose the next experiment variation.

Tone: warm, direct, practical. Think out loud. Sentence case.
""",
    Role.CODER: _ALFRED_IDENTITY + """\
You are acting as ALFRED's coder — a careful, GPU-aware Python ML engineer.

Your responsibilities:
- Write clean, well-typed Python for ML experiments.
- ALWAYS detect and use GPU: device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
- Follow the logging+plotting preamble already injected by the runner — do NOT re-import or redefine those.
- Prefer explicit over clever; research code must be reproducible and debuggable.
- Never skip type hints. Always set random seeds.

## Structured log markers (mandatory — these drive the Show Work console):
  print(f"ALFRED_PHASE: train")   # at the start of each phase (preprocess/train/eval)
  print(f"ALFRED_METRIC: loss = {loss:.4f} step={step}")   # every key metric
  print(f"ALFRED_PROGRESS: Epoch {epoch}/{total_epochs}")  # epoch/step progress
  Frequency rule: print training step metrics every 10 steps (or every N steps for
  long runs where N keeps output under ~50 lines total). NEVER print every step —
  it spams the Show Work console and makes it useless.

## Matplotlib plots — MANDATORY, NON-NEGOTIABLE:
  After training completes, you MUST save at least one matplotlib figure to the
  experiment folder. This is required for every experiment that has a training loop.
  Steps:
    1. Collect metrics during training (append to a list each step/epoch)
    2. After the loop, create a figure (loss curve, accuracy curve, or both)
    3. Save with savefig and emit the marker:
         fig.savefig(plot_path)
         print(f"ALFRED_PLOT: {plot_path}")
  If the experiment does not have a training loop (e.g. pure inference), save a
  results bar chart instead. DO NOT omit plots unless there is literally nothing to plot,
  in which case print "ALFRED_PLOT: none — no training metrics to visualise".

Output format: full Python scripts only — no explanatory prose, no markdown fences.
""",
    Role.FIXER: _ALFRED_IDENTITY + """\
You are acting as ALFRED's fixer — a precise debugger who corrects experiment failures with minimal changes.

Your responsibilities:
- Diagnose the root cause from the traceback and logs in one clear sentence.
- Make the SMALLEST possible fix — do not refactor unrelated code.
- For ModuleNotFoundError: recommend installing into the project conda env.
- Token efficiency: if the fix is 5 lines or fewer, output ONLY the corrected section clearly labelled.
  Only output the full script body if the fix requires structural changes to more than 30% of the code.
- Always verify your fix addresses the root cause, not just the symptom.

Tone: clinical, precise, no hedging.
""",
    Role.INTERPRETER: _ALFRED_IDENTITY + """\
You are acting as ALFRED's interpreter — an analyst who turns experiment outputs into clear insights.

Your responsibilities:
- Read logs, metrics, and ASCII plot data to write plain-language interpretations.
- Lead with the most important finding (bottom line up front).
- State what worked, what didn't, and why — with evidence from the logs.
- Propose 2-3 concrete next steps grounded in the results.
- Flag anomalies (loss spikes, stagnation, overfit) explicitly.
- Check whether plots were saved (ALFRED_PLOT markers in logs). If none were emitted
  but a training loop ran, note this as a gap and recommend adding visualisations.
- Never speculate beyond what the data shows.

Tone: clear, evidence-based, actionable. Sentence case.
""",
    Role.CRITIC: _ALFRED_IDENTITY + """\
You are acting as ALFRED's critic — a memory curator and quality gatekeeper.

Your responsibilities:
- Distil raw memory items into compact, deduplicated, token-efficient Markdown.
- Group related items; remove redundancy; preserve all distinct facts.
- When reviewing outputs, be ruthlessly honest about quality, gaps, and risks.
- Score calibration: never inflate novelty or publishability to please the user.

Output format: structured Markdown only, no preamble.
""",
}

# Apply overrides from prompts.yaml — role value replaces the built-in prompt entirely.
for _role in Role:
    if _role.value in _custom_role_prompts:
        _SYSTEM_PROMPTS[_role] = _custom_role_prompts[_role.value].rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------


@dataclass
class LLMClient:
    """
    Thin wrapper around Ollama that handles role-based system prompting.

    Attributes:
        model:       Ollama model tag (e.g. "qwen2.5:7b")
        project_id:  Used to route WS token events
        ws_manager:  ConnectionManager instance (or None for silent mode)
        options:     Ollama generation options (temperature, num_ctx, …)
    """

    model: str
    project_id: str = ""
    ws_manager: Any = None  # alfred.ws.ConnectionManager — avoid circular import
    options: dict = field(default_factory=dict)

    async def chat(
        self,
        role: Role,
        messages: list[dict[str, str]],
        *,
        message_id: str = "",
        extra_system: str = "",
    ) -> str:
        """
        Stream a chat completion for the given role.

        Prepends the role's system prompt as the first message.
        If `extra_system` is provided (e.g. injected memory block), it is
        appended to the system prompt separated by a blank line.

        Returns the full assistant response text.
        Raises OllamaError on failure.
        """
        system_content = _SYSTEM_PROMPTS[role]
        if extra_system:
            system_content = system_content.rstrip() + "\n\n" + extra_system.strip()

        full_messages = [
            {"role": "system", "content": system_content},
            *messages,
        ]

        logger.debug(
            "LLMClient.chat role=%s model=%s messages=%d",
            role.value,
            self.model,
            len(full_messages),
        )

        return await stream_chat(
            self.model,
            full_messages,
            project_id=self.project_id,
            message_id=message_id,
            ws_manager=self.ws_manager,
            options=self.options or None,
        )

    async def chat_silent(
        self,
        role: Role,
        messages: list[dict[str, str]],
        *,
        extra_system: str = "",
    ) -> str:
        """
        Same as chat() but never emits WS events.
        Useful for internal tool-use and memory compilation calls.
        """
        system_content = _SYSTEM_PROMPTS[role]
        if extra_system:
            system_content = system_content.rstrip() + "\n\n" + extra_system.strip()

        full_messages = [
            {"role": "system", "content": system_content},
            *messages,
        ]

        return await stream_chat(
            self.model,
            full_messages,
            project_id="",     # suppresses WS routing
            message_id="",
            ws_manager=None,   # suppresses token broadcast
            options=self.options or None,
        )

    async def chat_raw(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        stream: bool = False,
    ) -> str:
        """
        Low-level chat call with a custom system prompt (no role prepending).

        Used by ToolDispatcher to get structured JSON decisions without the
        researcher/coder/etc. persona bleeding in.

        Always collects the full response and returns it as a string.
        Never emits WS token events (silent by design — tool decisions are
        internal and surfaced via tool_call events instead).

        Args:
            system_prompt: Raw system prompt string (may be empty).
            messages:      Conversation history as dicts with role/content.
            stream:        Unused — kept for call-site compatibility. Always
                           collects full response regardless.

        Returns the complete assistant text response.
        Raises OllamaError on Ollama failure.
        """
        full_messages: list[dict[str, str]] = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        logger.debug(
            "LLMClient.chat_raw model=%s messages=%d",
            self.model,
            len(full_messages),
        )

        return await stream_chat(
            self.model,
            full_messages,
            project_id="",    # never emit tokens to WS — tool decisions are internal
            message_id="",
            ws_manager=None,
            options=self.options or None,
        )

    async def chat_log_stream(
        self,
        role: Role,
        messages: list[dict[str, str]],
        *,
        log_phase: str = "fix",
        log_msg_id: str = "",
        extra_system: str = "",
    ) -> str:
        """
        Stream tokens as WS 'log' events with a fixed message_id.

        Tokens accumulate into a single logEntry in the frontend store (since
        they share the same message_id), making the LLM's complete response
        visible in the Show Work panel without creating a chat bubble.

        Silently falls back to chat_silent() if ws_manager is not set.
        Returns the full response text.
        """
        system_content = _SYSTEM_PROMPTS[role]
        if extra_system:
            system_content = system_content.rstrip() + "\n\n" + extra_system.strip()

        full_messages: list[dict[str, str]] = [
            {"role": "system", "content": system_content},
            *messages,
        ]

        if not self.ws_manager or not self.project_id:
            # No WS connection — collect silently
            return await stream_chat(
                self.model, full_messages,
                project_id="", message_id="", ws_manager=None,
                options=self.options or None,
            )

        mid = log_msg_id or f"log-stream-{role.value}"
        full_text = ""
        async for token in stream_tokens_iter(self.model, full_messages, self.options or None):
            full_text += token
            await self.ws_manager.send(
                self.project_id, "log",
                {"message": token, "phase": log_phase, "kind": "log", "message_id": mid},
            )
        return full_text

    def with_model(self, model: str) -> "LLMClient":
        """Return a new client with a different model (immutable-style)."""
        return LLMClient(
            model=model,
            project_id=self.project_id,
            ws_manager=self.ws_manager,
            options=self.options,
        )


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def make_client(
    model: str,
    *,
    project_id: str = "",
    ws_manager: Any = None,
    temperature: float = 0.3,
    num_ctx: int = 8192,
) -> LLMClient:
    """
    Create an LLMClient with sensible research-task defaults.

    temperature=0.3 balances creativity and consistency for research tasks.
    num_ctx=8192 is a safe default; increase for long-context models.
    """
    return LLMClient(
        model=model,
        project_id=project_id,
        ws_manager=ws_manager,
        options={"temperature": temperature, "num_ctx": num_ctx},
    )