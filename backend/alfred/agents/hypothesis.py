"""
agents/hypothesis.py — Stage-1 Hypothesis Validator.

Five-phase deep-research loop:
  A. Query generation    (generating_queries)
  B. Broad sweep         (sweeping_sources)  — concurrent arXiv + S2 + OpenAlex
  C. Citation snowball   (snowballing)       — Semantic Scholar expand, one hop
  D. Web sweep           (web_sweep)         — DuckDuckGo GitHub/leaderboards
  E. Synthesis           (analyzing → scoring) — landscape + 3 scored verdicts

Entry point: HypothesisAgent.run(hypothesis, feedback="")

On rejection the caller may pass user feedback; Phase A uses it to refine queries.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
from typing import Any

from sqlmodel import Session, select

from alfred.agents.base import Role, make_client
from alfred.models.db_models import (
    Experiment,
    ExperimentStatus,
    Score,
    ScoreKind,
)
from alfred.state_machine.machine import (
    S1Sub,
    Stage,
    ExperimentStateMachine,
    register_machine,
    unregister_machine,
)
from alfred.tools.base import ToolRegistry

logger = logging.getLogger(__name__)

# ── Tuning constants ─────────────────────────────────────────────────────────
MAX_QUERIES = 5
SWEEP_PER_QUERY = 12     # results per source per query
SNOWBALL_TOP_K = 10      # papers to expand
FUZZY_THRESHOLD = 0.85   # title dedup threshold
MIN_TITLE_LEN_FOR_FUZZY = 20  # below this length, require exact match to avoid false positives


# ── Context-budget formatter ─────────────────────────────────────────────────


def compact_paper_list(papers: list[dict], max_tokens: int = 5000) -> str:
    """Format papers as one-liners, stopping when token budget is exceeded."""
    lines: list[str] = []
    approx_tokens = 0
    for p in papers:
        year = str(
            p.get("year") or str(p.get("published") or "?")[:4]
        ).strip() or "?"
        title = (p.get("title") or "Untitled")[:120]
        venue = p.get("venue") or p.get("source", "")
        tldr = (
            p.get("tldr")
            or p.get("snippet")
            or (p.get("abstract") or "")[:200]
        )
        line = f"[{year}] {title}"
        if venue:
            line += f" ({venue})"
        if tldr:
            line += f" — {str(tldr)[:200]}"
        approx_tokens += len(line) // 4
        if approx_tokens > max_tokens:
            break
        lines.append(line)
    return "\n".join(lines)


# ── Deduplication ─────────────────────────────────────────────────────────────


def _fuzzy_match(title_a: str, title_b: str) -> bool:
    a, b = title_a.lower().strip(), title_b.lower().strip()
    if a == b:
        return True
    # Short strings produce false positives; require exact match below the length floor
    if len(a) < MIN_TITLE_LEN_FOR_FUZZY or len(b) < MIN_TITLE_LEN_FOR_FUZZY:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= FUZZY_THRESHOLD


def _dedup(papers: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen_titles: list[str] = []
    for p in papers:
        t = p.get("title") or ""
        if not any(_fuzzy_match(t, s) for s in seen_titles):
            result.append(p)
            seen_titles.append(t)
    return result


# ── Agent ─────────────────────────────────────────────────────────────────────


class HypothesisAgent:
    """
    Runs the full 5-phase hypothesis validation loop.

    Designed to be created fresh for each run (or re-run on rejection).
    Registers/unregisters the state machine in the global registry.
    """

    def __init__(
        self,
        project_id: int,
        model: str,
        ws_manager: Any,
        db_session: Session,
        auto_approve: bool = False,
    ) -> None:
        self.project_id = project_id
        self.pid_str = str(project_id)
        self.model = model
        self.ws = ws_manager
        self.session = db_session
        self.auto_approve = auto_approve

        self.client = make_client(
            model, project_id=self.pid_str, ws_manager=ws_manager
        )
        self.registry = ToolRegistry.get()
        self.machine = ExperimentStateMachine(
            project_id=project_id,
            ws_manager=ws_manager,
            db_session=db_session,
            auto_approve=auto_approve,
        )

    # ── Public entry point ────────────────────────────────────────────────────

    async def run(self, hypothesis: str, feedback: str = "") -> None:
        """
        Run (or re-run) the full validation loop.
        Pass *feedback* from a previous rejection to refine query generation.
        """
        exp_id = self._get_or_create_experiment()
        register_machine(self.project_id, self.machine)

        try:
            await self._run_phases(hypothesis, feedback, exp_id)
        except Exception as exc:
            logger.exception("HypothesisAgent error: %s", exc)
            await self.machine.report_error(
                f"Hypothesis validation failed: {exc}",
                remediation="Check the backend terminal for details.",
            )
            unregister_machine(self.project_id)

    async def _run_phases(
        self, hypothesis: str, feedback: str, exp_id: int
    ) -> None:
        """Inner loop — separated so re-runs can call it cleanly."""

        await self._log(f"Starting hypothesis validation for: {hypothesis[:200]}")

        # ── Phase A: query generation ─────────────────────────────────────
        await self.machine.transition(
            S1Sub.GENERATING_QUERIES, label="Generating search queries"
        )
        queries = await self._phase_a(hypothesis, feedback)
        await self._log(f"Generated {len(queries)} search queries")

        # ── Phase B: broad sweep ──────────────────────────────────────────
        await self.machine.transition(
            S1Sub.SWEEPING_SOURCES, label="Sweeping academic sources"
        )
        papers = await self._phase_b(queries)
        await self.machine.report_progress(
            len(papers), len(papers), f"Found {len(papers)} unique papers"
        )

        # ── Phase C: snowball ─────────────────────────────────────────────
        await self.machine.transition(
            S1Sub.SNOWBALLING, label="Expanding citation network"
        )
        papers = await self._phase_c(papers)

        # ── Phase D: web sweep ────────────────────────────────────────────
        await self.machine.transition(
            S1Sub.WEB_SWEEP, label="Web sweep for implementations"
        )
        web_results = await self._phase_d(queries[:3])

        # ── Phase E: synthesis ────────────────────────────────────────────
        await self.machine.transition(
            S1Sub.ANALYZING, label="Synthesising literature landscape"
        )
        plan = await self._phase_e(hypothesis, papers, web_results)

        await self.machine.transition(S1Sub.SCORING, label="Computing scores")
        plan["experiment_id"] = exp_id

        # ── Approval gate ─────────────────────────────────────────────────
        response = await self.machine.transition(
            S1Sub.AWAITING_APPROVAL,
            plan=plan,
            label="Awaiting hypothesis approval",
        )

        if response and response.approved:
            final_plan = response.edited_plan if response.edited_plan else plan
            self._save_scores(final_plan)
            self._update_experiment(exp_id, final_plan)
            await self.machine.transition(S1Sub.DONE, label="Hypothesis validated")
            # Advance stage — machine stays registered for setup agent
            await self.machine.advance_to_stage(Stage.SETUP)
            await self._log(
                "Hypothesis validated ✓ — advancing to experiment setup."
            )
            # Emit a conversational cue so the chat shows a response
            await self.ws.send(self.pid_str, "token", {
                "token": (
                    "\n\n**Hypothesis validated!** I've completed the literature review "
                    "and scored your idea. Review the scorecard above, then let's move "
                    "on to designing your experiment.\n\n"
                    "Tell me about your implementation plan — what dataset, model "
                    "architecture, and training setup did you have in mind?"
                ),
                "message_id": "hypothesis-done",
            })
            await self.ws.broadcast_done(self.pid_str, summary="Hypothesis validated")

        elif response and not response.approved:
            fb = response.feedback or ""
            await self._log(f"Rejected. Re-running with feedback: {fb or '(none)'}")
            # Re-run immediately with feedback; unregister first so _run_phases
            # can re-register cleanly (advance_to_stage wasn't called, so stage
            # is still hypothesis — no need to reset)
            unregister_machine(self.project_id)
            await self.run(hypothesis, feedback=fb)

        else:
            await self.machine.report_error(
                "Approval response was missing.",
                remediation="Check the backend logs.",
            )
            unregister_machine(self.project_id)

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _log(self, msg: str) -> None:
        await self.ws.send(self.pid_str, "log", {
            "message": msg, "phase": "hypothesis"
        })

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _get_or_create_experiment(self) -> int:
        """Return the iteration-1 experiment ID for this project, creating if needed."""
        existing = self.session.exec(
            select(Experiment).where(
                Experiment.project_id == self.project_id,
                Experiment.iteration == 1,
            )
        ).first()
        if existing is not None:
            return existing.id  # type: ignore[return-value]

        exp = Experiment(
            project_id=self.project_id,
            iteration=1,
            seed=42,
            plan_json="{}",
            status=ExperimentStatus.planned,
        )
        self.session.add(exp)
        self.session.commit()
        self.session.refresh(exp)
        return exp.id  # type: ignore[return-value]

    def _save_scores(self, plan: dict) -> None:
        """Persist (or replace) the three Score rows for this project."""
        existing = self.session.exec(
            select(Score).where(Score.project_id == self.project_id)
        ).all()
        for s in existing:
            self.session.delete(s)

        for kind_str, score_key, rat_key, cite_key in [
            ("novelty", "novelty_score", "novelty_rationale", "novelty_citations"),
            ("gap", "gap_score", "gap_rationale", "gap_citations"),
            ("publishability", "publishability_score", "publishability_rationale", "publishability_citations"),
        ]:
            self.session.add(Score(
                project_id=self.project_id,
                kind=ScoreKind(kind_str),
                value=int(plan.get(score_key, 50)),
                rationale=str(plan.get(rat_key, "")),
                citations_json=json.dumps(plan.get(cite_key, [])),
            ))
        self.session.commit()

    def _update_experiment(self, exp_id: int, plan: dict) -> None:
        exp = self.session.get(Experiment, exp_id)
        if exp:
            exp.plan_json = json.dumps({
                k: v for k, v in plan.items() if k != "experiment_id"
            })
            self.session.add(exp)
            self.session.commit()

    # ── Phase A — query generation ────────────────────────────────────────────

    async def _phase_a(self, hypothesis: str, feedback: str = "") -> list[str]:
        feedback_section = ""
        if feedback:
            feedback_section = (
                f"\n\nIMPORTANT — user feedback from a previous run:\n{feedback}\n"
                "Adjust your queries to address this feedback."
            )

        prompt = (
            f"Generate exactly {MAX_QUERIES} diverse search queries for this ML research hypothesis.\n\n"
            f"Hypothesis: {hypothesis}{feedback_section}\n\n"
            "Query angles (one per angle):\n"
            "1. Core method / technique name\n"
            "2. Problem domain and application area\n"
            "3. 'Related prior work' framing (what already exists)\n"
            "4. Alternative terminology or phrasing\n"
            "5. 'What would make this already solved?' framing\n\n"
            f"Return ONLY a JSON array of {MAX_QUERIES} query strings. No markdown, no explanation.\n"
            'Example: ["query 1", "query 2", "query 3", "query 4", "query 5"]'
        )

        raw = await self.client.chat_raw(
            system_prompt=(
                "You generate precise academic search queries. "
                "Output valid JSON array only."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            clean = _strip_fences(raw)
            result = json.loads(clean)
            if isinstance(result, list):
                return [str(q) for q in result[:MAX_QUERIES] if q]
        except Exception:
            pass

        # Graceful fallback
        return [
            hypothesis[:200],
            f"{hypothesis[:100]} deep learning",
            f"{hypothesis[:100]} survey review",
            f"{hypothesis[:100]} benchmark comparison",
            f"{hypothesis[:100]} state of the art",
        ]

    # ── Phase B — broad sweep ─────────────────────────────────────────────────

    async def _phase_b(self, queries: list[str]) -> list[dict]:
        arxiv_tool = self.registry.get_tool("arxiv_search")
        ss_tool = self.registry.get_tool("semantic_scholar")
        oa_tool = self.registry.get_tool("openalex_search")

        all_papers: list[dict] = []

        for i, query in enumerate(queries):
            await self.machine.report_progress(
                i + 1, len(queries), f"Querying sources for query {i+1}/{len(queries)}"
            )

            tasks = []
            tool_names = []
            if arxiv_tool and arxiv_tool.enabled:
                tasks.append(arxiv_tool.execute({"query": query, "max_results": SWEEP_PER_QUERY}))
                tool_names.append("arxiv_search")
            if ss_tool and ss_tool.enabled:
                tasks.append(ss_tool.execute({"op": "search", "query": query, "max_results": SWEEP_PER_QUERY}))
                tool_names.append("semantic_scholar")
            if oa_tool and oa_tool.enabled:
                tasks.append(oa_tool.execute({"query": query, "max_results": SWEEP_PER_QUERY}))
                tool_names.append("openalex_search")

            if not tasks:
                continue

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for tool_name, result in zip(tool_names, results):
                if isinstance(result, Exception):
                    logger.warning("Phase B %s error: %s", tool_name, result)
                    continue
                if result.success and isinstance(result.data, list):
                    all_papers.extend(result.data)
                # Transparency: surface tool calls to WS
                await self.ws.send(self.pid_str, "tool_call", {
                    "tool_name": tool_name,
                    "input": {"query": query},
                    "status": "done" if (not isinstance(result, Exception) and result.success) else "error",
                    "result_count": len(result.data) if (not isinstance(result, Exception) and isinstance(result.data, list)) else 0,
                })

        deduplicated = _dedup(all_papers)
        await self._log(
            f"Broad sweep: {len(all_papers)} raw → {len(deduplicated)} unique papers"
        )
        return deduplicated

    # ── Phase C — citation snowball ───────────────────────────────────────────

    async def _phase_c(self, papers: list[dict]) -> list[dict]:
        ss_tool = self.registry.get_tool("semantic_scholar")
        if not ss_tool or not ss_tool.enabled:
            return papers

        # Ask LLM to select top papers for snowballing
        paper_text = compact_paper_list(papers[:60], max_tokens=3000)
        selection_prompt = (
            f"Select the {SNOWBALL_TOP_K} most relevant papers for citation snowballing "
            f"from the list below. Return ONLY a JSON array of exact titles.\n\n"
            f"Papers:\n{paper_text}"
        )
        raw = await self.client.chat_raw(
            system_prompt="You select relevant papers. Return valid JSON array of titles only.",
            messages=[{"role": "user", "content": selection_prompt}],
        )
        selected_titles: list[str] = []
        try:
            selected_titles = json.loads(_strip_fences(raw))
            if not isinstance(selected_titles, list):
                selected_titles = []
        except Exception:
            selected_titles = [p.get("title", "") for p in papers[:SNOWBALL_TOP_K]]

        # Map titles → Semantic Scholar paper IDs
        paper_ids: list[str] = []
        for title in selected_titles[:SNOWBALL_TOP_K]:
            for p in papers:
                if _fuzzy_match(p.get("title", ""), title):
                    pid = p.get("paper_id") or p.get("id", "")
                    if pid and not pid.startswith("http"):
                        paper_ids.append(str(pid))
                    break

        if not paper_ids:
            await self._log("No Semantic Scholar IDs found for snowballing — skipping")
            return papers

        expanded = list(papers)
        for i, paper_id in enumerate(paper_ids):
            await self.machine.report_progress(
                i + 1, len(paper_ids), f"Snowballing paper {i+1}/{len(paper_ids)}"
            )
            result = await ss_tool.execute({
                "op": "expand", "paper_id": paper_id, "max_results": 20
            })
            if result.success and isinstance(result.data, dict):
                for rel in (result.data.get("references") or []):
                    if rel.get("title"):
                        expanded.append({"title": rel["title"], "year": rel.get("year"), "source": "ss_ref"})
                for cit in (result.data.get("citations") or []):
                    if cit.get("title"):
                        expanded.append({"title": cit["title"], "year": cit.get("year"), "source": "ss_cite"})
            await self.ws.send(self.pid_str, "tool_call", {
                "tool_name": "semantic_scholar",
                "input": {"op": "expand", "paper_id": paper_id},
                "status": "done" if result.success else "error",
            })

        deduped = _dedup(expanded)
        await self._log(f"Snowball: {len(deduped)} unique papers after expansion")
        return deduped

    # ── Phase D — web sweep ────────────────────────────────────────────────────

    async def _phase_d(self, queries: list[str]) -> list[dict]:
        web_tool = self.registry.get_tool("web_search")
        if not web_tool or not web_tool.enabled:
            return []

        web_results: list[dict] = []
        for query in queries[:3]:
            result = await web_tool.execute({"query": query, "max_results": 10})
            if result.success and isinstance(result.data, list):
                web_results.extend(result.data)
            await self.ws.send(self.pid_str, "tool_call", {
                "tool_name": "web_search",
                "input": {"query": query},
                "status": "done" if result.success else "error",
                "result_count": len(result.data) if isinstance(result.data, list) else 0,
            })
        return web_results

    # ── Phase E — synthesis ────────────────────────────────────────────────────

    async def _phase_e(
        self, hypothesis: str, papers: list[dict], web_results: list[dict]
    ) -> dict:
        paper_text = compact_paper_list(papers, max_tokens=5000)

        web_text = "(none)"
        if web_results:
            lines = []
            for r in web_results[:10]:
                lines.append(
                    f"- {r.get('title', '')} — {r.get('url', '')} — "
                    f"{(r.get('snippet') or '')[:150]}"
                )
            web_text = "\n".join(lines)

        synthesis_prompt = (
            f"You are a rigorous ML researcher evaluating a research hypothesis.\n\n"
            f"Hypothesis: {hypothesis}\n\n"
            f"Academic papers found ({len(papers)} total, showing top):\n{paper_text}\n\n"
            f"Web sources:\n{web_text}\n\n"
            "Produce a research assessment in this EXACT JSON format (no markdown fences):\n"
            "{\n"
            '  "landscape": "2-4 paragraph SOTA summary: what exists, what is solved, what is open",\n'
            '  "novelty": {\n'
            '    "score": <0-100>,\n'
            '    "rationale": "1-2 paragraphs with specific paper references",\n'
            '    "citations": [{"title": "...", "year": 2023, "venue": "...", "url": "..."}]\n'
            "  },\n"
            '  "gap": {\n'
            '    "score": <0-100>,\n'
            '    "rationale": "...",\n'
            '    "citations": [...]\n'
            "  },\n"
            '  "publishability": {\n'
            '    "score": <0-100>,\n'
            '    "rationale": "plain honest assessment, target venue if positive",\n'
            '    "citations": [...]\n'
            "  }\n"
            "}\n\n"
            "Score guidelines:\n"
            "- Novelty 0=already published identically, 100=completely unexplored\n"
            "- Gap realness 0=fully solved, 100=clear open problem\n"
            "- Publishability: calibrated, never inflated. Top conf=80+, workshop=50-70, unlikely=<50\n"
            "- Include 2-5 REAL citations per score from the paper list above only (no fabrication)\n"
            "Return ONLY valid JSON."
        )

        raw = await self.client.chat_raw(
            system_prompt="You evaluate ML research hypotheses. Output valid JSON only.",
            messages=[{"role": "user", "content": synthesis_prompt}],
        )

        data: dict = {}
        try:
            data = json.loads(_strip_fences(raw))
        except Exception as exc:
            logger.warning("Phase E JSON parse failed (%s). Raw: %.300r", exc, raw)
            data = {
                "landscape": f"Literature review completed. {len(papers)} papers analyzed.",
                "novelty": {"score": 50, "rationale": "Assessment unavailable.", "citations": []},
                "gap": {"score": 50, "rationale": "Assessment unavailable.", "citations": []},
                "publishability": {"score": 40, "rationale": "Assessment unavailable.", "citations": []},
            }

        # Flatten into approval plan payload
        def _section(key: str) -> dict:
            v = data.get(key, {})
            return v if isinstance(v, dict) else {}

        nov = _section("novelty")
        gap = _section("gap")
        pub = _section("publishability")

        # Combined cited_papers for backward-compat ScorecardView
        all_cites: list[dict] = []
        seen: set[str] = set()
        for cite_list in (nov.get("citations", []), gap.get("citations", []), pub.get("citations", [])):
            for c in cite_list:
                t = (c.get("title") or "").lower()
                if t and t not in seen:
                    seen.add(t)
                    all_cites.append(c)

        return {
            "novelty_score": int(nov.get("score", 50)),
            "novelty_rationale": str(nov.get("rationale", "")),
            "novelty_citations": nov.get("citations", []),
            "gap_score": int(gap.get("score", 50)),
            "gap_rationale": str(gap.get("rationale", "")),
            "gap_citations": gap.get("citations", []),
            "publishability_score": int(pub.get("score", 40)),
            "publishability_rationale": str(pub.get("rationale", "")),
            "publishability_citations": pub.get("citations", []),
            "landscape": str(data.get("landscape", "")),
            "cited_papers": all_cites,
            "rationale": str(data.get("landscape", ""))[:400],
        }


# ── Utility ───────────────────────────────────────────────────────────────────


def _strip_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` fences."""
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    inner: list[str] = []
    for line in lines[1:]:
        if line.strip() == "```":
            break
        inner.append(line)
    return "\n".join(inner).strip()
