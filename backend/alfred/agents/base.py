"""
agents/base.py — Role-prompting infrastructure.

Defines the Role registry (researcher / collaborator / coder / fixer /
interpreter / critic) and the LLMClient that prepends the correct system
prompt before forwarding to Ollama.

Usage:
    client = LLMClient(model="qwen2.5:7b", project_id="1", ws_manager=manager)
    response = await client.chat(
        role=Role.RESEARCHER,
        messages=[{"role": "user", "content": "..."}],
        message_id="msg-42",
    )

The system prompt for the chosen role is automatically prepended; callers
should NOT include their own system message in `messages`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from alfred.services.ollama import OllamaError, stream_chat

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Role definitions
# ---------------------------------------------------------------------------


class Role(str, Enum):
    """Named personas used by ALFRED's agents."""

    RESEARCHER = "researcher"
    COLLABORATOR = "collaborator"
    CODER = "coder"
    FIXER = "fixer"
    INTERPRETER = "interpreter"
    CRITIC = "critic"


# System prompt templates for each role.
# These are prepended to every conversation as the "system" message.
_SYSTEM_PROMPTS: dict[Role, str] = {
    Role.RESEARCHER: """\
You are ALFRED's researcher persona — a rigorous, methodical ML research assistant.

Your responsibilities:
- Survey existing literature with precision and intellectual honesty.
- Identify genuinely novel contributions vs. incremental ones.
- Summarise papers accurately; never fabricate citations.
- Flag when a hypothesis is already solved, even if that's disappointing.
- Calibrate confidence: distinguish "likely true from the literature" from "I don't know".

Tone: concise, precise, academically grounded. Sentence case. No fluff.
""",
    Role.COLLABORATOR: """\
You are ALFRED's collaborator persona — a thoughtful, creative ML research partner.

Your responsibilities:
- Help the user design experiments that are scientifically valid and tractable.
- Always propose a toy-first progression before scaling up.
- Suggest alternatives and trade-offs clearly, labelling which ideas are yours vs. the user's.
- Never railroad the user — their decisions override your suggestions.
- Push for explicit success criteria and baselines before any code is written.

Tone: warm, direct, practical. Think out loud. Sentence case.
""",
    Role.CODER: """\
You are ALFRED's coder persona — a careful, logging-heavy Python ML engineer.

Your responsibilities:
- Write clean, well-typed Python for ML experiments.
- Every generated script MUST have dense logging: data load, preprocess, every train step with running metrics, eval.
- Always save matplotlib plots to the experiment folder.
- Follow the logging+plotting preamble already injected by the runner.
- Prefer explicit over clever; research code must be reproducible and debuggable.
- Never skip type hints. Always set random seeds.

Output format: full Python scripts only — no explanatory prose, no partial snippets.
""",
    Role.FIXER: """\
You are ALFRED's fixer persona — a debugger who diagnoses and corrects experiment failures.

Your responsibilities:
- Diagnose the root cause from the traceback and logs.
- Propose the minimal correct fix — do not refactor unrelated code.
- For ModuleNotFoundError: recommend installing into the project conda env only.
- Always explain what caused the error in one clear sentence before showing the fix.
- Show the fix as a diff or replacement block, never the whole file unless necessary.

Tone: clinical, precise, no hedging.
""",
    Role.INTERPRETER: """\
You are ALFRED's interpreter persona — an analyst who reads experiment results.

Your responsibilities:
- Read logs, metrics, and ASCII plot data to write plain-language interpretations.
- State what worked, what didn't, and why (with evidence from the logs).
- Propose concrete next steps grounded in the results.
- Flag anomalies (loss spikes, stagnation, overfit) explicitly.
- Never speculate beyond what the data shows — say "the data suggests" not "the model learned".

Tone: clear, evidence-based, actionable. Sentence case.
""",
    Role.CRITIC: """\
You are ALFRED's critic persona — a memory curator and quality gatekeeper.

Your responsibilities:
- Distil raw memory items into compact, deduplicated, token-efficient Markdown.
- Group related items; remove redundancy; preserve all distinct facts.
- When reviewing outputs, be ruthlessly honest about quality, gaps, and risks.
- Score calibration: never inflate novelty or publishability to please the user.

Output format: structured Markdown only, no preamble.
""",
}


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------


@dataclass
class LLMClient:
    """
    Thin wrapper around Ollama that handles role-based system prompting.

    Attributes:
        model:       Ollama model tag (e.g. "qwen2.5:7b")
        project_id:  Used to route WS token events
        ws_manager:  ConnectionManager instance (or None for silent mode)
        options:     Ollama generation options (temperature, num_ctx, …)
    """

    model: str
    project_id: str = ""
    ws_manager: Any = None  # alfred.ws.ConnectionManager — avoid circular import
    options: dict = field(default_factory=dict)

    async def chat(
        self,
        role: Role,
        messages: list[dict[str, str]],
        *,
        message_id: str = "",
        extra_system: str = "",
    ) -> str:
        """
        Stream a chat completion for the given role.

        Prepends the role's system prompt as the first message.
        If `extra_system` is provided (e.g. injected memory block), it is
        appended to the system prompt separated by a blank line.

        Returns the full assistant response text.
        Raises OllamaError on failure.
        """
        system_content = _SYSTEM_PROMPTS[role]
        if extra_system:
            system_content = system_content.rstrip() + "\n\n" + extra_system.strip()

        full_messages = [
            {"role": "system", "content": system_content},
            *messages,
        ]

        logger.debug(
            "LLMClient.chat role=%s model=%s messages=%d",
            role.value,
            self.model,
            len(full_messages),
        )

        return await stream_chat(
            self.model,
            full_messages,
            project_id=self.project_id,
            message_id=message_id,
            ws_manager=self.ws_manager,
            options=self.options or None,
        )

    async def chat_silent(
        self,
        role: Role,
        messages: list[dict[str, str]],
        *,
        extra_system: str = "",
    ) -> str:
        """
        Same as chat() but never emits WS events.
        Useful for internal tool-use calls.
        """
        system_content = _SYSTEM_PROMPTS[role]
        if extra_system:
            system_content = system_content.rstrip() + "\n\n" + extra_system.strip()

        full_messages = [
            {"role": "system", "content": system_content},
            *messages,
        ]

        return await stream_chat(
            self.model,
            full_messages,
            project_id="",     # suppresses WS routing
            message_id="",
            ws_manager=None,   # suppresses token broadcast
            options=self.options or None,
        )

    def with_model(self, model: str) -> "LLMClient":
        """Return a new client with a different model (immutable-style)."""
        return LLMClient(
            model=model,
            project_id=self.project_id,
            ws_manager=self.ws_manager,
            options=self.options,
        )


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def make_client(
    model: str,
    *,
    project_id: str = "",
    ws_manager: Any = None,
    temperature: float = 0.3,
    num_ctx: int = 8192,
) -> LLMClient:
    """
    Create an LLMClient with sensible research-task defaults.

    temperature=0.3 balances creativity and consistency for research tasks.
    num_ctx=8192 is a safe default; increase for long-context models.
    """
    return LLMClient(
        model=model,
        project_id=project_id,
        ws_manager=ws_manager,
        options={"temperature": temperature, "num_ctx": num_ctx},
    )