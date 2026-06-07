"""
api/dashboard_router.py — Stage 8: Comparison dashboard, compute budget, paper export.

Endpoints:
  GET  /api/projects/{id}/dashboard        — all experiments + metric curves
  GET  /api/projects/{id}/compute-estimate — median runtime from past completed runs
  POST /api/projects/{id}/export           — generate Markdown + LaTeX research note
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlmodel import Session, select

from alfred.db import get_session
from alfred.models.db_models import (
    Experiment,
    ExperimentStatus,
    Metric,
    Project,
    Score,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["dashboard"])


# ── Response models ────────────────────────────────────────────────────────────


class MetricPoint(BaseModel):
    step: int
    value: float


class MetricCurve(BaseModel):
    name: str
    experiment_id: int
    iteration: int
    points: list[MetricPoint]


class ExperimentRow(BaseModel):
    id: int
    iteration: int
    status: str
    runtime_seconds: Optional[float]
    git_commit: str
    version_mode: str
    metrics_summary: dict[str, float]
    plan_summary: str


class DashboardResponse(BaseModel):
    experiments: list[ExperimentRow]
    metric_curves: list[MetricCurve]
    metric_names: list[str]


class ComputeEstimateResponse(BaseModel):
    estimated_seconds: Optional[float]
    estimated_label: str
    based_on_n_runs: int
    note: str


class ExportRequest(BaseModel):
    include_latex: bool = True
    iterations: Optional[list[int]] = None


class ExportResponse(BaseModel):
    markdown: str
    latex: str
    filename: str


# ── GET /dashboard ─────────────────────────────────────────────────────────────


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    project_id: int,
    session: Session = Depends(get_session),
) -> DashboardResponse:
    """Return all experiments and their metric curves for the comparison dashboard."""
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    exps = session.exec(
        select(Experiment)
        .where(Experiment.project_id == project_id)
        .order_by(Experiment.iteration.asc())  # type: ignore[arg-type]
    ).all()

    # For the table we show all experiments; for charts only use completed ones
    done_exp_ids = {e.id for e in exps if e.status == ExperimentStatus.done and e.id is not None}
    all_metrics: list[Metric] = []
    if done_exp_ids:
        all_metrics = session.exec(
            select(Metric)
            .where(Metric.experiment_id.in_(list(done_exp_ids)))  # type: ignore[union-attr]
            .order_by(Metric.experiment_id.asc(), Metric.name.asc(), Metric.step.asc())  # type: ignore[arg-type]
        ).all()

    # Group by (exp_id, metric_name) — deduplicate by keeping last value per step
    raw_map: dict[tuple[int, str, int], float] = {}
    for m in all_metrics:
        raw_map[(m.experiment_id, m.name, m.step)] = m.value

    curves_map: dict[tuple[int, str], list[MetricPoint]] = defaultdict(list)
    for (exp_id, name, step), value in sorted(raw_map.items()):
        curves_map[(exp_id, name)].append(MetricPoint(step=step, value=value))

    metric_names_set: set[str] = set()
    metric_curves: list[MetricCurve] = []
    exp_by_id = {e.id: e for e in exps}

    for (exp_id, metric_name), points in curves_map.items():
        metric_names_set.add(metric_name)
        exp = exp_by_id.get(exp_id)
        metric_curves.append(
            MetricCurve(
                name=metric_name,
                experiment_id=exp_id,
                iteration=exp.iteration if exp else 0,
                points=sorted(points, key=lambda p: p.step),
            )
        )

    exp_rows: list[ExperimentRow] = []
    for exp in exps:
        metrics_summary: dict[str, float] = {}
        if exp.id in done_exp_ids:
            for (eid, mname), pts in curves_map.items():
                if eid == exp.id and pts:
                    metrics_summary[mname] = pts[-1].value

        plan_summary = ""
        try:
            plan = json.loads(exp.plan_json)
            plan_summary = (
                plan.get("objective")
                or plan.get("architecture")
                or plan.get("dataset")
                or ""
            )[:120]
        except Exception:
            pass

        exp_rows.append(
            ExperimentRow(
                id=exp.id,  # type: ignore[arg-type]
                iteration=exp.iteration,
                status=exp.status,
                runtime_seconds=exp.runtime_seconds,
                git_commit=(exp.git_commit or "")[:7],
                version_mode=exp.version_mode,
                metrics_summary=metrics_summary,
                plan_summary=plan_summary,
            )
        )

    return DashboardResponse(
        experiments=exp_rows,
        metric_curves=metric_curves,
        metric_names=sorted(metric_names_set),
    )


# ── GET /compute-estimate ──────────────────────────────────────────────────────


@router.get("/compute-estimate", response_model=ComputeEstimateResponse)
async def get_compute_estimate(
    project_id: int,
    session: Session = Depends(get_session),
) -> ComputeEstimateResponse:
    """Estimate next-run runtime from past completed runs on this machine."""
    exps = session.exec(
        select(Experiment).where(
            Experiment.project_id == project_id,
            Experiment.status == ExperimentStatus.done,
        )
    ).all()

    completed = [e for e in exps if e.runtime_seconds is not None]

    if not completed:
        return ComputeEstimateResponse(
            estimated_seconds=None,
            estimated_label="Unknown — no completed runs yet",
            based_on_n_runs=0,
            note="Estimate will appear after the first run completes.",
        )

    runtimes = sorted(e.runtime_seconds for e in completed)  # type: ignore[arg-type]
    median = runtimes[len(runtimes) // 2]

    if median < 60:
        label = f"~{int(median)}s on your hardware"
    elif median < 3600:
        label = f"~{int(median / 60)} min on your hardware"
    else:
        label = f"~{median / 3600:.1f}h on your hardware"

    n = len(completed)
    return ComputeEstimateResponse(
        estimated_seconds=median,
        estimated_label=label,
        based_on_n_runs=n,
        note=f"Median of {n} completed run{'s' if n != 1 else ''} on this machine.",
    )


# ── POST /export ───────────────────────────────────────────────────────────────


@router.post("/export", response_model=ExportResponse)
async def export_project(
    project_id: int,
    req: ExportRequest,
    session: Session = Depends(get_session),
) -> ExportResponse:
    """
    Generate a Markdown + LaTeX research note from the project data.
    Clearly labelled as DRAFT — not for submission.
    """
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    scores = session.exec(
        select(Score).where(Score.project_id == project_id)
    ).all()
    scores_by_kind = {s.kind: s for s in scores}

    exps = session.exec(
        select(Experiment)
        .where(Experiment.project_id == project_id)
        .order_by(Experiment.iteration.asc())  # type: ignore[arg-type]
    ).all()
    if req.iterations:
        exps = [e for e in exps if e.iteration in req.iterations]

    all_metrics: list[Metric] = []
    exp_ids = [e.id for e in exps if e.id is not None]
    if exp_ids:
        all_metrics = session.exec(
            select(Metric)
            .where(Metric.experiment_id.in_(exp_ids))  # type: ignore[union-attr]
            .order_by(Metric.experiment_id.asc(), Metric.step.asc())  # type: ignore[arg-type]
        ).all()

    md = _build_markdown(project.name, scores_by_kind, exps, all_metrics)
    latex = _build_latex(project.name, scores_by_kind, exps, all_metrics) if req.include_latex else ""

    safe_name = "".join(
        c if c.isalnum() or c in "-_" else "_" for c in project.name.lower()
    )
    filename = f"{safe_name}_research_note_{datetime.utcnow().strftime('%Y%m%d')}"

    return ExportResponse(markdown=md, latex=latex, filename=filename)


# ── Builders ───────────────────────────────────────────────────────────────────


def _build_markdown(name, scores_by_kind, exps, all_metrics) -> str:
    L: list[str] = [
        f"# {name} — Research Note (DRAFT)",
        "",
        "> **⚠ DRAFT** — Auto-generated by ALFRED. Starting point for writing, not a finished paper.",
        "",
        f"*Generated: {datetime.utcnow().strftime('%Y-%m-%d')}*",
        "",
    ]

    if scores_by_kind:
        L += ["## Hypothesis Assessment", ""]
        for kind in ("novelty", "gap", "publishability"):
            s = scores_by_kind.get(kind)
            if s:
                bar = "█" * (s.value // 10) + "░" * (10 - s.value // 10)
                L.append(f"**{kind.title()}**: {s.value}/100  `{bar}`")
                if s.rationale:
                    L.append(f"> {s.rationale[:500]}")
                L.append("")

    if exps:
        L += ["## Methodology", ""]
        for exp in exps:
            L.append(f"### Iteration {exp.iteration}")
            try:
                plan = json.loads(exp.plan_json)
                for k, v in plan.items():
                    if k not in ("experiment_id", "kind") and v:
                        L.append(f"- **{k.replace('_', ' ').title()}**: {str(v)[:300]}")
            except Exception:
                pass
            if exp.runtime_seconds:
                L.append(f"- **Runtime**: {exp.runtime_seconds:.1f}s")
            L.append("")

    by_exp: dict = defaultdict(lambda: defaultdict(list))
    for m in all_metrics:
        by_exp[m.experiment_id][m.name].append((m.step, m.value))

    if by_exp:
        L += ["## Results", ""]
        exp_by_id = {e.id: e for e in exps}
        for exp_id, metric_dict in by_exp.items():
            exp = exp_by_id.get(exp_id)
            if not exp:
                continue
            L.append(f"### Iteration {exp.iteration}")
            L.append("")
            L.append("| Metric | Final | Steps |")
            L.append("|--------|-------|-------|")
            for mname, pts in metric_dict.items():
                pts_sorted = sorted(pts, key=lambda x: x[0])
                L.append(f"| {mname} | {pts_sorted[-1][1]:.4f} | {len(pts_sorted)} |")
            L.append("")

    L += [
        "## Discussion",
        "",
        "*[To be completed by the researcher. Re-generate with an active Ollama model for an AI-drafted discussion.]*",
        "",
        "## Future Work",
        "",
        "*[To be filled in.]*",
        "",
    ]
    return "\n".join(L)


def _build_latex(name, scores_by_kind, exps, all_metrics) -> str:
    def esc(s: str) -> str:
        return (
            str(s)
            .replace("&", "\\&")
            .replace("%", "\\%")
            .replace("#", "\\#")
            .replace("_", "\\_")
            .replace("$", "\\$")
        )

    title = esc(name)
    L: list[str] = [
        r"\documentclass{article}",
        r"\usepackage{booktabs, hyperref, geometry}",
        r"\geometry{margin=1in}",
        r"\begin{document}",
        f"\\title{{{title} --- Research Note (DRAFT)}}",
        r"\date{\today}",
        r"\maketitle",
        r"\begin{abstract}",
        "DRAFT research note automatically generated by ALFRED. Not for submission.",
        r"\end{abstract}",
        "",
    ]

    if scores_by_kind:
        L.append(r"\section{Hypothesis Assessment}")
        for kind in ("novelty", "gap", "publishability"):
            s = scores_by_kind.get(kind)
            if s:
                L.append(f"\\textbf{{{kind.title()} Score}}: {s.value}/100.")
                if s.rationale:
                    L.append(esc(s.rationale[:300]))
                L.append("")

    if exps:
        L.append(r"\section{Methodology}")
        for exp in exps:
            L.append(f"\\subsection{{Iteration {exp.iteration}}}")
            try:
                plan = json.loads(exp.plan_json)
                L.append(r"\begin{itemize}")
                for k, v in plan.items():
                    if k not in ("experiment_id", "kind") and v:
                        L.append(f"  \\item \\textbf{{{esc(k)}}}: {esc(str(v)[:200])}")
                L.append(r"\end{itemize}")
            except Exception:
                pass

    by_exp: dict = defaultdict(lambda: defaultdict(list))
    for m in all_metrics:
        by_exp[m.experiment_id][m.name].append((m.step, m.value))

    if by_exp:
        L.append(r"\section{Results}")
        exp_by_id = {e.id: e for e in exps}
        for exp_id, metric_dict in by_exp.items():
            exp = exp_by_id.get(exp_id)
            if not exp:
                continue
            L.append(f"\\subsection{{Iteration {exp.iteration}}}")
            L.append(r"\begin{tabular}{lrr}")
            L.append(r"\toprule")
            L.append(r"Metric & Final & Steps \\ \midrule")
            for mname, pts in metric_dict.items():
                pts_s = sorted(pts, key=lambda x: x[0])
                L.append(f"{esc(mname)} & {pts_s[-1][1]:.4f} & {len(pts_s)} \\\\")
            L.append(r"\bottomrule")
            L.append(r"\end{tabular}")
            L.append("")

    L += [r"\section{Discussion}", "[To be completed.]", "", r"\end{document}"]
    return "\n".join(L)
