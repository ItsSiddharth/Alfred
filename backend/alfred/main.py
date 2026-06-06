"""
ALFRED FastAPI application entrypoint — Stage 5/6.

Changes from Stage 4:
  - Mounts /api/projects/{id}/hypothesis router (Stage 5)
  - _handle_chat now routes by project stage:
      hypothesis  → clarifying-questions mode; includes [START_RESEARCH] trigger
      setup       → SetupAgent multi-turn dialogue; checks for plan proposal
      other       → plain RESEARCHER chat (unchanged Stage 4 behaviour)
  - Lifespan runs add_column_if_missing for score.citations_json
  - _run_hypothesis_agent / _run_setup_agent_turn as background helpers

Everything else (lifespan, config, DB init, WS, demo pipeline) is UNCHANGED
from Stage 4.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncGenerator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from alfred.api.config_router import router as config_router
from alfred.api.experiments_router import router as experiments_router
from alfred.api.hypothesis_router import router as hypothesis_router   # ← Stage 5
from alfred.api.memory_router import global_router as memory_global_router
from alfred.api.memory_router import project_router as memory_project_router
from alfred.api.messages_router import router as messages_router
from alfred.api.models_router import router as models_router
from alfred.api.projects_router import router as projects_router
from alfred.api.tools_router import router as tools_router
from alfred.config import is_configured, load_config, setup_logging
from alfred.db import add_column_if_missing, init_db
from alfred.ws import manager

logger = logging.getLogger(__name__)

# Marker that signals the hypothesis clarifying-questions phase is done
_START_RESEARCH_MARKER = "[START_RESEARCH]"

# System-prompt addendum injected during the hypothesis clarifying-questions phase
_HYPOTHESIS_PREAMBLE = """\

You are helping a user clarify their ML research hypothesis before running a literature search.

Your job:
1. Ask targeted clarifying questions to understand:
   - The specific method or technique being proposed
   - The target domain / dataset / task
   - What the user believes makes their idea novel
   - Any existing work they are already aware of
2. Ask one or two questions at a time — do not overwhelm.
3. Once you have a clear picture (typically after 1–2 exchanges), include the exact
   token [START_RESEARCH] on its own line at the END of your response to trigger the
   literature validation pipeline. Only do this when you genuinely have enough context.
4. Never include [START_RESEARCH] on the very first message — always ask at least one
   clarifying question first.
"""


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    cfg = load_config()
    if cfg is not None:
        setup_logging(cfg)
        init_db(cfg.db_path)
        # Stage 5 migration: citations_json column on score table
        add_column_if_missing(
            "score", "citations_json",
            "TEXT NOT NULL DEFAULT '[]'",
            cfg.db_path,
        )
        logger.info("ALFRED backend ready — workspace: %s", cfg.workspace_path)
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        logger.info("ALFRED backend starting — awaiting first-run setup.")

    _load_tools()
    yield
    logger.info("ALFRED backend shutting down.")


def _load_tools() -> None:
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
    version="0.5.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(config_router)
app.include_router(projects_router)
app.include_router(models_router)
app.include_router(messages_router)
app.include_router(experiments_router)
app.include_router(memory_project_router)
app.include_router(memory_global_router)
app.include_router(tools_router)
app.include_router(hypothesis_router)         # ← Stage 5


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
# Chat handler — Stage 5/6 routing
# ---------------------------------------------------------------------------

async def _handle_chat(project_id: str, msg: dict) -> None:  # noqa: C901
    from alfred.agents.base import Role, make_client
    from alfred.services.ollama import OllamaError
    from alfred.models.db_models import (
        Message, MessageKind, MessageRole, Project, ProjectStage
    )

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

    # Resolve project_id to int
    pid_int: int | None = None
    try:
        pid_int = int(project_id)
    except ValueError:
        pass

    if pid_int is None:
        # Can't do stage-routing without a numeric project ID — fall through
        await _handle_chat_plain(project_id, msg, pid_int, model, content, message_id)
        return

    # Load project to determine stage
    project: Project | None = None
    try:
        from alfred.db import get_engine
        from sqlmodel import Session
        engine = get_engine()
        with Session(engine) as s:
            project = s.get(Project, pid_int)
    except Exception:
        pass

    if project is None:
        await _handle_chat_plain(project_id, msg, pid_int, model, content, message_id)
        return

    stage = project.current_stage

    if stage == ProjectStage.hypothesis:
        await _handle_chat_hypothesis(project_id, pid_int, model, content, message_id, project)
    elif stage == ProjectStage.setup:
        asyncio.create_task(
            _handle_chat_setup(project_id, pid_int, model, content, message_id, project)
        )
    else:
        await _handle_chat_plain(project_id, msg, pid_int, model, content, message_id)


# ---------------------------------------------------------------------------
# Hypothesis stage — clarifying questions + [START_RESEARCH] trigger
# ---------------------------------------------------------------------------

async def _handle_chat_hypothesis(
    project_id: str,
    pid_int: int,
    model: str,
    content: str,
    message_id: str,
    project: "Project",
) -> None:
    from alfred.agents.base import Role, make_client
    from alfred.db import get_engine
    from alfred.memory.context import build_memory_block
    from alfred.models.db_models import Message, MessageKind, MessageRole
    from alfred.services.ollama import OllamaError
    from alfred.state_machine.machine import get_machine
    from sqlmodel import Session

    # If hypothesis agent is already running — ignore new chat
    if get_machine(pid_int) is not None:
        await manager.send(project_id, "log", {
            "message": "Hypothesis validation is in progress — please wait.",
            "phase": "hypothesis",
        })
        return

    engine = get_engine()
    extra_system = ""
    user_msg_id: int | None = None
    asst_msg_id: int | None = None

    with Session(engine) as session:
        try:
            extra_system = build_memory_block(session, pid_int)
        except Exception:
            pass

        # Persist user message
        try:
            user_row = Message(
                project_id=pid_int, role=MessageRole.user,
                content=content, kind=MessageKind.chat, metadata_json="{}",
            )
            session.add(user_row)
            session.commit()
            session.refresh(user_row)
            user_msg_id = user_row.id
        except Exception as exc:
            logger.warning("Could not persist user message: %s", exc)

        # Create assistant placeholder
        try:
            asst_row = Message(
                project_id=pid_int, role=MessageRole.assistant,
                content="", kind=MessageKind.chat, metadata_json="{}",
            )
            session.add(asst_row)
            session.commit()
            session.refresh(asst_row)
            asst_msg_id = asst_row.id
        except Exception as exc:
            logger.warning("Could not create assistant placeholder: %s", exc)

    # Emit msg_start
    if asst_msg_id is not None:
        await manager.send(project_id, "msg_start", {
            "msg_id": asst_msg_id, "message_id": message_id,
        })

    # Stream clarifying-questions response
    client = make_client(model, project_id=project_id, ws_manager=manager)
    full_response = ""
    try:
        full_response = await client.chat(
            Role.RESEARCHER,
            [{"role": "user", "content": content}],
            message_id=message_id,
            extra_system=(extra_system + "\n\n" + _HYPOTHESIS_PREAMBLE).strip(),
        )
    except OllamaError as exc:
        full_response = f"⚠️ {exc}"
        await manager.broadcast_error(project_id, human_message=str(exc),
            remediation="Ensure Ollama is running and the selected model is pulled.")
    except Exception as exc:
        full_response = f"⚠️ Error: {exc}"
        await manager.broadcast_error(project_id, human_message=str(exc))

    # Check for [START_RESEARCH] trigger
    trigger_research = _START_RESEARCH_MARKER in full_response
    display_response = full_response.replace(_START_RESEARCH_MARKER, "").strip()

    # Persist assistant response (without marker)
    if asst_msg_id is not None:
        try:
            engine = get_engine()
            with Session(engine) as session:
                row = session.get(Message, asst_msg_id)
                if row:
                    row.content = display_response
                    row.metadata_json = json.dumps({
                        "model": model,
                        "memory_tokens": len(extra_system) // 4,
                        "memory_block": extra_system,
                        "tool_calls": [],
                    })
                    session.add(row)
                    session.commit()
        except Exception as exc:
            logger.warning("Could not update assistant message: %s", exc)

    await manager.send(project_id, "done", {
        "msg_id": asst_msg_id, "summary": "Response complete"
    })

    if trigger_research:
        # Launch hypothesis agent in background
        # Gather hypothesis text from the conversation
        hypothesis_text = await _extract_hypothesis_text(pid_int, content)
        asyncio.create_task(
            _run_hypothesis_agent(
                project_id, pid_int, model, hypothesis_text, project.auto_approve
            )
        )


async def _extract_hypothesis_text(pid_int: int, latest_user_msg: str) -> str:
    """
    Summarise the hypothesis from recent conversation history.
    Fallback: return the latest user message as-is.
    """
    try:
        from alfred.db import get_engine
        from alfred.models.db_models import Message, MessageRole
        from sqlmodel import Session, select
        engine = get_engine()
        with Session(engine) as session:
            rows = session.exec(
                select(Message)
                .where(Message.project_id == pid_int)
                .order_by(Message.created_at.asc())  # type: ignore[arg-type]
            ).all()
        user_msgs = [r.content for r in rows if r.role == MessageRole.user]
        if user_msgs:
            return " | ".join(user_msgs[-3:])  # last 3 user messages as context
    except Exception:
        pass
    return latest_user_msg


async def _run_hypothesis_agent(
    project_id_str: str,
    pid_int: int,
    model: str,
    hypothesis: str,
    auto_approve: bool,
    feedback: str = "",
) -> None:
    """Background task: instantiate HypothesisAgent and run it."""
    try:
        from alfred.agents.hypothesis import HypothesisAgent
        from alfred.db import get_engine
        from sqlmodel import Session

        engine = get_engine()
        with Session(engine) as session:
            agent = HypothesisAgent(
                project_id=pid_int,
                model=model,
                ws_manager=manager,
                db_session=session,
                auto_approve=auto_approve,
            )
            await agent.run(hypothesis, feedback=feedback)
    except Exception as exc:
        logger.exception("Hypothesis agent background task failed: %s", exc)
        await manager.broadcast_error(
            project_id_str,
            human_message=f"Hypothesis validation failed: {exc}",
            remediation="Check the backend terminal.",
        )


# ---------------------------------------------------------------------------
# Setup stage — multi-turn collaborative dialogue
# ---------------------------------------------------------------------------

async def _handle_chat_setup(
    project_id: str,
    pid_int: int,
    model: str,
    content: str,
    message_id: str,
    project: "Project",
) -> None:
    from alfred.agents.setup import SetupAgent
    from alfred.db import get_engine
    from alfred.memory.context import build_memory_block
    from alfred.models.db_models import Message, MessageKind, MessageRole
    from alfred.services.ollama import OllamaError
    from alfred.state_machine.machine import get_machine
    from sqlmodel import Session

    engine = get_engine()
    extra_system = ""
    user_msg_id: int | None = None
    asst_msg_id: int | None = None

    with Session(engine) as session:
        try:
            extra_system = build_memory_block(session, pid_int)
        except Exception:
            pass

        # Persist user message
        try:
            user_row = Message(
                project_id=pid_int, role=MessageRole.user,
                content=content, kind=MessageKind.chat, metadata_json="{}",
            )
            session.add(user_row)
            session.commit()
            session.refresh(user_row)
            user_msg_id = user_row.id
        except Exception as exc:
            logger.warning("Could not persist user message: %s", exc)

        # Create assistant placeholder
        try:
            asst_row = Message(
                project_id=pid_int, role=MessageRole.assistant,
                content="", kind=MessageKind.chat, metadata_json="{}",
            )
            session.add(asst_row)
            session.commit()
            session.refresh(asst_row)
            asst_msg_id = asst_row.id
        except Exception as exc:
            logger.warning("Could not create assistant placeholder: %s", exc)

    # Emit msg_start before streaming
    if asst_msg_id is not None:
        await manager.send(project_id, "msg_start", {
            "msg_id": asst_msg_id, "message_id": message_id,
        })

    # Run setup agent turn
    full_response = ""
    plan: dict | None = None

    try:
        with Session(engine) as session:
            agent = SetupAgent(
                project_id=pid_int,
                model=model,
                ws_manager=manager,
                db_session=session,
                auto_approve=project.auto_approve,
            )
            full_response, plan = await agent.generate_turn(
                user_message=content,
                asst_msg_id=asst_msg_id,
                memory_block=extra_system,
            )
    except OllamaError as exc:
        full_response = f"⚠️ {exc}"
        await manager.broadcast_error(project_id, human_message=str(exc),
            remediation="Ensure Ollama is running and the selected model is pulled.")
    except Exception as exc:
        full_response = f"⚠️ Error in setup agent: {exc}"
        logger.exception("Setup agent error: %s", exc)
        await manager.broadcast_error(project_id, human_message=str(exc))

    # Persist assistant response
    if asst_msg_id is not None:
        try:
            with Session(engine) as session:
                row = session.get(Message, asst_msg_id)
                if row:
                    row.content = full_response
                    row.metadata_json = json.dumps({
                        "model": model,
                        "memory_tokens": len(extra_system) // 4,
                        "memory_block": extra_system,
                        "tool_calls": [],
                    })
                    session.add(row)
                    session.commit()
        except Exception as exc:
            logger.warning("Could not update assistant message: %s", exc)

    await manager.send(project_id, "done", {
        "msg_id": asst_msg_id, "summary": "Response complete"
    })

    # If agent produced a plan, create the approval gate
    if plan is not None:
        await _create_setup_approval(
            project_id, pid_int, model, plan, project.auto_approve
        )


async def _create_setup_approval(
    project_id: str,
    pid_int: int,
    model: str,
    plan: dict,
    auto_approve: bool,
) -> None:
    """Transition state machine to AWAITING_APPROVAL with the setup plan."""
    from alfred.agents.setup import SetupAgent
    from alfred.db import get_engine
    from alfred.state_machine.machine import S2Sub
    from sqlmodel import Session

    try:
        engine = get_engine()
        with Session(engine) as session:
            agent = SetupAgent(
                project_id=pid_int,
                model=model,
                ws_manager=manager,
                db_session=session,
                auto_approve=auto_approve,
            )
            machine = agent._get_machine()

            # Attach experiment ID to plan
            exp_id = agent._get_or_create_experiment()
            plan["experiment_id"] = exp_id

            response = await machine.transition(
                S2Sub.AWAITING_APPROVAL,
                plan=plan,
                label="Awaiting plan approval",
            )

            if response and response.approved:
                final_plan = response.edited_plan if response.edited_plan else plan
                await agent.handle_approved_plan(final_plan)
            elif response:
                # Rejected — user continues chatting to refine the plan
                await machine.transition(
                    S2Sub.REFINING, label="Refining plan based on feedback"
                )
                await manager.send(project_id, "log", {
                    "message": f"Plan rejected. Feedback: {response.feedback or '(none)'}",
                    "phase": "setup",
                })
    except Exception as exc:
        logger.exception("Setup approval flow failed: %s", exc)
        await manager.broadcast_error(
            project_id,
            human_message=f"Setup approval failed: {exc}",
            remediation="Check the backend terminal.",
        )


# ---------------------------------------------------------------------------
# Plain chat (unchanged from Stage 4)
# ---------------------------------------------------------------------------

async def _handle_chat_plain(
    project_id: str,
    msg_or_none: dict | None,
    pid_int: int | None,
    model: str,
    content: str,
    message_id: str,
) -> None:
    from alfred.agents.base import Role, make_client
    from alfred.services.ollama import OllamaError

    extra_system = ""
    user_msg_id: int | None = None
    asst_msg_id: int | None = None

    if pid_int is not None:
        try:
            from alfred.db import get_engine
            from alfred.memory.context import build_memory_block
            from alfred.models.db_models import Message, MessageKind, MessageRole
            from sqlmodel import Session

            engine = get_engine()
            with Session(engine) as session:
                try:
                    extra_system = build_memory_block(session, pid_int)
                except Exception:
                    pass

                try:
                    user_row = Message(
                        project_id=pid_int, role=MessageRole.user,
                        content=content, kind=MessageKind.chat, metadata_json="{}",
                    )
                    session.add(user_row)
                    session.commit()
                    session.refresh(user_row)
                    user_msg_id = user_row.id
                except Exception as exc:
                    logger.warning("Could not persist user message: %s", exc)

                try:
                    asst_row = Message(
                        project_id=pid_int, role=MessageRole.assistant,
                        content="", kind=MessageKind.chat, metadata_json="{}",
                    )
                    session.add(asst_row)
                    session.commit()
                    session.refresh(asst_row)
                    asst_msg_id = asst_row.id
                except Exception as exc:
                    logger.warning("Could not create assistant placeholder: %s", exc)
        except Exception as exc:
            logger.debug("DB operations skipped in plain chat handler: %s", exc)

    if asst_msg_id is not None:
        await manager.send(project_id, "msg_start", {
            "msg_id": asst_msg_id, "message_id": message_id,
        })

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
        await manager.broadcast_error(project_id, human_message=str(exc),
            remediation="Make sure Ollama is running and the selected model is pulled.")
    except Exception as exc:
        full_response = f"⚠️ Error: {exc}"
        await manager.broadcast_error(project_id, human_message=str(exc))

    if asst_msg_id is not None and pid_int is not None:
        try:
            from alfred.db import get_engine
            from alfred.models.db_models import Message
            from sqlmodel import Session
            engine = get_engine()
            with Session(engine) as session:
                row = session.get(Message, asst_msg_id)
                if row:
                    row.content = full_response
                    row.metadata_json = json.dumps({
                        "raw_prompt": extra_system[:500] if extra_system else "",
                        "memory_block": extra_system,
                        "memory_tokens": len(extra_system) // 4,
                        "model": model,
                        "tool_calls": [],
                    })
                    session.add(row)
                    session.commit()
        except Exception as exc:
            logger.warning("Could not update assistant message: %s", exc)

    await manager.send(project_id, "done", {
        "msg_id": asst_msg_id, "summary": "Response complete",
    })


# ---------------------------------------------------------------------------
# Demo pipeline (UNCHANGED from Stage 4)
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
            "landscape": "Contrastive learning and sparse attention are both well-studied areas. "
                         "Their specific combination for the proposed task has limited prior coverage.",
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
