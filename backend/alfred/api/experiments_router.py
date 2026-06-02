"""
api/experiments_router.py — /api/projects/{project_id}/experiments

Endpoints:
  GET    /api/projects/{project_id}/experiments           — list all experiments
  POST   /api/projects/{project_id}/experiments           — create a new experiment record
  GET    /api/projects/{project_id}/experiments/{exp_id}  — single experiment
  POST   /api/projects/{project_id}/experiments/{exp_id}/approve   — approve a plan
  POST   /api/projects/{project_id}/experiments/{exp_id}/reject    — reject with feedback
  PATCH  /api/projects/{project_id}/experiments/{exp_id}           — update experiment fields

Also exposes:
  POST   /api/projects/{project_id}/auto_approve          — toggle auto-approve on project
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from alfred.db import get_session
from alfred.models.db_models import (
    Experiment,
    ExperimentStatus,
    Project,
    VersionMode,
)
from alfred.state_machine.machine import get_machine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["experiments"])


# ---------------------------------------------------------------------------
# I/O models
# ---------------------------------------------------------------------------


class ExperimentCreate(BaseModel):
    iteration: int = 1
    seed: int = 42
    plan_json: str = "{}"
    version_mode: VersionMode = VersionMode.modify


class ExperimentUpdate(BaseModel):
    status: Optional[ExperimentStatus] = None
    git_commit: Optional[str] = None
    code_path: Optional[str] = None
    dataset_hash: Optional[str] = None
    conda_snapshot_path: Optional[str] = None
    seed: Optional[int] = None
    runtime_seconds: Optional[float] = None
    version_mode: Optional[VersionMode] = None
    plan_json: Optional[str] = None


class ExperimentResponse(BaseModel):
    id: int
    project_id: int
    iteration: int
    git_commit: str
    code_path: str
    dataset_hash: str
    conda_snapshot_path: str
    seed: int
    status: ExperimentStatus
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    runtime_seconds: Optional[float]
    version_mode: VersionMode
    plan_json: str


class ApproveRequest(BaseModel):
    edited_plan: Optional[dict[str, Any]] = None


class RejectRequest(BaseModel):
    feedback: str = ""


class AutoApproveRequest(BaseModel):
    auto_approve: bool


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/experiments", response_model=List[ExperimentResponse])
async def list_experiments(
    project_id: int,
    session: Session = Depends(get_session),
) -> list:
    """Return all experiments for a project ordered by iteration."""
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    exps = session.exec(
        select(Experiment)
        .where(Experiment.project_id == project_id)
        .order_by(Experiment.iteration.asc())
    ).all()
    return exps


@router.post("/experiments", response_model=ExperimentResponse, status_code=201)
async def create_experiment(
    project_id: int,
    req: ExperimentCreate,
    session: Session = Depends(get_session),
) -> Experiment:
    """Create a new experiment record (planned status)."""
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        json.loads(req.plan_json)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="plan_json must be valid JSON")

    exp = Experiment(
        project_id=project_id,
        iteration=req.iteration,
        seed=req.seed,
        plan_json=req.plan_json,
        version_mode=req.version_mode,
        status=ExperimentStatus.planned,
    )
    session.add(exp)
    session.commit()
    session.refresh(exp)
    return exp


@router.get("/experiments/{exp_id}", response_model=ExperimentResponse)
async def get_experiment(
    project_id: int,
    exp_id: int,
    session: Session = Depends(get_session),
) -> Experiment:
    exp = session.get(Experiment, exp_id)
    if exp is None or exp.project_id != project_id:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return exp


@router.patch("/experiments/{exp_id}", response_model=ExperimentResponse)
async def update_experiment(
    project_id: int,
    exp_id: int,
    req: ExperimentUpdate,
    session: Session = Depends(get_session),
) -> Experiment:
    exp = session.get(Experiment, exp_id)
    if exp is None or exp.project_id != project_id:
        raise HTTPException(status_code=404, detail="Experiment not found")

    data = req.model_dump(exclude_none=True)
    for field, value in data.items():
        setattr(exp, field, value)
    session.add(exp)
    session.commit()
    session.refresh(exp)
    return exp


@router.post("/experiments/{exp_id}/approve")
async def approve_experiment(
    project_id: int,
    exp_id: int,
    req: ApproveRequest,
    session: Session = Depends(get_session),
) -> dict:
    """
    Approve the plan at the current approval gate.
    Unblocks the state machine coroutine waiting in _handle_approval_gate().
    """
    exp = session.get(Experiment, exp_id)
    if exp is None or exp.project_id != project_id:
        raise HTTPException(status_code=404, detail="Experiment not found")

    machine = get_machine(project_id)
    if machine is None:
        raise HTTPException(
            status_code=409,
            detail="No active state machine for this project. Is ALFRED running an experiment?",
        )

    edited_plan = req.edited_plan
    if edited_plan is not None:
        # Persist the edited plan back to the experiment row.
        exp.plan_json = json.dumps(edited_plan)
        session.add(exp)
        session.commit()

    machine.resolve_approval(approved=True, edited_plan=edited_plan)
    return {"status": "approved", "experiment_id": exp_id}


@router.post("/experiments/{exp_id}/reject")
async def reject_experiment(
    project_id: int,
    exp_id: int,
    req: RejectRequest,
    session: Session = Depends(get_session),
) -> dict:
    """
    Reject the plan at the current approval gate, with optional feedback.
    The agent will receive the feedback and can revise its proposal.
    """
    exp = session.get(Experiment, exp_id)
    if exp is None or exp.project_id != project_id:
        raise HTTPException(status_code=404, detail="Experiment not found")

    machine = get_machine(project_id)
    if machine is None:
        raise HTTPException(
            status_code=409,
            detail="No active state machine for this project.",
        )

    machine.resolve_approval(approved=False, feedback=req.feedback)
    return {"status": "rejected", "experiment_id": exp_id, "feedback": req.feedback}


@router.post("/auto_approve")
async def toggle_auto_approve(
    project_id: int,
    req: AutoApproveRequest,
    session: Session = Depends(get_session),
) -> dict:
    """Toggle auto-approve for a project. Updates both DB and live machine."""
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    project.auto_approve = req.auto_approve
    project.updated_at = datetime.utcnow()
    session.add(project)
    session.commit()

    # Update the live machine if one exists.
    machine = get_machine(project_id)
    if machine is not None:
        machine.set_auto_approve(req.auto_approve)

    return {"status": "ok", "auto_approve": req.auto_approve}