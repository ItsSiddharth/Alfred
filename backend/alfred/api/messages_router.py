"""
api/messages_router.py — /api/projects/{project_id}/messages

Endpoints:
  GET  /api/projects/{project_id}/messages   — paginated message history
  POST /api/projects/{project_id}/messages   — persist a new message
  GET  /api/projects/{project_id}/messages/{message_id} — single message
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from alfred.db import get_session
from alfred.models.db_models import (
    Message,
    MessageKind,
    MessageRole,
    Project,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects/{project_id}/messages", tags=["messages"])


# ---------------------------------------------------------------------------
# I/O models
# ---------------------------------------------------------------------------


class MessageCreate(BaseModel):
    role: MessageRole
    content: str
    kind: MessageKind = MessageKind.chat
    metadata_json: str = "{}"


class MessageResponse(BaseModel):
    id: int
    project_id: int
    role: MessageRole
    content: str
    created_at: datetime
    kind: MessageKind
    metadata_json: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/", response_model=List[MessageResponse])
async def list_messages(
    project_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> list:
    """
    Return messages for a project, oldest first, with pagination.
    The frontend loads all messages on project open; pagination is for
    very long projects.
    """
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    messages = session.exec(
        select(Message)
        .where(Message.project_id == project_id)
        .order_by(Message.created_at.asc())
        .offset(offset)
        .limit(limit)
    ).all()
    return messages


@router.post("/", response_model=MessageResponse, status_code=201)
async def create_message(
    project_id: int,
    req: MessageCreate,
    session: Session = Depends(get_session),
) -> Message:
    """Persist a new message.  Called by the backend after saving a user/assistant turn."""
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # Validate metadata_json is valid JSON.
    try:
        json.loads(req.metadata_json)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="metadata_json must be valid JSON")

    msg = Message(
        project_id=project_id,
        role=req.role,
        content=req.content,
        kind=req.kind,
        metadata_json=req.metadata_json,
    )
    session.add(msg)
    session.commit()
    session.refresh(msg)
    return msg


@router.get("/{message_id}", response_model=MessageResponse)
async def get_message(
    project_id: int,
    message_id: int,
    session: Session = Depends(get_session),
) -> Message:
    msg = session.get(Message, message_id)
    if msg is None or msg.project_id != project_id:
        raise HTTPException(status_code=404, detail="Message not found")
    return msg