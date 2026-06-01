"""
api/models_router.py — /api/models endpoints.

Endpoints:
  GET  /api/models/hardware          — detected GPU/VRAM/RAM info
  GET  /api/models/health            — Ollama availability check
  GET  /api/models/local             — locally pulled models
  GET  /api/models/recommended       — catalog ranked by VRAM fit
  POST /api/models/pull              — pull a model (streams WS progress)
  DELETE /api/models/{model_name}    — delete a local model
"""

from __future__ import annotations

import asyncio
import logging
import urllib.parse

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from alfred.services.gpu import detect_hardware, get_recommended_models
from alfred.services.ollama import OllamaError, delete_model, health_check, list_local_models, pull_model
from alfred.ws import manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/models", tags=["models"])

# Cache hardware info — detection is slightly expensive and doesn't change.
_hw_cache: dict | None = None


def _get_hw() -> dict:
    global _hw_cache
    if _hw_cache is None:
        hw = detect_hardware()
        _hw_cache = hw.to_dict()
        # Attach the raw object for internal use.
        _hw_cache["_hw_obj"] = hw
    return _hw_cache


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class PullRequest(BaseModel):
    model_name: str
    project_id: str = "global"   # which WS channel receives progress events


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/hardware")
async def get_hardware() -> dict:
    """
    Return detected hardware info.

    Response fields:
      backend       — "cuda" | "metal" | "cpu"
      gpu_name      — display name
      total_vram_mb, free_vram_mb, total_vram_gb, free_vram_gb
      total_ram_mb, total_ram_gb
      cpu_count
    """
    hw_dict = _get_hw()
    return {k: v for k, v in hw_dict.items() if not k.startswith("_")}


@router.get("/health")
async def get_ollama_health() -> dict:
    """
    Check if Ollama is running and return available local model names.

    Response:
      available — bool
      models    — list[str]  (only when available=true)
      guidance  — str        (only when available=false)
    """
    return await health_check()


@router.get("/local")
async def get_local_models() -> dict:
    """
    Return full metadata for all locally pulled models.

    Response: { models: [ {name, size_bytes, modified_at, digest, details}, ... ] }
    """
    models = await list_local_models()
    return {"models": models}


@router.get("/recommended")
async def get_recommended() -> dict:
    """
    Return the curated model catalog annotated with VRAM fit for this machine.

    Each entry includes:
      ollama_tag, display_name, family, params_b, quant_bits,
      context_k, description, strengths, required_vram_mb,
      required_vram_gb, fit ("fits" | "tight" | "too_large")

    Sorted best-fit first.
    """
    hw_dict = _get_hw()
    hw_obj = hw_dict["_hw_obj"]
    recommended = get_recommended_models(hw_obj)
    return {
        "hardware": {k: v for k, v in hw_dict.items() if not k.startswith("_")},
        "models": recommended,
    }


@router.post("/pull")
async def pull(req: PullRequest, background_tasks: BackgroundTasks) -> dict:
    """
    Start pulling a model from the Ollama registry.

    Pull runs in the background; progress is streamed as WS `progress` events
    on the channel identified by `project_id`.

    Returns immediately with {"status": "pulling", "model": "<name>"}.
    """
    if not req.model_name.strip():
        raise HTTPException(status_code=400, detail="model_name must not be empty")

    async def _do_pull() -> None:
        try:
            await pull_model(req.model_name, req.project_id, ws_manager=manager)
            # Notify the frontend the pull finished.
            await manager.send(
                req.project_id,
                "result",
                {
                    "kind": "model_pulled",
                    "model": req.model_name,
                    "message": f"Model '{req.model_name}' is ready.",
                },
            )
        except OllamaError as exc:
            await manager.broadcast_error(
                req.project_id,
                human_message=str(exc),
                remediation="Check that Ollama is running and the model name is correct.",
            )
        except Exception as exc:
            logger.exception("Unexpected error pulling model %s", req.model_name)
            await manager.broadcast_error(
                req.project_id,
                human_message=f"Pull failed unexpectedly: {exc}",
                remediation="Check the ALFRED logs for details.",
            )

    background_tasks.add_task(_do_pull)
    return {"status": "pulling", "model": req.model_name}


@router.delete("/{model_name:path}")
async def remove_model(model_name: str) -> dict:
    """
    Delete a locally pulled model.

    model_name is path-encoded to handle tags like "qwen2.5:7b".
    Returns {"status": "deleted", "model": "<name>"} on success.
    """
    decoded = urllib.parse.unquote(model_name)
    if not decoded.strip():
        raise HTTPException(status_code=400, detail="model_name must not be empty")
    try:
        await delete_model(decoded)
        # Invalidate hardware cache so free VRAM is refreshed.
        global _hw_cache
        _hw_cache = None
        return {"status": "deleted", "model": decoded}
    except OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc