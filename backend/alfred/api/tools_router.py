"""
/api/tools router — Stage 4.

Endpoints:
  GET  /api/tools/              — list all registered tools
  GET  /api/tools/{name}        — get a single tool's schema
  POST /api/tools/{name}/enable — enable a tool
  POST /api/tools/{name}/disable — disable a tool
  GET  /api/tools/calls/{project_id} — recent tool calls from DB
  POST /api/tools/test/{name}   — dev helper: run a tool with provided input
"""
from __future__ import annotations

import json
import logging
from typing import Any, Generator

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from alfred.tools.base import ToolRegistry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tools", tags=["tools"])


# ---------------------------------------------------------------------------
# Session dependency — graceful 503 when DB not yet initialised
# ---------------------------------------------------------------------------

def _get_session_safe() -> Generator[Session, None, None]:
    """
    Yields a SQLModel Session, or raises HTTP 503 if the DB is not yet
    initialised (i.e. first-run workspace setup hasn't been done).

    Using a proper generator so FastAPI's dependency system can inject it
    and the try/finally cleanup runs correctly.
    """
    try:
        from alfred.db import get_engine
        engine = get_engine()  # raises RuntimeError if not initialised
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="Database not ready. Complete workspace setup first.",
        ) from exc

    with Session(engine) as session:
        yield session


# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------

class ToolInfo(BaseModel):
    name: str
    description: str
    enabled: bool
    has_schema: bool
    parameters: dict[str, Any] | None = None


class ToolCallRecord(BaseModel):
    id: int
    project_id: int
    tool_name: str
    input_json: str
    output_summary: str
    created_at: str


class TestToolRequest(BaseModel):
    input: dict[str, Any]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/", response_model=list[ToolInfo])
async def list_tools() -> list[ToolInfo]:
    """Return all registered tools with their current enabled state and schema."""
    registry = ToolRegistry.get()
    result = []
    for tool in registry.list_tools():
        schema = tool.to_schema_dict()
        result.append(ToolInfo(
            name=tool.name,
            description=tool.description,
            enabled=tool.enabled,
            has_schema=tool.input_schema is not None,
            parameters=schema.get("parameters"),
        ))
    return result


@router.get("/{name}", response_model=ToolInfo)
async def get_tool(name: str) -> ToolInfo:
    registry = ToolRegistry.get()
    tool = registry.get_tool(name)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"Tool '{name}' not found")
    schema = tool.to_schema_dict()
    return ToolInfo(
        name=tool.name,
        description=tool.description,
        enabled=tool.enabled,
        has_schema=tool.input_schema is not None,
        parameters=schema.get("parameters"),
    )


@router.post("/{name}/enable")
async def enable_tool(name: str) -> dict[str, Any]:
    ok = ToolRegistry.get().set_enabled(name, True)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Tool '{name}' not found")
    return {"tool": name, "enabled": True}


@router.post("/{name}/disable")
async def disable_tool(name: str) -> dict[str, Any]:
    ok = ToolRegistry.get().set_enabled(name, False)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Tool '{name}' not found")
    return {"tool": name, "enabled": False}


@router.get("/calls/{project_id}", response_model=list[ToolCallRecord])
async def recent_tool_calls(
    project_id: int,
    limit: int = 50,
    session: Session = Depends(_get_session_safe),
) -> list[ToolCallRecord]:
    """Return the most recent tool calls for a project, newest first."""
    from alfred.models.db_models import ToolCall
    calls = session.exec(
        select(ToolCall)
        .where(ToolCall.project_id == project_id)
        .order_by(ToolCall.created_at.desc())
        .limit(limit)
    ).all()
    return [
        ToolCallRecord(
            id=c.id,
            project_id=c.project_id,
            tool_name=c.tool_name,
            input_json=c.input_json or "{}",
            output_summary=c.output_summary or "",
            created_at=c.created_at.isoformat() if c.created_at else "",
        )
        for c in calls
    ]


@router.post("/test/{name}")
async def test_tool(name: str, body: TestToolRequest) -> dict[str, Any]:
    """
    Dev helper — run a tool with the provided input and return the raw result.
    Useful for verifying tool behaviour from the sidebar or via curl.
    """
    registry = ToolRegistry.get()
    tool = registry.get_tool(name)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"Tool '{name}' not found")
    result = await tool.execute(body.input)
    return {
        "tool_name": result.tool_name,
        "success": result.success,
        "error": result.error,
        "sources": result.sources,
        "data": result.data,
    }