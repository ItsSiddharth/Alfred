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
    Experiment, MemoryItem, Message, Project, ProjectStage, Score, ToolCall,
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