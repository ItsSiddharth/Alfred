"""
memory/compress.py — LLM-powered memory compression.

Takes raw MemoryItem rows and uses the `critic` role to distill them into
a compact, deduplicated, grouped Markdown document.

The compiled doc is stored in a dedicated CompiledMemory "virtual" entry:
a MemoryItem with type=fact, tags="__compiled__", project_id matching, which
acts as the cached compiled doc.  This avoids adding a new DB table.

API:
  compile_memory(session, project_id, model, ws_manager) -> CompiledResult
  get_compiled(session, project_id) -> CompiledResult | None
  estimate_tokens(text) -> int

The threshold for triggering auto-compile is 20 active items.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session, select

from alfred.memory.store import (
    _clear_stale,
    _check_stale,
    list_items,
)
from alfred.models.db_models import MemoryItem, MemorySource, MemoryType

logger = logging.getLogger(__name__)

# Tag used to identify the compiled-doc cache entry
_COMPILED_TAG = "__compiled__"
# Trigger recompile when active items exceed this threshold
AUTO_COMPILE_THRESHOLD = 20


# ---------------------------------------------------------------------------
# Token estimation (no tokenizer dependency — uses word-count heuristic)
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """
    Rough token estimate: ~4 characters per token (BPE average).
    Good enough for budget tracking without a full tokenizer.
    """
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Compiled result dataclass
# ---------------------------------------------------------------------------


@dataclass
class CompiledResult:
    markdown: str
    token_estimate: int
    item_count: int
    is_stale: bool = False


# ---------------------------------------------------------------------------
# Storage helpers — compiled doc lives as a special MemoryItem row
# ---------------------------------------------------------------------------


def _get_compiled_item(session: Session, project_id: Optional[int]) -> Optional[MemoryItem]:
    """Fetch the compiled-doc cache entry for a project."""
    stmt = (
        select(MemoryItem)
        .where(MemoryItem.tags == _COMPILED_TAG)
        .where(MemoryItem.type == MemoryType.fact)
        .where(MemoryItem.active == True)  # noqa: E712
    )
    if project_id is None:
        stmt = stmt.where(MemoryItem.project_id == None)  # noqa: E711
    else:
        stmt = stmt.where(MemoryItem.project_id == project_id)

    return session.exec(stmt).first()


def _save_compiled_item(
    session: Session,
    project_id: Optional[int],
    markdown: str,
) -> MemoryItem:
    """Upsert the compiled-doc cache entry."""
    existing = _get_compiled_item(session, project_id)

    if existing is not None:
        existing.content = markdown
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    item = MemoryItem(
        project_id=project_id,
        type=MemoryType.fact,
        tags=_COMPILED_TAG,
        content=markdown,
        source=MemorySource.agent,
        active=True,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


# ---------------------------------------------------------------------------
# Read compiled doc
# ---------------------------------------------------------------------------


def get_compiled(session: Session, project_id: Optional[int]) -> Optional[CompiledResult]:
    """
    Return the cached compiled doc, or None if it has never been compiled.
    Also returns stale=True if items have been added/edited since last compile.
    """
    item = _get_compiled_item(session, project_id)
    if item is None:
        return None

    stale = _check_stale(session, project_id)
    tok = estimate_tokens(item.content)

    # Count raw (non-compiled) active items for display
    raw_items = [
        i for i in list_items(session, project_id=project_id, active_only=True)
        if i.tags != _COMPILED_TAG
    ]

    return CompiledResult(
        markdown=item.content,
        token_estimate=tok,
        item_count=len(raw_items),
        is_stale=stale,
    )


# ---------------------------------------------------------------------------
# Compile
# ---------------------------------------------------------------------------


def _format_items_for_prompt(items: list[MemoryItem]) -> str:
    """Format raw items into a numbered list for the LLM."""
    lines: list[str] = []
    by_type: dict[str, list[MemoryItem]] = {}
    for item in items:
        by_type.setdefault(item.type.value, []).append(item)

    for type_name, type_items in sorted(by_type.items()):
        lines.append(f"\n### {type_name.upper()}S")
        for i, item in enumerate(type_items, 1):
            tag_str = f" [tags: {item.tags}]" if item.tags else ""
            lines.append(f"{i}. {item.content}{tag_str}")

    return "\n".join(lines)


_COMPILE_PROMPT_TEMPLATE = """\
You are compiling a memory document for an AI research agent called ALFRED.

Below are raw memory items collected from the current project.
Your job: distil them into a compact, deduplicated, grouped Markdown document.

Rules:
- Group items by type: Mistakes, Preferences, Facts, Dataset References.
- Remove exact or near-duplicates — keep the most specific version.
- Each item should be one concise bullet (≤ 30 words).
- Use **bold** for the most important items in each group.
- Never fabricate information — only use what is provided.
- Do not include a preamble or postamble — just the Markdown.
- Target length: ≤ 400 words total.

Raw items:
{items}

Output the compiled Markdown document now:"""


async def compile_memory(
    session: Session,
    project_id: Optional[int],
    model: str,
    ws_manager=None,
) -> CompiledResult:
    """
    Use the LLM (critic role) to compile raw memory items into a compact doc.

    If Ollama is unavailable or there are no items, falls back gracefully.
    Always persists the result so subsequent calls return it from cache.
    """
    # Gather raw items (exclude the compiled sentinel)
    raw_items = [
        item
        for item in list_items(session, project_id=project_id, active_only=True)
        if item.tags != _COMPILED_TAG
    ]

    if not raw_items:
        markdown = "_No memory items recorded yet._"
        _save_compiled_item(session, project_id, markdown)
        _clear_stale(session, project_id)
        return CompiledResult(
            markdown=markdown,
            token_estimate=estimate_tokens(markdown),
            item_count=0,
            is_stale=False,
        )

    items_text = _format_items_for_prompt(raw_items)
    prompt = _COMPILE_PROMPT_TEMPLATE.format(items=items_text)

    markdown: str
    try:
        from alfred.agents.base import Role, make_client

        client = make_client(model, ws_manager=None)  # silent — no WS token emission
        markdown = await client.chat_silent(
            Role.CRITIC,
            [{"role": "user", "content": prompt}],
        )
        if not markdown.strip():
            raise ValueError("LLM returned empty response")

        logger.info(
            "Memory compiled via LLM: project=%s items=%d tokens≈%d",
            project_id,
            len(raw_items),
            estimate_tokens(markdown),
        )

    except Exception as exc:
        # Graceful fallback: simple concatenation
        logger.warning("Memory LLM compile failed (%s), using fallback.", exc)
        lines = ["# Memory (uncompressed)\n"]
        by_type: dict[str, list[MemoryItem]] = {}
        for item in raw_items:
            by_type.setdefault(item.type.value, []).append(item)
        for type_name, items in sorted(by_type.items()):
            lines.append(f"\n## {type_name.title()}s")
            for item in items:
                tag_str = f" `[{item.tags}]`" if item.tags else ""
                lines.append(f"- {item.content}{tag_str}")
        markdown = "\n".join(lines)

    _save_compiled_item(session, project_id, markdown)
    _clear_stale(session, project_id)

    return CompiledResult(
        markdown=markdown,
        token_estimate=estimate_tokens(markdown),
        item_count=len(raw_items),
        is_stale=False,
    )


def should_auto_compile(session: Session, project_id: Optional[int]) -> bool:
    """
    Return True if the number of raw active items exceeds the auto-compile
    threshold, indicating a recompile would be beneficial.
    """
    raw_items = [
        item
        for item in list_items(session, project_id=project_id, active_only=True)
        if item.tags != _COMPILED_TAG
    ]
    return len(raw_items) >= AUTO_COMPILE_THRESHOLD