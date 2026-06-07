"""
Config & workspace management.

On first run the backend has no config.json and returns status=needs_setup.
After the user picks a workspace directory via the frontend, POST /api/config/setup
creates the workspace structure and writes config.json.

Config is intentionally a plain JSON file (not a dotenv or system file) so that
it lives entirely within user-writable space and is portable.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# The config.json sits next to the binary / in the repo root when developing.
# We resolve relative to this file's parent so imports work from anywhere.
_APP_DIR = Path(__file__).resolve().parent.parent.parent  # repo root
_CONFIG_FILE = _APP_DIR / "alfred_config.json"


class AlfredConfig(BaseModel):
    workspace_path: str
    default_model: str = ""
    auto_approve_default: bool = False
    telemetry_opt_in: bool = False
    dataset_cache_path: str = ""  # defaults to <workspace>/datasets if empty
    research_num_queries: int = 1  # number of search queries per hypothesis run (1 = fast test, 5 = thorough)
    max_fix_attempts: int = 3     # max automatic error-fix retries per experiment run

    @property
    def workspace(self) -> Path:
        return Path(self.workspace_path).expanduser().resolve()

    @property
    def logs_dir(self) -> Path:
        return self.workspace / "logs"

    @property
    def projects_dir(self) -> Path:
        return self.workspace / "projects"

    @property
    def datasets_dir(self) -> Path:
        if self.dataset_cache_path:
            return Path(self.dataset_cache_path).expanduser().resolve()
        return self.workspace / "datasets"

    @property
    def db_path(self) -> Path:
        return self.workspace / "db.sqlite"


# Module-level singleton — populated by load_config() on startup.
_config: Optional[AlfredConfig] = None


def is_configured() -> bool:
    """Return True if a valid config.json exists and workspace is accessible."""
    return _CONFIG_FILE.exists()


def load_config() -> Optional[AlfredConfig]:
    """
    Load config from disk.  Returns None if not yet configured so callers can
    return needs_setup without crashing.
    """
    global _config
    if not _CONFIG_FILE.exists():
        return None
    try:
        data = json.loads(_CONFIG_FILE.read_text())
        _config = AlfredConfig(**data)
        return _config
    except Exception as exc:
        logger.warning("Could not parse config file: %s", exc)
        return None


def get_config() -> AlfredConfig:
    """
    Return the loaded config.  Raises RuntimeError if not yet configured.
    Call load_config() on startup before using this.
    """
    if _config is None:
        raise RuntimeError("ALFRED is not configured yet. Call load_config() first.")
    return _config


def setup_workspace(workspace_path: str) -> AlfredConfig:
    """
    Called once during first-run setup.  Creates the workspace directories and
    writes config.json.  No admin rights required — everything under a
    user-chosen path.
    """
    workspace = Path(workspace_path).expanduser().resolve()

    # Refuse to write to system directories as an extra hard-rule guard.
    forbidden_prefixes = ["/etc", "/usr", "/bin", "/sbin", "/lib", "/sys", "/proc"]
    for prefix in forbidden_prefixes:
        if str(workspace).startswith(prefix):
            raise ValueError(
                f"Workspace may not be inside a system directory ({prefix}). "
                f"Choose a path under your home directory."
            )

    # Create workspace subdirectories.
    for subdir in ("logs", "projects", "datasets"):
        (workspace / subdir).mkdir(parents=True, exist_ok=True)
    logger.info("Workspace directories created at %s", workspace)

    cfg = AlfredConfig(workspace_path=str(workspace))

    # Persist to disk.
    _CONFIG_FILE.write_text(json.dumps(cfg.model_dump(), indent=2))
    logger.info("Config written to %s", _CONFIG_FILE)

    global _config
    _config = cfg
    return cfg


def update_config(**kwargs: object) -> AlfredConfig:
    """Patch individual fields and persist.  Used by the settings screen."""
    cfg = get_config()
    updated = AlfredConfig(**{**cfg.model_dump(), **kwargs})
    _CONFIG_FILE.write_text(json.dumps(updated.model_dump(), indent=2))
    global _config
    _config = updated
    return updated


def setup_logging(cfg: AlfredConfig) -> None:
    """Configure stdlib logging to write to <workspace>/logs/alfred.log."""
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = cfg.logs_dir / "alfred.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),
        ],
    )
    logger.info("Logging initialised → %s", log_file)