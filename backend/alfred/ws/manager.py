"""
WebSocket ConnectionManager.

Maintains active WebSocket connections keyed by project_id.
All WS messages are JSON envelopes: { "type", "ts", "payload" }
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Singleton — one instance shared across all WS routes."""

    def __init__(self) -> None:
        # project_id → list of active WebSocket connections
        self._connections: dict[int, list[WebSocket]] = defaultdict(list)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def connect(self, project_id: int, ws: WebSocket) -> None:
        await ws.accept()
        self._connections[project_id].append(ws)
        logger.info("WS connected project=%s total=%s", project_id, len(self._connections[project_id]))

    def disconnect(self, project_id: int, ws: WebSocket) -> None:
        conns = self._connections.get(project_id, [])
        if ws in conns:
            conns.remove(ws)
        logger.info("WS disconnected project=%s remaining=%s", project_id, len(conns))

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #

    async def send_to_project(self, project_id: int, envelope: dict) -> None:
        """Send a JSON envelope to all connections for a project."""
        if "ts" not in envelope:
            envelope["ts"] = datetime.utcnow().isoformat()

        dead: list[WebSocket] = []
        for ws in list(self._connections.get(project_id, [])):
            try:
                await ws.send_text(json.dumps(envelope))
            except Exception as exc:
                logger.warning("WS send failed (project=%s): %s", project_id, exc)
                dead.append(ws)

        for ws in dead:
            self.disconnect(project_id, ws)

    # Convenience wrappers for canonical event types

    async def send_token(self, project_id: int, token: str, msg_id: int | None = None) -> None:
        await self.send_to_project(project_id, {
            "type": "token",
            "payload": {"token": token, "msg_id": msg_id},
        })

    async def send_progress(
        self,
        project_id: int,
        stage: int,
        substage: str,
        label: str,
        current: int,
        total: int,
        status: str = "running",
    ) -> None:
        await self.send_to_project(project_id, {
            "type": "progress",
            "payload": {
                "stage": stage,
                "substage": substage,
                "label": label,
                "current": current,
                "total": total,
                "status": status,
            },
        })

    async def send_error(self, project_id: int, message: str, hint: str = "") -> None:
        await self.send_to_project(project_id, {
            "type": "error",
            "payload": {"message": message, "hint": hint},
        })

    async def send_done(self, project_id: int, msg_id: int | None = None) -> None:
        await self.send_to_project(project_id, {
            "type": "done",
            "payload": {"msg_id": msg_id},
        })

    async def send_log(self, project_id: int, level: str, message: str) -> None:
        await self.send_to_project(project_id, {
            "type": "log",
            "payload": {"level": level, "message": message},
        })


# Global singleton
manager = ConnectionManager()