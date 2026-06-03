"""
api/memory_router.py — /api/projects/{project_id}/memory

Endpoints:
  GET    /api/projects/{pid}/memory/items                  — list raw items
  POST   /api/projects/{pid}/memory/items                  — create item
  PATCH  /api/projects/{pid}/memory/items/{item_id}        — update item
  DELETE /api/projects/{pid}/memory/items/{item_id}        — delete item
  GET    /api/projects/{pid}/memory/compiled               — get compiled doc
  POST   /api/projects/{pid}/memory/compile                — trigger recompile
  GET    /api/memory/global/items                          — global items
  POST   /api/memory/global/items                          — create global item
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from alfred.config import get_config, is_configured
from alfred.db import get_session
from alfred.memory import compress as mem_compress
from alfred.memory import store as mem_store
from alfred.models.db_models import MemoryItem, MemorySource, MemoryType, Project

logger = logging.getLogger(__name__)

# Two routers: project-scoped and global
project_router = APIRouter(
    prefix="/api/projects/{project_id}/memory", tags=["memory"]
)
global_router = APIRouter(prefix="/api/memory/global", tags=["memory"])


# ---------------------------------------------------------------------------
# I/O models
# ---------------------------------------------------------------------------


class MemoryItemCreate(BaseModel):
    type: MemoryType
    content: str
    tags: str = ""
    source: MemorySource = MemorySource.user


class MemoryItemUpdate(BaseModel):
    content: Optional[str] = None
    tags: Optional[str] = None
    active: Optional[bool] = None


class MemoryItemResponse(BaseModel):
    id: int
    project_id: Optional[int]
    type: MemoryType
    content: str
    tags: str
    created_at: datetime
    active: bool
    source: MemorySource


class CompiledResponse(BaseModel):
    markdown: str
    token_estimate: int
    item_count: int
    is_stale: bool


class CompileRequest(BaseModel):
    model: str = ""  # defaults to config.default_model if empty


# ---------------------------------------------------------------------------
# Project-scoped endpoints
# ---------------------------------------------------------------------------


@project_router.get("/items", response_model=List[MemoryItemResponse])
async def list_project_items(
    project_id: int,
    type: Optional[MemoryType] = None,
    active_only: bool = True,
    include_global: bool = True,
    session: Session = Depends(get_session),
) -> list:
    """List memory items for a project (optionally including global items)."""
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    items = mem_store.list_items(
        session,
        project_id=project_id,
        memory_type=type,
        active_only=active_only,
        include_global=include_global,
    )
    # Exclude compiled sentinel entries from external listings
    return [i for i in items if i.tags != mem_compress._COMPILED_TAG]


@project_router.post("/items", response_model=MemoryItemResponse, status_code=201)
async def create_project_item(
    project_id: int,
    req: MemoryItemCreate,
    session: Session = Depends(get_session),
) -> MemoryItem:
    """Create a project-scoped memory item."""
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if not req.content.strip():
        raise HTTPException(status_code=400, detail="content must not be empty")

    return mem_store.create_item(
        session,
        project_id=project_id,
        memory_type=req.type,
        content=req.content,
        tags=req.tags,
        source=req.source,
    )


@project_router.patch("/items/{item_id}", response_model=MemoryItemResponse)
async def update_project_item(
    project_id: int,
    item_id: int,
    req: MemoryItemUpdate,
    session: Session = Depends(get_session),
) -> MemoryItem:
    """Update content, tags, or active flag of an item."""
    item = session.get(MemoryItem, item_id)
    if item is None or item.project_id != project_id:
        raise HTTPException(status_code=404, detail="Memory item not found")

    updated = mem_store.update_item(
        session,
        item_id,
        content=req.content,
        tags=req.tags,
        active=req.active,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Memory item not found")
    return updated


@project_router.delete("/items/{item_id}", status_code=204)
async def delete_project_item(
    project_id: int,
    item_id: int,
    session: Session = Depends(get_session),
) -> None:
    """Hard-delete a memory item."""
    item = session.get(MemoryItem, item_id)
    if item is None or item.project_id != project_id:
        raise HTTPException(status_code=404, detail="Memory item not found")
    mem_store.delete_item(session, item_id)


@project_router.get("/compiled", response_model=CompiledResponse)
async def get_compiled_doc(
    project_id: int,
    session: Session = Depends(get_session),
) -> dict:
    """Return the current compiled memory doc for a project."""
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    compiled = mem_compress.get_compiled(session, project_id)
    if compiled is None:
        return {
            "markdown": "_No compiled memory yet. Add items and click Recompile._",
            "token_estimate": 0,
            "item_count": 0,
            "is_stale": True,
        }
    return {
        "markdown": compiled.markdown,
        "token_estimate": compiled.token_estimate,
        "item_count": compiled.item_count,
        "is_stale": compiled.is_stale,
    }


@project_router.post("/compile", response_model=CompiledResponse)
async def compile_memory(
    project_id: int,
    req: CompileRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> dict:
    """
    Trigger a synchronous recompile of the memory for a project.
    Uses the configured default model if req.model is empty.
    """
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    model = req.model.strip()
    if not model:
        if is_configured():
            cfg = get_config()
            model = cfg.default_model
        if not model:
            # Final fallback: try to get a local model
            model = "qwen2.5:7b"

    try:
        result = await mem_compress.compile_memory(session, project_id, model)
        return {
            "markdown": result.markdown,
            "token_estimate": result.token_estimate,
            "item_count": result.item_count,
            "is_stale": result.is_stale,
        }
    except Exception as exc:
        logger.exception("compile_memory failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Compile failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Global memory endpoints (project_id=None)
# ---------------------------------------------------------------------------


@global_router.get("/items", response_model=List[MemoryItemResponse])
async def list_global_items(
    type: Optional[MemoryType] = None,
    active_only: bool = True,
    session: Session = Depends(get_session),
) -> list:
    """List global (project-agnostic) memory items."""
    items = mem_store.list_items(
        session,
        project_id=None,
        memory_type=type,
        active_only=active_only,
        include_global=True,
    )
    return [i for i in items if i.tags != mem_compress._COMPILED_TAG]


@global_router.post("/items", response_model=MemoryItemResponse, status_code=201)
async def create_global_item(
    req: MemoryItemCreate,
    session: Session = Depends(get_session),
) -> MemoryItem:
    """Create a global (project-agnostic) memory item."""
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="content must not be empty")

    return mem_store.create_item(
        session,
        project_id=None,
        memory_type=req.type,
        content=req.content,
        tags=req.tags,
        source=req.source,
    )