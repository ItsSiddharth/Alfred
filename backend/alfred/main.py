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
from alfred.api.dashboard_router import router as dashboard_router       # ← Stage 8
from alfred.api.experiments_router import router as experiments_router
from alfred.api.hypothesis_router import router as hypothesis_router   # ← Stage 5
from alfred.api.runner_router import router as runner_router             # ← Stage 7
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

# Per-project background task registry — used by the stop handler to cancel generation
_active_tasks: dict[str, "asyncio.Task[None]"] = {}

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

_HYPOTHESIS_PREAMBLE_QUICK = """\

You are ALFRED in Quick Mode — the user wants fast iteration with minimal back-and-forth.

Your job:
1. Acknowledge the hypothesis briefly (1-2 sentences) and confirm what you understood.
2. Immediately emit [START_RESEARCH] on its own line to trigger the literature review.
3. Do NOT ask clarifying questions unless the hypothesis is so vague that research is impossible.

Quick Mode: assume the user knows what they want. Start research now.
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
    # Start WebSocket heartbeat (keeps connections alive, evicts zombies)
    asyncio.create_task(manager.start_heartbeat(interval=30.0))
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
    version="0.8.0",
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
app.include_router(runner_router)             # ← Stage 7
app.include_router(dashboard_router)          # ← Stage 8


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
            elif msg_type == "stop":
                task = _active_tasks.pop(project_id, None)
                if task and not task.done():
                    task.cancel()
                    # The task's CancelledError handler emits "stopped"
                else:
                    await manager.send(project_id, "stopped", {"summary": "Nothing running"})
            elif msg_type == "pong":
                pass  # heartbeat reply — connection is alive
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
        task = asyncio.create_task(
            _handle_chat_setup(project_id, pid_int, model, content, message_id, project)
        )
        _active_tasks[project_id] = task
        task.add_done_callback(lambda _: _active_tasks.pop(project_id, None))
    elif stage == ProjectStage.run:
        task = asyncio.create_task(
            _handle_chat_run(project_id, pid_int, model, content, message_id, project)
        )
        _active_tasks[project_id] = task
        task.add_done_callback(lambda _: _active_tasks.pop(project_id, None))
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

    # Emit msg_start + progress so ProgressStrip shows active and Stop appears
    if asst_msg_id is not None:
        await manager.send(project_id, "msg_start", {
            "msg_id": asst_msg_id, "message_id": message_id,
        })
    await manager.broadcast_progress(
        project_id, stage=1, substage="generating",
        label="Generating response…", current=0, total=0, status="running",
    )

    # Stream clarifying-questions response (quick mode skips clarification)
    hypothesis_preamble = (
        _HYPOTHESIS_PREAMBLE_QUICK if project.auto_approve else _HYPOTHESIS_PREAMBLE
    )
    client = make_client(model, project_id=project_id, ws_manager=manager)
    full_response = ""
    try:
        full_response = await client.chat(
            Role.RESEARCHER,
            [{"role": "user", "content": content}],
            message_id=message_id,
            extra_system=(extra_system + "\n\n" + hypothesis_preamble).strip(),
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
        # Signal immediately so the progress strip shows activity before the
        # research agent's first state_change arrives
        await manager.broadcast_progress(
            project_id, stage=1, substage="validating",
            label="Starting hypothesis validation…", current=0, total=0, status="running",
        )
        research_task = asyncio.create_task(
            _run_hypothesis_agent(
                project_id, pid_int, model, hypothesis_text, project.auto_approve
            )
        )
        # Register so the Stop button can cancel this task while research runs
        _active_tasks[project_id] = research_task
        research_task.add_done_callback(lambda t: _active_tasks.pop(project_id, None))


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
    except asyncio.CancelledError:
        await manager.send(project_id_str, "stopped", {"summary": "Stopped"})
        await manager.broadcast_progress(
            project_id_str, stage=1, substage="idle",
            label="Stopped", current=0, total=0, status="idle",
        )
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

        # If agent produced a plan, show a brief "preparing…" indicator during
        # the DB operations inside _create_setup_approval before approval_request fires
        if plan is not None:
            await manager.broadcast_progress(
                project_id, stage=2, substage="proposing",
                label="Preparing plan card…", current=0, total=0, status="running",
            )
            await _create_setup_approval(
                project_id, pid_int, model, plan, project.auto_approve
            )

    except asyncio.CancelledError:
        await manager.send(project_id, "stopped", {
            "msg_id": asst_msg_id, "summary": "Stopped",
        })
    except OllamaError as exc:
        full_response = f"⚠️ {exc}"
        await manager.broadcast_error(project_id, human_message=str(exc),
            remediation="Ensure Ollama is running and the selected model is pulled.")
        await manager.send(project_id, "done", {"msg_id": asst_msg_id})
    except Exception as exc:
        full_response = f"⚠️ Error in setup agent: {exc}"
        logger.exception("Setup agent error: %s", exc)
        await manager.broadcast_error(project_id, human_message=str(exc))
        await manager.send(project_id, "done", {"msg_id": asst_msg_id})


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
    from alfred.memory.context import build_memory_block
    from alfred.models.db_models import Message, MessageKind, MessageRole
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
            machine._db = session  # keep machine's DB ref in sync with the live session

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
                # Rejected — immediately refine the plan and open a new approval card
                feedback = response.feedback or ""
                await machine.transition(
                    S2Sub.REFINING, label="Refining plan based on feedback"
                )

                # Emit the feedback as a Show Work log entry (visible when Show Work is on)
                await manager.send(project_id, "log", {
                    "message": f"[Refinement] User feedback: {feedback or '(no feedback provided)'}",
                    "phase": "setup",
                })

                # Persist the rejection feedback as a user message so history carries it
                feedback_content = (
                    f"Revise the plan based on this feedback: {feedback}"
                    if feedback else "Please revise the plan."
                )
                feedback_msg = Message(
                    project_id=pid_int, role=MessageRole.user,
                    content=feedback_content, kind=MessageKind.chat, metadata_json="{}",
                )
                session.add(feedback_msg)
                session.commit()

                # Create an assistant placeholder so the refinement streams into chat
                asst_row = Message(
                    project_id=pid_int, role=MessageRole.assistant,
                    content="", kind=MessageKind.chat, metadata_json="{}",
                )
                session.add(asst_row)
                session.commit()
                session.refresh(asst_row)
                asst_msg_id: int = asst_row.id

                await manager.send(project_id, "msg_start", {
                    "msg_id": asst_msg_id, "message_id": str(asst_msg_id),
                })

                memory_block = ""
                try:
                    memory_block = build_memory_block(session, pid_int)
                except Exception:
                    pass

                # Stream the LLM's refinement — tokens appear in chat
                full_response, new_plan = await agent.generate_turn(
                    user_message=feedback_content,
                    asst_msg_id=asst_msg_id,
                    memory_block=memory_block,
                )

                # Persist the refinement response
                row = session.get(Message, asst_msg_id)
                if row:
                    row.content = full_response
                    row.metadata_json = json.dumps({
                        "model": model, "memory_tokens": 0,
                        "memory_block": "", "tool_calls": [],
                    })
                    session.add(row)
                    session.commit()

                await manager.send(project_id, "done", {
                    "msg_id": asst_msg_id, "summary": "Plan refined"
                })

                # If a refined plan emerged, open a fresh approval gate
                if new_plan is not None:
                    await _create_setup_approval(project_id, pid_int, model, new_plan, auto_approve)
                else:
                    await manager.send(project_id, "log", {
                        "message": (
                            "Plan needs more information — keep chatting and "
                            "ALFRED will propose a revised plan when ready."
                        ),
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
# Run stage — Stage-3 agent: Run & Iterate (Stage 7)
# ---------------------------------------------------------------------------

# Keywords that signal the user wants to execute code / start a run
_RUN_INTENT_KEYWORDS = frozenset({
    "run the experiment", "run experiment", "run it", "run this",
    "start the experiment", "start experiment", "execute", "begin training",
    "kick off", "launch", "start training", "let's run", "lets run",
    "go ahead and run", "fire it up", "run the code", "run code",
    "generate the code", "generate code", "write the code", "write code",
    "start the run", "begin the run",
    # "build" variants
    "build the experiment", "build it", "build this", "build the code",
    "let's build", "lets build", "start building", "begin building",
    # "proceed" / "go" variants
    "let's go", "lets go", "let's proceed", "lets proceed", "go ahead",
    "proceed", "let's start", "lets start",
    # "give it a go" / "do it" variants
    "give it a go", "give it a shot", "do it", "do the run", "do the experiment",
    "just run it", "just run", "run now", "execute now", "run please",
    "run the model", "train the model", "fit the model",
})

# Phrases that signal the user thinks the experiment did NOT actually run
# (code was displayed but not executed, or the run silently failed)
_RERUN_INTENT_KEYWORDS = frozenset({
    "i don't think that ran",
    "i dont think that ran",
    "i don't think it ran",
    "i dont think it ran",
    "it didn't run",
    "it didnt run",
    "that didn't run",
    "that didnt run",
    "nothing happened",
    "nothing ran",
    "nothing executed",
    "the code was just printed",
    "code was just printed",
    "just printed the code",
    "just printed code",
    "code wasn't run",
    "code wasnt run",
    "it wasn't executed",
    "it wasnt executed",
    "did it run",
    "did that run",
    "did the experiment run",
    "was it executed",
    "was it run",
    "try again",
    "try running again",
    "run it again",
    "run again",
    "re-run",
    "rerun",
    "restart the experiment",
    "i think it failed",
    "seems like it failed",
    "it failed silently",
    "something went wrong with the run",
    "the experiment didn't start",
    "experiment didn't start",
    "not sure it ran",
    "don't think it executed",
})


def _is_run_intent(content: str) -> bool:
    """True if the message is most likely asking to execute an experiment."""
    lower = content.lower().strip()
    for phrase in _RUN_INTENT_KEYWORDS:
        if phrase in lower:
            return True
    # Short imperative-style messages: "run" / "go" / "build" / "train" etc.
    words = lower.split()
    if len(words) <= 3 and words and words[0] in {
        "run", "go", "train", "execute", "start", "begin", "fire", "build", "proceed"
    }:
        return True
    return False


def _is_rerun_intent(content: str) -> bool:
    """True if the user seems to think the experiment didn't actually execute."""
    lower = content.lower().strip()
    for phrase in _RERUN_INTENT_KEYWORDS:
        if phrase in lower:
            return True
    return False


async def _handle_chat_run(
    project_id: str,
    pid_int: int,
    model: str,
    content: str,
    message_id: str,
    project: "Project",
) -> None:
    """
    Route chat in the 'run' stage:
      - RUN intent  → RunnerAgent pipeline
      - DISCUSS intent → free-form research discussion with full experiment context
    """
    from alfred.db import get_engine
    from alfred.models.db_models import Message, MessageKind, MessageRole
    from alfred.state_machine.machine import get_machine, S3Sub
    from sqlmodel import Session

    engine = get_engine()

    # Guard: project must be bound before running (but NOT before discussing)
    machine = get_machine(pid_int)
    experiment_running = (
        machine is not None
        and getattr(machine, "current_substage", None) not in (S3Sub.AWAITING_NEXT, None)
    )

    run_intent = _is_run_intent(content)
    rerun_intent = _is_rerun_intent(content)

    # Treat "I don't think that ran" etc. as a run intent with a note to ALFRED
    if rerun_intent and not run_intent:
        run_intent = True
        content = (
            content + "\n\n[ALFRED NOTE: The user believes the experiment did not "
            "actually execute — the code may have been displayed but not run. "
            "Please acknowledge this clearly, verify the situation, and run the experiment now.]"
        )

    if run_intent and (not project.conda_env or not project.experiment_folder):
        await manager.send(project_id, "log", {
            "message": (
                "⚠️  Project not bound. "
                "Set the conda environment and experiment folder in the sidebar before running."
            ),
            "phase": "run",
        })
        await manager.broadcast_error(
            project_id,
            human_message="Project not bound to a conda env / experiment folder.",
            remediation="Open the sidebar, fill in Conda env and Experiment folder, then save.",
        )
        return

    # Always persist user message (needed for history)
    with Session(engine) as session:
        try:
            user_row = Message(
                project_id=pid_int, role=MessageRole.user,
                content=content, kind=MessageKind.chat, metadata_json="{}",
            )
            session.add(user_row)
            session.commit()
        except Exception as exc:
            logger.warning("Could not persist user message (run stage): %s", exc)

    # Route: DISCUSS if experiment running or no run intent
    if experiment_running or not run_intent:
        # Discussion needs its own assistant placeholder
        asst_msg_id: int | None = None
        with Session(engine) as session:
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
                logger.warning("Could not create assistant placeholder (discuss): %s", exc)

        if asst_msg_id is not None:
            await manager.send(project_id, "msg_start", {
                "msg_id": asst_msg_id, "message_id": message_id,
            })

        hint = "An experiment is currently running — ALFRED will discuss while it proceeds." if experiment_running else ""
        await _research_discussion(
            project_id, pid_int, model, content, message_id, asst_msg_id, project,
            hint=hint,
        )
        return

    # RUN intent — delegate to RunnerAgent (no assistant placeholder; runner manages its own messages)
    try:
        from alfred.agents.runner import RunnerAgent  # noqa: PLC0415
        from alfred.db import get_engine  # noqa: PLC0415
        from sqlmodel import Session  # noqa: PLC0415

        with Session(get_engine()) as session:
            agent = RunnerAgent(
                project_id=pid_int,
                model=model,
                ws_manager=manager,
                db_session=session,
                auto_approve=project.auto_approve,
            )
            await agent.run(content, asst_msg_id=None)
    except asyncio.CancelledError:
        await manager.send(project_id, "stopped", {"summary": "Stopped"})
        return
    except ImportError:
        placeholder = (
            "✓ Project is bound. "
            f"conda env: `{project.conda_env}` | "
            f"folder: `{project.experiment_folder}`\n\n"
            "The experiment runner (Stage 7.2) is not yet built."
        )
        await manager.broadcast_error(
            project_id,
            human_message=placeholder,
            remediation="",
        )
    except Exception as exc:
        logger.exception("RunnerAgent failed: %s", exc)
        await manager.broadcast_error(
            project_id,
            human_message=f"Runner error: {exc}",
            remediation="Check the backend terminal for the full traceback.",
        )


# ---------------------------------------------------------------------------
# Free-form research discussion — collaborative brainstorming in run stage
# ---------------------------------------------------------------------------

async def _research_discussion(
    project_id: str,
    pid_int: int,
    model: str,
    content: str,
    message_id: str,
    asst_msg_id: int | None,
    project: "Project",
    hint: str = "",
) -> None:
    """
    Respond as a senior research collaborator with full context about what
    has been run, what the results are, and what the current plan is.

    Context injected into the system prompt:
      - Project name + stage
      - Last completed experiment: plan summary, final metrics, git commit
      - ALFRED's last interpretation message (if any)
      - Memory block
      - Recent conversation history (last 12 messages)
    """
    from alfred.agents.base import Role, make_client
    from alfred.db import get_engine
    from alfred.memory.context import build_memory_block
    from alfred.models.db_models import (
        Experiment, ExperimentStatus, Message, MessageKind,
        MessageRole, Metric,
    )
    from alfred.services.ollama import OllamaError
    from sqlmodel import Session, select

    engine = get_engine()
    extra_system = ""
    history: list[dict] = []
    full_response = ""

    with Session(engine) as session:
        # Memory block
        try:
            extra_system = build_memory_block(session, pid_int)
        except Exception:
            pass

        # Build experiment context string
        exp_context = _build_experiment_context(session, pid_int)

        # Load recent conversation history (last 12 messages, excluding the
        # current turn which isn't committed yet)
        try:
            rows = session.exec(
                select(Message)
                .where(Message.project_id == pid_int)
                .where(Message.kind == MessageKind.chat)
                .order_by(Message.created_at.desc())  # type: ignore[arg-type]
            ).all()
            for row in reversed(rows[-13:-1]):  # last 12 before current
                history.append({
                    "role": row.role.value,
                    "content": row.content[:800] if row.content else "",
                })
        except Exception as exc:
            logger.debug("Could not load history for discussion: %s", exc)

    # Build the research collaborator system prompt
    hint_section = f"\n\nNote: {hint}" if hint else ""

    not_bound = not project.conda_env or not project.experiment_folder
    binding_section = ""
    if not_bound:
        binding_section = (
            "\n\nCRITICAL — PROJECT NOT BOUND: This project has no conda environment "
            "or experiment folder configured. You MUST NOT fabricate, estimate, or speculate "
            "about actual experiment results, metrics, or training behaviour. "
            "If the user asks to run, build, or execute an experiment in any way, "
            "respond with: 'Before running, please set up the conda environment and "
            "experiment folder in the sidebar (click the project name to expand the "
            "binding panel). I cannot run experiments until the environment is configured.' "
            "Do not suggest workarounds or hypothetical outcomes."
        )

    research_system = f"""\
You are ALFRED — a proactive local AI research agent with full context about this project's \
experiments and results. You are the researcher's intelligent partner, not just a responder.

PROJECT: {project.name}
STAGE: Run & Iterate{hint_section}{binding_section}

{exp_context}

Your behaviour:
- Lead with the most relevant insight or recommendation first (bottom line up front).
- Be decisive: when you have enough context, make a specific recommendation rather than listing options.
- Proactively flag issues (overfit, unstable training, poor baseline) without being asked.
- When the user asks "what should I do next?", give ONE concrete answer with reasoning.
- Discuss with scientific rigour but keep responses tight — no padding.
- If the user asks to run an experiment, acknowledge and route it (they say "run" or "execute").

To run an experiment: user says "run" / "run the experiment" / "execute" — \
you do not need to prompt them for this.
"""

    full_system = (research_system + "\n\n" + extra_system).strip()

    # Append the current message to history
    history.append({"role": "user", "content": content})

    await manager.broadcast_progress(
        project_id, stage=3, substage="discussing",
        label="Generating response…", current=0, total=0, status="running",
    )

    client = make_client(model, project_id=project_id, ws_manager=manager)
    try:
        full_response = await client.chat(
            Role.COLLABORATOR,
            history,
            message_id=message_id,
            extra_system=full_system,
        )
    except OllamaError as exc:
        full_response = f"⚠️ {exc}"
        await manager.broadcast_error(
            project_id, human_message=str(exc),
            remediation="Ensure Ollama is running and the selected model is pulled."
        )
    except Exception as exc:
        full_response = f"⚠️ {exc}"
        await manager.broadcast_error(project_id, human_message=str(exc))

    # Persist the response
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
                        "mode": "discussion",
                    })
                    session.add(row)
                    session.commit()
        except Exception as exc:
            logger.warning("Could not persist discussion response: %s", exc)

    await manager.send(project_id, "done", {
        "msg_id": asst_msg_id, "summary": "Discussion complete",
    })


def _build_experiment_context(session: "Session", pid_int: int) -> str:
    """
    Build a compact natural-language summary of the most recent completed
    experiment for injection into the discussion system prompt.
    """
    from alfred.models.db_models import Experiment, ExperimentStatus, Message, MessageKind, MessageRole, Metric
    from sqlmodel import select

    lines: list[str] = []

    try:
        # Most recent completed experiment
        exp = session.exec(
            select(Experiment)
            .where(Experiment.project_id == pid_int)
            .where(Experiment.status == ExperimentStatus.done)
            .order_by(Experiment.iteration.desc())  # type: ignore[arg-type]
        ).first()

        if exp is None:
            # Check if there's a planned experiment (not yet run)
            planned = session.exec(
                select(Experiment)
                .where(Experiment.project_id == pid_int)
                .order_by(Experiment.iteration.desc())  # type: ignore[arg-type]
            ).first()
            if planned:
                lines.append(f"CURRENT PLAN (Iteration {planned.iteration}, not yet run):")
                try:
                    plan = json.loads(planned.plan_json)
                    for k, v in plan.items():
                        if k not in ("experiment_id", "kind") and v:
                            lines.append(f"  {k}: {str(v)[:200]}")
                except Exception:
                    pass
            else:
                lines.append("No experiments have been run yet.")
            return "\n".join(lines)

        lines.append(f"LAST COMPLETED EXPERIMENT (Iteration {exp.iteration}):")
        try:
            plan = json.loads(exp.plan_json)
            for k in ("objective", "architecture", "dataset", "metrics"):
                v = plan.get(k)
                if v:
                    lines.append(f"  {k.title()}: {str(v)[:200]}")
        except Exception:
            pass

        if exp.runtime_seconds:
            lines.append(f"  Runtime: {exp.runtime_seconds:.1f}s")
        if exp.git_commit:
            lines.append(f"  Git commit: {exp.git_commit[:7]}")

        # Final metric values
        metrics = session.exec(
            select(Metric)
            .where(Metric.experiment_id == exp.id)
            .order_by(Metric.name.asc(), Metric.step.desc())  # type: ignore[arg-type]
        ).all()

        seen_names: set[str] = set()
        metric_lines: list[str] = []
        for m in metrics:
            if m.name not in seen_names:
                seen_names.add(m.name)
                metric_lines.append(f"  {m.name}: {m.value:.4f} (step {m.step})")
        if metric_lines:
            lines.append("  Final metrics:")
            lines.extend(metric_lines[:8])

        # ALFRED's last interpretation (most recent assistant message in run stage)
        last_interp = session.exec(
            select(Message)
            .where(Message.project_id == pid_int)
            .where(Message.role == MessageRole.assistant)
            .where(Message.kind == MessageKind.chat)
            .order_by(Message.created_at.desc())  # type: ignore[arg-type]
        ).first()
        if last_interp and last_interp.content:
            snippet = last_interp.content[:600].replace("\n", " ")
            lines.append(f"\nALFRED'S LAST ANALYSIS: {snippet}{'…' if len(last_interp.content) > 600 else ''}")

        # All iterations summary (if multiple)
        all_done = session.exec(
            select(Experiment)
            .where(Experiment.project_id == pid_int)
            .where(Experiment.status == ExperimentStatus.done)
            .order_by(Experiment.iteration.asc())  # type: ignore[arg-type]
        ).all()
        if len(all_done) > 1:
            lines.append(f"\nALL COMPLETED ITERATIONS: {len(all_done)} total (iterations {', '.join(str(e.iteration) for e in all_done)})")

    except Exception as exc:
        logger.debug("Could not build experiment context: %s", exc)

    return "\n".join(lines) if lines else "No experiment context available yet."


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
