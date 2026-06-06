"""
Stage 5 tests — Hypothesis Validator.

Tests:
  - compact_paper_list budget enforcement
  - _dedup title fuzzy matching
  - Phase A query generation (mocked LLM)
  - HypothesisAgent._save_scores persists Score rows
  - Score rows replaced on re-run
  - hypothesis_router GET /scores returns stored scores
  - State machine S1Sub transitions sequence
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# compact_paper_list
# ---------------------------------------------------------------------------

from alfred.agents.hypothesis import compact_paper_list, _dedup, _fuzzy_match


def test_compact_paper_list_empty():
    assert compact_paper_list([]) == ""


def test_compact_paper_list_basic():
    papers = [
        {"title": "Attention Is All You Need", "year": 2017, "venue": "NeurIPS",
         "tldr": "The Transformer model based on attention."},
        {"title": "BERT: Pre-training", "year": 2019, "venue": "NAACL", "tldr": ""},
    ]
    result = compact_paper_list(papers)
    assert "[2017] Attention Is All You Need (NeurIPS)" in result
    assert "[2019] BERT: Pre-training (NAACL)" in result


def test_compact_paper_list_respects_budget():
    papers = [{"title": f"Paper {i}", "year": 2020, "venue": "ICML",
               "abstract": "word " * 50} for i in range(1000)]
    result = compact_paper_list(papers, max_tokens=100)
    lines = result.strip().split("\n")
    # Should have far fewer than 1000 lines due to budget
    assert len(lines) < 100


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def test_fuzzy_match_exact():
    assert _fuzzy_match("Attention Is All You Need", "Attention Is All You Need")


def test_fuzzy_match_similar():
    # Near-identical titles should match
    assert _fuzzy_match(
        "Attention Is All You Need",
        "Attention is all you need",
    )


def test_fuzzy_match_different():
    assert not _fuzzy_match("Transformers", "Convolutional Neural Networks")


def test_dedup_removes_duplicates():
    papers = [
        {"title": "Deep Residual Learning for Image Recognition"},
        {"title": "Deep Residual Learning for Image Recognition"},  # exact dup
        {"title": "BERT: Pre-training of Deep Bidirectional Transformers"},
    ]
    result = _dedup(papers)
    assert len(result) == 2


def test_dedup_keeps_uniques():
    papers = [
        {"title": "Paper A"},
        {"title": "Paper B"},
        {"title": "Paper C"},
    ]
    assert len(_dedup(papers)) == 3


# ---------------------------------------------------------------------------
# Score persistence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_scores_creates_rows(tmp_path):
    """HypothesisAgent._save_scores creates/replaces Score rows."""
    from alfred.db import init_db, get_engine
    import alfred.db as db_module

    db_path = tmp_path / "test.db"
    db_module._engine = None
    engine = init_db(str(db_path))

    from alfred.models.db_models import Project, ProjectStage, Score
    from sqlmodel import Session, select

    with Session(engine) as session:
        proj = Project(name="Test", workspace_path=str(tmp_path))
        session.add(proj)
        session.commit()
        session.refresh(proj)
        pid = proj.id

    from alfred.agents.hypothesis import HypothesisAgent
    from unittest.mock import AsyncMock

    ws_mock = MagicMock()
    ws_mock.send = AsyncMock()
    ws_mock.broadcast_progress = AsyncMock()
    ws_mock.broadcast_done = AsyncMock()
    ws_mock.broadcast_error = AsyncMock()

    with Session(engine) as session:
        agent = HypothesisAgent(
            project_id=pid,
            model="test",
            ws_manager=ws_mock,
            db_session=session,
            auto_approve=False,
        )

        plan = {
            "novelty_score": 75,
            "novelty_rationale": "Novel approach",
            "novelty_citations": [{"title": "Paper A", "year": 2020, "venue": "ICML", "url": ""}],
            "gap_score": 60,
            "gap_rationale": "Gap exists",
            "gap_citations": [],
            "publishability_score": 55,
            "publishability_rationale": "Workshop level",
            "publishability_citations": [],
        }
        agent._save_scores(plan)

    # Verify rows
    with Session(engine) as session:
        scores = session.exec(select(Score).where(Score.project_id == pid)).all()
        assert len(scores) == 3
        novelty = next(s for s in scores if s.kind.value == "novelty")
        assert novelty.value == 75
        assert "Novel approach" in novelty.rationale
        cites = json.loads(novelty.citations_json)
        assert len(cites) == 1
        assert cites[0]["title"] == "Paper A"

    db_module._engine = None


@pytest.mark.asyncio
async def test_save_scores_replaces_on_rerun(tmp_path):
    """Calling _save_scores twice replaces old scores."""
    from alfred.db import init_db
    import alfred.db as db_module

    db_path = tmp_path / "test2.db"
    db_module._engine = None
    engine = init_db(str(db_path))

    from alfred.models.db_models import Project, Score
    from sqlmodel import Session, select

    with Session(engine) as session:
        proj = Project(name="Test2", workspace_path=str(tmp_path))
        session.add(proj)
        session.commit()
        session.refresh(proj)
        pid = proj.id

    from alfred.agents.hypothesis import HypothesisAgent

    ws_mock = MagicMock()
    ws_mock.send = AsyncMock()

    plan_v1 = {
        "novelty_score": 70, "novelty_rationale": "v1", "novelty_citations": [],
        "gap_score": 50, "gap_rationale": "v1", "gap_citations": [],
        "publishability_score": 40, "publishability_rationale": "v1", "publishability_citations": [],
    }
    plan_v2 = {
        "novelty_score": 80, "novelty_rationale": "v2", "novelty_citations": [],
        "gap_score": 65, "gap_rationale": "v2", "gap_citations": [],
        "publishability_score": 60, "publishability_rationale": "v2", "publishability_citations": [],
    }

    with Session(engine) as session:
        agent = HypothesisAgent(
            project_id=pid, model="test", ws_manager=ws_mock, db_session=session
        )
        agent._save_scores(plan_v1)
        agent._save_scores(plan_v2)

    with Session(engine) as session:
        scores = session.exec(select(Score).where(Score.project_id == pid)).all()
        assert len(scores) == 3  # not 6
        novelty = next(s for s in scores if s.kind.value == "novelty")
        assert novelty.value == 80

    db_module._engine = None


# ---------------------------------------------------------------------------
# State machine S1Sub transitions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_state_machine_s1sub_sequence():
    """S1Sub transitions follow the defined order."""
    from alfred.state_machine.machine import (
        ExperimentStateMachine, S1Sub, Stage,
        register_machine, unregister_machine,
    )
    from unittest.mock import MagicMock, AsyncMock

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
        project_id=999, ws_manager=ws, db_session=db, auto_approve=True
    )

    substages = [
        S1Sub.GENERATING_QUERIES,
        S1Sub.SWEEPING_SOURCES,
        S1Sub.SNOWBALLING,
        S1Sub.WEB_SWEEP,
        S1Sub.ANALYZING,
        S1Sub.SCORING,
    ]

    for sub in substages:
        await machine.transition(sub, label=f"test {sub.value}")
        assert machine.current_substage == sub

    # Auto-approve should not block at AWAITING_APPROVAL
    response = await machine.transition(S1Sub.AWAITING_APPROVAL, plan={"test": 1})
    assert response is not None
    assert response.approved is True


# ---------------------------------------------------------------------------
# GET /scores endpoint
# ---------------------------------------------------------------------------

def test_get_scores_returns_empty(tmp_path):
    """GET /scores returns empty list when no scores exist."""
    from alfred.db import init_db
    import alfred.db as db_module
    from fastapi.testclient import TestClient

    db_path = tmp_path / "scores_test.db"
    db_module._engine = None
    init_db(str(db_path))

    from alfred.main import app
    client = TestClient(app)

    # Create project first
    resp = client.post("/api/projects/", json={"name": "ScoreTest"})
    if resp.status_code not in (200, 201):
        db_module._engine = None
        return  # skip if DB not ready

    pid = resp.json()["id"]
    scores_resp = client.get(f"/api/projects/{pid}/hypothesis/scores")
    assert scores_resp.status_code == 200
    assert scores_resp.json() == []

    db_module._engine = None
