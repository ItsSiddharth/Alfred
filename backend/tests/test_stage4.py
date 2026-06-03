"""
tests/test_stage4.py — Stage 4 tests.

Covers:
  - Tool base / registry
  - WebSearchTool (mocked)
  - ArxivSearchTool (mocked)
  - SemanticScholarTool (mocked)
  - OpenAlexSearchTool (mocked)
  - Tools REST endpoints
  - Message ordering fix (ASC, PATCH endpoint)
  - Project delete
  - State machine registry (get_or_create_machine / remove_machine aliases)
  - Memory store / compress / context (via conftest fixtures)
  - Path jail
  - Config and project API
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))


# ─────────────────────────────────────────────────────────────────────────────
# Tool base / registry
# ─────────────────────────────────────────────────────────────────────────────

class TestToolBase:
    def test_tool_registry_singleton(self):
        from alfred.tools.base import ToolRegistry
        r1 = ToolRegistry.get()
        r2 = ToolRegistry.get()
        assert r1 is r2

    def test_tool_result_success(self):
        from alfred.tools.base import ToolResult
        r = ToolResult(tool_name="web_search", success=True, data=[{"title": "t"}])
        assert r.success
        assert len(r.data) == 1

    def test_tool_result_failure(self):
        from alfred.tools.base import ToolResult
        r = ToolResult(tool_name="web_search", success=False, data=[], error="rate limited")
        assert not r.success
        assert r.error == "rate limited"

    def test_registry_set_enabled(self):
        from alfred.tools.base import ToolRegistry
        from alfred.tools.web_search import WebSearchTool
        reg = ToolRegistry()
        tool = WebSearchTool()
        reg._tools["web_search"] = tool
        assert reg.set_enabled("web_search", False)
        assert not tool.enabled
        assert reg.set_enabled("nonexistent", True) is False

    def test_registry_enabled_tools_filter(self):
        from alfred.tools.base import ToolRegistry
        from alfred.tools.web_search import WebSearchTool
        from alfred.tools.arxiv_search import ArxivSearchTool
        reg = ToolRegistry()
        t1 = WebSearchTool(); t1.enabled = True
        t2 = ArxivSearchTool(); t2.enabled = False
        reg._tools["web_search"] = t1
        reg._tools["arxiv_search"] = t2
        enabled = reg.enabled_tools()
        assert t1 in enabled
        assert t2 not in enabled

    def test_to_schema_dict(self):
        from alfred.tools.web_search import WebSearchTool
        tool = WebSearchTool()
        schema = tool.to_schema_dict()
        assert schema["name"] == "web_search"
        assert schema["enabled"] is True
        assert "parameters" in schema

    def test_tools_yaml_loads(self):
        """All 4 tools must load from tools.yaml without error."""
        from alfred.tools.base import ToolRegistry
        reg = ToolRegistry()
        # __file__ is backend/tests/test_stage4.py
        # parent       → backend/tests/
        # parent.parent → backend/
        # tools.yaml   → backend/alfred/tools/tools.yaml
        yaml_path = (
            Path(__file__).parent.parent / "alfred" / "tools" / "tools.yaml"
        )
        reg.load_from_yaml(yaml_path)
        names = {t.name for t in reg.list_tools()}
        assert "web_search" in names
        assert "arxiv_search" in names
        assert "semantic_scholar" in names
        assert "openalex_search" in names


# ─────────────────────────────────────────────────────────────────────────────
# Web search tool
# ─────────────────────────────────────────────────────────────────────────────

class TestWebSearchTool:
    @pytest.mark.asyncio
    async def test_graceful_failure_no_ddgs(self):
        from alfred.tools.web_search import WebSearchTool
        tool = WebSearchTool()
        with patch.dict("sys.modules", {"duckduckgo_search": None}):
            result = await tool.execute({"query": "test"})
        assert not result.success
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_success_path(self):
        from alfred.tools.web_search import WebSearchTool
        mock_ddgs_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.text.return_value = [
            {"title": "Paper A", "href": "https://a.com", "body": "Summary A"},
            {"title": "Paper B", "href": "https://b.com", "body": "Summary B"},
        ]
        mock_ddgs_cls.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_ddgs_cls.return_value.__exit__ = MagicMock(return_value=False)

        with patch.dict("sys.modules", {
            "duckduckgo_search": MagicMock(DDGS=mock_ddgs_cls)
        }):
            tool = WebSearchTool()
            result = await tool.execute({"query": "neural scaling laws", "max_results": 2})

        assert result.success
        assert len(result.data) == 2
        assert result.data[0]["title"] == "Paper A"
        assert len(result.sources) == 2

    @pytest.mark.asyncio
    async def test_invalid_input(self):
        from alfred.tools.web_search import WebSearchTool
        tool = WebSearchTool()
        result = await tool.execute({})   # missing required 'query'
        assert not result.success

    @pytest.mark.asyncio
    async def test_rate_limit_returns_graceful(self):
        from alfred.tools.web_search import WebSearchTool
        mock_ddgs_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.text.side_effect = Exception("202 Ratelimit")
        mock_ddgs_cls.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_ddgs_cls.return_value.__exit__ = MagicMock(return_value=False)

        with patch.dict("sys.modules", {
            "duckduckgo_search": MagicMock(DDGS=mock_ddgs_cls)
        }):
            tool = WebSearchTool()
            result = await tool.execute({"query": "test"})
        assert not result.success
        assert result.error is not None


# ─────────────────────────────────────────────────────────────────────────────
# arXiv tool
# ─────────────────────────────────────────────────────────────────────────────

class TestArxivTool:
    @pytest.mark.asyncio
    async def test_missing_arxiv_package(self):
        from alfred.tools.arxiv_search import ArxivSearchTool
        tool = ArxivSearchTool()
        with patch.dict("sys.modules", {"arxiv": None}):
            result = await tool.execute({"query": "transformers"})
        assert not result.success

    @pytest.mark.asyncio
    async def test_success_path(self):
        from alfred.tools.arxiv_search import ArxivSearchTool
        from datetime import datetime

        mock_result = MagicMock()
        mock_result.entry_id = "http://arxiv.org/abs/1234.5678"
        mock_result.title = "Attention Is All You Need"
        mock_result.summary = "Abstract text here"
        mock_result.authors = [MagicMock(__str__=lambda s: "Vaswani")]
        mock_result.published = datetime(2017, 6, 12)
        mock_result.categories = ["cs.LG"]
        mock_result.pdf_url = "http://arxiv.org/pdf/1234.5678"

        mock_client = MagicMock()
        mock_client.results.return_value = [mock_result]
        mock_arxiv = MagicMock()
        mock_arxiv.Client.return_value = mock_client
        mock_arxiv.Search = MagicMock()
        mock_arxiv.SortCriterion = MagicMock()
        mock_arxiv.SortCriterion.Relevance = "relevance"

        with patch.dict("sys.modules", {"arxiv": mock_arxiv}):
            tool = ArxivSearchTool(config={"categories": ["cs.LG"], "max_results": 5})
            result = await tool.execute({"query": "attention mechanism"})

        assert result.success
        assert len(result.data) == 1
        assert result.data[0]["title"] == "Attention Is All You Need"

    @pytest.mark.asyncio
    async def test_category_filter_injected(self):
        """ML category filter must appear in the query string sent to arXiv."""
        from alfred.tools.arxiv_search import ArxivSearchTool

        captured_queries: list[str] = []
        mock_client = MagicMock()
        mock_client.results.return_value = []
        mock_arxiv = MagicMock()
        mock_arxiv.Client.return_value = mock_client
        mock_arxiv.SortCriterion = MagicMock()
        mock_arxiv.SortCriterion.Relevance = "relevance"

        def capture_search(query, **kwargs):
            captured_queries.append(query)
            return MagicMock()

        mock_arxiv.Search = capture_search

        with patch.dict("sys.modules", {"arxiv": mock_arxiv}):
            tool = ArxivSearchTool(config={"categories": ["cs.LG", "cs.AI"], "max_results": 5})
            await tool.execute({"query": "GAN training"})

        assert len(captured_queries) == 1
        assert "cs.LG" in captured_queries[0]
        assert "cs.AI" in captured_queries[0]


# ─────────────────────────────────────────────────────────────────────────────
# Semantic Scholar tool
# ─────────────────────────────────────────────────────────────────────────────

class TestSemanticScholarTool:
    @pytest.mark.asyncio
    async def test_search_op(self):
        from alfred.tools.semantic_scholar import SemanticScholarTool

        mock_paper = MagicMock()
        mock_paper.paperId = "abc123"
        mock_paper.title = "BERT: Pre-training"
        mock_paper.abstract = "We introduce BERT"
        mock_paper.year = 2018
        mock_paper.venue = "NAACL"
        mock_paper.citationCount = 50000
        mock_paper.tldr = {"text": "BERT is a language model"}

        mock_sch_instance = MagicMock()
        mock_sch_instance.search_paper.return_value = [mock_paper]
        mock_sch_cls = MagicMock(return_value=mock_sch_instance)

        with patch.dict("sys.modules", {
            "semanticscholar": MagicMock(SemanticScholar=mock_sch_cls)
        }):
            tool = SemanticScholarTool(config={"sleep_seconds": 0})
            result = await tool.execute({"op": "search", "query": "BERT language model"})

        assert result.success
        assert len(result.data) == 1
        assert result.data[0]["paper_id"] == "abc123"
        assert result.data[0]["tldr"] == "BERT is a language model"

    @pytest.mark.asyncio
    async def test_expand_op(self):
        from alfred.tools.semantic_scholar import SemanticScholarTool

        mock_ref = MagicMock()
        mock_ref.paperId = "ref1"; mock_ref.title = "Word2Vec"; mock_ref.year = 2013

        mock_cite = MagicMock()
        mock_cite.paperId = "cite1"; mock_cite.title = "RoBERTa"; mock_cite.year = 2019

        mock_paper = MagicMock()
        mock_paper.paperId = "abc123"; mock_paper.title = "BERT"
        mock_paper.references = [mock_ref]; mock_paper.citations = [mock_cite]

        mock_sch_instance = MagicMock()
        mock_sch_instance.get_paper.return_value = mock_paper
        mock_sch_cls = MagicMock(return_value=mock_sch_instance)

        with patch.dict("sys.modules", {
            "semanticscholar": MagicMock(SemanticScholar=mock_sch_cls)
        }):
            tool = SemanticScholarTool(config={"sleep_seconds": 0})
            result = await tool.execute({"op": "expand", "paper_id": "abc123"})

        assert result.success
        assert "references" in result.data
        assert "citations" in result.data
        assert result.data["references"][0]["title"] == "Word2Vec"
        assert result.data["citations"][0]["title"] == "RoBERTa"

    @pytest.mark.asyncio
    async def test_expand_missing_paper_id(self):
        from alfred.tools.semantic_scholar import SemanticScholarTool
        tool = SemanticScholarTool(config={"sleep_seconds": 0})
        result = await tool.execute({"op": "expand"})
        assert not result.success

    @pytest.mark.asyncio
    async def test_search_missing_query(self):
        from alfred.tools.semantic_scholar import SemanticScholarTool
        tool = SemanticScholarTool(config={"sleep_seconds": 0})
        result = await tool.execute({"op": "search"})
        assert not result.success


# ─────────────────────────────────────────────────────────────────────────────
# OpenAlex tool
# ─────────────────────────────────────────────────────────────────────────────

class TestOpenAlexTool:
    def test_abstract_reconstruction(self):
        from alfred.tools.openalex_search import _reconstruct_abstract
        inv = {"the": [0, 4], "cat": [1], "sat": [2], "on": [3], "mat": [5]}
        result = _reconstruct_abstract(inv)
        assert result.startswith("the cat")

    def test_abstract_reconstruction_empty(self):
        from alfred.tools.openalex_search import _reconstruct_abstract
        assert _reconstruct_abstract(None) == ""
        assert _reconstruct_abstract({}) == ""

    @pytest.mark.asyncio
    async def test_success_path(self):
        from alfred.tools.openalex_search import OpenAlexSearchTool

        mock_response = {
            "results": [{
                "id": "https://openalex.org/W1234",
                "title": "Deep Residual Learning",
                "publication_year": 2016,
                "cited_by_count": 100000,
                "primary_location": {"source": {"display_name": "CVPR"}},
                "concepts": [
                    {"display_name": "Deep Learning"},
                    {"display_name": "Computer Vision"},
                ],
                "abstract_inverted_index": {
                    "Deep": [0], "residual": [1], "networks": [2]
                },
                "doi": "10.1109/cvpr.2016.90",
            }]
        }

        async def mock_get(*args, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = mock_response
            return resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(side_effect=mock_get)
            mock_client_cls.return_value = mock_ctx

            tool = OpenAlexSearchTool()
            result = await tool.execute({"query": "residual networks"})

        assert result.success
        assert len(result.data) == 1
        assert result.data[0]["title"] == "Deep Residual Learning"
        assert result.data[0]["venue"] == "CVPR"
        assert "Deep" in result.data[0]["abstract"]

    @pytest.mark.asyncio
    async def test_http_error_graceful(self):
        from alfred.tools.openalex_search import OpenAlexSearchTool
        import httpx

        async def mock_get_error(*args, **kwargs):
            raise httpx.ConnectError("connection refused")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(side_effect=mock_get_error)
            mock_client_cls.return_value = mock_ctx

            tool = OpenAlexSearchTool()
            result = await tool.execute({"query": "test"})

        assert not result.success
        assert result.error is not None


# ─────────────────────────────────────────────────────────────────────────────
# Tools REST endpoints (use conftest client + project fixtures)
# ─────────────────────────────────────────────────────────────────────────────

class TestToolsEndpoints:
    def test_list_tools(self, client):
        from alfred.tools.base import ToolRegistry
        from alfred.tools.web_search import WebSearchTool
        ToolRegistry.get()._tools["web_search"] = WebSearchTool()

        resp = client.get("/api/tools/")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        names = [t["name"] for t in data]
        assert "web_search" in names

    def test_get_single_tool(self, client):
        from alfred.tools.base import ToolRegistry
        from alfred.tools.web_search import WebSearchTool
        ToolRegistry.get()._tools["web_search"] = WebSearchTool()

        resp = client.get("/api/tools/web_search")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "web_search"
        assert "enabled" in data

    def test_get_nonexistent_tool(self, client):
        resp = client.get("/api/tools/nonexistent_tool_xyz")
        assert resp.status_code == 404

    def test_enable_disable_tool(self, client):
        from alfred.tools.base import ToolRegistry
        from alfred.tools.web_search import WebSearchTool
        tool = WebSearchTool(); tool.enabled = True
        ToolRegistry.get()._tools["web_search"] = tool

        resp = client.post("/api/tools/web_search/disable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        resp = client.post("/api/tools/web_search/enable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

    def test_recent_calls_empty(self, client, project):
        resp = client.get(f"/api/tools/calls/{project.id}")
        assert resp.status_code == 200
        assert resp.json() == []


# ─────────────────────────────────────────────────────────────────────────────
# Message ordering and PATCH endpoint (Stage 4 bug fixes)
# ─────────────────────────────────────────────────────────────────────────────

class TestMessageOrdering:
    def test_messages_returned_asc(self, client, project, messages):
        """Messages must be returned in chronological ASC order."""
        resp = client.get(f"/api/projects/{project.id}/messages/")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        timestamps = [d["created_at"] for d in data]
        assert timestamps == sorted(timestamps), \
            f"Messages not in ASC order: {timestamps}"
        assert data[0]["role"] == "user"
        assert data[0]["content"] == "Hello ALFRED"
        assert data[1]["role"] == "assistant"
        assert data[2]["content"] == "Run the demo"

    def test_message_patch_updates_content(self, client, project, messages):
        """PATCH /messages/{id} must update content and metadata_json."""
        msg_id = messages[1].id  # assistant message
        resp = client.patch(
            f"/api/projects/{project.id}/messages/{msg_id}",
            json={
                "content": "Updated assistant response",
                "metadata_json": '{"model":"test"}',
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "Updated assistant response"
        assert data["metadata_json"] == '{"model":"test"}'

    def test_message_patch_partial(self, client, project, messages):
        """PATCH with only content should not wipe metadata_json."""
        msg_id = messages[0].id
        # First set some metadata
        client.patch(
            f"/api/projects/{project.id}/messages/{msg_id}",
            json={"metadata_json": '{"foo":"bar"}'},
        )
        # Then patch only content
        resp = client.patch(
            f"/api/projects/{project.id}/messages/{msg_id}",
            json={"content": "Patched content only"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "Patched content only"
        # metadata_json should be unchanged from the previous patch
        assert data["metadata_json"] == '{"foo":"bar"}'

    def test_message_create_returns_id(self, client, project):
        """Creating an assistant placeholder must return a valid numeric id."""
        resp = client.post(
            f"/api/projects/{project.id}/messages/",
            json={"role": "assistant", "content": "", "kind": "chat"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] is not None
        assert data["id"] > 0
        assert data["role"] == "assistant"
        assert data["content"] == ""

    def test_messages_from_deleted_project_404(self, client):
        """Requesting messages for a non-existent project returns 404."""
        resp = client.get("/api/projects/99999/messages/")
        assert resp.status_code == 404

    def test_message_patch_wrong_project_404(self, client, project, messages):
        """Patching a message with a wrong project_id returns 404."""
        msg_id = messages[0].id
        resp = client.patch(
            f"/api/projects/99999/messages/{msg_id}",
            json={"content": "hijack"},
        )
        assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Project delete
# ─────────────────────────────────────────────────────────────────────────────

class TestProjectDelete:
    def test_delete_project(self, client, project, messages):
        pid = project.id
        resp = client.get(f"/api/projects/{pid}")
        assert resp.status_code == 200

        resp = client.delete(f"/api/projects/{pid}")
        assert resp.status_code == 204

        resp = client.get(f"/api/projects/{pid}")
        assert resp.status_code == 404

        # Messages should also be gone
        resp = client.get(f"/api/projects/{pid}/messages/")
        assert resp.status_code == 404

    def test_delete_nonexistent_project(self, client):
        resp = client.delete("/api/projects/99999")
        assert resp.status_code == 404

    def test_deleted_project_not_in_list(self, client, project):
        pid = project.id
        resp = client.delete(f"/api/projects/{pid}")
        assert resp.status_code == 204

        resp = client.get("/api/projects/")
        assert resp.status_code == 200
        ids = [p["id"] for p in resp.json()]
        assert pid not in ids

    def test_delete_already_deleted_returns_404(self, client, project):
        pid = project.id
        client.delete(f"/api/projects/{pid}")
        resp = client.delete(f"/api/projects/{pid}")
        assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# State machine — including new registry aliases
# ─────────────────────────────────────────────────────────────────────────────

class TestStateMachine:
    @pytest.mark.asyncio
    async def test_transition_s1(self):
        from alfred.state_machine.machine import ExperimentStateMachine, S1Sub, Stage
        m = ExperimentStateMachine(project_id=1, ws_manager=_noop_ws(), db_session=MagicMock())
        m.current_stage = Stage.HYPOTHESIS
        m.current_substage = S1Sub.GENERATING_QUERIES
        # transition modifies internal state; we test snapshot reflects it
        snap = m.snapshot()
        assert snap["stage"] == Stage.HYPOTHESIS.value

    @pytest.mark.asyncio
    async def test_approval_gate_auto(self):
        """Auto-approve bypasses the blocking gate."""
        import asyncio
        from alfred.state_machine.machine import ExperimentStateMachine, S2Sub, Stage
        m = ExperimentStateMachine(
            project_id=2, ws_manager=_noop_ws(), db_session=MagicMock(),
            auto_approve=True,
        )
        m._stage = Stage.SETUP
        result = await asyncio.wait_for(
            m._handle_approval_gate({"score": 72}),
            timeout=2.0,
        )
        assert result.approved is True

    @pytest.mark.asyncio
    async def test_approval_gate_manual(self):
        """Manual gate blocks until resolve_approval() is called."""
        import asyncio
        from alfred.state_machine.machine import ExperimentStateMachine, S2Sub, Stage

        m = ExperimentStateMachine(
            project_id=3, ws_manager=_noop_ws(), db_session=MagicMock(),
            auto_approve=False,
        )
        m._stage = Stage.SETUP

        async def _resolve():
            await asyncio.sleep(0.05)
            m.resolve_approval(approved=True, feedback="looks good")

        task = asyncio.create_task(_resolve())
        result = await asyncio.wait_for(
            m._handle_approval_gate({"plan": "test"}),
            timeout=2.0,
        )
        await task
        assert result.approved is True
        assert result.feedback == "looks good"

    def test_snapshot_restore(self):
        from alfred.state_machine.machine import ExperimentStateMachine, S1Sub, Stage

        m = ExperimentStateMachine(
            project_id=4, ws_manager=_noop_ws(), db_session=MagicMock()
        )
        m._stage = Stage.HYPOTHESIS
        m._substage = S1Sub.SCORING

        snap = m.snapshot()
        # snapshot() returns keys "stage" (int) and "substage" (str)
        assert snap["stage"] == Stage.HYPOTHESIS.value
        assert snap["substage"] == "scoring"

    @pytest.mark.asyncio
    async def test_registry_aliases(self):
        """get_or_create_machine and remove_machine must work correctly."""
        from alfred.state_machine.machine import (
            get_machine, get_or_create_machine, remove_machine, _machines
        )
        # Clean up any leftover
        remove_machine(9999)
        assert get_machine(9999) is None

        m = get_or_create_machine(9999)
        assert get_machine(9999) is m

        remove_machine(9999)
        assert get_machine(9999) is None

    @pytest.mark.asyncio
    async def test_registry_canonical_names(self):
        """register_machine / unregister_machine also work."""
        from alfred.state_machine.machine import (
            get_machine, register_machine, unregister_machine, ExperimentStateMachine,
        )
        unregister_machine(8888)
        assert get_machine(8888) is None

        m = ExperimentStateMachine(8888, _noop_ws(), MagicMock())
        register_machine(8888, m)
        assert get_machine(8888) is m

        unregister_machine(8888)
        assert get_machine(8888) is None


def _noop_ws():
    """Return a no-op WS manager stub for state machine tests."""
    class _NoOpWS:
        async def send(self, *a, **kw): ...
        async def broadcast_progress(self, *a, **kw): ...
        async def broadcast_done(self, *a, **kw): ...
        async def broadcast_error(self, *a, **kw): ...
    return _NoOpWS()


# ─────────────────────────────────────────────────────────────────────────────
# Memory (using conftest session + project fixtures)
# ─────────────────────────────────────────────────────────────────────────────

class TestMemoryStore:
    def test_create_and_list(self, session, project):
        from alfred.memory.store import create_item, list_items
        from alfred.models.db_models import MemoryType, MemorySource
        item = create_item(
            session,
            project_id=project.id,
            memory_type=MemoryType.fact,
            content="transformers use self-attention",
        )
        items = list_items(session, project_id=project.id)
        assert any(i.id == item.id for i in items)
        assert item.content == "transformers use self-attention"

    def test_update_item(self, session, project):
        from alfred.memory.store import create_item, update_item
        from alfred.models.db_models import MemoryType
        item = create_item(
            session, project_id=project.id,
            memory_type=MemoryType.preference, content="prefer short answers",
        )
        updated = update_item(session, item.id, content="prefer concise responses")
        assert updated is not None
        assert updated.content == "prefer concise responses"

    def test_delete_item(self, session, project):
        from alfred.memory.store import create_item, delete_item, list_items
        from alfred.models.db_models import MemoryType
        item = create_item(
            session, project_id=project.id,
            memory_type=MemoryType.mistake, content="forgot to normalise data",
        )
        assert delete_item(session, item.id)
        items = list_items(session, project_id=project.id)
        assert not any(i.id == item.id for i in items)

    def test_capture_hooks(self, session, project):
        from alfred.memory.store import (
            capture_mistake, capture_preference, capture_fact, capture_dataset_ref,
        )
        from alfred.models.db_models import MemoryType
        m = capture_mistake(session, project.id, "OOM error")
        assert m.type == MemoryType.mistake
        p = capture_preference(session, project.id, "use early stopping")
        assert p.type == MemoryType.preference
        f = capture_fact(session, project.id, "CIFAR-10 has 10 classes")
        assert f.type == MemoryType.fact
        d = capture_dataset_ref(session, project.id, "huggingface/mnist")
        assert d.type == MemoryType.dataset_ref

    def test_estimate_tokens_in_compress(self):
        """estimate_tokens lives in compress.py — verify import path."""
        from alfred.memory.compress import estimate_tokens
        assert estimate_tokens("") >= 1
        text = "a" * 400  # ~100 tokens
        assert 90 <= estimate_tokens(text) <= 110

    def test_get_compiled_returns_none_before_compile(self, session, project):
        from alfred.memory.compress import get_compiled
        result = get_compiled(session, project.id)
        assert result is None

    @pytest.mark.asyncio
    async def test_compile_fallback(self, session, project):
        from alfred.memory.store import capture_fact
        from alfred.memory.compress import compile_memory
        capture_fact(session, project.id, "GPU is NVIDIA A100")
        result = await compile_memory(session, project.id, model="nonexistent-model:test")
        assert len(result.markdown) > 0
        assert result.item_count >= 1
        assert result.is_stale is False

    def test_context_build_empty(self, session):
        from alfred.memory.context import build_memory_block
        block = build_memory_block(session, project_id=9999)
        assert block == ""

    def test_context_build_with_items(self, session, project):
        from alfred.memory.store import capture_fact
        from alfred.memory.context import build_memory_block
        capture_fact(session, project.id, "GPU is NVIDIA A100")
        block = build_memory_block(session, project.id)
        assert len(block) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Path jail
# ─────────────────────────────────────────────────────────────────────────────

class TestPathJail:
    def test_valid_path(self, tmp_path):
        from alfred.utils.paths import assert_within
        target = tmp_path / "subdir" / "file.txt"
        target.parent.mkdir()
        result = assert_within(tmp_path, target)
        assert result == target.resolve()

    def test_escape_raises(self, tmp_path):
        from alfred.utils.paths import assert_within, PathJailError
        with pytest.raises(PathJailError):
            assert_within(tmp_path / "sandbox", tmp_path / "escape" / "file.txt")

    def test_dotdot_escape_raises(self, tmp_path):
        from alfred.utils.paths import assert_within, PathJailError
        with pytest.raises(PathJailError):
            assert_within(
                tmp_path / "sandbox",
                tmp_path / "sandbox" / ".." / ".." / "etc" / "passwd",
            )


# ─────────────────────────────────────────────────────────────────────────────
# Config and project API
# ─────────────────────────────────────────────────────────────────────────────

class TestConfigAndProjects:
    def test_config_ready(self, client):
        resp = client.get("/api/config/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("ready", "needs_setup", "configured")

    def test_create_and_list_projects(self, client):
        resp = client.post("/api/projects/", json={"name": "My Research"})
        assert resp.status_code == 201
        created = resp.json()
        assert created["name"] == "My Research"
        assert created["id"] > 0

        resp = client.get("/api/projects/")
        assert resp.status_code == 200
        ids = [p["id"] for p in resp.json()]
        assert created["id"] in ids

    def test_auto_approve_toggle(self, client, project):
        resp = client.post(f"/api/projects/{project.id}/auto_approve")
        assert resp.status_code == 200
        data = resp.json()
        first_val = data["auto_approve"]

        resp = client.post(f"/api/projects/{project.id}/auto_approve")
        data = resp.json()
        assert data["auto_approve"] != first_val

    def test_health_endpoint(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"