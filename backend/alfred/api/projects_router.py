"""
/api/projects router.

Full CRUD for Project rows.  Projects are the top-level container for
everything in ALFRED: messages, experiments, memory, scores.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from alfred.db import get_session
from alfred.models.db_models import Project, ProjectStage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# Pydantic I/O shapes (separate from SQLModel table models)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/", response_model=List[ProjectResponse])
async def list_projects(session: Session = Depends(get_session)) -> list:
    """Return all projects ordered newest first."""
    projects = session.exec(select(Project).order_by(Project.created_at.desc())).all()
    return projects


@router.post("/", response_model=ProjectResponse, status_code=201)
async def create_project(
    req: ProjectCreate, session: Session = Depends(get_session)
) -> Project:
    """Create a new project."""
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
async def get_project(project_id: int, session: Session = Depends(get_session)) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: int, req: ProjectUpdate, session: Session = Depends(get_session)
) -> Project:
    project = session.get(Project, project_id)
    if project is None:
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
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    session.delete(project)
    session.commit()
    logger.info("Project deleted: id=%s", project_id)