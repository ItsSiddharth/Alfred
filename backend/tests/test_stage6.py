"""
Stage 6 tests — Experiment Setup agent.

Tests:
  - SetupAgent.generate_turn streams response and returns (text, None) for early turns
  - SetupAgent._check_plan_ready returns None when LLM says not ready
  - SetupAgent._check_plan_ready returns plan dict when LLM says ready
  - SetupAgent.handle_approved_plan updates Experiment.plan_json and advances stage
  - _get_or_create_experiment reuses existing row
  - State machine S2Sub transitions sequence
  - MIN_TURNS_BEFORE_PROPOSAL guard (no check before turn 3)
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# _strip_fences utility
# ---------------------------------------------------------------------------

from alfred.agents.setup import _strip_fences


def test_strip_fences_no_fences():
    assert _strip_fences('{"key": "value"}') == '{"key": "value"}'


def test_strip_fences_with_json_fence():
    text = '```json\n{"key": "value"}\n```'
    assert _strip_fences(text) == '{"key": "value"}'


def test_strip_fences_with_plain_fence():
    text = '```\n{"key": "value"}\n```'
    assert _strip_fences(text) == '{"key": "value"}'


# ---------------------------------------------------------------------------
# _check_plan_ready
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_plan_ready_returns_none_when_not_ready(tmp_path):
    """Returns None when LLM says not ready."""
    from alfred.db import init_db
    import alfred.db as db_module
    from alfred.models.db_models import Project
    from sqlmodel import Session

    db_path = tmp_path / "setup_test1.db"
    db_module._engine = None
    engine = init_db(str(db_path))

    with Session(engine) as session:
        proj = Project(name="Setup1", workspace_path=str(tmp_path))
        session.add(proj)
        session.commit()
        session.refresh(proj)
        pid = proj.id

    from alfred.agents.setup import SetupAgent

    ws_mock = MagicMock()
    ws_mock.send = AsyncMock()

    with Session(engine) as session:
        agent = SetupAgent(
            project_id=pid, model="test", ws_manager=ws_mock, db_session=session
        )
        # Mock chat_raw to return "not ready"
        agent.client.chat_raw = AsyncMock(return_value='{"ready": false}')

        messages = [{"role": "user", "content": "I want to test a new model"}]
        plan = await agent._check_plan_ready(messages)
        assert plan is None

    db_module._engine = None


@pytest.mark.asyncio
async def test_check_plan_ready_returns_plan_when_ready(tmp_path):
    """Returns plan dict when LLM says ready."""
    from alfred.db import init_db
    import alfred.db as db_module
    from alfred.models.db_models import Project
    from sqlmodel import Session

    db_path = tmp_path / "setup_test2.db"
    db_module._engine = None
    engine = init_db(str(db_path))

    with Session(engine) as session:
        proj = Project(name="Setup2", workspace_path=str(tmp_path))
        session.add(proj)
        session.commit()
        session.refresh(proj)
        pid = proj.id

    from alfred.agents.setup import SetupAgent

    ws_mock = MagicMock()
    ws_mock.send = AsyncMock()

    ready_response = json.dumps({
        "ready": True,
        "objective": "Train a ResNet on CIFAR-10",
        "toy_dataset": "CIFAR-10 subset (1000 samples)",
        "scale_dataset": "Full CIFAR-10",
        "architectures": ["ResNet-18"],
        "baselines": ["Vanilla CNN"],
        "metrics": ["accuracy", "loss"],
        "success_criteria": ">85% validation accuracy",
        "first_iteration_spec": "5 epochs, LR=0.001",
        "alfreds_suggestions": ["Try data augmentation"],
    })

    with Session(engine) as session:
        agent = SetupAgent(
            project_id=pid, model="test", ws_manager=ws_mock, db_session=session
        )
        agent.client.chat_raw = AsyncMock(return_value=ready_response)

        messages = [{"role": "user", "content": "I want to train ResNet on CIFAR-10"}]
        plan = await agent._check_plan_ready(messages)

    assert plan is not None
    assert plan["objective"] == "Train a ResNet on CIFAR-10"
    assert "ResNet-18" in plan["architectures"]
    assert "ready" not in plan  # key should be removed

    db_module._engine = None


# ---------------------------------------------------------------------------
# _get_or_create_experiment
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_or_create_experiment_creates_on_first_call(tmp_path):
    from alfred.db import init_db
    import alfred.db as db_module
    from alfred.models.db_models import Experiment, Project
    from sqlmodel import Session, select

    db_path = tmp_path / "setup_test3.db"
    db_module._engine = None
    engine = init_db(str(db_path))

    with Session(engine) as session:
        proj = Project(name="Setup3", workspace_path=str(tmp_path))
        session.add(proj)
        session.commit()
        session.refresh(proj)
        pid = proj.id

    from alfred.agents.setup import SetupAgent

    ws_mock = MagicMock()

    with Session(engine) as session:
        agent = SetupAgent(project_id=pid, model="t", ws_manager=ws_mock, db_session=session)
        eid1 = agent._get_or_create_experiment()
        eid2 = agent._get_or_create_experiment()  # should return same ID

    assert eid1 == eid2

    with Session(engine) as session:
        exps = session.exec(
            select(Experiment).where(Experiment.project_id == pid)
        ).all()
        assert len(exps) == 1

    db_module._engine = None


# ---------------------------------------------------------------------------
# handle_approved_plan
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_approved_plan_updates_experiment(tmp_path):
    from alfred.db import init_db
    import alfred.db as db_module
    from alfred.models.db_models import Experiment, Project, ProjectStage
    from sqlmodel import Session

    db_path = tmp_path / "setup_test4.db"
    db_module._engine = None
    engine = init_db(str(db_path))

    with Session(engine) as session:
        proj = Project(name="Setup4", workspace_path=str(tmp_path),
                       current_stage=ProjectStage.setup)
        session.add(proj)
        session.commit()
        session.refresh(proj)
        pid = proj.id

    from alfred.agents.setup import SetupAgent
    from alfred.state_machine.machine import (
        ExperimentStateMachine, S2Sub, Stage,
        register_machine, unregister_machine,
    )

    ws_mock = MagicMock()
    ws_mock.send = AsyncMock()
    ws_mock.broadcast_progress = AsyncMock()
    ws_mock.broadcast_done = AsyncMock()
    ws_mock.broadcast_error = AsyncMock()

    with Session(engine) as session:
        # Register a machine in setup stage
        machine = ExperimentStateMachine(
            project_id=pid, ws_manager=ws_mock, db_session=session, auto_approve=True
        )
        machine.current_stage = Stage.SETUP
        machine.current_substage = S2Sub.AWAITING_APPROVAL
        register_machine(pid, machine)

        agent = SetupAgent(project_id=pid, model="t", ws_manager=ws_mock, db_session=session)
        # Create experiment first
        exp_id = agent._get_or_create_experiment()

        plan = {
            "objective": "Train ResNet",
            "toy_dataset": "CIFAR-10 small",
            "experiment_id": exp_id,
        }
        await agent.handle_approved_plan(plan)

    # Verify experiment updated
    with Session(engine) as session:
        exp = session.get(Experiment, exp_id)
        assert exp is not None
        stored_plan = json.loads(exp.plan_json)
        assert stored_plan["objective"] == "Train ResNet"

    unregister_machine(pid)
    db_module._engine = None


# ---------------------------------------------------------------------------
# S2Sub state machine transitions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_s2sub_transition_sequence():
    from alfred.state_machine.machine import (
        ExperimentStateMachine, S2Sub, Stage
    )

    ws = MagicMock()
    ws.send = AsyncMock()
    ws.broadcast_progress = AsyncMock()
    ws.broadcast_done = AsyncMock()
    ws.broadcast_error = AsyncMock()
    db = MagicMock()
    db.get = MagicMock(return_value=None)
    db.add = MagicMock()
    db.commit = MagicMock()

    machine = ExperimentStateMachine(
        project_id=888, ws_manager=ws, db_session=db, auto_approve=True
    )
    machine.current_stage = Stage.SETUP

    for sub in [S2Sub.PROPOSING, S2Sub.REFINING]:
        await machine.transition(sub, label=f"test {sub.value}")
        assert machine.current_substage == sub

    # Auto-approve at AWAITING_APPROVAL
    response = await machine.transition(S2Sub.AWAITING_APPROVAL, plan={"x": 1})
    assert response is not None
    assert response.approved is True

    await machine.transition(S2Sub.FINALIZED)
    assert machine.current_substage == S2Sub.FINALIZED


# ---------------------------------------------------------------------------
# Min turns guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_min_turns_guard_no_plan_check_before_turn_3(tmp_path):
    """
    generate_turn should not call _check_plan_ready on turns < 3.
    """
    from alfred.db import init_db
    import alfred.db as db_module
    from alfred.models.db_models import Project, ProjectStage
    from sqlmodel import Session
    from alfred.agents.setup import MIN_TURNS_BEFORE_PROPOSAL

    db_path = tmp_path / "setup_test5.db"
    db_module._engine = None
    engine = init_db(str(db_path))

    with Session(engine) as session:
        proj = Project(name="Setup5", workspace_path=str(tmp_path),
                       current_stage=ProjectStage.setup)
        session.add(proj)
        session.commit()
        session.refresh(proj)
        pid = proj.id

    from alfred.agents.setup import SetupAgent
    from alfred.state_machine.machine import register_machine, ExperimentStateMachine, Stage, S2Sub

    ws = MagicMock()
    ws.send = AsyncMock()
    ws.broadcast_progress = AsyncMock()
    ws.broadcast_done = AsyncMock()
    ws.broadcast_error = AsyncMock()

    with Session(engine) as session:
        m = ExperimentStateMachine(project_id=pid, ws_manager=ws, db_session=session, auto_approve=True)
        m.current_stage = Stage.SETUP
        m.current_substage = S2Sub.PROPOSING
        register_machine(pid, m)

        agent = SetupAgent(project_id=pid, model="t", ws_manager=ws, db_session=session)

        # Mock chat to return a response without triggering plan check
        agent.client.chat = AsyncMock(return_value="Tell me more about your dataset.")
        check_mock = AsyncMock(return_value=None)
        agent._check_plan_ready = check_mock

        # Simulate 0 existing assistant messages — turn count < MIN_TURNS_BEFORE_PROPOSAL
        _, plan = await agent.generate_turn("I want to build a classifier", memory_block="")

    # check_plan_ready should NOT have been called (0 turns < MIN_TURNS)
    assert check_mock.call_count == 0
    assert plan is None

    db_module._engine = None
