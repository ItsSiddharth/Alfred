"""
Stage 0 smoke tests.

Verify:
- DB initialises with all C6 tables
- Config setup/load round-trip works
- Path-jail enforces boundaries
- FastAPI app starts and health endpoint responds
- WS manager envelope format is correct
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_temp_workspace() -> Path:
    tmp = tempfile.mkdtemp(prefix="alfred_test_")
    return Path(tmp)


# ---------------------------------------------------------------------------
# Path jail
# ---------------------------------------------------------------------------


class TestPathJail:
    def test_allowed_path_returns_resolved(self) -> None:
        from alfred.utils.paths import assert_within

        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "subdir" / "file.txt"
            target.parent.mkdir(parents=True)
            target.touch()
            result = assert_within(root, target)
            assert result.is_absolute()

    def test_escape_via_dotdot_raises(self) -> None:
        from alfred.utils.paths import PathJailError, assert_within

        with tempfile.TemporaryDirectory() as root:
            evil = Path(root) / ".." / "escape"
            with pytest.raises(PathJailError):
                assert_within(root, evil)

    def test_completely_different_path_raises(self) -> None:
        from alfred.utils.paths import PathJailError, assert_within

        with tempfile.TemporaryDirectory() as root:
            with pytest.raises(PathJailError):
                assert_within(root, "/tmp/outside")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_needs_setup_when_no_config(self, tmp_path: Path) -> None:
        """load_config() returns None when config file is absent."""
        import alfred.config as cfg_module

        # Temporarily redirect config file location.
        original = cfg_module._CONFIG_FILE
        cfg_module._CONFIG_FILE = tmp_path / "alfred_config.json"
        cfg_module._config = None
        try:
            result = cfg_module.load_config()
            assert result is None
        finally:
            cfg_module._CONFIG_FILE = original
            cfg_module._config = None

    def test_setup_workspace_creates_dirs(self, tmp_path: Path) -> None:
        import alfred.config as cfg_module

        workspace = tmp_path / "my_workspace"
        original = cfg_module._CONFIG_FILE
        cfg_module._CONFIG_FILE = tmp_path / "alfred_config.json"
        cfg_module._config = None
        try:
            cfg = cfg_module.setup_workspace(str(workspace))
            assert (workspace / "logs").is_dir()
            assert (workspace / "projects").is_dir()
            assert (workspace / "datasets").is_dir()
            assert cfg_module._CONFIG_FILE.exists()
        finally:
            cfg_module._CONFIG_FILE = original
            cfg_module._config = None

    def test_rejects_system_paths(self, tmp_path: Path) -> None:
        import alfred.config as cfg_module

        original = cfg_module._CONFIG_FILE
        cfg_module._CONFIG_FILE = tmp_path / "alfred_config.json"
        cfg_module._config = None
        try:
            with pytest.raises(ValueError, match="system directory"):
                cfg_module.setup_workspace("/etc/alfred")
        finally:
            cfg_module._CONFIG_FILE = original
            cfg_module._config = None


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


class TestDatabase:
    def test_all_c6_tables_created(self, tmp_path: Path) -> None:
        """All nine canonical tables from C6 must exist after init_db()."""
        import sqlite3

        import alfred.config as cfg_module
        import alfred.db as db_module

        # Reset the engine singleton so we can point at a temp DB.
        db_module._engine = None

        db_path = tmp_path / "test.sqlite"
        db_module.init_db(db_path)

        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        expected = {
            "project",
            "message",
            "memoryitem",
            "experiment",
            "metric",
            "runlog",
            "toolcall",
            "datasetcacheentry",
            "score",
        }
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"

        # Reset engine so other tests get a fresh one.
        db_module._engine = None

    def test_add_column_if_missing(self, tmp_path: Path) -> None:
        """Migration shim adds a new column without destroying existing data."""
        import sqlite3

        import alfred.db as db_module

        db_module._engine = None
        db_path = tmp_path / "migration_test.sqlite"
        db_module.init_db(db_path)
        db_module._engine = None

        # Add a column that doesn't exist.
        db_module.add_column_if_missing("project", "test_col", "TEXT DEFAULT ''", db_path)

        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("PRAGMA table_info(project)")
        cols = {row[1] for row in cursor.fetchall()}
        conn.close()
        assert "test_col" in cols

        # Calling again must not raise.
        db_module.add_column_if_missing("project", "test_col", "TEXT DEFAULT ''", db_path)


# ---------------------------------------------------------------------------
# WebSocket envelope
# ---------------------------------------------------------------------------


class TestWSManager:
    def test_make_envelope_shape(self) -> None:
        from alfred.ws import make_envelope

        raw = make_envelope("progress", {"stage": 1, "substage": "sweeping"})
        envelope = json.loads(raw)
        assert envelope["type"] == "progress"
        assert "ts" in envelope
        assert envelope["payload"]["stage"] == 1

    def test_make_envelope_canonical_types(self) -> None:
        """All canonical event types from C7 must be acceptable (no rename check)."""
        from alfred.ws import make_envelope

        canonical = [
            "token", "progress", "log", "plan", "approval_request",
            "tool_call", "result", "error", "state_change", "plot", "done",
        ]
        for event_type in canonical:
            raw = make_envelope(event_type, {})
            assert json.loads(raw)["type"] == event_type


# ---------------------------------------------------------------------------
# FastAPI app — health and config endpoints
# ---------------------------------------------------------------------------


class TestApp:
    @pytest.fixture()
    def client(self, tmp_path: Path):
        """
        Spin up a TestClient with a fresh temp config so each test is isolated.
        """
        import alfred.config as cfg_module
        import alfred.db as db_module

        # Point config + DB at temp paths.
        original_cfg_file = cfg_module._CONFIG_FILE
        cfg_module._CONFIG_FILE = tmp_path / "alfred_config.json"
        cfg_module._config = None
        db_module._engine = None

        from alfred.main import app

        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

        cfg_module._CONFIG_FILE = original_cfg_file
        cfg_module._config = None
        db_module._engine = None

    def test_health_endpoint(self, client: TestClient) -> None:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "configured" in body

    def test_config_status_needs_setup(self, client: TestClient) -> None:
        resp = client.get("/api/config/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "needs_setup"

    def test_config_setup_then_status(self, client: TestClient, tmp_path: Path) -> None:
        workspace = str(tmp_path / "ws")
        resp = client.post("/api/config/setup", json={"workspace_path": workspace})
        assert resp.status_code == 200
        assert resp.json()["status"] == "configured"

        resp2 = client.get("/api/config/status")
        assert resp2.json()["status"] == "configured"

    def test_projects_crud(self, client: TestClient, tmp_path: Path) -> None:
        # Setup workspace first so the DB is initialised.
        client.post("/api/config/setup", json={"workspace_path": str(tmp_path / "ws")})

        import alfred.db as db_module
        import alfred.config as cfg_module
        cfg = cfg_module.get_config()
        db_module.init_db(cfg.db_path)

        # Create
        resp = client.post("/api/projects/", json={"name": "Test Project"})
        assert resp.status_code == 201
        project = resp.json()
        assert project["name"] == "Test Project"
        pid = project["id"]

        # List
        resp = client.get("/api/projects/")
        assert resp.status_code == 200
        assert any(p["id"] == pid for p in resp.json())

        # Get
        resp = client.get(f"/api/projects/{pid}")
        assert resp.status_code == 200

        # Patch
        resp = client.patch(f"/api/projects/{pid}", json={"auto_approve": True})
        assert resp.status_code == 200
        assert resp.json()["auto_approve"] is True

        # Delete
        resp = client.delete(f"/api/projects/{pid}")
        assert resp.status_code == 204