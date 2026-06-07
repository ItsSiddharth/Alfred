"""
state_machine/machine.py — ExperimentStateMachine (C8).

Explicit Python enums for every stage/substage combination.
Every transition:
  1. Updates self._stage / self._substage
  2. Persists snapshot to DB (Project.status field carries the serialised substage)
  3. Emits state_change + progress WS events
  4. Blocks at awaiting_approval until resolve_approval() is called

Machine registry functions (all public):
  get_machine(project_id)             → ExperimentStateMachine | None
  register_machine(project_id, machine)
  unregister_machine(project_id)      — canonical name
  get_or_create_machine(project_id, **kwargs) → ExperimentStateMachine
  remove_machine(project_id)          — alias for unregister_machine
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
# Approval request / response
# ---------------------------------------------------------------------------


class ApprovalRequest:
    def __init__(self, plan: dict[str, Any], substage: str, project_id: int) -> None:
        self.plan = plan
        self.substage = substage
        self.project_id = project_id


class ApprovalResponse:
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
    Thread-safety: used within a single asyncio event loop.
    """

    def __init__(
        self,
        project_id: int,
        ws_manager: Any,
        db_session: Session,
        auto_approve: bool = False,
    ) -> None:
        self._project_id = project_id
        self._project_id_str = str(project_id)
        self._ws = ws_manager
        self._db = db_session
        self._auto_approve = auto_approve

        self._stage: Stage = Stage.HYPOTHESIS
        self._substage: AnySubstage = S1Sub.GENERATING_QUERIES

        self._approval_event: asyncio.Event | None = None
        self._approval_response: ApprovalResponse | None = None
        self._pending_plan: dict[str, Any] = {}

    # ── Public properties ──────────────────────────────────────────────

    @property
    def current_stage(self) -> Stage:
        return self._stage

    @current_stage.setter
    def current_stage(self, value: Stage) -> None:
        self._stage = value

    @property
    def current_substage(self) -> AnySubstage:
        return self._substage

    @current_substage.setter
    def current_substage(self, value: AnySubstage) -> None:
        self._substage = value

    @property
    def is_awaiting_approval(self) -> bool:
        return self._substage in _APPROVAL_SUBSTAGES

    # ── Snapshot / restore ─────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        return {
            "stage": self._stage.value,
            "substage": self._substage.value,
            "auto_approve": self._auto_approve,
            "pending_plan": self._pending_plan,
            "ts": datetime.utcnow().isoformat(),
        }

    async def restore(self) -> bool:
        project = self._db.get(Project, self._project_id)
        if project is None:
            return False
        try:
            data = json.loads(project.status)
            if not isinstance(data, dict) or "stage" not in data:
                return False
            self._stage = Stage(data["stage"])
            substage_enum = _SUBSTAGE_ENUMS[self._stage]
            self._substage = substage_enum(data["substage"])
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
        project = self._db.get(Project, self._project_id)
        if project is None:
            return
        project.status = json.dumps(self.snapshot())
        project.updated_at = datetime.utcnow()
        self._db.add(project)
        self._db.commit()

    # ── Transition ─────────────────────────────────────────────────────

    async def transition(
        self,
        new_substage: AnySubstage,
        *,
        plan: dict[str, Any] | None = None,
        label: str = "",
        stage: Stage | None = None,
    ) -> ApprovalResponse | None:
        if stage is not None:
            self._stage = stage

        self._substage = new_substage
        self._pending_plan = plan or {}

        self._persist()

        await self._ws.send(
            self._project_id_str,
            "state_change",
            {
                "stage": self._stage.value,
                "substage": self._substage.value,
                "label": label or self._substage.value.replace("_", " "),
            },
        )
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

        if new_substage in _APPROVAL_SUBSTAGES:
            return await self._handle_approval_gate(plan or {})
        return None

    # ── Approval gate ──────────────────────────────────────────────────

    async def _handle_approval_gate(self, plan: dict[str, Any]) -> ApprovalResponse:
        experiment_id = plan.get("experiment_id")
        payload: dict[str, Any] = {
            "stage": self._stage.value,
            "substage": self._substage.value,
            "plan": plan,
            "auto_approve": self._auto_approve,
        }
        if experiment_id is not None:
            payload["experiment_id"] = experiment_id

        await self._ws.send(self._project_id_str, "approval_request", payload)

        if self._auto_approve:
            logger.info(
                "Auto-approved: project=%s substage=%s", self._project_id, self._substage
            )
            return ApprovalResponse(approved=True, edited_plan=plan)

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

    # ── Backward-compat: wait_for_approval (used in some tests) ────────

    async def wait_for_approval(self) -> dict[str, Any]:
        """
        Await the current approval gate and return a dict with 'approved' and
        'feedback' keys.  Provided for test compatibility.
        """
        if self._approval_event is None:
            return {"approved": False, "feedback": "no gate active"}
        await self._approval_event.wait()
        resp = self._approval_response
        if resp is None:
            return {"approved": False, "feedback": ""}
        return {"approved": resp.approved, "feedback": resp.feedback}

    # ── Progress reporting ─────────────────────────────────────────────

    async def report_progress(
        self,
        current: int,
        total: int,
        label: str,
        *,
        status: str = "running",
    ) -> None:
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

    # ── Stage advancement ──────────────────────────────────────────────

    async def advance_to_stage(self, new_stage: Stage) -> None:
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

        # Update internal state directly — do NOT call self.transition() here.
        # transition() would emit state_change/progress WS events that make the
        # frontend show "writing code" (S3Sub.WRITING_CODE) immediately after plan
        # approval, confusing the user into thinking something is already running.
        first_sub_map: dict[Stage, AnySubstage] = {
            Stage.HYPOTHESIS: S1Sub.GENERATING_QUERIES,
            Stage.SETUP:      S2Sub.PROPOSING,
            Stage.RUN:        S3Sub.AWAITING_NEXT,  # idle — user must ask to run
        }
        self._stage = new_stage
        self._substage = first_sub_map[new_stage]
        self._persist()

        # Signal frontend: project stage changed → sidebar should reload binding panel
        await self._ws.send(
            self._project_id_str,
            "stage_advance",
            {"new_stage": stage_map[new_stage].value},
        )
        # Reset progress strip to idle
        await self._ws.broadcast_progress(
            self._project_id_str,
            stage=new_stage.value,
            substage="idle",
            label="Ready",
            current=0,
            total=0,
            status="idle",
        )

    # ── Auto-approve toggle ────────────────────────────────────────────

    def set_auto_approve(self, value: bool) -> None:
        self._auto_approve = value
        self._persist()


# ---------------------------------------------------------------------------
# Machine registry — one machine per active project
# ---------------------------------------------------------------------------

_machines: dict[int, ExperimentStateMachine] = {}


def get_machine(project_id: int) -> ExperimentStateMachine | None:
    """Return the live machine for a project, or None if not registered."""
    return _machines.get(project_id)


def register_machine(project_id: int, machine: ExperimentStateMachine) -> None:
    """Register a machine so approval endpoints can reach it."""
    _machines[project_id] = machine


def unregister_machine(project_id: int) -> None:
    """Remove a machine when a project's pipeline finishes or is interrupted."""
    _machines.pop(project_id, None)


def get_or_create_machine(
    project_id: int,
    *,
    ws_manager: Any = None,
    db_session: Session | None = None,
    auto_approve: bool = False,
) -> ExperimentStateMachine:
    """
    Return the existing registered machine for project_id, or create and
    register a new one with the provided arguments.

    When called without ws_manager/db_session (e.g. from tests), a minimal
    machine is created with placeholder values — callers are responsible for
    providing real sessions before using the machine for actual pipeline work.
    """
    existing = _machines.get(project_id)
    if existing is not None:
        return existing

    # Need a fallback for ws_manager and db_session in test contexts
    if ws_manager is None:
        # Create a no-op ws_manager stub
        class _NoOpWS:
            async def send(self, *a: Any, **kw: Any) -> None: ...
            async def broadcast_progress(self, *a: Any, **kw: Any) -> None: ...
            async def broadcast_done(self, *a: Any, **kw: Any) -> None: ...
            async def broadcast_error(self, *a: Any, **kw: Any) -> None: ...
        ws_manager = _NoOpWS()

    if db_session is None:
        # Try to get a real session from the global engine
        try:
            from alfred.db import get_engine
            db_session = Session(get_engine())
        except Exception:
            # Last resort: create a MagicMock-compatible stub for tests
            from unittest.mock import MagicMock
            db_session = MagicMock()  # type: ignore[assignment]

    machine = ExperimentStateMachine(
        project_id=project_id,
        ws_manager=ws_manager,
        db_session=db_session,
        auto_approve=auto_approve,
    )
    _machines[project_id] = machine
    return machine


def remove_machine(project_id: int) -> None:
    """Alias for unregister_machine — provided for test compatibility."""
    unregister_machine(project_id)