"""
services/conda.py — Sandboxed conda environment executor (Stage 7.1).

All experiment code is executed exclusively through this service.
The executor:
  - Uses `conda run -n <env> --no-capture-output` for live stdout streaming
  - Sets PYTHONUNBUFFERED=1 to defeat Python's output buffering
  - Calls on_line_cb for each output line (for WS streaming + parsing)
  - Returns the subprocess exit code

Conda env jail: every subprocess call is pinned to the project's named env.
Never calls bare `python` or touches any other environment.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from alfred.utils.paths import assert_within

logger = logging.getLogger(__name__)

# Type alias for the per-line callback passed to run_script
OnLineCb = Callable[[str], Coroutine[Any, Any, None]]


class CondaError(Exception):
    """Raised when a conda operation fails unrecoverably."""


class CondaExecutor:
    """
    Executes Python scripts inside a named conda environment.

    Every script path is jail-checked against `experiment_folder` before
    execution.  Output streams live through `on_line_cb` as each line arrives.
    """

    def __init__(
        self,
        conda_env: str,
        experiment_folder: Path,
    ) -> None:
        self.conda_env = conda_env
        self.experiment_folder = experiment_folder

    # ── Public API ─────────────────────────────────────────────────────────

    async def run_script(
        self,
        script_path: Path,
        on_line_cb: OnLineCb,
    ) -> int:
        """
        Execute `script_path` inside the conda env.

        Streams stdout + stderr (merged) line-by-line through `on_line_cb`.
        Returns the subprocess exit code (0 = success).
        """
        # Env-jail: script must live inside the experiment folder
        assert_within(self.experiment_folder, script_path)

        conda_exe = self._conda_exe()
        cmd = [
            conda_exe, "run",
            "-n", self.conda_env,
            "--no-capture-output",   # don't buffer; stream directly to stdout
            "python", str(script_path),
        ]
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}

        logger.info(
            "CondaExecutor.run_script: env=%s script=%s",
            self.conda_env, script_path.name,
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,   # merge stderr into stdout
            cwd=str(self.experiment_folder),
            env=env,
        )

        if proc.stdout:
            async for raw in proc.stdout:
                try:
                    line = raw.decode("utf-8", errors="replace").rstrip("\n")
                except Exception:
                    line = repr(raw)
                try:
                    await on_line_cb(line)
                except Exception as exc:
                    logger.warning("on_line_cb raised: %s", exc)

        await proc.wait()
        return proc.returncode if proc.returncode is not None else 0

    async def install_package(self, package: str) -> int:
        """
        Install `package` into the project conda env.

        Tries `conda install` first; falls back to `pip install` inside the env.
        Returns exit code (0 = success).
        """
        conda_exe = self._conda_exe()
        logger.info("Installing %s via conda in env %s", package, self.conda_env)

        # Attempt 1: conda install
        proc = await asyncio.create_subprocess_exec(
            conda_exe, "install", "-n", self.conda_env,
            package, "-y", "--quiet",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        await proc.communicate()
        if proc.returncode == 0:
            logger.info("conda install %s succeeded", package)
            return 0

        # Attempt 2: pip install inside the env
        logger.info("conda install %s failed; falling back to pip", package)
        proc2 = await asyncio.create_subprocess_exec(
            conda_exe, "run", "-n", self.conda_env,
            "--no-capture-output",
            "pip", "install", package, "--quiet",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        await proc2.communicate()
        return proc2.returncode if proc2.returncode is not None else 0

    async def snapshot_env(self, dest: Path) -> Path:
        """
        Export the conda env spec to `dest` (a YAML file).
        Returns `dest`.  Logs a warning on failure but does not raise.
        """
        conda_exe = self._conda_exe()
        dest.parent.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            conda_exe, "env", "export",
            "-n", self.conda_env,
            "--file", str(dest),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(
                "conda env export failed for %s: %s",
                self.conda_env,
                err.decode(errors="replace") if err else "",
            )
        return dest

    # ── Internal helpers ───────────────────────────────────────────────────

    @staticmethod
    def _conda_exe() -> str:
        found = shutil.which("conda")
        if found:
            return found
        # Common locations when `conda` isn't on PATH
        for candidate in [
            os.path.expanduser("~/miniconda3/bin/conda"),
            os.path.expanduser("~/anaconda3/bin/conda"),
            "/opt/conda/bin/conda",
        ]:
            if os.path.isfile(candidate):
                return candidate
        return "conda"   # let the subprocess raise a clear error if not found
