"""
tests/test_stage2.py — Stage 2 smoke tests.
"""
from __future__ import annotations
import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    import alfred.config as cfg_mod
    import alfred.db as db_mod

    with tempfile.TemporaryDirectory() as tmpdir:
        cfg_mod._CONFIG_FILE = Path(tmpdir) / "alfred_config.json"
        cfg_mod._config = None
        db_mod._engine = None

        cfg = cfg_mod.setup_workspace(tmpdir)
        from alfred.db import init_db
        init_db(cfg.db_path)

        from alfred.main import app
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

        cfg_mod._config = None
        db_mod._engine = None


def _make_project(client: TestClient, name: str = "Test Project") -> dict:
    r = client.post("/api/projects/", json={"name": name})
    assert r.status_code == 201
    return r.json()


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict]] = []

    async def send(self, project_id: str, event_type: str, payload: dict) -> None:
        self.sent.append((project_id, event_type, payload))

    async def broadcast_progress(self, project_id, stage, substage, label, current, total, status="running") -> None:
        self.sent.append((project_id, "progress", {
            "stage": stage, "substage": substage, "label": label,
            "current": current, "total": total, "status": status,
        }))

    async def broadcast_done(self, project_id, summary="") -> None:
        self.sent.append((project_id, "done", {"summary": summary}))

    async def broadcast_error(self, project_id, human_message, remediation="") -> None:
        self.sent.append((project_id, "error", {"message": human_message}))


def _make_machine(project_id: int, session, auto_approve: bool = False):
    from alfred.state_machine.machine import ExperimentStateMachine
    ws = FakeWS()
    return ExperimentStateMachine(
        project_id=project_id,
        ws_manager=ws,
        db_session=session,
        auto_approve=auto_approve,
    ), ws


class TestStateMachineEnums:
    def test_s1_substages_exist(self):
        from alfred.state_machine.machine import S1Sub
        required = {
            "GENERATING_QUERIES", "SWEEPING_SOURCES", "SNOWBALLING",
            "WEB_SWEEP", "ANALYZING", "SCORING", "AWAITING_APPROVAL", "DONE",
        }
        actual = {m.name for m in S1Sub}
        assert required.issubset(actual)

    def test_s2_substages_exist(self):
        from alfred.state_machine.machine import S2Sub
        required = {"PROPOSING", "REFINING", "AWAITING_APPROVAL", "FINALIZED"}
        assert required.issubset({m.name for m in S2Sub})

    def test_s3_substages_exist(self):
        from alfred.state_machine.machine import S3Sub
        required = {
            "WRITING_CODE", "AWAITING_APPROVAL", "SETTING_UP_DATA",
            "PREPROCESSING", "TRAINING", "EVALUATING", "INTERPRETING",
            "DIAGNOSING_ERROR", "FIXING", "AWAITING_NEXT",
        }
        assert required.issubset({m.name for m in S3Sub})

    def test_stage_enum_values(self):
        from alfred.state_machine.machine import Stage
        assert Stage.HYPOTHESIS.value == 1
        assert Stage.SETUP.value == 2
        assert Stage.RUN.value == 3


class TestStateMachineTransition:
    async def test_transition_emits_state_change(self, client: TestClient):
        import alfred.db as db_mod
        from sqlmodel import Session
        from alfred.state_machine.machine import S1Sub

        project = _make_project(client)
        pid = project["id"]

        engine = db_mod.get_engine()
        with Session(engine) as session:
            machine, ws = _make_machine(pid, session)
            await machine.transition(S1Sub.SWEEPING_SOURCES, label="test sweep")

        types = [e[1] for e in ws.sent]
        assert "state_change" in types
        assert "progress" in types

    async def test_transition_persists_to_db(self, client: TestClient):
        import alfred.db as db_mod
        from sqlmodel import Session
        from alfred.models.db_models import Project
        from alfred.state_machine.machine import S1Sub

        project = _make_project(client)
        pid = project["id"]

        engine = db_mod.get_engine()
        with Session(engine) as session:
            machine, _ = _make_machine(pid, session)
            await machine.transition(S1Sub.SNOWBALLING)
            p = session.get(Project, pid)
            assert p is not None
            snap = json.loads(p.status)
            assert snap["substage"] == "snowballing"

    async def test_snapshot_restore(self, client: TestClient):
        import alfred.db as db_mod
        from sqlmodel import Session
        from alfred.state_machine.machine import S1Sub, ExperimentStateMachine

        project = _make_project(client)
        pid = project["id"]

        engine = db_mod.get_engine()
        ws = FakeWS()

        with Session(engine) as session:
            m1 = ExperimentStateMachine(pid, ws, session)
            await m1.transition(S1Sub.ANALYZING)

        with Session(engine) as session:
            m2 = ExperimentStateMachine(pid, ws, session)
            restored = await m2.restore()
            assert restored is True
            assert m2.current_substage == S1Sub.ANALYZING

    async def test_report_progress_payload(self, client: TestClient):
        import alfred.db as db_mod
        from sqlmodel import Session
        from alfred.state_machine.machine import S1Sub

        project = _make_project(client)
        pid = project["id"]

        engine = db_mod.get_engine()
        with Session(engine) as session:
            machine, ws = _make_machine(pid, session)
            await machine.transition(S1Sub.SWEEPING_SOURCES)
            ws.sent.clear()
            await machine.report_progress(5, 30, "Papers found: 5")

        assert len(ws.sent) == 1
        _, event_type, payload = ws.sent[0]
        assert event_type == "progress"
        assert payload["current"] == 5
        assert payload["total"] == 30
        assert payload["label"] == "Papers found: 5"

    async def test_auto_approve_does_not_block(self, client: TestClient):
        import alfred.db as db_mod
        from sqlmodel import Session
        from alfred.state_machine.machine import S1Sub

        project = _make_project(client)
        pid = project["id"]

        engine = db_mod.get_engine()
        with Session(engine) as session:
            machine, ws = _make_machine(pid, session, auto_approve=True)
            response = await asyncio.wait_for(
                machine.transition(S1Sub.AWAITING_APPROVAL, plan={"score": 72}),
                timeout=2.0,
            )
        assert response is not None
        assert response.approved is True

    async def test_set_auto_approve_persists(self, client: TestClient):
        import alfred.db as db_mod
        from sqlmodel import Session
        from alfred.models.db_models import Project

        project = _make_project(client)
        pid = project["id"]

        engine = db_mod.get_engine()
        with Session(engine) as session:
            machine, _ = _make_machine(pid, session, auto_approve=False)
            machine.set_auto_approve(True)
            p = session.get(Project, pid)
            snap = json.loads(p.status)
            assert snap["auto_approve"] is True


class TestMachineRegistry:
    def test_register_get_unregister(self):
        from alfred.state_machine.machine import (
            get_machine, register_machine, unregister_machine, ExperimentStateMachine
        )
        ws = FakeWS()
        m = ExperimentStateMachine(999, ws, MagicMock())
        register_machine(999, m)
        assert get_machine(999) is m
        unregister_machine(999)
        assert get_machine(999) is None


class TestMessagesRouter:
    def test_create_and_list_messages(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        r = client.post(
            f"/api/projects/{pid}/messages/",
            json={"role": "user", "content": "Hello ALFRED", "kind": "chat"},
        )
        assert r.status_code == 201
        msg = r.json()
        assert msg["content"] == "Hello ALFRED"
        assert msg["role"] == "user"
        assert msg["kind"] == "chat"
        assert msg["project_id"] == pid

        r2 = client.post(
            f"/api/projects/{pid}/messages/",
            json={"role": "assistant", "content": "Hello!", "kind": "chat"},
        )
        assert r2.status_code == 201

        r3 = client.get(f"/api/projects/{pid}/messages/")
        assert r3.status_code == 200
        msgs = r3.json()
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    def test_list_messages_wrong_project(self, client: TestClient):
        r = client.get("/api/projects/99999/messages/")
        assert r.status_code == 404

    def test_create_message_invalid_metadata(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]
        r = client.post(
            f"/api/projects/{pid}/messages/",
            json={"role": "user", "content": "hi", "metadata_json": "not-json{"},
        )
        assert r.status_code == 400

    def test_get_single_message(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]
        r = client.post(
            f"/api/projects/{pid}/messages/",
            json={"role": "user", "content": "test"},
        )
        mid = r.json()["id"]
        r2 = client.get(f"/api/projects/{pid}/messages/{mid}")
        assert r2.status_code == 200
        assert r2.json()["id"] == mid

    def test_message_kinds(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]
        for kind in ["chat", "plan", "result", "error", "thinking"]:
            r = client.post(
                f"/api/projects/{pid}/messages/",
                json={"role": "assistant", "content": f"kind={kind}", "kind": kind},
            )
            assert r.status_code == 201
            assert r.json()["kind"] == kind


class TestExperimentsRouter:
    def test_create_and_list_experiments(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        r = client.post(
            f"/api/projects/{pid}/experiments",
            json={"iteration": 1, "seed": 42, "plan_json": '{"objective": "test"}'},
        )
        assert r.status_code == 201
        exp = r.json()
        assert exp["iteration"] == 1
        assert exp["seed"] == 42
        assert exp["status"] == "planned"
        eid = exp["id"]

        r2 = client.get(f"/api/projects/{pid}/experiments")
        assert r2.status_code == 200
        exps = r2.json()
        assert any(e["id"] == eid for e in exps)

    def test_update_experiment(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]
        r = client.post(f"/api/projects/{pid}/experiments", json={"iteration": 1})
        eid = r.json()["id"]

        r2 = client.patch(
            f"/api/projects/{pid}/experiments/{eid}",
            json={"status": "running", "git_commit": "abc123"},
        )
        assert r2.status_code == 200
        assert r2.json()["status"] == "running"
        assert r2.json()["git_commit"] == "abc123"

    def test_approve_without_machine_returns_409(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]
        r = client.post(f"/api/projects/{pid}/experiments", json={"iteration": 1})
        eid = r.json()["id"]

        r2 = client.post(f"/api/projects/{pid}/experiments/{eid}/approve", json={})
        assert r2.status_code == 409

    def test_reject_without_machine_returns_409(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]
        r = client.post(f"/api/projects/{pid}/experiments", json={"iteration": 1})
        eid = r.json()["id"]

        r2 = client.post(
            f"/api/projects/{pid}/experiments/{eid}/reject",
            json={"feedback": "needs more detail"},
        )
        assert r2.status_code == 409

    def test_experiment_not_found(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]
        r = client.get(f"/api/projects/{pid}/experiments/99999")
        assert r.status_code == 404


class TestAutoApproveToggle:
    def test_toggle_auto_approve(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]
        assert project["auto_approve"] is False

        r = client.post(
            f"/api/projects/{pid}/auto_approve",
            json={"auto_approve": True},
        )
        assert r.status_code == 200
        assert r.json()["auto_approve"] is True

        r2 = client.get(f"/api/projects/{pid}")
        assert r2.json()["auto_approve"] is True

        client.post(f"/api/projects/{pid}/auto_approve", json={"auto_approve": False})
        r3 = client.get(f"/api/projects/{pid}")
        assert r3.json()["auto_approve"] is False

    def test_toggle_nonexistent_project(self, client: TestClient):
        r = client.post(
            "/api/projects/99999/auto_approve",
            json={"auto_approve": True},
        )
        assert r.status_code == 404