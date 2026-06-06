"""
Canonical SQLModel database models (C6).

All nine tables are defined here.  Stages may ADD columns to this file but
must never create a parallel schema or rename existing columns.

Enum values are stored as strings in SQLite for human readability and to
survive schema migrations without data loss.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ProjectStage(str, enum.Enum):
    hypothesis = "hypothesis"
    setup = "setup"
    run = "run"


class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"
    system = "system"
    tool = "tool"


class MessageKind(str, enum.Enum):
    chat = "chat"
    plan = "plan"
    result = "result"
    error = "error"
    thinking = "thinking"


class MemoryType(str, enum.Enum):
    mistake = "mistake"
    preference = "preference"
    fact = "fact"
    dataset_ref = "dataset_ref"


class MemorySource(str, enum.Enum):
    user = "user"
    agent = "agent"


class ExperimentStatus(str, enum.Enum):
    planned = "planned"
    running = "running"
    done = "done"
    failed = "failed"


class VersionMode(str, enum.Enum):
    modify = "modify"
    branch = "branch"


class RunPhase(str, enum.Enum):
    preprocess = "preprocess"
    train = "train"
    eval = "eval"
    error = "error"
    fix = "fix"


class ScoreKind(str, enum.Enum):
    novelty = "novelty"
    gap = "gap"
    publishability = "publishability"


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


class Project(SQLModel, table=True):
    """Top-level research project.  One conda env, one experiment folder."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    workspace_path: str = Field(default="")
    conda_env: str = Field(default="")
    experiment_folder: str = Field(default="")
    current_stage: ProjectStage = Field(default=ProjectStage.hypothesis)
    auto_approve: bool = Field(default=False)
    status: str = Field(default="active")


class Message(SQLModel, table=True):
    """Persistent chat message including thinking / plan / result kinds."""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    role: MessageRole
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    kind: MessageKind = Field(default=MessageKind.chat)
    metadata_json: str = Field(default="{}")  # arbitrary JSON blob per message


class MemoryItem(SQLModel, table=True):
    """A single remembered fact, preference, mistake, or dataset reference."""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: Optional[int] = Field(default=None, foreign_key="project.id", index=True)
    type: MemoryType
    tags: str = Field(default="")  # comma-separated
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    active: bool = Field(default=True)
    source: MemorySource = Field(default=MemorySource.agent)


class Experiment(SQLModel, table=True):
    """One iteration of an experiment inside a project."""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    iteration: int = Field(default=1)
    git_commit: str = Field(default="")
    code_path: str = Field(default="")
    dataset_hash: str = Field(default="")
    conda_snapshot_path: str = Field(default="")
    seed: int = Field(default=42)
    status: ExperimentStatus = Field(default=ExperimentStatus.planned)
    started_at: Optional[datetime] = Field(default=None)
    finished_at: Optional[datetime] = Field(default=None)
    runtime_seconds: Optional[float] = Field(default=None)
    version_mode: VersionMode = Field(default=VersionMode.modify)
    plan_json: str = Field(default="{}")


class Metric(SQLModel, table=True):
    """A single named scalar metric emitted during a training run."""

    id: Optional[int] = Field(default=None, primary_key=True)
    experiment_id: int = Field(foreign_key="experiment.id", index=True)
    name: str = Field(index=True)
    step: int = Field(default=0)
    value: float


class RunLog(SQLModel, table=True):
    """A line from the experiment subprocess stdout/stderr."""

    id: Optional[int] = Field(default=None, primary_key=True)
    experiment_id: int = Field(foreign_key="experiment.id", index=True)
    level: str = Field(default="INFO")
    message: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    phase: RunPhase = Field(default=RunPhase.train)


class ToolCall(SQLModel, table=True):
    """Audit record of every tool invocation (C5 transparency requirement)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    tool_name: str
    input_json: str = Field(default="{}")
    output_summary: str = Field(default="")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DatasetCacheEntry(SQLModel, table=True):
    """Tracks locally cached datasets to avoid re-downloading."""

    id: Optional[int] = Field(default=None, primary_key=True)
    content_hash: str = Field(index=True, unique=True)
    source_uri: str
    local_path: str
    size_bytes: int = Field(default=0)
    last_used_at: datetime = Field(default_factory=datetime.utcnow)


class Score(SQLModel, table=True):
    """One of the three hypothesis verdicts produced by the Stage-1 agent."""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    kind: ScoreKind
    value: int  # 0–100
    rationale: str = Field(default="")
    citations_json: str = Field(default="[]")  # JSON array of {title,year,venue,url}
    created_at: datetime = Field(default_factory=datetime.utcnow)