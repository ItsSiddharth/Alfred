# ALFRED — Architecture

> Full documentation completed in Stage 9. This file is a placeholder.

## Overview

ALFRED is a monorepo with a Python FastAPI backend and a React + TypeScript frontend.
Communication is via REST (`/api/*`) and a persistent WebSocket per project (`/ws/project/{id}`).

All data lives in SQLite (`<workspace>/db.sqlite`). All LLM inference runs through
a local Ollama instance. No data leaves the machine.

## Component map

```
Browser (React)
    │
    ├── REST  → FastAPI /api/*  → SQLModel / SQLite
    └── WS    → /ws/project/{id} → ConnectionManager → Agents
                                                      → StateMachine
                                                      → ToolBus
```

## Key design decisions

- **Local-first:** Workspace path is user-configured at first run; no system writes.
- **Transparent:** Every action (tool calls, diffs, git commits) is visible in the UI.
- **Resumable:** State machine snapshots to DB on every transition; crashes are recoverable.
- **Pluggable tools:** `AlfredTool` ABC + `tools.yaml` registry — add a tool with one file.