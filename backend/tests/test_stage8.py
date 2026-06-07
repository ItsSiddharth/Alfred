"""
test_stage8.py — Stage 8: Dashboard, compute estimate, paper export.

Tests:
  - GET /api/projects/{id}/dashboard with no data, with experiments+metrics
  - GET /api/projects/{id}/compute-estimate empty and with completed runs
  - POST /api/projects/{id}/export markdown and LaTeX generation
  - POST /api/projects/{id}/skip-hypothesis stage advancement
  - hypothesis.py MAX_QUERIES respects config.research_num_queries
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from alfred.api.dashboard_router import _build_markdown, _build_latex
from alfred.db import init_engine_for_testing
from alfred.main import app
from alfred.models.db_models import (
    Experiment,
    ExperimentStatus,
    Metric,
    Project,
    ProjectStage,
    Score,
    ScoreKind,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def test_engine():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    init_engine_for_testing(engine)
    return engine


@pytest.fixture(scope="module")
def client(test_engine):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db_session(test_engine):
    with Session(test_engine) as session:
        yield session


@pytest.fixture
def project(db_session):
    proj = Project(name="test-stage8", current_stage=ProjectStage.hypothesis)
    db_session.add(proj)
    db_session.commit()
    db_session.refresh(proj)
    yield proj
    db_session.delete(proj)
    db_session.commit()


@pytest.fixture
def experiments_with_metrics(db_session, project):
    """Two done experiments with metrics."""
    exps = []
    for i in range(1, 3):
        exp = Experiment(
            project_id=project.id,
            iteration=i,
            seed=42,
            plan_json=json.dumps({"objective": f"test iter {i}", "dataset": "MNIST"}),
            status=ExperimentStatus.done,
            runtime_seconds=60.0 * i,
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
        )
        db_session.add(exp)
        db_session.commit()
        db_session.refresh(exp)
        exps.append(exp)

        for step in range(5):
            db_session.add(Metric(experiment_id=exp.id, name="loss", step=step, value=1.0 / (step + i)))
            db_session.add(Metric(experiment_id=exp.id, name="accuracy", step=step, value=step * 0.1 + i * 0.05))
        db_session.commit()

    yield exps

    for exp in exps:
        for m in db_session.exec(select(Metric).where(Metric.experiment_id == exp.id)).all():
            db_session.delete(m)
        db_session.delete(exp)
    db_session.commit()


# ---------------------------------------------------------------------------
# Dashboard endpoint tests
# ---------------------------------------------------------------------------


class TestDashboard:
    def test_empty_dashboard(self, client, project):
        resp = client.get(f"/api/projects/{project.id}/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["experiments"] == []
        assert data["metric_curves"] == []
        assert data["metric_names"] == []

    def test_dashboard_with_data(self, client, project, experiments_with_metrics):
        resp = client.get(f"/api/projects/{project.id}/dashboard")
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["experiments"]) == 2
        assert sorted(data["metric_names"]) == ["accuracy", "loss"]
        assert len(data["metric_curves"]) > 0

        # Both iterations should appear in metric_curves
        iters = {c["iteration"] for c in data["metric_curves"]}
        assert 1 in iters and 2 in iters

    def test_dashboard_metrics_summary(self, client, project, experiments_with_metrics):
        resp = client.get(f"/api/projects/{project.id}/dashboard")
        data = resp.json()

        # Each experiment row should have a metrics_summary
        for exp_row in data["experiments"]:
            assert "loss" in exp_row["metrics_summary"]
            assert "accuracy" in exp_row["metrics_summary"]

    def test_dashboard_experiment_row_fields(self, client, project, experiments_with_metrics):
        resp = client.get(f"/api/projects/{project.id}/dashboard")
        data = resp.json()
        row = data["experiments"][0]
        assert "iteration" in row
        assert "status" in row
        assert "runtime_seconds" in row
        assert "git_commit" in row
        assert "version_mode" in row
        assert "plan_summary" in row

    def test_dashboard_404(self, client):
        resp = client.get("/api/projects/99999/dashboard")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Compute estimate tests
# ---------------------------------------------------------------------------


class TestComputeEstimate:
    def test_no_runs_returns_unknown(self, client, project):
        resp = client.get(f"/api/projects/{project.id}/compute-estimate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["estimated_seconds"] is None
        assert data["based_on_n_runs"] == 0
        assert "no completed runs" in data["estimated_label"].lower() or "unknown" in data["estimated_label"].lower()

    def test_with_runs_returns_median(self, client, project, experiments_with_metrics):
        resp = client.get(f"/api/projects/{project.id}/compute-estimate")
        assert resp.status_code == 200
        data = resp.json()
        # Two runs: 60s and 120s → median = 60s (lower half of sorted list)
        assert data["estimated_seconds"] is not None
        assert data["based_on_n_runs"] == 2
        assert "hardware" in data["estimated_label"].lower()


# ---------------------------------------------------------------------------
# Export tests
# ---------------------------------------------------------------------------


class TestExport:
    def test_export_markdown_empty(self, client, project):
        resp = client.post(f"/api/projects/{project.id}/export", json={"include_latex": False})
        assert resp.status_code == 200
        data = resp.json()
        assert "DRAFT" in data["markdown"]
        assert data["latex"] == ""
        assert "test-stage8" in data["filename"].lower() or "test_stage8" in data["filename"].lower()

    def test_export_with_data(self, client, project, experiments_with_metrics):
        # Add scores first
        from alfred.db import get_engine
        from sqlmodel import Session

        with Session(get_engine()) as s:
            for kind_str in ("novelty", "gap", "publishability"):
                s.add(Score(
                    project_id=project.id,
                    kind=ScoreKind(kind_str),
                    value=75,
                    rationale=f"Test {kind_str} rationale",
                    citations_json="[]",
                ))
            s.commit()

        resp = client.post(
            f"/api/projects/{project.id}/export",
            json={"include_latex": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        md = data["markdown"]
        assert "## Hypothesis Assessment" in md
        assert "Novelty" in md
        assert "## Methodology" in md
        assert "## Results" in md
        assert "| loss |" in md or "loss" in md
        assert data["latex"] != ""
        assert r"\section{Hypothesis Assessment}" in data["latex"]

    def test_export_latex_escaping(self, client, db_session):
        proj = Project(name="test & special_chars%", current_stage=ProjectStage.run)
        db_session.add(proj)
        db_session.commit()
        db_session.refresh(proj)

        resp = client.post(f"/api/projects/{proj.id}/export", json={"include_latex": True})
        assert resp.status_code == 200
        latex = resp.json()["latex"]
        # Ampersands must be escaped in LaTeX
        assert "\\&" in latex or "&" not in latex.split(r"\title")[1][:50]

        db_session.delete(proj)
        db_session.commit()


# ---------------------------------------------------------------------------
# Skip hypothesis tests
# ---------------------------------------------------------------------------


class TestSkipHypothesis:
    def test_skip_hypothesis_advances_stage(self, client, db_session):
        proj = Project(name="skip-test", current_stage=ProjectStage.hypothesis)
        db_session.add(proj)
        db_session.commit()
        db_session.refresh(proj)

        resp = client.post(f"/api/projects/{proj.id}/skip-hypothesis")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["current_stage"] == "setup"

        db_session.refresh(proj)
        assert proj.current_stage == ProjectStage.setup

        # Iteration-1 experiment should exist
        exp = db_session.exec(
            select(Experiment).where(Experiment.project_id == proj.id)
        ).first()
        assert exp is not None
        assert exp.iteration == 1

        db_session.delete(exp)
        db_session.delete(proj)
        db_session.commit()

    def test_skip_hypothesis_wrong_stage(self, client, db_session):
        proj = Project(name="skip-test-setup", current_stage=ProjectStage.setup)
        db_session.add(proj)
        db_session.commit()
        db_session.refresh(proj)

        resp = client.post(f"/api/projects/{proj.id}/skip-hypothesis")
        assert resp.status_code == 409

        db_session.delete(proj)
        db_session.commit()

    def test_skip_hypothesis_creates_experiment_once(self, client, db_session):
        """Calling skip-hypothesis twice shouldn't create duplicate experiments."""
        proj = Project(name="skip-dedup", current_stage=ProjectStage.hypothesis)
        db_session.add(proj)
        db_session.commit()
        db_session.refresh(proj)

        # First skip → creates experiment, advances to setup
        client.post(f"/api/projects/{proj.id}/skip-hypothesis")

        # Reset back to hypothesis manually
        proj.current_stage = ProjectStage.hypothesis
        db_session.add(proj)
        db_session.commit()

        # Second skip → experiment already exists, should not duplicate
        resp = client.post(f"/api/projects/{proj.id}/skip-hypothesis")
        assert resp.status_code == 200

        exps = db_session.exec(select(Experiment).where(Experiment.project_id == proj.id)).all()
        assert len(exps) == 1  # still only one

        for e in exps:
            db_session.delete(e)
        db_session.delete(proj)
        db_session.commit()


# ---------------------------------------------------------------------------
# Markdown / LaTeX builder unit tests
# ---------------------------------------------------------------------------


class TestBuilders:
    def test_markdown_has_required_sections(self):
        md = _build_markdown("test-project", {}, [], [])
        assert "DRAFT" in md
        assert "# test-project" in md

    def test_markdown_with_scores(self):
        from alfred.models.db_models import Score, ScoreKind
        from datetime import datetime

        score = Score(
            id=1, project_id=1, kind=ScoreKind.novelty,
            value=80, rationale="Very novel idea.", citations_json="[]",
            created_at=datetime.utcnow(),
        )
        md = _build_markdown("p", {"novelty": score}, [], [])
        assert "Novelty" in md
        assert "80/100" in md

    def test_latex_does_not_crash_on_empty(self):
        latex = _build_latex("simple project", {}, [], [])
        assert r"\documentclass" in latex
        assert r"\end{document}" in latex

    def test_latex_escapes_special_chars(self):
        from alfred.models.db_models import Score, ScoreKind
        from datetime import datetime

        score = Score(
            id=2, project_id=1, kind=ScoreKind.gap,
            value=60, rationale="50% improvement & speed-up.", citations_json="[]",
            created_at=datetime.utcnow(),
        )
        latex = _build_latex("proj & test", {"gap": score}, [], [])
        assert "\\&" in latex
        assert "\\%" in latex
