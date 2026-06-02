"""
state_machine/machine.py — ExperimentStateMachine (C8).

Explicit Python enums for every stage/substage combination.
Every transition:
  1. Updates self._state / self._substage
  2. Persists snapshot to DB (Project.status field carries the serialised substage)
  3. Emits state_change + progress WS events
  4. Blocks at awaiting_approval until resolve_approval() is called

Usage (inside an agent coroutine):
    machine = ExperimentStateMachine(
        project_id=project.id,
        ws_manager=manager,
        db_session=session,
    )
    await machine.restore()                         # resume after crash
    await machine.transition(S1Sub.SWEEPING_SOURCES)
    await machine.report_progress(4, 10, "paper 4/10")
    await machine.transition(S1Sub.AWAITING_APPROVAL, plan={...})
    # ↑ blocks here until UI calls /api/experiments/{id}/approve
    await machine.transition(S1Sub.SCORING)
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from sqlmodel import Session

from alfred.models.db_models import Experiment, ExperimentStatus, Project, ProjectStage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage / substage enums — canonical names used in WS payloads and DB
# ---------------------------------------------------------------------------


class Stage(int, Enum):
    HYPOTHESIS = 1
    SETUP = 2
    RUN = 3


# Stage 1 substages
class S1Sub(str, Enum):
    GENERATING_QUERIES = "generating_queries"
    SWEEPING_SOURCES   = "sweeping_sources"
    SNOWBALLING        = "snowballing"
    WEB_SWEEP          = "web_sweep"
    ANALYZING          = "analyzing"
    SCORING            = "scoring"
    AWAITING_APPROVAL  = "awaiting_approval"
    DONE               = "done"


# Stage 2 substages
class S2Sub(str, Enum):
    PROPOSING         = "proposing"
    REFINING          = "refining"
    AWAITING_APPROVAL = "awaiting_approval"
    FINALIZED         = "finalized"


# Stage 3 substages
class S3Sub(str, Enum):
    WRITING_CODE      = "writing_code"
    AWAITING_APPROVAL = "awaiting_approval"
    SETTING_UP_DATA   = "setting_up_data"
    PREPROCESSING     = "preprocessing"
    TRAINING          = "training"
    EVALUATING        = "evaluating"
    INTERPRETING      = "interpreting"
    DIAGNOSING_ERROR  = "diagnosing_error"
    FIXING            = "fixing"
    AWAITING_NEXT     = "awaiting_next"


# Union type for substage values
AnySubstage = S1Sub | S2Sub | S3Sub

# Map stage → substage enum for serialisation/deserialisation
_SUBSTAGE_ENUMS: dict[Stage, type] = {
    Stage.HYPOTHESIS: S1Sub,
    Stage.SETUP:      S2Sub,
    Stage.RUN:        S3Sub,
}

_APPROVAL_SUBSTAGES = {
    S1Sub.AWAITING_APPROVAL,
    S2Sub.AWAITING_APPROVAL,
    S3Sub.AWAITING_APPROVAL,
    S3Sub.AWAITING_NEXT,
}


# ---------------------------------------------------------------------------
# Approval request / response dataclasses
# ---------------------------------------------------------------------------


class ApprovalRequest:
    """Sent to the UI when entering an awaiting_approval substage."""

    def __init__(self, plan: dict[str, Any], substage: str, project_id: int) -> None:
        self.plan = plan
        self.substage = substage
        self.project_id = project_id


class ApprovalResponse:
    """Returned by resolve_approval() after the user acts."""

    def __init__(
        self,
        approved: bool,
        edited_plan: dict[str, Any] | None = None,
        feedback: str = "",
    ) -> None:
        self.approved = approved
        self.edited_plan = edited_plan or {}
        self.feedback = feedback


# ---------------------------------------------------------------------------
# ExperimentStateMachine
# ---------------------------------------------------------------------------


class ExperimentStateMachine:
    """
    Drives the ALFRED pipeline through substages.

    One instance per active project; created fresh from DB snapshot on resume.
    Thread-safety: this class is used within a single asyncio event loop.
    """

    def __init__(
        self,
        project_id: int,
        ws_manager: Any,           # alfred.ws.ConnectionManager
        db_session: Session,
        auto_approve: bool = False,
    ) -> None:
        self._project_id = project_id
        self._project_id_str = str(project_id)
        self._ws = ws_manager
        self._db = db_session
        self._auto_approve = auto_approve

        # Current state
        self._stage: Stage = Stage.HYPOTHESIS
        self._substage: AnySubstage = S1Sub.GENERATING_QUERIES

        # Approval gate — set to an asyncio.Event when entering awaiting_approval.
        self._approval_event: asyncio.Event | None = None
        self._approval_response: ApprovalResponse | None = None

        # Pending plan for the current approval gate (stored for resume).
        self._pending_plan: dict[str, Any] = {}

    # ── Public properties ─────────────────────────────────────────────────

    @property
    def current_stage(self) -> Stage:
        return self._stage

    @property
    def current_substage(self) -> AnySubstage:
        return self._substage

    @property
    def is_awaiting_approval(self) -> bool:
        return self._substage in _APPROVAL_SUBSTAGES

    # ── Snapshot / restore ────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Return a serialisable dict representing the current state."""
        return {
            "stage": self._stage.value,
            "substage": self._substage.value,
            "auto_approve": self._auto_approve,
            "pending_plan": self._pending_plan,
            "ts": datetime.utcnow().isoformat(),
        }

    async def restore(self) -> bool:
        """
        Restore state from the Project row's status field (JSON blob).
        Returns True if a snapshot was found and restored, False if starting fresh.
        """
        project = self._db.get(Project, self._project_id)
        if project is None:
            return False

        try:
            data = json.loads(project.status)
            if not isinstance(data, dict) or "stage" not in data:
                return False

            stage_val = data["stage"]
            substage_val = data["substage"]

            self._stage = Stage(stage_val)
            substage_enum = _SUBSTAGE_ENUMS[self._stage]
            self._substage = substage_enum(substage_val)
            self._auto_approve = data.get("auto_approve", self._auto_approve)
            self._pending_plan = data.get("pending_plan", {})

            logger.info(
                "StateMachine restored: project=%s stage=%s substage=%s",
                self._project_id, self._stage, self._substage,
            )
            return True

        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.debug("StateMachine restore failed (fresh start): %s", exc)
            return False

    def _persist(self) -> None:
        """Write current snapshot to Project.status in DB."""
        project = self._db.get(Project, self._project_id)
        if project is None:
            return
        project.status = json.dumps(self.snapshot())
        project.updated_at = datetime.utcnow()
        self._db.add(project)
        self._db.commit()

    # ── Transition ────────────────────────────────────────────────────────

    async def transition(
        self,
        new_substage: AnySubstage,
        *,
        plan: dict[str, Any] | None = None,
        label: str = "",
        stage: Stage | None = None,
    ) -> ApprovalResponse | None:
        """
        Transition to a new substage (optionally a new stage).

        If transitioning to an awaiting_approval substage:
          - Emits approval_request WS event with plan
          - Blocks until resolve_approval() is called (or auto-approves)
          - Returns the ApprovalResponse

        Otherwise returns None.
        """
        if stage is not None:
            self._stage = stage

        self._substage = new_substage
        self._pending_plan = plan or {}

        # Persist to DB on every transition.
        self._persist()

        # Emit state_change event.
        await self._ws.send(
            self._project_id_str,
            "state_change",
            {
                "stage": self._stage.value,
                "substage": self._substage.value,
                "label": label or self._substage.value.replace("_", " "),
            },
        )

        # Emit progress event so the strip updates.
        await self._ws.broadcast_progress(
            self._project_id_str,
            stage=self._stage.value,
            substage=self._substage.value,
            label=label or self._substage.value.replace("_", " "),
            current=0,
            total=0,
            status="running",
        )

        logger.info(
            "StateMachine transition: project=%s stage=%s substage=%s",
            self._project_id, self._stage, self._substage,
        )

        # Handle approval gate.
        if new_substage in _APPROVAL_SUBSTAGES:
            return await self._handle_approval_gate(plan or {})

        return None

    # ── Approval gate ─────────────────────────────────────────────────────

    async def _handle_approval_gate(self, plan: dict[str, Any]) -> ApprovalResponse:
        """
        Emit approval_request and either auto-approve or block until resolved.

        experiment_id is hoisted from the plan dict to the top-level of the
        WS payload so the frontend can read it as payload.experiment_id.
        """
        # Hoist experiment_id to the top level of the WS payload.
        experiment_id = plan.get("experiment_id")

        payload: dict[str, Any] = {
            "stage": self._stage.value,
            "substage": self._substage.value,
            "plan": plan,
            "auto_approve": self._auto_approve,
        }
        if experiment_id is not None:
            payload["experiment_id"] = experiment_id

        await self._ws.send(
            self._project_id_str,
            "approval_request",
            payload,
        )

        if self._auto_approve:
            response = ApprovalResponse(approved=True, edited_plan=plan)
            logger.info(
                "Auto-approved: project=%s substage=%s", self._project_id, self._substage
            )
            return response

        # Block until resolve_approval() is called from the REST endpoint.
        self._approval_event = asyncio.Event()
        self._approval_response = None

        logger.info(
            "Blocking at approval gate: project=%s substage=%s",
            self._project_id, self._substage,
        )
        await self._approval_event.wait()

        response = self._approval_response
        self._approval_event = None
        self._approval_response = None

        if response is None:
            # Fallback — should not happen.
            response = ApprovalResponse(approved=False, feedback="Timed out")

        logger.info(
            "Approval resolved: project=%s approved=%s",
            self._project_id, response.approved,
        )
        return response

    def resolve_approval(
        self,
        approved: bool,
        edited_plan: dict[str, Any] | None = None,
        feedback: str = "",
    ) -> None:
        """
        Called from the REST endpoint to unblock the approval gate.
        Must be called from the same event loop as the machine.
        """
        if self._approval_event is None:
            logger.warning(
                "resolve_approval called but no gate is active: project=%s",
                self._project_id,
            )
            return

        self._approval_response = ApprovalResponse(
            approved=approved,
            edited_plan=edited_plan,
            feedback=feedback,
        )
        self._approval_event.set()

    # ── Progress reporting ────────────────────────────────────────────────

    async def report_progress(
        self,
        current: int,
        total: int,
        label: str,
        *,
        status: str = "running",
    ) -> None:
        """
        Emit a progress event from inside a long substage loop.
        Agents call this to update the tqdm-style bar without transitioning.
        """
        await self._ws.broadcast_progress(
            self._project_id_str,
            stage=self._stage.value,
            substage=self._substage.value,
            label=label,
            current=current,
            total=total,
            status=status,
        )

    async def report_done(self, summary: str = "") -> None:
        """Mark the pipeline as done (final substage completed)."""
        await self._ws.broadcast_progress(
            self._project_id_str,
            stage=self._stage.value,
            substage=self._substage.value,
            label=summary or "Complete",
            current=1,
            total=1,
            status="done",
        )
        await self._ws.broadcast_done(self._project_id_str, summary=summary)

    async def report_error(self, message: str, remediation: str = "") -> None:
        """Emit an error event and mark the strip as errored."""
        await self._ws.broadcast_progress(
            self._project_id_str,
            stage=self._stage.value,
            substage=self._substage.value,
            label=f"Error: {message}",
            current=0,
            total=0,
            status="error",
        )
        await self._ws.broadcast_error(self._project_id_str, message, remediation)

    # ── Stage advancement ─────────────────────────────────────────────────

    async def advance_to_stage(self, new_stage: Stage) -> None:
        """
        Advance the project to a new top-level stage.
        Updates Project.current_stage in DB and transitions to the first substage.
        """
        project = self._db.get(Project, self._project_id)
        if project is None:
            return

        stage_map: dict[Stage, ProjectStage] = {
            Stage.HYPOTHESIS: ProjectStage.hypothesis,
            Stage.SETUP: ProjectStage.setup,
            Stage.RUN: ProjectStage.run,
        }
        project.current_stage = stage_map[new_stage]
        project.updated_at = datetime.utcnow()
        self._db.add(project)
        self._db.commit()

        first_sub_map: dict[Stage, AnySubstage] = {
            Stage.HYPOTHESIS: S1Sub.GENERATING_QUERIES,
            Stage.SETUP:      S2Sub.PROPOSING,
            Stage.RUN:        S3Sub.WRITING_CODE,
        }
        await self.transition(first_sub_map[new_stage], stage=new_stage)

    # ── Auto-approve toggle ───────────────────────────────────────────────

    def set_auto_approve(self, value: bool) -> None:
        self._auto_approve = value
        self._persist()


# ---------------------------------------------------------------------------
# Machine registry — one machine per active project (in-process singleton)
# ---------------------------------------------------------------------------

_machines: dict[int, ExperimentStateMachine] = {}


def get_machine(project_id: int) -> ExperimentStateMachine | None:
    """Return the live machine for a project, or None."""
    return _machines.get(project_id)


def register_machine(project_id: int, machine: ExperimentStateMachine) -> None:
    """Register a machine so approval endpoints can reach it."""
    _machines[project_id] = machine


def unregister_machine(project_id: int) -> None:
    """Remove a machine when a project closes."""
    _machines.pop(project_id, None)