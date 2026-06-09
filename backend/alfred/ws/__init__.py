"""
WebSocket connection manager.

Maintains a map of project_id -> active WebSocket connections.
All messages are JSON envelopes per C7:
  { "type": "<event_type>", "ts": "<iso8601>", "payload": { ... } }

Canonical event types (never rename): token, progress, log, plan,
approval_request, tool_call, result, error, state_change, plot, done.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_envelope(event_type: str, payload: dict[str, Any]) -> str:
    """Serialise a canonical WS envelope to JSON string."""
    return json.dumps({"type": event_type, "ts": _now_iso(), "payload": payload})


class ConnectionManager:
    """
    Manages one WebSocket connection per project.

    A new connection for a project_id replaces the previous one -- this handles
    page refreshes cleanly.
    """

    def __init__(self) -> None:
        # project_id (str) -> WebSocket
        self._connections: dict[str, WebSocket] = {}
        self._lock = asyncio.Lock()

    async def connect(self, project_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            old = self._connections.get(project_id)
            if old is not None:
                try:
                    await old.close()
                except Exception:
                    pass
            self._connections[project_id] = websocket
        logger.info("WS connected: project_id=%s", project_id)

    async def disconnect(self, project_id: str) -> None:
        async with self._lock:
            self._connections.pop(project_id, None)
        logger.info("WS disconnected: project_id=%s", project_id)

    async def send(
        self, project_id: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        """Send a typed event envelope to the given project's WebSocket."""
        ws = self._connections.get(project_id)
        if ws is None:
            logger.debug("No WS connection for project_id=%s, dropping event.", project_id)
            return
        try:
            await ws.send_text(make_envelope(event_type, payload))
        except Exception as exc:
            logger.warning("WS send failed for project_id=%s: %s", project_id, exc)
            await self.disconnect(project_id)

    async def broadcast_progress(
        self,
        project_id: str,
        stage: int,
        substage: str,
        label: str,
        current: int,
        total: int,
        status: str = "running",
        model: str = "",
    ) -> None:
        """Convenience wrapper for the canonical progress payload shape (C7)."""
        payload: dict = {
            "stage": stage,
            "substage": substage,
            "label": label,
            "current": current,
            "total": total,
            "status": status,
        }
        if model:
            payload["model"] = model
        await self.send(project_id, "progress", payload)

    async def broadcast_token(self, project_id: str, token: str, message_id: str = "") -> None:
        """Stream a single LLM token."""
        await self.send(project_id, "token", {"token": token, "message_id": message_id})

    async def broadcast_error(
        self, project_id: str, human_message: str, remediation: str = ""
    ) -> None:
        """Send a user-facing error (never raw stack traces)."""
        await self.send(
            project_id,
            "error",
            {"message": human_message, "remediation": remediation},
        )

    async def broadcast_done(self, project_id: str, summary: str = "") -> None:
        await self.send(project_id, "done", {"summary": summary})

    def has_connection(self, project_id: str) -> bool:
        return project_id in self._connections

    async def start_heartbeat(self, interval: float = 30.0) -> None:
        """
        Background task: ping every connected project every `interval` seconds.
        Dead connections are cleaned up automatically when the send fails.
        Call once from the FastAPI lifespan.
        """
        while True:
            await asyncio.sleep(interval)
            for pid in list(self._connections.keys()):
                await self.send(pid, "ping", {})


# Module-level singleton shared by all routers and agents.
manager = ConnectionManager()