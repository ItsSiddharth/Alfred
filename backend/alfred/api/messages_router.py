"""
api/messages_router.py — /api/projects/{project_id}/messages

Endpoints:
  GET   /api/projects/{project_id}/messages/              — paginated message history (ASC)
  POST  /api/projects/{project_id}/messages/              — persist a new message
  GET   /api/projects/{project_id}/messages/{message_id} — single message
  PATCH /api/projects/{project_id}/messages/{message_id} — update content/metadata after streaming

The PATCH endpoint exists so the backend can create an empty assistant placeholder
before streaming begins, stream tokens via WebSocket, then write the final full
content back to the DB row once streaming completes.
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


class MessageUpdate(BaseModel):
    """Partial update — only provided fields are changed."""
    content: Optional[str] = None
    metadata_json: Optional[str] = None
    kind: Optional[MessageKind] = None


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
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> list:
    """
    Return messages for a project, oldest first (ASC created_at), with pagination.

    Why ASC: chat threads read top-to-bottom; the frontend appends new messages
    at the bottom, so the DB order must match chronological order.
    """
    project = session.get(Project, project_id)
    if project is None or project.status == "deleted":
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
    """
    Persist a new message row.

    Called by the backend to create:
    - User messages immediately on receipt.
    - Empty assistant placeholder rows before streaming starts (content="").
      The placeholder is then patched via PATCH /{message_id} when streaming ends.
    """
    project = session.get(Project, project_id)
    if project is None or project.status == "deleted":
        raise HTTPException(status_code=404, detail="Project not found")

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


@router.patch("/{message_id}", response_model=MessageResponse)
async def update_message(
    project_id: int,
    message_id: int,
    req: MessageUpdate,
    session: Session = Depends(get_session),
) -> Message:
    """
    Update an existing message's content and/or metadata.

    Primary use-case: after streaming completes, the backend writes the full
    assistant response back to the placeholder row it created before streaming.

    Also used by tests to verify the patch endpoint works correctly.
    """
    msg = session.get(Message, message_id)
    if msg is None or msg.project_id != project_id:
        raise HTTPException(status_code=404, detail="Message not found")

    changed = False
    if req.content is not None:
        msg.content = req.content
        changed = True
    if req.metadata_json is not None:
        try:
            json.loads(req.metadata_json)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="metadata_json must be valid JSON")
        msg.metadata_json = req.metadata_json
        changed = True
    if req.kind is not None:
        msg.kind = req.kind
        changed = True

    if changed:
        session.add(msg)
        session.commit()
        session.refresh(msg)

    return msg