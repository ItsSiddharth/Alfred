"""
services/git_service.py — Git versioning for experiment folders (Stage 7.1).

One git repository is initialised per experiment folder on first run.
Every successful experiment run produces a commit with a structured message:
  exp <N>: <summary> | metrics | seed=<seed> | env-hash=<hash[:8]>

Rollback is intentionally destructive (git reset --hard) and should only be
called after explicit user confirmation in the UI.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_GITIGNORE = """\
__pycache__/
*.pyc
*.pyo
*.pyd
.ipynb_checkpoints/
*.egg-info/
.env
*.log
"""


class GitError(Exception):
    """Raised when a git command fails with a non-zero exit code."""


class GitService:
    """Manages a git repository rooted at `experiment_folder`."""

    def __init__(self, experiment_folder: Path) -> None:
        self.experiment_folder = experiment_folder

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def init(self) -> None:
        """Initialise git repo if one doesn't already exist."""
        if (self.experiment_folder / ".git").exists():
            return

        self.experiment_folder.mkdir(parents=True, exist_ok=True)

        # Prefer `--initial-branch=main`; older git versions may not support it
        result = self._run(["git", "init", "--initial-branch=main"])
        if result.returncode != 0:
            self._run_check(["git", "init"])

        # Write .gitignore
        gitignore = self.experiment_folder / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(_GITIGNORE)

        # Initial empty commit so rollback always has a valid base
        self._run(["git", "add", ".gitignore"])
        self._run([
            "git", "-c", "user.email=alfred@local",
            "-c", "user.name=ALFRED",
            "commit", "--allow-empty",
            "-m", "init: alfred experiment repo",
        ])
        logger.info("Git repo initialised in %s", self.experiment_folder)

    # ── Commit ─────────────────────────────────────────────────────────────

    def commit(self, message: str) -> str:
        """
        Stage all changes and create a commit.
        Returns the new full commit hash (40 hex chars), or '' on failure.
        """
        self._run(["git", "add", "-A"])
        self._run([
            "git", "-c", "user.email=alfred@local",
            "-c", "user.name=ALFRED",
            "commit", "--allow-empty",
            "-m", message,
        ])
        result = self._run(["git", "rev-parse", "HEAD"])
        commit_hash = result.stdout.strip() if result.returncode == 0 else ""
        if commit_hash:
            logger.info("Git commit: %s — %s", commit_hash[:8], message[:70])
        return commit_hash

    # ── Log ────────────────────────────────────────────────────────────────

    def log(self, n: int = 30) -> list[dict]:
        """Return the last `n` commits as a list of dicts."""
        result = self._run([
            "git", "log",
            "--format=%H|%h|%s|%ai",
            f"-{n}",
        ])
        if result.returncode != 0 or not result.stdout.strip():
            return []

        entries: list[dict] = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                entries.append({
                    "hash": parts[0],
                    "short_hash": parts[1],
                    "message": parts[2],
                    "date": parts[3],
                })
        return entries

    # ── Rollback ───────────────────────────────────────────────────────────

    def rollback(self, commit_hash: str) -> None:
        """
        Hard-reset the working tree to `commit_hash`.

        DESTRUCTIVE — caller must obtain user confirmation in the UI
        before calling this method.
        """
        if not self._is_valid_hash(commit_hash):
            raise GitError(f"Invalid commit hash: {commit_hash!r}")

        result = self._run(["git", "reset", "--hard", commit_hash])
        if result.returncode != 0:
            raise GitError(
                f"git reset --hard {commit_hash[:8]} failed:\n{result.stderr.strip()}"
            )
        logger.info("Git rollback: reset to %s", commit_hash[:8])

    # ── Helpers ────────────────────────────────────────────────────────────

    def get_current_hash(self) -> str:
        """Return HEAD commit hash, or '' if no commits yet."""
        result = self._run(["git", "rev-parse", "HEAD"])
        return result.stdout.strip() if result.returncode == 0 else ""

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd,
            cwd=str(self.experiment_folder),
            capture_output=True,
            text=True,
            check=False,
        )

    def _run_check(self, cmd: list[str]) -> subprocess.CompletedProcess:
        result = self._run(cmd)
        if result.returncode != 0:
            raise GitError(
                f"git command failed: {' '.join(cmd)}\n{result.stderr.strip()}"
            )
        return result

    @staticmethod
    def _is_valid_hash(h: str) -> bool:
        """Basic sanity check: non-empty, hex chars, length >= 7."""
        if not h or len(h) < 7:
            return False
        return all(c in "0123456789abcdefABCDEF" for c in h)
