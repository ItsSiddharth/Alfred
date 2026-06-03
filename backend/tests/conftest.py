"""
Shared pytest fixtures for ALFRED tests.
Uses a per-test file-based SQLite DB so each test run is isolated.

KEY DESIGN: all fixtures that need DB access share the SAME engine instance
so that direct-session writes and HTTP client reads see the same data.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from sqlmodel import Session, SQLModel, create_engine

# Make sure backend is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

# ── Session-scoped temp workspace ─────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def _temp_workspace(tmp_path_factory):
    ws = tmp_path_factory.mktemp("alfred_workspace")
    for sub in ("logs", "projects", "datasets"):
        (ws / sub).mkdir()
    import alfred.config as cfg_mod
    cfg_mod._workspace_path = ws
    yield ws


# ── Per-test engine (file-based so TestClient + session share it) ─────────────

@pytest.fixture(scope="function")
def engine(tmp_path):
    db_file = str(tmp_path / "test.sqlite")
    from alfred.models import models as _  # noqa: F401 — register all tables
    eng = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)

    # Patch the global engine so get_session() uses this engine
    import alfred.db as db_mod
    db_mod._engine = eng
    yield eng
    SQLModel.metadata.drop_all(eng)


@pytest.fixture(scope="function")
def session(engine):
    with Session(engine) as s:
        yield s


# ── FastAPI test app ──────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def app(engine):
    from alfred.tools.base import ToolRegistry
    ToolRegistry._instance = None  # reset for isolation
    from alfred.main import app as _app
    return _app


@pytest.fixture(scope="function")
def client(app, engine):
    """TestClient sharing the same engine as `session` fixture."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest_asyncio.fixture(scope="function")
async def async_client(app, engine):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ── Data factories — create via API so TestClient sees the same rows ──────────

@pytest.fixture
def project(client):
    """Create a project via the API — visible to both client and session."""
    resp = client.post("/api/projects/", json={"name": "Test Project"})
    assert resp.status_code == 201, resp.text

    class _Project:
        pass

    p = _Project()
    data = resp.json()
    p.id = data["id"]
    p.name = data["name"]
    p.auto_approve = data["auto_approve"]
    return p


@pytest.fixture
def messages(client, project):
    """Create 3 messages via API in known chronological order."""
    created = []
    for role, content in [
        ("user",      "Hello ALFRED"),
        ("assistant", "Hello! How can I help?"),
        ("user",      "Run the demo"),
    ]:
        resp = client.post(
            f"/api/projects/{project.id}/messages/",
            json={"role": role, "content": content, "kind": "chat"},
        )
        assert resp.status_code == 201, resp.text

        class _Msg:
            pass

        m = _Msg()
        d = resp.json()
        m.id = d["id"]
        m.role = d["role"]
        m.content = d["content"]
        m.project_id = d["project_id"]
        created.append(m)
    return created