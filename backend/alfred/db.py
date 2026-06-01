"""
Database initialisation and migration shim.

Engine is created lazily on first call to get_engine() so tests can override
the DB path before any import side-effects run.

Migration strategy (Stage 0):  We use a simple check-and-add-column approach
rather than Alembic to keep the dependency count low.  Later stages that add
columns call add_column_if_missing() at startup.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

from sqlalchemy import Engine, text
from sqlmodel import Session, SQLModel, create_engine

logger = logging.getLogger(__name__)

_engine: Optional[Engine] = None


def get_engine(db_path: Optional[str | Path] = None) -> Engine:
    """
    Return the singleton SQLModel engine.

    If *db_path* is provided and no engine exists yet, create one at that path.
    Subsequent calls return the cached engine regardless of *db_path*.
    """
    global _engine
    if _engine is None:
        if db_path is None:
            raise RuntimeError(
                "get_engine() called before the engine was initialised. "
                "Pass db_path on the first call."
            )
        url = f"sqlite:///{db_path}"
        _engine = create_engine(
            url,
            echo=False,  # set True to see SQL in development
            connect_args={"check_same_thread": False},
        )
        logger.info("SQLite engine created → %s", db_path)
    return _engine


def init_db(db_path: str | Path) -> Engine:
    """
    Initialise the database: create engine, create all tables defined in
    SQLModel metadata, then run the migration shim for any future-stage
    columns that might already be in the model but not yet in the DB.
    """
    # Import all models so SQLModel.metadata knows about every table.
    import alfred.models.db_models  # noqa: F401

    engine = get_engine(db_path)
    SQLModel.metadata.create_all(engine)
    logger.info("All tables created / verified in %s", db_path)
    return engine


def get_session() -> Session:
    """FastAPI dependency: yields a SQLModel Session, commits on exit."""
    engine = get_engine()
    with Session(engine) as session:
        yield session


# ---------------------------------------------------------------------------
# Migration shim — called by later stages to add new columns without data loss
# ---------------------------------------------------------------------------


def add_column_if_missing(
    table: str, column: str, column_def: str, db_path: str | Path
) -> None:
    """
    Add *column* to *table* if it does not already exist.

    *column_def* is the SQLite column definition string, e.g.
    ``"TEXT NOT NULL DEFAULT ''"`` or ``"INTEGER"``.

    Using raw sqlite3 here (not SQLAlchemy) because SQLite's ALTER TABLE
    is limited and we only need ADD COLUMN.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute(f"PRAGMA table_info({table})")
        existing_columns = {row[1] for row in cursor.fetchall()}
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")
            conn.commit()
            logger.info("Migration: added column '%s' to table '%s'", column, table)
        else:
            logger.debug("Column '%s' already exists in '%s', skipping.", column, table)
    finally:
        conn.close()