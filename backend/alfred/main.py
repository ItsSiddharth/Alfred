"""
ALFRED FastAPI application entrypoint — Stage 2.

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
from alfred.api.experiments_router import router as experiments_router
from alfred.api.messages_router import router as messages_router
from alfred.api.models_router import router as models_router
from alfred.api.projects_router import router as projects_router
from alfred.config import is_configured, load_config, setup_logging
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

# Mount routers — Stage 0 + Stage 2 additions.
app.include_router(config_router)
app.include_router(projects_router)
app.include_router(models_router)
app.include_router(messages_router)
app.include_router(experiments_router)


# ---------------------------------------------------------------------------
# WebSocket — /ws/project/{project_id}
# ---------------------------------------------------------------------------


@app.websocket("/ws/project/{project_id}")
async def websocket_endpoint(websocket: WebSocket, project_id: str) -> None:
    """
    Single persistent WebSocket per project.

    Handled client→server message types:
      chat          — stream a response from Ollama using the selected model
      demo_pipeline — trigger the scripted Stage-1 state machine demo (QA/dev)
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
            elif msg_type == "demo_pipeline":
                # Run as a background task so it doesn't block the WS receive loop.
                asyncio.create_task(_handle_demo_pipeline(project_id, msg))
            else:
                # Echo unknown messages back for debugging.
                await manager.send(project_id, "result", {"echo": msg})

    except WebSocketDisconnect:
        await manager.disconnect(project_id)


# ---------------------------------------------------------------------------
# Chat handler
# ---------------------------------------------------------------------------


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
# Demo pipeline handler — scripted Stage-1 state machine walkthrough
# ---------------------------------------------------------------------------


async def _handle_demo_pipeline(project_id: str, msg: dict) -> None:
    """
    Scripted demo that drives the ExperimentStateMachine through all
    Stage-1 substages and pauses at an approval gate with a sample scorecard.

    This is a dev/QA convenience; real agents replace it in Stage 5.
    The machine is registered in the global registry so the REST approval
    endpoint can reach it.

    Substage flow:
        generating_queries → sweeping_sources → snowballing
        → web_sweep → analyzing → scoring → awaiting_approval → done
    """
    from alfred.db import get_engine
    from alfred.models.db_models import Experiment, ExperimentStatus, Project
    from alfred.state_machine.machine import (
        ExperimentStateMachine,
        S1Sub,
        register_machine,
        unregister_machine,
    )
    from sqlmodel import Session

    logger.info("Demo pipeline started: project_id=%s", project_id)

    # Resolve project integer ID.
    try:
        pid_int = int(project_id)
    except ValueError:
        await manager.broadcast_error(
            project_id,
            human_message="Demo requires a numeric project ID.",
        )
        return

    # Ensure DB engine is available.
    try:
        engine = get_engine()
    except RuntimeError:
        await manager.broadcast_error(
            project_id,
            human_message="Database not ready. Complete the first-run workspace setup.",
            remediation="Go through the first-run setup via the browser.",
        )
        return

    # Create the demo experiment row in a short-lived session.
    try:
        with Session(engine) as setup_session:
            project = setup_session.get(Project, pid_int)
            if project is None:
                await manager.broadcast_error(
                    project_id,
                    human_message=f"Project {pid_int} not found. Create a project first.",
                )
                return
            auto_approve = project.auto_approve

            exp = Experiment(
                project_id=pid_int,
                iteration=1,
                seed=42,
                plan_json="{}",
                status=ExperimentStatus.planned,
            )
            setup_session.add(exp)
            setup_session.commit()
            setup_session.refresh(exp)
            exp_id = exp.id
    except Exception as exc:
        logger.exception("Demo pipeline setup failed: %s", exc)
        await manager.broadcast_error(
            project_id,
            human_message="Demo setup failed — see backend logs.",
        )
        return

    # Open a long-lived session for the machine (must stay open for all transitions).
    machine_session = Session(engine)
    machine = ExperimentStateMachine(
        project_id=pid_int,
        ws_manager=manager,
        db_session=machine_session,
        auto_approve=auto_approve,
    )
    register_machine(pid_int, machine)

    try:
        # Phase A — generating_queries
        await machine.transition(S1Sub.GENERATING_QUERIES, label="Generating search queries")
        await asyncio.sleep(1.2)

        # Phase B — sweeping_sources (progress ticks)
        await machine.transition(S1Sub.SWEEPING_SOURCES, label="Sweeping academic sources")
        for i in range(1, 6):
            await asyncio.sleep(0.4)
            await machine.report_progress(i, 5, f"Querying source {i}/5")

        # Phase C — snowballing (progress ticks)
        await machine.transition(S1Sub.SNOWBALLING, label="Expanding citation network")
        for i in range(1, 5):
            await asyncio.sleep(0.3)
            await machine.report_progress(i, 4, f"Snowballing paper {i}/4")

        # Phase D — web_sweep
        await machine.transition(S1Sub.WEB_SWEEP, label="Web sweep for implementations")
        await asyncio.sleep(0.6)

        # Phase E — analyzing
        await machine.transition(S1Sub.ANALYZING, label="Synthesising literature landscape")
        await asyncio.sleep(1.0)

        # Scoring
        await machine.transition(S1Sub.SCORING, label="Computing novelty & publishability scores")
        await asyncio.sleep(0.4)

        # Build the demo scorecard plan.
        demo_plan = {
            "experiment_id": exp_id,
            "novelty_score": 72,
            "gap_score": 68,
            "publishability_score": 61,
            "novelty_rationale": (
                "The proposed method combines contrastive learning with sparse attention "
                "in a way not directly addressed by prior work."
            ),
            "gap_rationale": (
                "While individual components are well-studied, their combination for "
                "this specific task remains an open research question."
            ),
            "publishability_rationale": (
                "Scores suggest a reasonable target at workshop or regional conference "
                "level. Stronger baselines are needed for top-tier venues."
            ),
            "rationale": (
                "The hypothesis shows moderate novelty. The gap is real but partially "
                "addressed in concurrent work. Publishability depends heavily on execution quality."
            ),
            "cited_papers": [
                {
                    "title": "Contrastive Learning of Structured World Models",
                    "year": 2020,
                    "venue": "ICLR",
                    "url": "https://arxiv.org/abs/1911.12247",
                },
                {
                    "title": "Sparse is Enough in Scaling Transformers",
                    "year": 2021,
                    "venue": "NeurIPS",
                    "url": "https://arxiv.org/abs/2111.12763",
                },
            ],
        }

        # Transition to awaiting_approval — blocks until user acts (or auto-approved).
        # experiment_id is inside the plan dict; _handle_approval_gate hoists it
        # to the top-level of the WS payload so the frontend can read it directly.
        response = await machine.transition(
            S1Sub.AWAITING_APPROVAL,
            plan=demo_plan,
            label="Awaiting hypothesis approval",
        )

        if response and response.approved:
            await machine.transition(S1Sub.DONE, label="Hypothesis validated")
            await machine.report_done("Hypothesis validated — ready for experiment setup.")
        else:
            feedback = response.feedback if response else "rejected"
            logger.info("Demo pipeline rejected: feedback=%r", feedback)
            await machine.report_error(
                "Plan rejected — revise and re-run the demo.",
                remediation=f"Feedback: {feedback}",
            )

    except Exception as exc:
        logger.exception("Demo pipeline error: %s", exc)
        await machine.report_error(
            f"Demo pipeline error: {exc}",
            remediation="Check the backend terminal for the full traceback.",
        )
    finally:
        unregister_machine(pid_int)
        machine_session.close()
        logger.info("Demo pipeline finished: project_id=%s", project_id)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "configured": is_configured(),
    }