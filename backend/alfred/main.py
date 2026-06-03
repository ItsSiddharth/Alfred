"""
ALFRED FastAPI application entrypoint — Stage 4.

Changes from Stage 3:
  - Mounts /api/tools router
  - Loads ToolRegistry from tools.yaml in lifespan
  - Patches _handle_chat to store msg_id + persist assistant row before streaming
    so that messages survive page refresh (fixes "disappears on reload" bug)
  - Adds project DELETE endpoint
  - Adds get_session_or_none guard so endpoints return 503 rather than
    crashing with RuntimeError when DB is not yet initialised

Everything else (lifespan, config, DB init, WS, demo pipeline) is UNCHANGED
from Stage 3.
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
from alfred.api.memory_router import global_router as memory_global_router
from alfred.api.memory_router import project_router as memory_project_router
from alfred.api.messages_router import router as messages_router
from alfred.api.models_router import router as models_router
from alfred.api.projects_router import router as projects_router
from alfred.api.tools_router import router as tools_router          # ← Stage 4
from alfred.config import is_configured, load_config, setup_logging
from alfred.db import init_db
from alfred.ws import manager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialise config, database, and tool registry on startup."""
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

    # Load tool registry (Stage 4) — safe even if workspace not set up yet
    _load_tools()

    yield

    logger.info("ALFRED backend shutting down.")


def _load_tools() -> None:
    """Load tools from tools.yaml; graceful if file is missing."""
    try:
        from pathlib import Path
        from alfred.tools.base import ToolRegistry
        yaml_path = Path(__file__).parent / "tools" / "tools.yaml"
        ToolRegistry.get().load_from_yaml(yaml_path)
        names = [t.name for t in ToolRegistry.get().list_tools()]
        logger.info("Tools loaded: %s", names)
    except Exception as exc:
        logger.warning("Tool registry load failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="ALFRED Research Agent",
    version="0.4.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers — Stages 0–4
app.include_router(config_router)
app.include_router(projects_router)
app.include_router(models_router)
app.include_router(messages_router)
app.include_router(experiments_router)
app.include_router(memory_project_router)
app.include_router(memory_global_router)
app.include_router(tools_router)          # ← Stage 4


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws/project/{project_id}")
async def websocket_endpoint(websocket: WebSocket, project_id: str) -> None:
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
                asyncio.create_task(_handle_demo_pipeline(project_id, msg))
            else:
                await manager.send(project_id, "result", {"echo": msg})

    except WebSocketDisconnect:
        await manager.disconnect(project_id)


# ---------------------------------------------------------------------------
# Chat handler — FIXED for Stage 4
#
# Key change: we create the assistant Message row BEFORE streaming starts
# and emit a msg_start WS event with its id.  The frontend stores tokens
# against that id.  After streaming, we patch the row with full content +
# metadata.  On page refresh, the full content is already in the DB.
# ---------------------------------------------------------------------------

async def _handle_chat(project_id: str, msg: dict) -> None:
    from alfred.agents.base import Role, make_client
    from alfred.services.ollama import OllamaError

    content: str = msg.get("content", "").strip()
    model: str = msg.get("model", "")
    message_id: str = msg.get("message_id", "")

    if not content:
        return

    if not model:
        await manager.broadcast_error(
            project_id,
            human_message="No model selected. Pick a model from the Find models panel.",
            remediation="Open the sidebar → Find models → pull a model → select it.",
        )
        return

    # --- Inject memory + persist messages to DB if DB is available ----------
    extra_system = ""
    pid_int: int | None = None
    try:
        pid_int = int(project_id)
    except ValueError:
        pass

    user_msg_id: int | None = None
    asst_msg_id: int | None = None

    if pid_int is not None:
        try:
            from alfred.db import get_engine
            from alfred.memory.context import build_memory_block
            from alfred.models.db_models import (
                Message, MessageKind, MessageRole, Project
            )
            from sqlmodel import Session, select
            from datetime import datetime

            engine = get_engine()
            with Session(engine) as session:
                # Build memory block
                try:
                    extra_system = build_memory_block(session, pid_int)
                except Exception:
                    pass

                # Persist user message
                try:
                    user_row = Message(
                        project_id=pid_int,
                        role=MessageRole.user,
                        content=content,
                        kind=MessageKind.chat,
                        metadata_json="{}",
                    )
                    session.add(user_row)
                    session.commit()
                    session.refresh(user_row)
                    user_msg_id = user_row.id
                except Exception as exc:
                    logger.warning("Could not persist user message: %s", exc)

                # Create empty assistant placeholder BEFORE streaming
                # so msg_id exists when the frontend receives msg_start
                try:
                    asst_row = Message(
                        project_id=pid_int,
                        role=MessageRole.assistant,
                        content="",          # filled in after streaming
                        kind=MessageKind.chat,
                        metadata_json="{}",
                    )
                    session.add(asst_row)
                    session.commit()
                    session.refresh(asst_row)
                    asst_msg_id = asst_row.id
                except Exception as exc:
                    logger.warning("Could not create assistant placeholder: %s", exc)

        except Exception as exc:
            logger.debug("DB operations skipped in chat handler: %s", exc)

    # Emit msg_start so frontend knows which DB row to attach tokens to
    if asst_msg_id is not None:
        await manager.send(project_id, "msg_start", {
            "msg_id": asst_msg_id,
            "message_id": message_id,   # legacy field for existing frontend
        })

    # --- Stream from Ollama --------------------------------------------------
    client = make_client(model, project_id=project_id, ws_manager=manager)
    full_response = ""
    try:
        full_response = await client.chat(
            Role.RESEARCHER,
            [{"role": "user", "content": content}],
            message_id=message_id,
            extra_system=extra_system,
        )
        await manager.broadcast_done(project_id, summary="Response complete")
    except OllamaError as exc:
        full_response = f"⚠️ {exc}"
        await manager.broadcast_error(
            project_id,
            human_message=str(exc),
            remediation="Make sure Ollama is running and the selected model is pulled.",
        )
    except Exception as exc:
        full_response = f"⚠️ Error: {exc}"
        await manager.broadcast_error(project_id, human_message=str(exc))

    # --- Write full content back to DB row ----------------------------------
    if asst_msg_id is not None and pid_int is not None:
        try:
            from alfred.db import get_engine
            from alfred.models.db_models import Message
            from sqlmodel import Session

            metadata = {
                "raw_prompt": extra_system[:500] if extra_system else "",
                "memory_block": extra_system,
                "memory_tokens": len(extra_system) // 4,
                "model": model,
                "tool_calls": [],
            }
            engine = get_engine()
            with Session(engine) as session:
                row = session.get(Message, asst_msg_id)
                if row:
                    row.content = full_response
                    row.metadata_json = json.dumps(metadata)
                    session.add(row)
                    session.commit()
        except Exception as exc:
            logger.warning("Could not update assistant message: %s", exc)

    # Emit done with msg_id so frontend can finalise the streaming row
    await manager.send(project_id, "done", {
        "msg_id": asst_msg_id,
        "summary": "Response complete",
    })


# ---------------------------------------------------------------------------
# Demo pipeline (UNCHANGED from Stage 3)
# ---------------------------------------------------------------------------

async def _handle_demo_pipeline(project_id: str, msg: dict) -> None:
    from alfred.db import get_engine
    from alfred.models.db_models import Experiment, ExperimentStatus, Project
    from alfred.state_machine.machine import (
        ExperimentStateMachine, S1Sub,
        register_machine, unregister_machine,
    )
    from sqlmodel import Session

    logger.info("Demo pipeline started: project_id=%s", project_id)

    try:
        pid_int = int(project_id)
    except ValueError:
        await manager.broadcast_error(project_id, human_message="Demo requires a numeric project ID.")
        return

    try:
        engine = get_engine()
    except RuntimeError:
        await manager.broadcast_error(
            project_id,
            human_message="Database not ready. Complete the first-run workspace setup.",
            remediation="Go through the first-run setup via the browser.",
        )
        return

    try:
        with Session(engine) as setup_session:
            project = setup_session.get(Project, pid_int)
            if project is None:
                await manager.broadcast_error(project_id,
                    human_message=f"Project {pid_int} not found. Create a project first.")
                return
            auto_approve = project.auto_approve
            exp = Experiment(project_id=pid_int, iteration=1, seed=42,
                             plan_json="{}", status=ExperimentStatus.planned)
            setup_session.add(exp)
            setup_session.commit()
            setup_session.refresh(exp)
            exp_id = exp.id
    except Exception as exc:
        logger.exception("Demo pipeline setup failed: %s", exc)
        await manager.broadcast_error(project_id, human_message="Demo setup failed — see backend logs.")
        return

    machine_session = Session(engine)
    machine = ExperimentStateMachine(
        project_id=pid_int, ws_manager=manager,
        db_session=machine_session, auto_approve=auto_approve,
    )
    register_machine(pid_int, machine)

    try:
        await machine.transition(S1Sub.GENERATING_QUERIES, label="Generating search queries")
        await asyncio.sleep(1.2)
        await machine.transition(S1Sub.SWEEPING_SOURCES, label="Sweeping academic sources")
        for i in range(1, 6):
            await asyncio.sleep(0.4)
            await machine.report_progress(i, 5, f"Querying source {i}/5")
        await machine.transition(S1Sub.SNOWBALLING, label="Expanding citation network")
        for i in range(1, 5):
            await asyncio.sleep(0.3)
            await machine.report_progress(i, 4, f"Snowballing paper {i}/4")
        await machine.transition(S1Sub.WEB_SWEEP, label="Web sweep for implementations")
        await asyncio.sleep(0.6)
        await machine.transition(S1Sub.ANALYZING, label="Synthesising literature landscape")
        await asyncio.sleep(1.0)
        await machine.transition(S1Sub.SCORING, label="Computing novelty & publishability scores")
        await asyncio.sleep(0.4)

        demo_plan = {
            "experiment_id": exp_id,
            "novelty_score": 72, "gap_score": 68, "publishability_score": 61,
            "novelty_rationale": "The proposed method combines contrastive learning with sparse attention.",
            "gap_rationale": "Their combination for this specific task remains an open research question.",
            "publishability_rationale": "Scores suggest a workshop or regional conference level target.",
            "rationale": "Moderate novelty. Gap is real but partially addressed in concurrent work.",
            "cited_papers": [
                {"title": "Contrastive Learning of Structured World Models", "year": 2020, "venue": "ICLR",
                 "url": "https://arxiv.org/abs/1911.12247"},
                {"title": "Sparse is Enough in Scaling Transformers", "year": 2021, "venue": "NeurIPS",
                 "url": "https://arxiv.org/abs/2111.12763"},
            ],
        }
        response = await machine.transition(S1Sub.AWAITING_APPROVAL, plan=demo_plan,
                                            label="Awaiting hypothesis approval")
        if response and response.approved:
            await machine.transition(S1Sub.DONE, label="Hypothesis validated")
            await machine.report_done("Hypothesis validated — ready for experiment setup.")
        else:
            feedback = response.feedback if response else "rejected"
            await machine.report_error("Plan rejected — revise and re-run the demo.",
                                       remediation=f"Feedback: {feedback}")
    except Exception as exc:
        logger.exception("Demo pipeline error: %s", exc)
        await machine.report_error(f"Demo pipeline error: {exc}",
                                   remediation="Check the backend terminal.")
    finally:
        unregister_machine(pid_int)
        machine_session.close()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "configured": is_configured()}