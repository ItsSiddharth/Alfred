"""
/api/config router.

Handles first-run detection, workspace setup, and config reads/updates.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from alfred.config import (
    AlfredConfig,
    get_config,
    is_configured,
    setup_workspace,
    update_config,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/config", tags=["config"])


class SetupRequest(BaseModel):
    workspace_path: str


class UpdateConfigRequest(BaseModel):
    default_model: str | None = None
    auto_approve_default: bool | None = None
    telemetry_opt_in: bool | None = None
    dataset_cache_path: str | None = None


@router.get("/status")
async def get_status() -> dict:
    """
    Return current configuration status.

    Frontend polls this on load to decide whether to show the first-run screen.
    """
    if not is_configured():
        return {"status": "needs_setup", "default_workspace": "~/alfred-workspace"}
    cfg = get_config()
    return {
        "status": "configured",
        "workspace_path": cfg.workspace_path,
        "default_model": cfg.default_model,
        "auto_approve_default": cfg.auto_approve_default,
        "telemetry_opt_in": cfg.telemetry_opt_in,
        "dataset_cache_path": cfg.dataset_cache_path,
    }


@router.post("/setup")
async def setup(req: SetupRequest) -> dict:
    """
    First-run endpoint: create workspace directories and write config.json.
    Called once by the frontend first-run screen.
    """
    if not req.workspace_path.strip():
        raise HTTPException(status_code=400, detail="workspace_path must not be empty")
    try:
        cfg = setup_workspace(req.workspace_path)
        logger.info("Workspace set up at %s", cfg.workspace_path)
        return {
            "status": "configured",
            "workspace_path": cfg.workspace_path,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("setup_workspace failed")
        raise HTTPException(status_code=500, detail=f"Setup failed: {exc}") from exc


@router.patch("/")
async def patch_config(req: UpdateConfigRequest) -> dict:
    """Update individual config fields.  Used by the settings screen (Stage 9)."""
    if not is_configured():
        raise HTTPException(status_code=400, detail="Not configured yet")
    kwargs = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        cfg = update_config(**kwargs)
        return {"status": "ok", "workspace_path": cfg.workspace_path}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc