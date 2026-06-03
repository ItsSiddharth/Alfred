"""
tests/test_stage3.py — Stage 3 smoke tests.

Covers:
  - MemoryItem CRUD (create, list, update, deactivate, delete)
  - Stale flag toggled on write operations
  - estimate_tokens arithmetic
  - get_compiled returns None before first compile
  - compile_memory fallback when Ollama unavailable
  - build_memory_block returns correct blocks for compiled and raw paths
  - should_auto_compile threshold logic
  - REST endpoints: create, list, update, delete, compiled, compile
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


def _make_project(client: TestClient, name: str = "MemTest") -> dict:
    r = client.post("/api/projects/", json={"name": name})
    assert r.status_code == 201
    return r.json()


def _get_session():
    import alfred.db as db_mod
    from sqlmodel import Session
    return Session(db_mod.get_engine())


# ---------------------------------------------------------------------------
# Unit tests — store.py
# ---------------------------------------------------------------------------


class TestMemoryStore:
    def test_create_and_list(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        with _get_session() as session:
            from alfred.memory.store import create_item, list_items
            from alfred.models.db_models import MemoryType

            item = create_item(
                session,
                project_id=pid,
                memory_type=MemoryType.fact,
                content="The sky is blue",
                tags="test",
            )
            assert item.id is not None
            assert item.content == "The sky is blue"
            assert item.tags == "test"
            assert item.active is True

            items = list_items(session, project_id=pid)
            assert any(i.id == item.id for i in items)

    def test_create_global_item(self, client: TestClient):
        with _get_session() as session:
            from alfred.memory.store import create_item, list_items
            from alfred.models.db_models import MemoryType

            item = create_item(
                session,
                project_id=None,
                memory_type=MemoryType.preference,
                content="Always use short variable names",
            )
            assert item.project_id is None

            # Should appear in global list
            global_items = list_items(session, project_id=None, include_global=True)
            assert any(i.id == item.id for i in global_items)

    def test_update_item(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        with _get_session() as session:
            from alfred.memory.store import create_item, update_item
            from alfred.models.db_models import MemoryType

            item = create_item(
                session, project_id=pid,
                memory_type=MemoryType.mistake, content="Original error"
            )
            updated = update_item(session, item.id, content="Fixed error", tags="fixed")
            assert updated is not None
            assert updated.content == "Fixed error"
            assert updated.tags == "fixed"

    def test_deactivate_item(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        with _get_session() as session:
            from alfred.memory.store import create_item, list_items, update_item
            from alfred.models.db_models import MemoryType

            item = create_item(
                session, project_id=pid,
                memory_type=MemoryType.fact, content="Temporary fact"
            )
            update_item(session, item.id, active=False)

            # Should not appear in active-only list
            active = list_items(session, project_id=pid, active_only=True)
            assert not any(i.id == item.id for i in active)

            # Should appear when active_only=False
            all_items = list_items(session, project_id=pid, active_only=False)
            assert any(i.id == item.id for i in all_items)

    def test_delete_item(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        with _get_session() as session:
            from alfred.memory.store import create_item, delete_item, get_item
            from alfred.models.db_models import MemoryType

            item = create_item(
                session, project_id=pid,
                memory_type=MemoryType.dataset_ref, content="s3://bucket/data.csv"
            )
            iid = item.id
            assert delete_item(session, iid) is True
            assert get_item(session, iid) is None

    def test_stale_flag_set_on_write(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        with _get_session() as session:
            from alfred.memory.store import _check_stale, _clear_stale, create_item
            from alfred.models.db_models import MemoryType

            # Clear stale flag first
            _clear_stale(session, pid)
            assert _check_stale(session, pid) is False

            # Create an item — should mark stale
            create_item(
                session, project_id=pid,
                memory_type=MemoryType.fact, content="New fact"
            )
            assert _check_stale(session, pid) is True

    def test_capture_hooks(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        with _get_session() as session:
            from alfred.memory.store import (
                capture_dataset_ref,
                capture_fact,
                capture_mistake,
                capture_preference,
            )
            from alfred.models.db_models import MemoryType

            m = capture_mistake(session, pid, "Model diverged at epoch 5")
            assert m.type == MemoryType.mistake

            p = capture_preference(session, pid, "Use AdamW over Adam")
            assert p.type == MemoryType.preference

            f = capture_fact(session, pid, "Dataset has 50k samples")
            assert f.type == MemoryType.fact

            d = capture_dataset_ref(session, pid, "s3://data/train.csv hash=abc123")
            assert d.type == MemoryType.dataset_ref

    def test_type_filter(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        with _get_session() as session:
            from alfred.memory.store import capture_fact, capture_mistake, list_items
            from alfred.models.db_models import MemoryType

            capture_fact(session, pid, "Fact one")
            capture_mistake(session, pid, "Mistake one")

            facts = list_items(session, project_id=pid, memory_type=MemoryType.fact)
            mistakes = list_items(session, project_id=pid, memory_type=MemoryType.mistake)

            assert all(i.type == MemoryType.fact for i in facts)
            assert all(i.type == MemoryType.mistake for i in mistakes)


# ---------------------------------------------------------------------------
# Unit tests — compress.py
# ---------------------------------------------------------------------------


class TestMemoryCompress:
    def test_estimate_tokens_approximate(self):
        from alfred.memory.compress import estimate_tokens

        text = "a" * 400  # 400 chars → ~100 tokens
        tok = estimate_tokens(text)
        assert 90 <= tok <= 110

    def test_estimate_tokens_minimum(self):
        from alfred.memory.compress import estimate_tokens
        assert estimate_tokens("") >= 1

    def test_get_compiled_none_before_compile(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        with _get_session() as session:
            from alfred.memory.compress import get_compiled
            result = get_compiled(session, pid)
            assert result is None

    @pytest.mark.asyncio
    async def test_compile_fallback_no_ollama(self, client: TestClient):
        """compile_memory falls back gracefully when Ollama is unavailable."""
        project = _make_project(client)
        pid = project["id"]

        with _get_session() as session:
            from alfred.memory.store import capture_fact, capture_mistake
            capture_fact(session, pid, "Learning rate 1e-3 worked well")
            capture_mistake(session, pid, "Forgot to normalise inputs")

        with _get_session() as session:
            from alfred.memory.compress import compile_memory
            result = await compile_memory(
                session, pid, model="nonexistent-model:test"
            )
            # Fallback should produce non-empty markdown
            assert len(result.markdown) > 10
            assert result.item_count == 2
            assert result.is_stale is False

    @pytest.mark.asyncio
    async def test_compile_empty_project(self, client: TestClient):
        """compile_memory on a project with no items returns a placeholder."""
        project = _make_project(client)
        pid = project["id"]

        with _get_session() as session:
            from alfred.memory.compress import compile_memory
            result = await compile_memory(session, pid, model="any-model")
            assert "No memory items" in result.markdown
            assert result.item_count == 0

    @pytest.mark.asyncio
    async def test_compile_persists_compiled_doc(self, client: TestClient):
        """After compile, get_compiled returns a non-None result."""
        project = _make_project(client)
        pid = project["id"]

        with _get_session() as session:
            from alfred.memory.store import capture_fact
            capture_fact(session, pid, "Test fact for persistence")

        with _get_session() as session:
            from alfred.memory.compress import compile_memory, get_compiled
            await compile_memory(session, pid, model="nonexistent-model:test")

        with _get_session() as session:
            from alfred.memory.compress import get_compiled
            compiled = get_compiled(session, pid)
            assert compiled is not None
            assert len(compiled.markdown) > 0
            assert compiled.is_stale is False

    def test_should_auto_compile_threshold(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        with _get_session() as session:
            from alfred.memory.compress import AUTO_COMPILE_THRESHOLD, should_auto_compile
            from alfred.memory.store import capture_fact

            assert should_auto_compile(session, pid) is False

            # Add enough items to hit the threshold
            for i in range(AUTO_COMPILE_THRESHOLD):
                capture_fact(session, pid, f"Fact number {i}")

            assert should_auto_compile(session, pid) is True


# ---------------------------------------------------------------------------
# Unit tests — context.py
# ---------------------------------------------------------------------------


class TestMemoryContext:
    @pytest.mark.asyncio
    async def test_build_memory_block_empty(self, client: TestClient):
        """Empty project returns empty string."""
        project = _make_project(client)
        pid = project["id"]

        with _get_session() as session:
            from alfred.memory.context import build_memory_block
            block = build_memory_block(session, pid)
            assert block == ""

    @pytest.mark.asyncio
    async def test_build_memory_block_raw_fallback(self, client: TestClient):
        """With no compiled doc, raw items are returned inline."""
        project = _make_project(client)
        pid = project["id"]

        with _get_session() as session:
            from alfred.memory.store import capture_fact, capture_mistake
            capture_fact(session, pid, "Batch size 32 works")
            capture_mistake(session, pid, "Overfit on tiny dataset")

        with _get_session() as session:
            from alfred.memory.context import build_memory_block
            block = build_memory_block(session, pid)
            assert "Batch size 32 works" in block
            assert "Overfit on tiny dataset" in block
            assert "ALFRED Memory" in block

    @pytest.mark.asyncio
    async def test_build_memory_block_from_compiled(self, client: TestClient):
        """After compile, block uses compiled doc."""
        project = _make_project(client)
        pid = project["id"]

        with _get_session() as session:
            from alfred.memory.store import capture_fact
            capture_fact(session, pid, "Very specific fact for compiled test")

        with _get_session() as session:
            from alfred.memory.compress import compile_memory
            await compile_memory(session, pid, model="nonexistent-model:test")

        with _get_session() as session:
            from alfred.memory.context import build_memory_block
            block = build_memory_block(session, pid)
            # The compiled doc should be present (even the fallback format)
            assert len(block) > 10
            assert "Memory" in block

    def test_build_memory_block_respects_token_budget(self, client: TestClient):
        """Block stays within the requested token budget (approximate)."""
        project = _make_project(client)
        pid = project["id"]

        with _get_session() as session:
            from alfred.memory.store import capture_fact
            # Create many items so the block would exceed a tiny budget
            for i in range(50):
                capture_fact(session, pid, f"Fact {i}: " + "x" * 80)

        with _get_session() as session:
            from alfred.memory.compress import estimate_tokens
            from alfred.memory.context import build_memory_block
            block = build_memory_block(session, pid, max_tokens=200)
            tok = estimate_tokens(block)
            # Allow 20% slack for header overhead
            assert tok < 280, f"Block too large: {tok} tokens"


# ---------------------------------------------------------------------------
# REST endpoint tests
# ---------------------------------------------------------------------------


class TestMemoryEndpoints:
    def test_create_and_list_items(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        r = client.post(
            f"/api/projects/{pid}/memory/items",
            json={"type": "fact", "content": "LR 1e-3 converges well", "tags": "training"},
        )
        assert r.status_code == 201
        item = r.json()
        assert item["type"] == "fact"
        assert item["content"] == "LR 1e-3 converges well"
        assert item["active"] is True
        iid = item["id"]

        r2 = client.get(f"/api/projects/{pid}/memory/items")
        assert r2.status_code == 200
        items = r2.json()
        assert any(i["id"] == iid for i in items)

    def test_create_item_empty_content_rejected(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        r = client.post(
            f"/api/projects/{pid}/memory/items",
            json={"type": "fact", "content": "   "},
        )
        assert r.status_code == 400

    def test_update_item(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        r = client.post(
            f"/api/projects/{pid}/memory/items",
            json={"type": "mistake", "content": "Wrong activation function"},
        )
        iid = r.json()["id"]

        r2 = client.patch(
            f"/api/projects/{pid}/memory/items/{iid}",
            json={"content": "Used sigmoid instead of ReLU", "tags": "architecture"},
        )
        assert r2.status_code == 200
        assert r2.json()["content"] == "Used sigmoid instead of ReLU"
        assert r2.json()["tags"] == "architecture"

    def test_deactivate_via_patch(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        r = client.post(
            f"/api/projects/{pid}/memory/items",
            json={"type": "preference", "content": "Use float16"},
        )
        iid = r.json()["id"]

        client.patch(f"/api/projects/{pid}/memory/items/{iid}", json={"active": False})

        # Should not appear in default (active_only=True) list
        r2 = client.get(f"/api/projects/{pid}/memory/items")
        assert not any(i["id"] == iid for i in r2.json())

    def test_delete_item(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        r = client.post(
            f"/api/projects/{pid}/memory/items",
            json={"type": "dataset_ref", "content": "s3://bucket/ds.csv"},
        )
        iid = r.json()["id"]

        r2 = client.delete(f"/api/projects/{pid}/memory/items/{iid}")
        assert r2.status_code == 204

        r3 = client.get(f"/api/projects/{pid}/memory/items")
        assert not any(i["id"] == iid for i in r3.json())

    def test_item_wrong_project_returns_404(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        r = client.post(
            f"/api/projects/{pid}/memory/items",
            json={"type": "fact", "content": "Private fact"},
        )
        iid = r.json()["id"]

        # Try to delete from wrong project
        r2 = client.delete(f"/api/projects/99999/memory/items/{iid}")
        assert r2.status_code == 404

    def test_get_compiled_before_compile(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        r = client.get(f"/api/projects/{pid}/memory/compiled")
        assert r.status_code == 200
        data = r.json()
        assert "markdown" in data
        assert "token_estimate" in data
        assert "is_stale" in data
        assert data["is_stale"] is True  # never compiled = stale

    def test_compile_endpoint(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        # Add a few items first
        for i in range(3):
            client.post(
                f"/api/projects/{pid}/memory/items",
                json={"type": "fact", "content": f"Test fact {i}"},
            )

        # Compile — Ollama may not be running in CI so fallback is used
        r = client.post(
            f"/api/projects/{pid}/memory/compile",
            json={"model": "nonexistent-model:test"},
        )
        assert r.status_code == 200
        data = r.json()
        assert "markdown" in data
        assert data["item_count"] == 3
        assert data["is_stale"] is False

    def test_compiled_not_stale_after_compile(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        client.post(
            f"/api/projects/{pid}/memory/items",
            json={"type": "fact", "content": "Relevant fact"},
        )
        client.post(
            f"/api/projects/{pid}/memory/compile",
            json={"model": "nonexistent-model:test"},
        )

        r = client.get(f"/api/projects/{pid}/memory/compiled")
        assert r.json()["is_stale"] is False

    def test_compiled_stale_after_item_added(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        # Compile first
        client.post(
            f"/api/projects/{pid}/memory/items",
            json={"type": "fact", "content": "Initial fact"},
        )
        client.post(
            f"/api/projects/{pid}/memory/compile",
            json={"model": "nonexistent-model:test"},
        )

        # Now add another item — should mark stale
        client.post(
            f"/api/projects/{pid}/memory/items",
            json={"type": "fact", "content": "New fact after compile"},
        )

        r = client.get(f"/api/projects/{pid}/memory/compiled")
        assert r.json()["is_stale"] is True

    def test_global_items_endpoints(self, client: TestClient):
        r = client.post(
            "/api/memory/global/items",
            json={"type": "preference", "content": "Always log loss per step"},
        )
        assert r.status_code == 201
        iid = r.json()["id"]

        r2 = client.get("/api/memory/global/items")
        assert r2.status_code == 200
        assert any(i["id"] == iid for i in r2.json())

    def test_type_filter_query_param(self, client: TestClient):
        project = _make_project(client)
        pid = project["id"]

        client.post(f"/api/projects/{pid}/memory/items", json={"type": "fact", "content": "A fact"})
        client.post(f"/api/projects/{pid}/memory/items", json={"type": "mistake", "content": "A mistake"})

        r = client.get(f"/api/projects/{pid}/memory/items?type=fact")
        assert r.status_code == 200
        items = r.json()
        assert all(i["type"] == "fact" for i in items)

    def test_unknown_project_memory_returns_404(self, client: TestClient):
        r = client.get("/api/projects/99999/memory/items")
        assert r.status_code == 404