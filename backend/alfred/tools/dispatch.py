"""
Tool dispatch engine — Stage 4.

At decision points, the agent:
 1. Receives the list of enabled tool schemas.
 2. Makes a structured LLM call via chat_raw(): "do I need a tool? which one?
    with what input? or answer directly?"
 3. Calls the tool, feeds result back, continues or calls again.
 4. Caps iterations at MAX_TOOL_ITERATIONS.
 5. Logs every ToolCall to DB and emits a `tool_call` WS event.

Usage:
    dispatcher = ToolDispatcher(ws_manager, project_id, session, llm_client)
    final_text, tool_results = await dispatcher.run(messages, context)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlmodel import Session

from alfred.tools.base import ToolRegistry, ToolResult

if TYPE_CHECKING:
    from alfred.ws import ConnectionManager
    from alfred.agents.base import LLMClient

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 6   # cap to avoid runaway loops


# ---------------------------------------------------------------------------
# Decision schema (what we ask the LLM to return)
# ---------------------------------------------------------------------------

_DECISION_SYSTEM = """\
You are a research assistant deciding whether to use a tool.

You will be given:
- The user's current request / context
- A list of available tools with their descriptions

Respond ONLY with a JSON object (no markdown fences, no explanation):

If you should call a tool:
{"action":"tool","tool_name":"<exact tool name>","input":{<tool input fields>},"reason":"<one sentence why>"}

If you can answer directly without a tool:
{"action":"answer","reason":"<one sentence why no tool is needed>"}

Be conservative: only use a tool when it adds real value (e.g. you need current
literature, web results, or real paper data you don't have in context).
For general conversation or questions you can answer from context, choose "answer".
"""


def _build_tool_system(tools_schema: list[dict[str, Any]]) -> str:
    tools_text = json.dumps(tools_schema, indent=2)
    return f"{_DECISION_SYSTEM}\n\nAvailable tools:\n{tools_text}"


def _strip_json_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` fences from LLM output."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Drop first line (``` or ```json) and last line (```) if present
        inner_lines = lines[1:]
        if inner_lines and inner_lines[-1].strip() == "```":
            inner_lines = inner_lines[:-1]
        text = "\n".join(inner_lines).strip()
    return text


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

class ToolDispatcher:
    """Orchestrates the tool-use loop for one agent turn."""

    def __init__(
        self,
        ws_manager: "ConnectionManager",
        project_id: int,
        session: Session,
        llm_client: "LLMClient",
    ) -> None:
        self.ws = ws_manager
        self.project_id = project_id
        self.project_id_str = str(project_id)
        self.session = session
        self.llm = llm_client
        self.registry = ToolRegistry.get()

    async def run(
        self,
        messages: list[dict[str, Any]],
        extra_context: str = "",
        max_iterations: int = MAX_TOOL_ITERATIONS,
    ) -> tuple[str, list[ToolResult]]:
        """
        Run the tool decision-and-execution loop.

        Returns:
            (final_answer_text, list_of_tool_results_used)
        """
        enabled_schemas = self.registry.to_schema_list(only_enabled=True)
        if not enabled_schemas:
            # No tools available — answer directly
            return await self._direct_answer(messages, extra_context), []

        tool_results_collected: list[ToolResult] = []

        # Build the running message list; prepend context if provided
        running_messages = list(messages)
        if extra_context:
            running_messages = [
                {"role": "system", "content": extra_context},
                *running_messages,
            ]

        for iteration in range(max_iterations):
            # Step 1: ask the LLM what to do (tool or direct answer)
            decision = await self._decide(running_messages, enabled_schemas)

            if decision.get("action") != "tool":
                # LLM chose to answer directly — exit the loop
                logger.debug("Tool dispatcher: direct answer after %d iterations", iteration)
                break

            tool_name: str = decision.get("tool_name", "")
            tool_input: dict[str, Any] = decision.get("input", {})
            reason: str = decision.get("reason", "")

            tool = self.registry.get_tool(tool_name)
            if tool is None or not tool.enabled:
                logger.warning("LLM requested unknown/disabled tool: %s", tool_name)
                running_messages.append({
                    "role": "user",
                    "content": (
                        f"Tool '{tool_name}' is not available. "
                        "Please answer directly with what you know."
                    ),
                })
                continue

            # Step 2: emit tool_call WS event (transparency — user sees it)
            await self._emit_tool_call_event(tool_name, tool_input, reason, status="running")

            # Step 3: execute the tool
            result = await tool.execute(tool_input)
            tool_results_collected.append(result)

            # Step 4: persist to DB
            await self._persist_tool_call(tool_name, tool_input, result)

            # Step 5: emit result event so Tools panel updates
            await self._emit_tool_call_event(
                tool_name, tool_input, reason,
                status="done" if result.success else "error",
                result=result,
            )

            # Step 6: feed result back into the conversation so LLM can use it
            result_text = self._format_result_for_llm(tool_name, result)
            running_messages.append({
                "role": "user",
                "content": (
                    f"Tool result for {tool_name}:\n{result_text}\n\n"
                    "Continue your response using this information."
                ),
            })

        # Final answer — use the full running_messages (includes tool results)
        answer = await self._direct_answer(running_messages, "")
        return answer, tool_results_collected

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    async def _decide(
        self,
        messages: list[dict[str, Any]],
        tools_schema: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Ask the LLM to decide: call a tool or answer directly.
        Returns a parsed dict; falls back to {"action": "answer"} on any error.
        """
        system = _build_tool_system(tools_schema)
        try:
            raw = await self.llm.chat_raw(
                system_prompt=system,
                messages=messages,
                stream=False,
            )
            clean = _strip_json_fences(raw)
            parsed = json.loads(clean)
            if "action" not in parsed:
                raise ValueError("Missing 'action' key in LLM response")
            return parsed
        except (json.JSONDecodeError, ValueError, Exception) as exc:
            logger.warning(
                "Tool decision parse failed (%s) — defaulting to direct answer. "
                "Raw response: %.200r",
                exc,
                raw if "raw" in dir() else "<no response>",
            )
            return {"action": "answer", "reason": "parse failure"}

    async def _direct_answer(
        self,
        messages: list[dict[str, Any]],
        extra_context: str,
    ) -> str:
        """
        Get a plain conversational answer from the LLM.
        This streams tokens to the WS (visible to user).
        """
        full_messages = list(messages)
        if extra_context:
            full_messages = [
                {"role": "system", "content": extra_context},
                *full_messages,
            ]

        # Use chat_raw with empty system so we don't double-prepend a role
        # The caller's messages already contain the right context.
        # We DO want to stream tokens to WS here (this is the user-visible response).
        from alfred.services.ollama import stream_chat
        return await stream_chat(
            self.llm.model,
            full_messages,
            project_id=self.project_id_str,
            message_id="",
            ws_manager=self.ws,
            options=self.llm.options or None,
        )

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def _format_result_for_llm(self, tool_name: str, result: ToolResult) -> str:
        """Format a ToolResult into a concise string for LLM consumption."""
        if not result.success:
            return f"ERROR calling {tool_name}: {result.error}"

        data = result.data
        if isinstance(data, list):
            lines = []
            for item in data[:20]:   # cap to avoid blowing context
                if isinstance(item, dict):
                    title = item.get("title", item.get("url", str(item)))
                    year = item.get("year") or item.get("publication_year", "")
                    tldr = (
                        item.get("tldr")
                        or item.get("snippet")
                        or item.get("abstract", "")
                    )
                    venue = item.get("venue", "")
                    line = f"[{year}] {title}"
                    if venue:
                        line += f" ({venue})"
                    if tldr:
                        line += f" — {str(tldr)[:200]}"
                    lines.append(line)
                else:
                    lines.append(str(item)[:200])
            return "\n".join(lines) if lines else "(no results)"

        if isinstance(data, dict):
            return json.dumps(data, indent=2)[:2000]

        return str(data)[:2000]

    # ------------------------------------------------------------------
    # WS event emission
    # ------------------------------------------------------------------

    async def _emit_tool_call_event(
        self,
        tool_name: str,
        tool_input: dict,
        reason: str,
        *,
        status: str = "running",
        result: ToolResult | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "tool_name": tool_name,
            "input": tool_input,
            "reason": reason,
            "status": status,
        }
        if result is not None:
            payload["success"] = result.success
            payload["sources"] = result.sources
            payload["error"] = result.error
            payload["result_count"] = (
                len(result.data) if isinstance(result.data, list) else 1
            )
        await self.ws.send(
            self.project_id_str,
            "tool_call",
            payload,
        )

    # ------------------------------------------------------------------
    # DB persistence
    # ------------------------------------------------------------------

    async def _persist_tool_call(
        self,
        tool_name: str,
        tool_input: dict,
        result: ToolResult,
    ) -> None:
        try:
            from alfred.models.db_models import ToolCall

            call = ToolCall(
                project_id=self.project_id,
                tool_name=tool_name,
                input_json=json.dumps(tool_input),
                output_summary=(
                    f"{len(result.data)} results"
                    if isinstance(result.data, list)
                    else (result.error or "done")
                ),
                created_at=datetime.utcnow(),
            )
            self.session.add(call)
            self.session.commit()
        except Exception as exc:
            logger.warning("Failed to persist tool call to DB: %s", exc)