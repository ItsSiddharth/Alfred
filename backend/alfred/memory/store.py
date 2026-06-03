"""
memory/store.py — CRUD layer for MemoryItem table.

All public functions accept a SQLModel Session (injected by callers) so
they can participate in the caller's transaction.

Types: mistake | preference | fact | dataset_ref
Scope: project_id=None → global; project_id=N → project-local

Capture hooks (called by Stage-7 code and the chat handler):
  capture_mistake(session, project_id, content, tags)
  capture_preference(session, project_id, content, tags)
  capture_fact(session, project_id, content, tags)
  capture_dataset_ref(session, project_id, content, tags)

Any write that changes items marks the compiled-doc cache stale via a
lightweight sentinel flag stored in the Project.status JSON blob.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Sequence

from sqlmodel import Session, select

from alfred.models.db_models import MemoryItem, MemorySource, MemoryType, Project

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mark_stale(session: Session, project_id: Optional[int]) -> None:
    """
    Mark the compiled memory doc as stale so the next context build
    triggers a recompile.  We store a 'memory_stale' flag inside the
    Project.status JSON blob (which is also used by the state machine).
    """
    if project_id is None:
        return
    import json

    project = session.get(Project, project_id)
    if project is None:
        return

    try:
        status_data = json.loads(project.status) if project.status else {}
        if not isinstance(status_data, dict):
            status_data = {}
    except (json.JSONDecodeError, TypeError):
        status_data = {}

    status_data["memory_stale"] = True
    project.status = json.dumps(status_data)
    project.updated_at = datetime.utcnow()
    session.add(project)
    session.commit()


def _check_stale(session: Session, project_id: Optional[int]) -> bool:
    """Return True if the compiled doc is stale (or project has never compiled)."""
    if project_id is None:
        return True
    import json

    project = session.get(Project, project_id)
    if project is None:
        return True

    try:
        status_data = json.loads(project.status) if project.status else {}
        if not isinstance(status_data, dict):
            return True
        return bool(status_data.get("memory_stale", True))
    except (json.JSONDecodeError, TypeError):
        return True


def _clear_stale(session: Session, project_id: Optional[int]) -> None:
    """Clear the stale flag after a successful recompile."""
    if project_id is None:
        return
    import json

    project = session.get(Project, project_id)
    if project is None:
        return

    try:
        status_data = json.loads(project.status) if project.status else {}
        if not isinstance(status_data, dict):
            status_data = {}
    except (json.JSONDecodeError, TypeError):
        status_data = {}

    status_data["memory_stale"] = False
    project.status = json.dumps(status_data)
    project.updated_at = datetime.utcnow()
    session.add(project)
    session.commit()


# ---------------------------------------------------------------------------
# Core CRUD
# ---------------------------------------------------------------------------


def list_items(
    session: Session,
    *,
    project_id: Optional[int] = None,
    memory_type: Optional[MemoryType] = None,
    active_only: bool = True,
    include_global: bool = True,
) -> Sequence[MemoryItem]:
    """
    List memory items for a project, optionally including global items.

    If project_id is given:
        Returns project-scoped items + (if include_global) global items.
    If project_id is None:
        Returns only global items.

    Filters by type and active flag if specified.
    """
    stmt = select(MemoryItem)

    if project_id is not None:
        if include_global:
            stmt = stmt.where(
                (MemoryItem.project_id == project_id)
                | (MemoryItem.project_id == None)  # noqa: E711
            )
        else:
            stmt = stmt.where(MemoryItem.project_id == project_id)
    else:
        stmt = stmt.where(MemoryItem.project_id == None)  # noqa: E711

    if active_only:
        stmt = stmt.where(MemoryItem.active == True)  # noqa: E712

    if memory_type is not None:
        stmt = stmt.where(MemoryItem.type == memory_type)

    stmt = stmt.order_by(MemoryItem.created_at.desc())
    return session.exec(stmt).all()


def get_item(session: Session, item_id: int) -> Optional[MemoryItem]:
    """Fetch a single MemoryItem by primary key."""
    return session.get(MemoryItem, item_id)


def create_item(
    session: Session,
    *,
    project_id: Optional[int],
    memory_type: MemoryType,
    content: str,
    tags: str = "",
    source: MemorySource = MemorySource.agent,
) -> MemoryItem:
    """
    Create a new MemoryItem and mark the compiled doc stale.
    tags is a comma-separated string of tag names.
    """
    item = MemoryItem(
        project_id=project_id,
        type=memory_type,
        content=content.strip(),
        tags=tags.strip(),
        source=source,
        active=True,
    )
    session.add(item)
    session.commit()
    session.refresh(item)

    _mark_stale(session, project_id)
    logger.info(
        "MemoryItem created: id=%s type=%s project=%s", item.id, memory_type, project_id
    )
    return item


def update_item(
    session: Session,
    item_id: int,
    *,
    content: Optional[str] = None,
    tags: Optional[str] = None,
    active: Optional[bool] = None,
) -> Optional[MemoryItem]:
    """
    Update mutable fields of a MemoryItem.
    Any change marks the compiled doc stale.
    """
    item = session.get(MemoryItem, item_id)
    if item is None:
        return None

    changed = False
    if content is not None:
        item.content = content.strip()
        changed = True
    if tags is not None:
        item.tags = tags.strip()
        changed = True
    if active is not None:
        item.active = active
        changed = True

    if changed:
        session.add(item)
        session.commit()
        session.refresh(item)
        _mark_stale(session, item.project_id)
        logger.info("MemoryItem updated: id=%s", item_id)

    return item


def delete_item(session: Session, item_id: int) -> bool:
    """
    Hard-delete a MemoryItem.  Returns True if found and deleted.
    Prefer deactivating (update_item active=False) for soft-delete.
    """
    item = session.get(MemoryItem, item_id)
    if item is None:
        return False
    project_id = item.project_id
    session.delete(item)
    session.commit()
    _mark_stale(session, project_id)
    logger.info("MemoryItem deleted: id=%s", item_id)
    return True


def count_active_items(session: Session, project_id: Optional[int]) -> int:
    """Return the number of active items for a project (including global)."""
    return len(list_items(session, project_id=project_id, active_only=True))


# ---------------------------------------------------------------------------
# Capture hooks — called from agents and Stage-7 code
# ---------------------------------------------------------------------------


def capture_mistake(
    session: Session,
    project_id: Optional[int],
    content: str,
    tags: str = "auto-captured",
) -> MemoryItem:
    """Record a mistake ALFRED made (and optionally fixed)."""
    return create_item(
        session,
        project_id=project_id,
        memory_type=MemoryType.mistake,
        content=content,
        tags=tags,
        source=MemorySource.agent,
    )


def capture_preference(
    session: Session,
    project_id: Optional[int],
    content: str,
    tags: str = "user-feedback",
) -> MemoryItem:
    """Record a preference expressed by the user via corrective feedback."""
    return create_item(
        session,
        project_id=project_id,
        memory_type=MemoryType.preference,
        content=content,
        tags=tags,
        source=MemorySource.user,
    )


def capture_fact(
    session: Session,
    project_id: Optional[int],
    content: str,
    tags: str = "",
) -> MemoryItem:
    """Record a factual observation (e.g. dataset characteristics, metric baseline)."""
    return create_item(
        session,
        project_id=project_id,
        memory_type=MemoryType.fact,
        content=content,
        tags=tags,
        source=MemorySource.agent,
    )


def capture_dataset_ref(
    session: Session,
    project_id: Optional[int],
    content: str,
    tags: str = "",
) -> MemoryItem:
    """Record a dataset reference (URI, hash, local path, size)."""
    return create_item(
        session,
        project_id=project_id,
        memory_type=MemoryType.dataset_ref,
        content=content,
        tags=tags,
        source=MemorySource.agent,
    )