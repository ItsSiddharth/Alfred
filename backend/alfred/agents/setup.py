"""
agents/setup.py — Stage-2 Experiment Setup agent.

Multi-turn collaborative dialogue that produces an approvable experiment plan.

Flow:
  - Each user message in 'setup' stage calls generate_turn().
  - Turns 1–3 are purely conversational (COLLABORATOR role).
  - Turn 4+ also silently checks (via chat_raw) if enough info exists to
    propose a plan.  If yes, produces a structured plan dict and transitions
    the state machine to AWAITING_APPROVAL.
  - After approval, the plan is stored in Experiment.plan_json and the stage
    advances to 'run'.

The caller (_handle_chat_setup in main.py) is responsible for:
  - Creating the assistant placeholder message row before calling generate_turn.
  - Persisting the returned response text to that row after the call.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlmodel import Session, select

from alfred.agents.base import Role, make_client
from alfred.memory.context import build_memory_block
from alfred.models.db_models import (
    Experiment,
    ExperimentStatus,
    Message,
    MessageRole,
)
from alfred.state_machine.machine import (
    S2Sub,
    Stage,
    ExperimentStateMachine,
    get_machine,
    register_machine,
    unregister_machine,
)

logger = logging.getLogger(__name__)

# Minimum turns of conversation before plan proposal is considered
MIN_TURNS_BEFORE_PROPOSAL = 3

# ALFRED's proactive suggestion marker in plan JSON
ALFREDS_SUGGESTION_KEY = "alfreds_suggestions"

_COLLABORATOR_EXTRA = """
You are helping a researcher design an ML experiment. Your responsibilities:
- Gather: objective, dataset (toy first, then scale), model architecture, baselines, metrics, success criteria.
- Ask clarifying questions one at a time — don't overwhelm.
- Proactively suggest improvements (label them clearly as "ALFRED suggests: ...").
- Never override the user's stated decisions.
- Toy-first: always propose a small-scale version before a full-scale experiment.

When you have gathered enough information across the conversation to propose a concrete plan,
you will do so via a separate mechanism — just focus on the dialogue for now.
"""

_PLAN_CHECK_SYSTEM = """
You review a conversation between a user and ALFRED about designing an ML experiment.
Determine if enough information has been gathered to propose a concrete plan.

Required fields: objective, toy_dataset, scale_dataset, architectures, baselines, metrics, success_criteria.

If ready, output EXACTLY this JSON (no other text, no markdown):
{
  "ready": true,
  "objective": "...",
  "toy_dataset": "...",
  "scale_dataset": "...",
  "architectures": ["..."],
  "baselines": ["..."],
  "metrics": ["..."],
  "success_criteria": "...",
  "first_iteration_spec": "...",
  "alfreds_suggestions": ["..."]
}

If NOT ready, output EXACTLY:
{"ready": false}
"""


class SetupAgent:
    """
    Multi-turn collaborative setup agent.

    One instance is created per chat turn.  State is persisted in the DB
    (Message rows + Experiment.plan_json) so the instance does not need to
    be long-lived.
    """

    def __init__(
        self,
        project_id: int,
        model: str,
        ws_manager: Any,
        db_session: Session,
        auto_approve: bool = False,
    ) -> None:
        self.project_id = project_id
        self.pid_str = str(project_id)
        self.model = model
        self.ws = ws_manager
        self.session = db_session
        self.auto_approve = auto_approve

        self.client = make_client(
            model, project_id=self.pid_str, ws_manager=ws_manager
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    async def generate_turn(
        self,
        user_message: str,
        asst_msg_id: int | None = None,
        memory_block: str = "",
    ) -> tuple[str, dict | None]:
        """
        Generate one dialogue turn.

        Returns:
            (response_text, plan_dict or None)

        plan_dict is non-None when the agent is ready to propose a plan.
        The caller should:
          1. Update the assistant message row with response_text.
          2. If plan_dict, create/update the Experiment row and call
             machine.transition(S2Sub.AWAITING_APPROVAL, plan=plan_dict).
        """
        machine = self._get_machine()

        # Transition state machine for this turn
        if machine.current_substage not in (
            S2Sub.REFINING, S2Sub.AWAITING_APPROVAL, S2Sub.FINALIZED
        ):
            await machine.transition(S2Sub.PROPOSING, label="Discussing experiment design")

        # Build conversation history for the LLM
        history = self._load_history()
        turn_count = sum(1 for m in history if m.role == MessageRole.assistant)

        messages: list[dict] = []
        for msg in history:
            if msg.role in (MessageRole.user, MessageRole.assistant):
                messages.append({
                    "role": msg.role.value,
                    "content": msg.content,
                })
        messages.append({"role": "user", "content": user_message})

        # Stream conversational response
        response = await self.client.chat(
            Role.COLLABORATOR,
            messages,
            message_id=str(asst_msg_id or ""),
            extra_system=(memory_block + "\n\n" + _COLLABORATOR_EXTRA).strip(),
        )

        # After turn 3+, check if plan is ready
        plan: dict | None = None
        if turn_count >= MIN_TURNS_BEFORE_PROPOSAL:
            plan = await self._check_plan_ready(
                messages + [{"role": "assistant", "content": response}]
            )

        if plan is not None:
            await machine.transition(S2Sub.REFINING, label="Finalising experiment plan")

        return response, plan

    async def handle_approved_plan(self, plan: dict) -> None:
        """
        Called after the approval gate resolves successfully.
        Stores the plan in the Experiment row and advances the stage.
        """
        machine = self._get_machine()
        exp_id = self._get_or_create_experiment()

        exp = self.session.get(Experiment, exp_id)
        if exp:
            exp.plan_json = json.dumps(plan)
            self.session.add(exp)
            self.session.commit()

        await machine.transition(S2Sub.FINALIZED, label="Plan approved")
        # Advance to run stage and unregister machine
        await machine.advance_to_stage(Stage.RUN)
        unregister_machine(self.project_id)

        await self.ws.send(self.pid_str, "log", {
            "message": "Experiment plan approved. Advancing to run stage.",
            "phase": "setup",
        })

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _get_machine(self) -> ExperimentStateMachine:
        """Return the registered machine or create and register a new one."""
        m = get_machine(self.project_id)
        if m is not None:
            return m
        m = ExperimentStateMachine(
            project_id=self.project_id,
            ws_manager=self.ws,
            db_session=self.session,
            auto_approve=self.auto_approve,
        )
        m.current_stage = Stage.SETUP
        m.current_substage = S2Sub.PROPOSING
        register_machine(self.project_id, m)
        return m

    def _load_history(self) -> list[Message]:
        """Load all setup-stage messages for this project in order."""
        rows = self.session.exec(
            select(Message)
            .where(Message.project_id == self.project_id)
            .order_by(Message.created_at.asc())  # type: ignore[arg-type]
        ).all()
        # Only return user/assistant messages (not system/tool)
        return [m for m in rows if m.role in (MessageRole.user, MessageRole.assistant)]

    def _get_or_create_experiment(self) -> int:
        """Return iteration-1 experiment ID, creating if needed."""
        existing = self.session.exec(
            select(Experiment).where(
                Experiment.project_id == self.project_id,
                Experiment.iteration == 1,
            )
        ).first()
        if existing is not None:
            return existing.id  # type: ignore[return-value]

        exp = Experiment(
            project_id=self.project_id,
            iteration=1,
            seed=42,
            plan_json="{}",
            status=ExperimentStatus.planned,
        )
        self.session.add(exp)
        self.session.commit()
        self.session.refresh(exp)
        return exp.id  # type: ignore[return-value]

    async def _check_plan_ready(
        self, messages: list[dict]
    ) -> dict | None:
        """
        Ask the LLM (silently) if enough info exists to propose a plan.
        Returns a plan dict if ready, None otherwise.
        """
        raw = await self.client.chat_raw(
            system_prompt=_PLAN_CHECK_SYSTEM,
            messages=messages + [
                {
                    "role": "user",
                    "content": (
                        "Based on the conversation above, are you ready to propose "
                        "a complete experiment plan? Output your JSON decision."
                    ),
                }
            ],
        )
        try:
            data = json.loads(_strip_fences(raw))
            if data.get("ready"):
                plan = {k: v for k, v in data.items() if k != "ready"}
                return plan
        except Exception:
            pass
        return None


def _strip_fences(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    inner: list[str] = []
    for line in lines[1:]:
        if line.strip() == "```":
            break
        inner.append(line)
    return "\n".join(inner).strip()
