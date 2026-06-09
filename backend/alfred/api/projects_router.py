"""
/api/projects router — project CRUD.

Note: POST /auto_approve lives in experiments_router.py (it also controls the
live state machine). This file is CRUD-only to avoid duplicate route registration.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from alfred.db import get_session
from alfred.models.db_models import (
    Experiment, ExperimentStatus, MemoryItem, Message, Project, ProjectStage, Score, ToolCall,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str
    workspace_path: str = ""
    conda_env: str = ""
    experiment_folder: str = ""


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    conda_env: Optional[str] = None
    experiment_folder: Optional[str] = None
    current_stage: Optional[ProjectStage] = None
    auto_approve: Optional[bool] = None
    status: Optional[str] = None


class ProjectResponse(BaseModel):
    id: int
    name: str
    created_at: datetime
    updated_at: datetime
    workspace_path: str
    conda_env: str
    experiment_folder: str
    current_stage: ProjectStage
    auto_approve: bool
    status: str


@router.get("/", response_model=List[ProjectResponse])
async def list_projects(session: Session = Depends(get_session)) -> list:
    """Return all non-deleted projects ordered newest first."""
    projects = session.exec(
        select(Project)
        .where(Project.status != "deleted")
        .order_by(Project.created_at.desc())
    ).all()
    return projects


@router.post("/", response_model=ProjectResponse, status_code=201)
async def create_project(
    req: ProjectCreate, session: Session = Depends(get_session)
) -> Project:
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Project name must not be empty")
    project = Project(
        name=req.name.strip(),
        workspace_path=req.workspace_path,
        conda_env=req.conda_env,
        experiment_folder=req.experiment_folder,
    )
    session.add(project)
    session.commit()
    session.refresh(project)
    logger.info("Project created: id=%s name=%s", project.id, project.name)
    return project


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int, session: Session = Depends(get_session)
) -> Project:
    project = session.get(Project, project_id)
    if project is None or project.status == "deleted":
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: int, req: ProjectUpdate, session: Session = Depends(get_session)
) -> Project:
    project = session.get(Project, project_id)
    if project is None or project.status == "deleted":
        raise HTTPException(status_code=404, detail="Project not found")
    data = req.model_dump(exclude_none=True)
    for field, value in data.items():
        setattr(project, field, value)
    project.updated_at = datetime.utcnow()
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: int, session: Session = Depends(get_session)
) -> None:
    """
    Soft-delete a project (status='deleted') and hard-delete all child rows.

    Child tables deleted: Message, MemoryItem, Experiment, Score, ToolCall.
    The project row itself is kept with status='deleted' so referential integrity
    is preserved for any foreign keys in existing data.
    """
    project = session.get(Project, project_id)
    if project is None or project.status == "deleted":
        raise HTTPException(status_code=404, detail="Project not found")

    for model_cls in [Message, MemoryItem, Experiment, Score, ToolCall]:
        try:
            records = session.exec(
                select(model_cls).where(model_cls.project_id == project_id)
            ).all()
            for rec in records:
                session.delete(rec)
        except Exception as exc:
            logger.warning(
                "Could not delete %s records for project %s: %s",
                model_cls.__name__, project_id, exc,
            )

    project.status = "deleted"
    project.updated_at = datetime.utcnow()
    session.add(project)
    session.commit()
    logger.info("Project deleted: id=%s", project_id)


@router.post("/{project_id}/skip-hypothesis")
async def skip_hypothesis(
    project_id: int,
    session: Session = Depends(get_session),
) -> dict:
    """
    Skip hypothesis validation and advance the project directly to experiment setup.
    Creates an iteration-1 experiment record if one doesn't exist yet.
    """
    project = session.get(Project, project_id)
    if project is None or project.status == "deleted":
        raise HTTPException(status_code=404, detail="Project not found")
    if project.current_stage != ProjectStage.hypothesis:
        raise HTTPException(
            status_code=409,
            detail=f"Project is in stage '{project.current_stage}', not 'hypothesis'.",
        )

    project.current_stage = ProjectStage.setup
    project.updated_at = datetime.utcnow()
    session.add(project)

    # Create iteration-1 experiment if absent
    existing = session.exec(
        select(Experiment).where(
            Experiment.project_id == project_id,
            Experiment.iteration == 1,
        )
    ).first()
    if existing is None:
        exp = Experiment(
            project_id=project_id,
            iteration=1,
            seed=42,
            plan_json="{}",
            status=ExperimentStatus.planned,
        )
        session.add(exp)

    session.commit()

    # Broadcast a WS message so the chat shows the stage transition
    try:
        from alfred.ws import manager
        import asyncio
        asyncio.create_task(manager.send(
            str(project_id), "log",
            {"message": "Skipped hypothesis research — entering experiment design.", "phase": "setup"},
        ))
    except Exception:
        pass

    return {"status": "ok", "current_stage": "setup", "project_id": project_id}


@router.post("/{project_id}/force-reset")
async def force_reset_project(
    project_id: int,
    session: Session = Depends(get_session),
) -> dict:
    """
    Force reset: cancel any running task, restore to the last stable checkpoint,
    clear the approval gate, and notify the frontend via WS.

    The "last stable checkpoint" is the last non-approval substage that was
    successfully entered — stored in project.status as checkpoint_stage/substage.
    """
    project = session.get(Project, project_id)
    if project is None or project.status == "deleted":
        raise HTTPException(status_code=404, detail="Project not found")

    # Cancel active background task if any
    try:
        from alfred.main import _active_tasks  # local import avoids circular dep at module level
        task = _active_tasks.pop(str(project_id), None)
        if task and not task.done():
            task.cancel()
    except Exception as exc:
        logger.warning("Could not cancel active task for project %s: %s", project_id, exc)

    # Unregister state machine (releases the approval gate lock)
    try:
        from alfred.state_machine.machine import unregister_machine
        unregister_machine(project_id)
    except Exception:
        pass

    # Determine rollback target from persisted snapshot
    rollback_stage: int = 1
    rollback_substage: str = "generating_queries"
    try:
        snapshot = json.loads(project.status) if project.status else {}
        cp_stage = snapshot.get("checkpoint_stage")
        cp_sub = snapshot.get("checkpoint_substage")
        if cp_stage and cp_sub:
            rollback_stage = int(cp_stage)
            rollback_substage = str(cp_sub)
        else:
            # Fall back to current stage's start
            cur_stage = snapshot.get("stage", 1)
            rollback_stage = int(cur_stage)
            stage_starts = {1: "generating_queries", 2: "proposing", 3: "awaiting_next"}
            rollback_substage = stage_starts.get(rollback_stage, "generating_queries")
    except Exception as exc:
        logger.warning("Could not read checkpoint for project %s: %s", project_id, exc)

    new_snapshot = {
        "stage": rollback_stage,
        "substage": rollback_substage,
        "auto_approve": project.auto_approve,
        "pending_plan": {},
        "checkpoint_stage": rollback_stage,
        "checkpoint_substage": rollback_substage,
        "ts": datetime.utcnow().isoformat(),
        "force_reset": True,
    }
    project.status = json.dumps(new_snapshot)
    project.updated_at = datetime.utcnow()
    session.add(project)
    session.commit()

    # Notify frontend
    try:
        from alfred.ws import manager
        import asyncio
        asyncio.create_task(manager.send(
            str(project_id), "force_reset",
            {
                "stage": rollback_stage,
                "substage": rollback_substage,
                "label": f"Force reset — restored to {rollback_substage.replace('_', ' ')}",
            },
        ))
        asyncio.create_task(manager.broadcast_progress(
            str(project_id),
            stage=rollback_stage,
            substage=rollback_substage,
            label=f"Force reset — restored to {rollback_substage.replace('_', ' ')}",
            current=0, total=0, status="idle",
        ))
    except Exception as exc:
        logger.warning("Could not send force_reset WS event: %s", exc)

    logger.info(
        "Force reset: project=%s restored to stage=%s substage=%s",
        project_id, rollback_stage, rollback_substage,
    )
    return {
        "status": "ok",
        "restored_to": {"stage": rollback_stage, "substage": rollback_substage},
    }


@router.post("/{project_id}/auto_approve")
async def toggle_auto_approve(
    project_id: int, session: Session = Depends(get_session)
) -> dict:
    """
    Toggle auto-approve (flip current value) and return the new value.

    This lightweight version is called by the Sidebar toggle button which
    doesn't know the current value. The experiments_router version accepts
    an explicit boolean and also syncs the live state machine.
    """
    project = session.get(Project, project_id)
    if project is None or project.status == "deleted":
        raise HTTPException(status_code=404, detail="Project not found")
    project.auto_approve = not project.auto_approve
    project.updated_at = datetime.utcnow()
    session.add(project)
    session.commit()
    return {"status": "ok", "auto_approve": project.auto_approve}