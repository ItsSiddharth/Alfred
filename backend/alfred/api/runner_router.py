"""
/api/projects/{project_id}/ runner endpoints — Stage 7.1.

Endpoints
─────────
PATCH  /bind                         Bind conda env + experiment folder
GET    /runner/status                Current run substage + active experiment
GET    /runner/git/log               Git commit history for experiment folder
POST   /runner/git/rollback          Hard-reset to a prior commit (destructive)
GET    /runner/runs                  All Experiment rows for this project
GET    /runner/runs/{exp_id}/metrics Metric curves grouped by name
GET    /runner/runs/{exp_id}/logs    RunLog entries for an experiment
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from alfred.db import get_session
from alfred.models.db_models import Experiment, Metric, Project, RunLog
from alfred.services.git_service import GitError, GitService
from alfred.state_machine.machine import get_machine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects/{project_id}", tags=["runner"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class BindRequest(BaseModel):
    conda_env: str
    experiment_folder: str


class BindResponse(BaseModel):
    id: int
    conda_env: str
    experiment_folder: str
    status: str


class RunnerStatusResponse(BaseModel):
    current_stage: str
    current_substage: str
    active_experiment_id: Optional[int] = None
    status: str


class GitLogEntry(BaseModel):
    hash: str
    short_hash: str
    message: str
    date: str


class RollbackRequest(BaseModel):
    commit_hash: str


class MetricPoint(BaseModel):
    step: int
    value: float


class MetricCurve(BaseModel):
    name: str
    points: List[MetricPoint]


class RunLogEntryResponse(BaseModel):
    id: int
    level: str
    message: str
    created_at: datetime
    phase: str


# ── /bind ──────────────────────────────────────────────────────────────────────

@router.patch("/bind", response_model=BindResponse)
async def bind_project(
    project_id: int,
    req: BindRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Bind the project to a conda env and an experiment folder."""
    project = session.get(Project, project_id)
    if project is None or project.status == "deleted":
        raise HTTPException(status_code=404, detail="Project not found")

    # Validate: must be absolute
    folder = Path(req.experiment_folder)
    if not folder.is_absolute():
        raise HTTPException(
            status_code=400,
            detail="experiment_folder must be an absolute path (e.g. /home/user/myexp)",
        )

    # Create if it doesn't exist (no sudo required)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot create folder (permission denied): {exc}",
        )

    if not os.access(folder, os.W_OK):
        raise HTTPException(
            status_code=400,
            detail=f"Experiment folder is not writable: {folder}",
        )

    if not req.conda_env.strip():
        raise HTTPException(status_code=400, detail="conda_env must not be empty")

    project.conda_env = req.conda_env.strip()
    project.experiment_folder = str(folder.resolve())
    project.updated_at = datetime.utcnow()
    session.add(project)
    session.commit()

    logger.info(
        "Project %s bound: conda_env=%s folder=%s",
        project_id, project.conda_env, project.experiment_folder,
    )
    return {
        "id": project.id,
        "conda_env": project.conda_env,
        "experiment_folder": project.experiment_folder,
        "status": "ok",
    }


# ── /runner/status ─────────────────────────────────────────────────────────────

@router.get("/runner/status", response_model=RunnerStatusResponse)
async def runner_status(
    project_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return current run status from live state machine or DB snapshot."""
    project = session.get(Project, project_id)
    if project is None or project.status == "deleted":
        raise HTTPException(status_code=404, detail="Project not found")

    machine = get_machine(project_id)
    if machine is not None:
        substage = machine.current_substage.value if machine.current_substage else "idle"
        stage = str(machine.current_stage.value) if machine.current_stage else "3"
    else:
        stage = "3"
        substage = "idle"

    # Latest experiment for this project
    exp = session.exec(
        select(Experiment)
        .where(Experiment.project_id == project_id)
        .order_by(Experiment.iteration.desc())  # type: ignore[arg-type]
    ).first()

    return {
        "current_stage": stage,
        "current_substage": substage,
        "active_experiment_id": exp.id if exp else None,
        "status": exp.status.value if exp else "no_experiments",
    }


# ── /runner/git/* ──────────────────────────────────────────────────────────────

@router.get("/runner/git/log", response_model=List[GitLogEntry])
async def git_log(
    project_id: int,
    session: Session = Depends(get_session),
) -> list:
    """Return git commit history for the project's experiment folder."""
    project = session.get(Project, project_id)
    if project is None or project.status == "deleted":
        raise HTTPException(status_code=404, detail="Project not found")

    if not project.experiment_folder:
        return []

    folder = Path(project.experiment_folder)
    if not (folder / ".git").exists():
        return []

    return GitService(folder).log(n=30)


@router.post("/runner/git/rollback")
async def git_rollback(
    project_id: int,
    req: RollbackRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """
    Hard-reset the experiment folder to a prior commit.

    Blocked while an experiment is actively training/evaluating.
    The UI must obtain explicit user confirmation before calling this endpoint.
    """
    project = session.get(Project, project_id)
    if project is None or project.status == "deleted":
        raise HTTPException(status_code=404, detail="Project not found")

    # Block if training/preprocessing/evaluating is live
    machine = get_machine(project_id)
    if machine is not None:
        from alfred.state_machine.machine import S3Sub  # noqa: PLC0415
        active_substages = {S3Sub.TRAINING, S3Sub.PREPROCESSING, S3Sub.EVALUATING}
        if machine.current_substage in active_substages:
            raise HTTPException(
                status_code=400,
                detail="Cannot rollback while an experiment is running. Wait for it to finish.",
            )

    if not project.experiment_folder:
        raise HTTPException(status_code=400, detail="No experiment folder configured")

    folder = Path(project.experiment_folder)
    if not folder.exists():
        raise HTTPException(status_code=400, detail=f"Experiment folder not found: {folder}")

    git = GitService(folder)
    try:
        git.rollback(req.commit_hash)
    except GitError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    logger.info("Project %s rolled back to %s", project_id, req.commit_hash[:8])
    return {"status": "ok", "commit_hash": req.commit_hash}


# ── /runner/runs/* ─────────────────────────────────────────────────────────────

@router.get("/runner/runs")
async def list_runs(
    project_id: int,
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """Return all Experiment rows for this project, newest first."""
    project = session.get(Project, project_id)
    if project is None or project.status == "deleted":
        raise HTTPException(status_code=404, detail="Project not found")

    exps = session.exec(
        select(Experiment)
        .where(Experiment.project_id == project_id)
        .order_by(Experiment.iteration.desc())  # type: ignore[arg-type]
    ).all()

    return [
        {
            "id": e.id,
            "iteration": e.iteration,
            "status": e.status.value,
            "started_at": e.started_at.isoformat() if e.started_at else None,
            "finished_at": e.finished_at.isoformat() if e.finished_at else None,
            "runtime_seconds": e.runtime_seconds,
            "git_commit": e.git_commit,
            "version_mode": e.version_mode.value,
            "code_path": e.code_path,
        }
        for e in exps
    ]


@router.get("/runner/runs/{exp_id}/metrics", response_model=List[MetricCurve])
async def get_metrics(
    project_id: int,
    exp_id: int,
    session: Session = Depends(get_session),
) -> list:
    """Return all metric curves for an experiment, grouped by name."""
    rows = session.exec(
        select(Metric)
        .where(Metric.experiment_id == exp_id)
        .order_by(Metric.name, Metric.step)  # type: ignore[arg-type]
    ).all()

    curves: dict[str, list[MetricPoint]] = {}
    for row in rows:
        if row.name not in curves:
            curves[row.name] = []
        curves[row.name].append(MetricPoint(step=row.step, value=row.value))

    return [{"name": name, "points": pts} for name, pts in sorted(curves.items())]


@router.get("/runner/runs/{exp_id}/logs", response_model=List[RunLogEntryResponse])
async def get_run_logs(
    project_id: int,
    exp_id: int,
    session: Session = Depends(get_session),
) -> list:
    """Return run log entries for an experiment, oldest first."""
    rows = session.exec(
        select(RunLog)
        .where(RunLog.experiment_id == exp_id)
        .order_by(RunLog.created_at)  # type: ignore[arg-type]
    ).all()

    return [
        {
            "id": r.id,
            "level": r.level,
            "message": r.message,
            "created_at": r.created_at,
            "phase": r.phase.value,
        }
        for r in rows
    ]


class PlotEntry(BaseModel):
    filename: str
    base64_png: str
    ascii_art: str
    experiment_id: int


@router.get("/runner/runs/{exp_id}/plots", response_model=List[PlotEntry])
async def get_run_plots(
    project_id: int,
    exp_id: int,
    session: Session = Depends(get_session),
) -> list:
    """Return base64-encoded PNG plots for an experiment by scanning the experiment folder."""
    import base64

    from alfred.services.plotting import png_to_ascii

    exp = session.get(Experiment, exp_id)
    if not exp or exp.project_id != project_id:
        raise HTTPException(status_code=404, detail="Experiment not found")

    if not exp.code_path:
        return []

    exp_dir = Path(exp.code_path).parent
    if not exp_dir.exists():
        return []

    results = []
    for png_path in sorted(exp_dir.glob("*.png")):
        try:
            raw = png_path.read_bytes()
            b64 = base64.b64encode(raw).decode("ascii")
            ascii_art = png_to_ascii(png_path)
            results.append({
                "filename": png_path.name,
                "base64_png": b64,
                "ascii_art": ascii_art,
                "experiment_id": exp_id,
            })
        except OSError:
            continue

    return results
