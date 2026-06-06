"""
api/hypothesis_router.py — /api/projects/{project_id}/hypothesis

Endpoints:
  GET  /api/projects/{project_id}/hypothesis/scores  — return persisted Score rows
  POST /api/projects/{project_id}/hypothesis/start   — kick off a new research run
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from alfred.db import get_session
from alfred.models.db_models import Project, ProjectStage, Score, ScoreKind
from alfred.state_machine.machine import get_machine

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/projects/{project_id}/hypothesis", tags=["hypothesis"]
)


# ── I/O models ────────────────────────────────────────────────────────────────


class ScoreResponse(BaseModel):
    id: int
    project_id: int
    kind: ScoreKind
    value: int
    rationale: str
    citations: list[dict[str, Any]]
    created_at: datetime


class HypothesisStartRequest(BaseModel):
    hypothesis: str
    model: str
    feedback: str = ""


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/scores", response_model=list[ScoreResponse])
async def get_scores(
    project_id: int,
    session: Session = Depends(get_session),
) -> list[ScoreResponse]:
    """Return the three verdict scores for this project (empty list if none yet)."""
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    scores = session.exec(
        select(Score).where(Score.project_id == project_id)
    ).all()

    return [
        ScoreResponse(
            id=s.id,  # type: ignore[arg-type]
            project_id=s.project_id,
            kind=s.kind,
            value=s.value,
            rationale=s.rationale,
            citations=_parse_citations(s.citations_json),
            created_at=s.created_at,
        )
        for s in scores
    ]


@router.post("/start")
async def start_hypothesis(
    project_id: int,
    req: HypothesisStartRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> dict:
    """
    Kick off a new hypothesis validation run as a background task.

    Returns immediately.  Progress is delivered via the project's WebSocket.
    """
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.current_stage not in (ProjectStage.hypothesis,):
        raise HTTPException(
            status_code=409,
            detail=f"Project is in stage '{project.current_stage.value}', not 'hypothesis'.",
        )

    if get_machine(project_id) is not None:
        raise HTTPException(
            status_code=409,
            detail="A hypothesis validation is already running for this project.",
        )

    if not req.hypothesis.strip():
        raise HTTPException(status_code=400, detail="hypothesis must not be empty")

    background_tasks.add_task(
        _run_hypothesis_bg,
        project_id=project_id,
        hypothesis=req.hypothesis,
        model=req.model,
        auto_approve=project.auto_approve,
        feedback=req.feedback,
    )
    return {"status": "started", "project_id": project_id}


# ── Background task helper ────────────────────────────────────────────────────


async def _run_hypothesis_bg(
    project_id: int,
    hypothesis: str,
    model: str,
    auto_approve: bool,
    feedback: str = "",
) -> None:
    """Instantiate HypothesisAgent and run it; handle DB session lifecycle."""
    try:
        from alfred.db import get_engine
        from alfred.agents.hypothesis import HypothesisAgent
        from alfred.ws import manager
        from sqlmodel import Session

        engine = get_engine()
        with Session(engine) as session:
            agent = HypothesisAgent(
                project_id=project_id,
                model=model,
                ws_manager=manager,
                db_session=session,
                auto_approve=auto_approve,
            )
            await agent.run(hypothesis, feedback=feedback)
    except Exception as exc:
        logger.exception("Background hypothesis run failed: %s", exc)


def _parse_citations(citations_json: str) -> list[dict]:
    try:
        return json.loads(citations_json or "[]")
    except Exception:
        return []
