"""
ALFRED FastAPI application entrypoint.

Startup sequence:
1. load_config() — read alfred_config.json if it exists
2. init_db()     — create SQLite tables (idempotent)
3. Mount routers and WebSocket endpoint
4. Serve via uvicorn (started by scripts/dev.py or `uvicorn alfred.main:app`)
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from alfred.api.config_router import router as config_router
from alfred.api.models_router import router as models_router
from alfred.api.projects_router import router as projects_router
from alfred.config import get_config, is_configured, load_config, setup_logging
from alfred.db import init_db
from alfred.ws import manager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan — runs once on startup and once on shutdown
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialise config and database on startup."""
    cfg = load_config()
    if cfg is not None:
        setup_logging(cfg)
        init_db(cfg.db_path)
        logger.info("ALFRED backend ready — workspace: %s", cfg.workspace_path)
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        logger.info("ALFRED backend starting — awaiting first-run setup.")

    yield  # Application runs here.

    logger.info("ALFRED backend shutting down.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


app = FastAPI(
    title="ALFRED Research Agent",
    version="0.1.0",
    lifespan=lifespan,
)

# Allow Vite dev server to call the backend during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers.
app.include_router(config_router)
app.include_router(projects_router)
app.include_router(models_router)


# ---------------------------------------------------------------------------
# WebSocket — /ws/project/{project_id}
# ---------------------------------------------------------------------------


@app.websocket("/ws/project/{project_id}")
async def websocket_endpoint(websocket: WebSocket, project_id: str) -> None:
    """
    Single persistent WebSocket per project.

    Receives JSON messages from the frontend:
      {"type": "chat", "content": "...", "model": "qwen2.5:7b", "message_id": "..."}

    In Stage 1 the only handled client→server message type is "chat" which
    streams a response from Ollama back over the WS.  Unknown message types
    are echoed back as "result" events for debugging.
    """
    await manager.connect(project_id, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                msg = {"type": "unknown", "raw": raw}

            msg_type = msg.get("type", "unknown")

            if msg_type == "chat":
                await _handle_chat(project_id, msg)
            else:
                # Echo unknown messages back for debugging.
                await manager.send(project_id, "result", {"echo": msg})

    except WebSocketDisconnect:
        await manager.disconnect(project_id)


async def _handle_chat(project_id: str, msg: dict) -> None:
    """
    Handle a chat message from the frontend.

    Expected msg fields:
      content    — user's message text
      model      — Ollama model tag to use
      message_id — unique ID for this streaming response
    """
    from alfred.agents.base import Role, make_client
    from alfred.services.ollama import OllamaError

    content: str = msg.get("content", "").strip()
    model: str = msg.get("model", "")
    message_id: str = msg.get("message_id", "chat-response")

    if not content:
        return

    if not model:
        await manager.broadcast_error(
            project_id,
            human_message="No model selected. Pick a model from the Find models panel.",
            remediation="Open the sidebar → Find models → pull a model → select it.",
        )
        return

    client = make_client(model, project_id=project_id, ws_manager=manager)
    try:
        await client.chat(
            Role.RESEARCHER,
            [{"role": "user", "content": content}],
            message_id=message_id,
        )
        await manager.broadcast_done(project_id, summary="Response complete")
    except OllamaError as exc:
        await manager.broadcast_error(
            project_id,
            human_message=str(exc),
            remediation="Make sure Ollama is running and the selected model is pulled.",
        )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "configured": is_configured(),
    }