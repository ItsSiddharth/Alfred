# ALFRED — Plugin / Tool API

> Full documentation completed in Stage 4. This file is a placeholder.

## Adding a tool

1. Create `backend/alfred/tools/my_tool.py` implementing `AlfredTool`.
2. Add an entry to `backend/alfred/tools/tools.yaml`.
3. Restart the backend — the tool appears in the Tools sidebar automatically.

See `docs/architecture.md` for the full `AlfredTool` ABC specification.