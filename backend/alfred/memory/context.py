"""
memory/context.py — Build the memory block injected into every agent prompt.

Strategy:
1. Try to use the compiled doc (fast path).
2. If the compiled doc is stale or absent, fall back to inline formatting
   of the raw items (up to budget), marked as "needs recompile".
3. Always stay within MAX_MEMORY_TOKENS.

The returned string is passed as `extra_system` to LLMClient.chat().

Public API:
    build_memory_block(session, project_id, max_tokens) -> str
    get_memory_context(session, project_id, model, ws_manager) -> str
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session

from alfred.memory.compress import (
    _COMPILED_TAG,
    estimate_tokens,
    get_compiled,
)
from alfred.memory.store import list_items
from alfred.models.db_models import MemoryItem

logger = logging.getLogger(__name__)

# Hard budget: never inject more than this many tokens of memory into a prompt
MAX_MEMORY_TOKENS = 1_200

# Header and footer tokens (approximate)
_WRAPPER_TOKENS = 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_raw_fallback(items: list[MemoryItem], budget: int) -> str:
    """
    Format raw items into a compact bulleted block within the token budget.
    Used when the compiled doc is unavailable or stale.
    """
    lines: list[str] = []
    used = _WRAPPER_TOKENS

    by_type: dict[str, list[MemoryItem]] = {}
    for item in items:
        by_type.setdefault(item.type.value, []).append(item)

    for type_name, type_items in sorted(by_type.items()):
        header = f"\n**{type_name.title()}s:**"
        header_tok = estimate_tokens(header)
        if used + header_tok > budget:
            break
        lines.append(header)
        used += header_tok

        for item in type_items:
            bullet = f"- {item.content}"
            tok = estimate_tokens(bullet)
            if used + tok > budget:
                lines.append("- *(more items omitted — recompile memory)*")
                break
            lines.append(bullet)
            used += tok

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_memory_block(
    session: Session,
    project_id: Optional[int],
    max_tokens: int = MAX_MEMORY_TOKENS,
) -> str:
    """
    Build the memory block string for injection into an agent system prompt.

    Returns an empty string if there are no memory items.
    """
    # First, try the compiled doc
    compiled = get_compiled(session, project_id)

    if compiled is not None and not compiled.is_stale:
        if compiled.token_estimate <= max_tokens:
            block = (
                "## ALFRED Memory (compiled)\n\n"
                + compiled.markdown
            )
            logger.debug(
                "Memory block from compiled doc: tokens≈%d", compiled.token_estimate
            )
            return block

        # Compiled doc exceeds budget — truncate it
        chars = max_tokens * 4  # rough char limit
        truncated = compiled.markdown[:chars] + "\n\n*(truncated — recompile to reduce size)*"
        block = "## ALFRED Memory (compiled, truncated)\n\n" + truncated
        logger.debug("Memory block truncated: tokens≈%d > budget=%d", compiled.token_estimate, max_tokens)
        return block

    # Fallback: format raw items directly
    raw_items = [
        item
        for item in list_items(session, project_id=project_id, active_only=True)
        if item.tags != _COMPILED_TAG
    ]

    if not raw_items:
        return ""  # Nothing to inject

    stale_note = ""
    if compiled is not None and compiled.is_stale:
        stale_note = " *(stale — recompile recommended)*"

    fallback_text = _format_raw_fallback(raw_items, max_tokens - _WRAPPER_TOKENS)
    if not fallback_text.strip():
        return ""

    block = f"## ALFRED Memory (raw{stale_note})\n{fallback_text}"
    logger.debug("Memory block from raw items: %d items", len(raw_items))
    return block


async def get_memory_context(
    session: Session,
    project_id: Optional[int],
    model: str,
    ws_manager=None,
    max_tokens: int = MAX_MEMORY_TOKENS,
) -> str:
    """
    Get the memory block, triggering a compile if the doc is absent.
    Used by agents before a long LLM call where fresh context matters.

    Returns the memory block string (may be empty if no items exist).
    """
    compiled = get_compiled(session, project_id)

    # Auto-compile if no compiled doc exists yet
    if compiled is None:
        try:
            from alfred.memory.compress import compile_memory
            await compile_memory(session, project_id, model, ws_manager)
        except Exception as exc:
            logger.warning("Auto-compile in get_memory_context failed: %s", exc)

    return build_memory_block(session, project_id, max_tokens)