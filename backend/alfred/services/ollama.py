"""
services/ollama.py — Ollama HTTP API wrapper.

Responsibilities:
- Health check (structured "not available" if Ollama isn't running)
- List local models
- Pull a model with streaming progress → WS progress events
- Delete a model
- Streaming generate / chat with role system prompt → WS token events

Ollama base URL: http://localhost:11434  (configurable via OLLAMA_HOST env var)

All methods degrade gracefully — they never crash the FastAPI process.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import AsyncIterator

import httpx

logger = logging.getLogger(__name__)

OLLAMA_BASE = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0)
# Seconds to wait for the very first token before declaring the model frozen
_FIRST_TOKEN_TIMEOUT = 60.0


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


async def health_check() -> dict:
    """
    Return {"available": True, "models": [...]} or
    {"available": False, "guidance": "<install hint>"}.
    Never raises.
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
            r = await client.get(f"{OLLAMA_BASE}/api/tags")
            r.raise_for_status()
            data = r.json()
            model_names = [m["name"] for m in data.get("models", [])]
            return {"available": True, "models": model_names}
    except httpx.ConnectError:
        return {
            "available": False,
            "guidance": (
                "Ollama is not running. "
                "Install from https://ollama.com and run `ollama serve` in a terminal."
            ),
        }
    except Exception as exc:
        logger.warning("Ollama health check failed: %s", exc)
        return {
            "available": False,
            "guidance": f"Ollama unavailable: {exc}",
        }


# ---------------------------------------------------------------------------
# Model management
# ---------------------------------------------------------------------------


async def list_local_models() -> list[dict]:
    """
    Return list of locally available models.
    Each dict: {name, size_bytes, modified_at, digest, details}.
    Returns [] if Ollama unavailable.
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(f"{OLLAMA_BASE}/api/tags")
            r.raise_for_status()
            return r.json().get("models", [])
    except Exception as exc:
        logger.warning("list_local_models failed: %s", exc)
        return []


async def pull_model(
    model_name: str,
    project_id: str,
    *,
    ws_manager=None,
) -> None:
    """
    Pull a model from the Ollama registry.

    Streams progress events via ws_manager (if provided):
      WS progress events: stage=0, substage="pulling", label="<status>", current, total

    Raises OllamaError on failure.
    """
    logger.info("Pulling model: %s", model_name)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=3600.0, write=30.0, pool=5.0)) as client:
            async with client.stream(
                "POST",
                f"{OLLAMA_BASE}/api/pull",
                json={"name": model_name, "stream": True},
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    import json as _json
                    try:
                        event = _json.loads(line)
                    except Exception:
                        continue

                    status: str = event.get("status", "")
                    completed: int = event.get("completed", 0)
                    total: int = event.get("total", 0)
                    digest: str = event.get("digest", "")

                    label = status
                    if digest:
                        short = digest[-12:] if len(digest) > 12 else digest
                        label = f"{status} [{short}]"

                    if ws_manager and project_id:
                        await ws_manager.broadcast_progress(
                            project_id,
                            stage=0,
                            substage="pulling",
                            label=label,
                            current=completed,
                            total=total if total else 0,
                            status="running",
                            model=model_name,
                        )

                    if event.get("status") == "success":
                        logger.info("Model pull complete: %s", model_name)
                        if ws_manager and project_id:
                            await ws_manager.broadcast_progress(
                                project_id,
                                stage=0,
                                substage="pulling",
                                label=f"Pull complete: {model_name}",
                                current=1,
                                total=1,
                                status="done",
                                model=model_name,
                            )
                        return

    except httpx.HTTPStatusError as exc:
        raise OllamaError(f"Pull failed ({exc.response.status_code}): {model_name}") from exc
    except httpx.ConnectError as exc:
        raise OllamaError("Ollama is not running. Run `ollama serve`.") from exc
    except Exception as exc:
        raise OllamaError(f"Pull failed: {exc}") from exc


async def delete_model(model_name: str) -> None:
    """Delete a locally pulled model. Raises OllamaError on failure."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.request(
                "DELETE",
                f"{OLLAMA_BASE}/api/delete",
                json={"name": model_name},
            )
            r.raise_for_status()
            logger.info("Deleted model: %s", model_name)
    except Exception as exc:
        raise OllamaError(f"Delete failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Model keepalive — prevents Ollama from unloading the model during long runs
# ---------------------------------------------------------------------------


async def keepalive_model(model: str, keep_alive: str = "10m") -> None:
    """
    Send a no-op generate request to keep the model loaded in Ollama memory.
    Use keep_alive="10m" to extend the idle timeout.  Silent on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            await client.post(
                f"{OLLAMA_BASE}/api/generate",
                json={"model": model, "prompt": "", "keep_alive": keep_alive},
            )
    except Exception:
        pass  # keepalive is best-effort


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


async def stream_chat(
    model: str,
    messages: list[dict],
    *,
    project_id: str = "",
    message_id: str = "",
    ws_manager=None,
    options: dict | None = None,
) -> str:
    """
    Stream a chat completion from Ollama.

    messages: list of {"role": "user"|"assistant"|"system", "content": str}
    Tokens are broadcast as WS "token" events AND collected into full_text.
    Returns the complete assistant response as a string.
    Raises OllamaError on failure.
    """
    import asyncio
    import json as _json

    full_text = ""
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    if options:
        payload["options"] = options

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream(
                "POST",
                f"{OLLAMA_BASE}/api/chat",
                json=payload,
            ) as response:
                response.raise_for_status()
                first_token = True
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = _json.loads(line)
                    except Exception:
                        continue

                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        first_token = False
                        full_text += token
                        if ws_manager and project_id:
                            await ws_manager.broadcast_token(
                                project_id, token, message_id=message_id
                            )

                    if chunk.get("done"):
                        break

        return full_text

    except httpx.ConnectError as exc:
        raise OllamaError("Ollama is not running. Run `ollama serve`.") from exc
    except httpx.HTTPStatusError as exc:
        raise OllamaError(
            f"Ollama returned {exc.response.status_code}. Is the model pulled?"
        ) from exc
    except asyncio.TimeoutError as exc:
        raise OllamaError(
            "Ollama stopped responding — the model may have been unloaded. "
            "Check `ollama ps` and try reloading the model."
        ) from exc
    except Exception as exc:
        raise OllamaError(f"Chat stream failed: {exc}") from exc


async def stream_generate(
    model: str,
    prompt: str,
    *,
    project_id: str = "",
    message_id: str = "",
    ws_manager=None,
    system: str = "",
    options: dict | None = None,
) -> str:
    """
    Stream a raw generate (non-chat) completion.
    Useful for role-less one-shot prompts. Returns full text.
    """
    full_text = ""
    payload: dict = {
        "model": model,
        "prompt": prompt,
        "stream": True,
    }
    if system:
        payload["system"] = system
    if options:
        payload["options"] = options

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream(
                "POST",
                f"{OLLAMA_BASE}/api/generate",
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    import json as _json
                    try:
                        chunk = _json.loads(line)
                    except Exception:
                        continue

                    token = chunk.get("response", "")
                    if token:
                        full_text += token
                        if ws_manager and project_id:
                            await ws_manager.broadcast_token(
                                project_id, token, message_id=message_id
                            )

                    if chunk.get("done"):
                        break

        return full_text

    except httpx.ConnectError as exc:
        raise OllamaError("Ollama is not running. Run `ollama serve`.") from exc
    except Exception as exc:
        raise OllamaError(f"Generate stream failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


async def stream_tokens_iter(
    model: str,
    messages: list[dict],
    options: dict | None = None,
) -> AsyncIterator[str]:
    """
    Async generator that yields raw tokens from Ollama chat without broadcasting.
    Used by LLMClient.chat_log_stream to surface internal LLM reasoning in the
    Show Work log panel.
    """
    import json as _json

    payload: dict = {"model": model, "messages": messages, "stream": True}
    if options:
        payload["options"] = options
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream(
                "POST", f"{OLLAMA_BASE}/api/chat", json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = _json.loads(line)
                    except Exception:
                        continue
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        yield token
                    if chunk.get("done"):
                        break
    except Exception as exc:
        raise OllamaError(f"Token stream failed: {exc}") from exc


class OllamaError(Exception):
    """Raised for any Ollama API failure.  Always has a human-readable message."""