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
from fastapi.staticfiles import StaticFiles

from alfred.api.config_router import router as config_router
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
        # No config yet; just set up basic console logging.
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


# ---------------------------------------------------------------------------
# WebSocket — /ws/project/{project_id}
# ---------------------------------------------------------------------------


@app.websocket("/ws/project/{project_id}")
async def websocket_endpoint(websocket: WebSocket, project_id: str) -> None:
    """
    Single persistent WebSocket per project.

    On connection, immediately streams a demo sequence of progress + token
    events so the frontend pipeline can be verified end-to-end in Stage 0.
    In later stages, agents use manager.send() / broadcast_*() directly.
    """
    await manager.connect(project_id, websocket)
    try:
        # --- Demo stream (Stage 0 validation only) -------------------------
        # Drives the progress strip through a fake pipeline so the frontend
        # rendering can be verified before any real agent logic exists.
        asyncio.create_task(_demo_stream(project_id))
        # -------------------------------------------------------------------

        # Keep the connection alive; wait for client messages (echoed back).
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                msg = {"raw": data}
            # Echo the message back so the frontend can confirm round-trip.
            await manager.send(project_id, "result", {"echo": msg})

    except WebSocketDisconnect:
        await manager.disconnect(project_id)


async def _demo_stream(project_id: str) -> None:
    """
    Sends a fake Stage-1 pipeline sequence over WebSocket.

    Used only for Stage-0 acceptance testing.  Later stages replace this
    with real agent events and this function is never called in production.
    """
    await asyncio.sleep(0.5)  # let the frontend settle

    substages = [
        ("generating_queries", "Generating search queries", 1, 5),
        ("sweeping_sources", "Sweeping academic sources", 2, 5),
        ("snowballing", "Expanding citations", 3, 5),
        ("web_sweep", "Web sweep for implementations", 4, 5),
        ("analyzing", "Synthesising results", 5, 5),
    ]

    for substage, label, current, total in substages:
        await manager.broadcast_progress(
            project_id,
            stage=1,
            substage=substage,
            label=label,
            current=current,
            total=total,
            status="running",
        )
        await asyncio.sleep(0.8)

    # Stream some fake tokens into the chat area.
    fake_tokens = (
        "Hello! I'm ALFRED, your local research agent. "
        "This is a demo stream to verify the WebSocket pipeline is working end-to-end. "
        "Once you configure a workspace and connect an Ollama model, real research will appear here."
    ).split()

    for token in fake_tokens:
        await manager.broadcast_token(project_id, token + " ", message_id="demo-1")
        await asyncio.sleep(0.05)

    await manager.broadcast_done(project_id, summary="Demo stream complete")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "configured": is_configured(),
    }