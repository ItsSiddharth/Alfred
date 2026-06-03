"""
models/models.py — DEPRECATED.

This file is kept as a thin re-export shim for backward compatibility.
All canonical model definitions live in models/db_models.py.

Any code that imports from this module should be updated to import from
alfred.models.db_models or alfred.models directly.
"""
from alfred.models.db_models import (  # noqa: F401
    Project,
    Message,
    MemoryItem,
    Experiment,
    Metric,
    RunLog,
    ToolCall,
    DatasetCacheEntry,
    Score,
    ProjectStage,
    MessageRole,
    MessageKind,
    MemoryType,
    MemorySource,
    ExperimentStatus,
    VersionMode,
    RunPhase,
    ScoreKind,
)