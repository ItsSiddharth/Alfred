"""
tests/test_stage7.py — Stage 7 smoke tests.

Sub-step 7.1: conda executor, dataset cache, git service, runner router.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

# ---------------------------------------------------------------------------
# Path jail
# ---------------------------------------------------------------------------

def test_assert_within_ok(tmp_path: Path) -> None:
    from alfred.utils.paths import assert_within
    child = tmp_path / "subdir" / "file.txt"
    child.parent.mkdir()
    child.touch()
    result = assert_within(tmp_path, child)
    assert result == child.resolve()


def test_assert_within_escape(tmp_path: Path) -> None:
    from alfred.utils.paths import assert_within, PathJailError
    outside = tmp_path / ".." / "outside.txt"
    with pytest.raises(PathJailError):
        assert_within(tmp_path, outside)


# ---------------------------------------------------------------------------
# CondaExecutor
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_conda_executor_streams_lines(tmp_path: Path) -> None:
    """CondaExecutor.run_script collects all output lines via on_line_cb."""
    from alfred.services.conda import CondaExecutor

    script = tmp_path / "hello.py"
    script.write_text("print('line1')\nprint('line2')\n")

    lines: list[str] = []

    async def collect(line: str) -> None:
        lines.append(line)

    executor = CondaExecutor(conda_env="base", experiment_folder=tmp_path)

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        # Simulate a subprocess that emits two lines then exits
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)

        async def fake_lines():
            yield b"line1\n"
            yield b"line2\n"

        mock_proc.stdout = fake_lines()
        mock_exec.return_value = mock_proc

        exit_code = await executor.run_script(script, collect)

    assert exit_code == 0
    assert lines == ["line1", "line2"]


@pytest.mark.asyncio
async def test_conda_executor_jail_rejects_outside(tmp_path: Path) -> None:
    """run_script raises PathJailError for scripts outside experiment_folder."""
    from alfred.services.conda import CondaExecutor
    from alfred.utils.paths import PathJailError

    other = tmp_path / "other"
    other.mkdir()
    script = other / "evil.py"
    script.write_text("pass\n")

    jail = tmp_path / "jail"
    jail.mkdir()
    executor = CondaExecutor(conda_env="base", experiment_folder=jail)

    async def noop(line: str) -> None:
        pass

    with pytest.raises(PathJailError):
        await executor.run_script(script, noop)


# ---------------------------------------------------------------------------
# DatasetCache
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dataset_cache_http_miss_then_hit(tmp_path: Path) -> None:
    """First call downloads; second call returns from DB without downloading."""
    from alfred.services.dataset_cache import DatasetCache

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    exp_folder = tmp_path / "exp"
    exp_folder.mkdir()

    cache = DatasetCache(workspace)

    # Fake file content
    fake_content = b"fake dataset bytes" * 100
    fake_hash = hashlib.sha256(fake_content).hexdigest()

    # Mock httpx streaming response
    async def fake_stream_bytes(size):
        yield fake_content

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.aiter_bytes = fake_stream_bytes
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    url = "http://example.com/data.csv"

    with patch("httpx.AsyncClient", return_value=mock_client):
        with Session(engine) as session:
            path1 = await cache.get_or_download(url, exp_folder, session)

    assert path1.exists() or path1.is_symlink()

    # Second call: should hit the DB and not call httpx again
    call_count = 0
    orig_fetch_http = cache._fetch_http

    async def counted_fetch_http(u):
        nonlocal call_count
        call_count += 1
        return await orig_fetch_http(u)

    with Session(engine) as session:
        with patch.object(cache, "_fetch_http", side_effect=counted_fetch_http):
            path2 = await cache.get_or_download(url, exp_folder, session)

    assert call_count == 0   # should NOT have re-downloaded
    assert str(path2.parent) == str(exp_folder / "data")


# ---------------------------------------------------------------------------
# GitService
# ---------------------------------------------------------------------------

def test_git_service_init_commit_log(tmp_path: Path) -> None:
    from alfred.services.git_service import GitService

    folder = tmp_path / "experiment"
    folder.mkdir()
    git = GitService(folder)

    git.init()
    assert (folder / ".git").exists()
    assert (folder / ".gitignore").exists()

    # Write a file and commit it
    (folder / "run_1.py").write_text("print('hello')\n")
    commit_hash = git.commit("exp 1: hello world | loss=0.5 | seed=42")

    assert len(commit_hash) == 40
    assert all(c in "0123456789abcdef" for c in commit_hash)

    entries = git.log(n=5)
    assert len(entries) >= 1
    assert entries[0]["hash"] == commit_hash
    assert "exp 1:" in entries[0]["message"]


def test_git_service_rollback(tmp_path: Path) -> None:
    from alfred.services.git_service import GitService

    folder = tmp_path / "experiment"
    folder.mkdir()
    git = GitService(folder)
    git.init()

    # Commit v1
    (folder / "run.py").write_text("# v1\n")
    hash1 = git.commit("v1")

    # Commit v2
    (folder / "run.py").write_text("# v2\n")
    git.commit("v2")

    assert (folder / "run.py").read_text() == "# v2\n"

    # Rollback to v1
    git.rollback(hash1)
    assert (folder / "run.py").read_text() == "# v1\n"


def test_git_service_invalid_hash(tmp_path: Path) -> None:
    from alfred.services.git_service import GitService, GitError

    folder = tmp_path / "experiment"
    folder.mkdir()
    git = GitService(folder)
    git.init()

    with pytest.raises(GitError):
        git.rollback("not-a-hash")


def test_git_service_log_empty(tmp_path: Path) -> None:
    """log() returns [] when no git repo or no commits."""
    from alfred.services.git_service import GitService

    folder = tmp_path / "norepo"
    folder.mkdir()
    git = GitService(folder)
    # No init — no .git
    entries = git.log()
    assert entries == []


# ---------------------------------------------------------------------------
# Runner router — project binding (use conftest `client` fixture)
# ---------------------------------------------------------------------------

def test_bind_project_ok(client: TestClient, tmp_path: Path) -> None:
    resp = client.post("/api/projects/", json={"name": "BindTest"})
    assert resp.status_code == 201
    project_id = resp.json()["id"]

    folder = str(tmp_path / "expfolder")
    resp = client.patch(
        f"/api/projects/{project_id}/bind",
        json={"conda_env": "myenv", "experiment_folder": folder},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["conda_env"] == "myenv"
    assert data["experiment_folder"] == folder
    assert Path(folder).exists()


def test_bind_project_relative_path(client: TestClient) -> None:
    resp = client.post("/api/projects/", json={"name": "BindRelTest"})
    project_id = resp.json()["id"]

    resp = client.patch(
        f"/api/projects/{project_id}/bind",
        json={"conda_env": "myenv", "experiment_folder": "relative/path"},
    )
    assert resp.status_code == 400
    assert "absolute" in resp.json()["detail"].lower()


def test_bind_project_empty_env(client: TestClient, tmp_path: Path) -> None:
    resp = client.post("/api/projects/", json={"name": "BindEnvTest"})
    project_id = resp.json()["id"]

    resp = client.patch(
        f"/api/projects/{project_id}/bind",
        json={"conda_env": "  ", "experiment_folder": str(tmp_path)},
    )
    assert resp.status_code == 400


def test_git_log_no_repo(client: TestClient, tmp_path: Path) -> None:
    resp = client.post("/api/projects/", json={"name": "GitLogTest"})
    project_id = resp.json()["id"]

    folder = str(tmp_path / "nogit")
    Path(folder).mkdir()
    client.patch(
        f"/api/projects/{project_id}/bind",
        json={"conda_env": "base", "experiment_folder": folder},
    )

    resp = client.get(f"/api/projects/{project_id}/runner/git/log")
    assert resp.status_code == 200
    assert resp.json() == []


def test_runner_status_no_machine(client: TestClient) -> None:
    resp = client.post("/api/projects/", json={"name": "StatusTest"})
    project_id = resp.json()["id"]

    # Ensure no machine is registered for this project (cleanup global registry)
    from alfred.state_machine.machine import unregister_machine
    unregister_machine(project_id)

    resp = client.get(f"/api/projects/{project_id}/runner/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "current_substage" in data
    assert "active_experiment_id" in data
    assert data["active_experiment_id"] is None   # no experiments yet
    assert data["current_substage"] == "idle"      # no machine registered


# ---------------------------------------------------------------------------
# Sub-step 7.2 — plotting preamble + runner helpers
# ---------------------------------------------------------------------------

def test_standard_preamble_has_required_markers() -> None:
    """STANDARD_PREAMBLE must define log_metric and plt.savefig monkey-patch."""
    from alfred.services.plotting import STANDARD_PREAMBLE
    assert "log_metric" in STANDARD_PREAMBLE
    assert "ALFRED_METRIC" in STANDARD_PREAMBLE
    assert "ALFRED_PLOT" in STANDARD_PREAMBLE
    assert "matplotlib.use" in STANDARD_PREAMBLE
    assert "ALFRED_PHASE" in STANDARD_PREAMBLE


def test_get_preamble_returns_string() -> None:
    from alfred.services.plotting import get_preamble
    preamble = get_preamble()
    assert isinstance(preamble, str)
    assert len(preamble) > 100


def test_png_to_ascii_no_pillow(tmp_path: Path) -> None:
    """png_to_ascii returns a placeholder string when Pillow is not available."""
    from unittest.mock import patch
    from alfred.services.plotting import png_to_ascii
    fake_png = tmp_path / "test.png"
    fake_png.write_bytes(b"not a real png")
    with patch.dict("sys.modules", {"PIL": None, "PIL.Image": None}):
        result = png_to_ascii(fake_png)
    assert "test.png" in result


def test_metric_regex_parses_line() -> None:
    """ALFRED_METRIC: name=value step=N is parsed by the regex."""
    import re
    from alfred.agents.runner import _METRIC_RE
    line = "ALFRED_METRIC: train_loss=0.123456 step=42"
    m = _METRIC_RE.search(line)
    assert m is not None
    name, val, step = m.groups()
    assert name == "train_loss"
    assert abs(float(val) - 0.123456) < 1e-5
    assert int(step) == 42


def test_metric_regex_ignores_unrelated_lines() -> None:
    from alfred.agents.runner import _METRIC_RE
    assert _METRIC_RE.search("2024-01-01 Training epoch 3") is None
    assert _METRIC_RE.search("loss: 0.5") is None


def test_phase_regex_parses_phases() -> None:
    from alfred.agents.runner import _PHASE_RE
    for phase in ("preprocess", "train", "eval"):
        line = f"ALFRED_PHASE: {phase}"
        m = _PHASE_RE.search(line)
        assert m is not None, f"Phase {phase} not matched"
        assert m.group(1) == phase


def test_compute_diff_new_file() -> None:
    """Diff with empty old_code shows all lines as additions."""
    from alfred.agents.runner import _compute_diff
    diff = _compute_diff("", "x = 1\ny = 2\n", "run_1.py")
    assert "+x = 1" in diff
    assert "+y = 2" in diff
    assert "a/run_1.py" in diff


def test_compute_diff_modification() -> None:
    from alfred.agents.runner import _compute_diff
    old = "x = 1\ny = 2\n"
    new = "x = 1\ny = 3\n"
    diff = _compute_diff(old, new, "run.py")
    assert "-y = 2" in diff
    assert "+y = 3" in diff


def test_script_path_modify_mode(tmp_path: Path) -> None:
    from alfred.agents.runner import _script_path_for
    path = _script_path_for(tmp_path, iteration=2, plan={"version_mode": "modify"})
    assert path == tmp_path / "run_2.py"


def test_script_path_branch_mode(tmp_path: Path) -> None:
    from alfred.agents.runner import _script_path_for
    path = _script_path_for(tmp_path, iteration=3, plan={"version_mode": "branch"})
    assert path == tmp_path / "runs" / "iter_3" / "run.py"


def test_strip_code_fences_removes_backticks() -> None:
    from alfred.agents.runner import _strip_code_fences
    code = "```python\nimport sys\nprint('hello')\n```"
    result = _strip_code_fences(code)
    assert result == "import sys\nprint('hello')"
    # No fences → unchanged
    plain = "import sys\nprint('hello')"
    assert _strip_code_fences(plain) == plain


def test_extract_dataset_uris_string() -> None:
    from alfred.agents.runner import _extract_dataset_uris
    plan = {"dataset": "hf://mnist", "objective": "train"}
    uris = _extract_dataset_uris(plan)
    assert uris == ["hf://mnist"]


def test_extract_dataset_uris_list() -> None:
    from alfred.agents.runner import _extract_dataset_uris
    plan = {"datasets": ["hf://cifar10", "https://example.com/data.csv"]}
    uris = _extract_dataset_uris(plan)
    assert "hf://cifar10" in uris
    assert "https://example.com/data.csv" in uris


# ---------------------------------------------------------------------------
# Sub-step 7.3 — error classification + fix loop helpers
# ---------------------------------------------------------------------------

def test_classify_module_not_found() -> None:
    from alfred.agents.runner import _classify_error
    tb = (
        "Traceback (most recent call last):\n"
        "  File 'run_1.py', line 3, in <module>\n"
        "    import transformers\n"
        "ModuleNotFoundError: No module named 'transformers'"
    )
    error_type, extra = _classify_error(tb)
    assert error_type == "ModuleNotFoundError"
    assert extra == "transformers"


def test_classify_cuda_oom() -> None:
    from alfred.agents.runner import _classify_error
    tb = "RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB"
    error_type, extra = _classify_error(tb)
    assert error_type == "CUDA_OOM"
    assert extra == ""


def test_classify_generic_fallback() -> None:
    from alfred.agents.runner import _classify_error
    tb = "ZeroDivisionError: division by zero"
    error_type, extra = _classify_error(tb)
    # ZeroDivisionError is not in our classified list → generic
    assert error_type == "generic"


def test_classify_file_not_found() -> None:
    from alfred.agents.runner import _classify_error
    tb = "FileNotFoundError: [Errno 2] No such file or directory: 'data/mnist.csv'"
    error_type, extra = _classify_error(tb)
    assert error_type == "FileNotFoundError"
    assert "data/mnist.csv" in extra


def test_capture_mistake_does_not_raise(tmp_path: Path) -> None:
    """_capture_mistake is fire-and-forget — must never raise."""
    from alfred.agents.runner import _capture_mistake
    # project_id=999 doesn't exist in DB — should swallow the error gracefully
    _capture_mistake(project_id=999, content="test mistake — no DB")


def test_strip_code_fences_no_fences_unchanged() -> None:
    from alfred.agents.runner import _strip_code_fences
    plain = "x = 1\nprint(x)"
    assert _strip_code_fences(plain) == plain


def test_strip_code_fences_plain_backticks() -> None:
    from alfred.agents.runner import _strip_code_fences
    code = "```\nx = 1\n```"
    assert _strip_code_fences(code) == "x = 1"


# ---------------------------------------------------------------------------
# Sub-step 7.5 — Next-iteration loop + versioning
# ---------------------------------------------------------------------------

def test_reset_run_state_clears_all_fields(tmp_path: Path) -> None:
    """_reset_run_state() zeroes every per-run accumulator."""
    from unittest.mock import MagicMock
    from alfred.agents.runner import RunnerAgent
    from alfred.models.db_models import RunPhase

    agent = RunnerAgent(
        project_id=1,
        model="test-model",
        ws_manager=MagicMock(),
        db_session=MagicMock(),
    )
    # Populate with fake data
    agent._current_phase = RunPhase.train
    agent._pending_logs = [("INFO", "line", RunPhase.train)]
    agent._pending_metrics = [("loss", 0.5, 1)]
    agent._collected_ascii = ["ascii"]
    agent._script_code = "x = 1"
    agent._script_path = tmp_path / "run.py"
    agent._active_exp_id = 42
    agent._recent_lines = ["traceback line"]

    agent._reset_run_state()

    assert agent._current_phase == RunPhase.preprocess
    assert agent._pending_logs == []
    assert agent._pending_metrics == []
    assert agent._collected_ascii == []
    assert agent._script_code == ""
    assert agent._script_path is None
    assert agent._active_exp_id is None
    assert agent._recent_lines == []


def test_propose_next_iteration_creates_experiment(tmp_path: Path) -> None:
    """_propose_next_iteration creates a new Experiment row when approved."""
    import asyncio
    import json
    from unittest.mock import AsyncMock, MagicMock, patch
    from sqlmodel import Session, SQLModel, create_engine

    # In-memory DB
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    from alfred.models.db_models import (
        Experiment, ExperimentStatus, Project, VersionMode,
    )
    from alfred.agents.runner import RunnerAgent

    with Session(engine) as s:
        project = Project(
            name="test", workspace_path=str(tmp_path),
            conda_env="base", experiment_folder=str(tmp_path),
        )
        s.add(project)
        s.commit()
        s.refresh(project)
        pid = project.id

        prev_exp = Experiment(
            project_id=pid, iteration=1, status=ExperimentStatus.done,
            plan_json=json.dumps({"objective": "test", "seed": 42}),
            version_mode=VersionMode.modify, seed=42,
        )
        s.add(prev_exp)
        s.commit()
        s.refresh(prev_exp)
        exp_id = prev_exp.id

    # Build agent with mocked WS
    ws = MagicMock()
    ws.send = AsyncMock()

    agent = RunnerAgent(
        project_id=pid, model="m", ws_manager=ws, db_session=MagicMock(),
    )

    # Mock the state machine
    mock_machine = MagicMock()
    approval_response = MagicMock()
    approval_response.approved = True
    approval_response.edited_plan = {"version_mode": "branch"}
    mock_machine.transition = AsyncMock(return_value=approval_response)
    agent._machine = mock_machine

    # Mock the LLM to return a valid proposal
    proposal_json = json.dumps({
        "changes": ["Increase LR"],
        "objective": "Improve convergence",
        "architecture": "MLP",
        "hyperparams": {"lr": 0.01},
        "dataset": "",
        "seed": 43,
        "version_mode": "modify",
        "rationale": "Loss plateaued.",
    })
    agent.client = MagicMock()
    agent.client.chat_raw = AsyncMock(return_value=proposal_json)

    with Session(engine) as s:
        prev_exp_obj = s.get(Experiment, exp_id)

    with patch("alfred.agents.runner.get_engine", return_value=engine):
        result = asyncio.run(agent._propose_next_iteration(prev_exp_obj, "good results"))

    assert result is True  # approved

    # New experiment should exist with iteration=2, version_mode=branch
    with Session(engine) as s:
        new_exp = s.exec(
            __import__("sqlmodel").select(Experiment)
            .where(Experiment.project_id == pid)
            .where(Experiment.iteration == 2)
        ).first()

    assert new_exp is not None
    assert new_exp.version_mode == VersionMode.branch
    assert new_exp.status == ExperimentStatus.planned


def test_propose_next_iteration_rejected_returns_false(tmp_path: Path) -> None:
    """_propose_next_iteration returns False and cleans up when user rejects."""
    import asyncio
    import json
    from unittest.mock import AsyncMock, MagicMock, patch
    from sqlmodel import Session, SQLModel, create_engine

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    from alfred.models.db_models import (
        Experiment, ExperimentStatus, Project, VersionMode,
    )
    from alfred.agents.runner import RunnerAgent
    from alfred.state_machine.machine import unregister_machine

    with Session(engine) as s:
        project = Project(
            name="test2", workspace_path=str(tmp_path),
            conda_env="base", experiment_folder=str(tmp_path),
        )
        s.add(project)
        s.commit()
        s.refresh(project)
        pid = project.id

        exp = Experiment(
            project_id=pid, iteration=1, status=ExperimentStatus.done,
            plan_json=json.dumps({"objective": "test", "seed": 42}),
            version_mode=VersionMode.modify, seed=42,
        )
        s.add(exp)
        s.commit()
        s.refresh(exp)
        exp_obj = exp

    ws = MagicMock()
    ws.send = AsyncMock()

    agent = RunnerAgent(
        project_id=pid, model="m", ws_manager=ws, db_session=MagicMock(),
    )

    mock_machine = MagicMock()
    approval_response = MagicMock()
    approval_response.approved = False
    mock_machine.transition = AsyncMock(return_value=approval_response)
    mock_machine.report_done = AsyncMock()
    agent._machine = mock_machine

    proposal_json = json.dumps({
        "changes": ["Increase LR"], "objective": "test", "architecture": "MLP",
        "hyperparams": {}, "dataset": "", "seed": 43,
        "version_mode": "modify", "rationale": "Testing.",
    })
    agent.client = MagicMock()
    agent.client.chat_raw = AsyncMock(return_value=proposal_json)

    with Session(engine) as s:
        exp_reload = s.get(Experiment, exp_obj.id)

    with patch("alfred.agents.runner.get_engine", return_value=engine), \
         patch("alfred.agents.runner.unregister_machine") as mock_unreg:
        result = asyncio.run(agent._propose_next_iteration(exp_reload, "ok"))

    assert result is False
    mock_machine.report_done.assert_awaited_once()
    mock_unreg.assert_called_once_with(pid)


def test_next_iter_proposal_fallback_on_json_error() -> None:
    """When the LLM returns invalid JSON, _propose_next_iteration uses a safe fallback."""
    import asyncio
    import json
    from unittest.mock import AsyncMock, MagicMock, patch
    from sqlmodel import Session, SQLModel, create_engine, select

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    from alfred.models.db_models import (
        Experiment, ExperimentStatus, Project, VersionMode,
    )
    from alfred.agents.runner import RunnerAgent
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with Session(engine) as s:
            project = Project(
                name="test3", workspace_path=tmp,
                conda_env="base", experiment_folder=tmp,
            )
            s.add(project)
            s.commit()
            s.refresh(project)
            pid = project.id

            exp = Experiment(
                project_id=pid, iteration=2, status=ExperimentStatus.done,
                plan_json=json.dumps({"objective": "test", "seed": 5}),
                version_mode=VersionMode.modify, seed=5,
            )
            s.add(exp)
            s.commit()
            s.refresh(exp)
            exp_obj_id = exp.id

        ws = MagicMock()
        ws.send = AsyncMock()

        agent = RunnerAgent(project_id=pid, model="m", ws_manager=ws, db_session=MagicMock())

        mock_machine = MagicMock()
        approval_response = MagicMock()
        approval_response.approved = True
        approval_response.edited_plan = None
        mock_machine.transition = AsyncMock(return_value=approval_response)
        agent._machine = mock_machine

        # LLM returns garbage
        agent.client = MagicMock()
        agent.client.chat_raw = AsyncMock(return_value="not valid json {{{}}")

        with Session(engine) as s:
            exp_reload = s.get(Experiment, exp_obj_id)

        with patch("alfred.agents.runner.get_engine", return_value=engine):
            result = asyncio.run(agent._propose_next_iteration(exp_reload, "ok"))

        assert result is True  # fallback proposal still approved

        # New experiment created with seed 6 (prev seed 5 + 1)
        with Session(engine) as s:
            new_exp = s.exec(
                select(Experiment)
                .where(Experiment.project_id == pid)
                .where(Experiment.iteration == 3)
            ).first()

        assert new_exp is not None
        assert new_exp.seed == 6
