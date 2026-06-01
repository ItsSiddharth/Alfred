# ALFRED — Local-First AI Research Agent

ALFRED is a local-first AI research agent that helps you validate hypotheses,
design experiments, run them inside a conda environment, and iterate — all with
full transparency and human approval gates.

## What it does

- **Stage 1 — Hypothesis Validator:** Deep multi-source literature research
  (arXiv, Semantic Scholar, OpenAlex, DuckDuckGo). Outputs novelty, gap, and
  publishability scores with real citations.
- **Stage 2 — Experiment Setup:** Collaborative experiment design with a
  toy-first progression and an approvable plan card.
- **Stage 3 — Run & Iterate:** Sandboxed code generation, conda execution,
  live streaming logs, error-fix loop, git versioning, and result interpretation.

All LLMs run locally via [Ollama](https://ollama.ai). No data leaves your machine.

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.11+ | [python.org](https://python.org) |
| Node.js | 18+ | [nodejs.org](https://nodejs.org) |
| pnpm | 8+ | `npm install -g pnpm` |
| Ollama | latest | [ollama.ai](https://ollama.ai) |
| conda | any | [miniforge](https://github.com/conda-forge/miniforge) |

---

## Quick start

```bash
# 1. Clone
git clone <repo> alfred && cd alfred

# 2. Install Python dependencies
pip install -e ".[dev]"

# 3. Install JS dependencies
pnpm install

# 4. Start both servers
python scripts/dev.py
```

Open **http://localhost:5173** in your browser.

On first run you'll see a setup screen — enter a workspace path (default
`~/alfred-workspace`) and click **Set up workspace**. ALFRED creates the
directory, writes `alfred_config.json` next to the repo, and you're ready.

---

## Development commands

```bash
# Run both servers
python scripts/dev.py

# Backend only
cd backend && uvicorn alfred.main:app --reload --port 8000

# Frontend only
cd frontend && pnpm dev

# Run tests
pytest

# Lint Python
ruff check backend/ && black --check backend/

# Format Python
ruff check --fix backend/ && black backend/
```

Or use `just` if you have it installed:

```bash
just dev       # boot both servers
just test      # run pytest
just lint      # check linting
just fmt       # auto-format
```

---

## Directory layout

```
alfred/
├── backend/alfred/     # FastAPI + SQLModel backend
│   ├── main.py         # App entrypoint
│   ├── config.py       # Workspace config
│   ├── db.py           # SQLite / SQLModel
│   ├── models/         # DB table definitions (C6)
│   ├── api/            # REST routers
│   ├── ws/             # WebSocket manager
│   ├── agents/         # LLM agent logic (Stages 1-3)
│   ├── state_machine/  # ExperimentStateMachine (Stage 2)
│   ├── memory/         # Memory engine (Stage 3)
│   ├── tools/          # Tool bus (Stage 4)
│   └── services/       # Ollama, GPU, git, conda, cache
├── frontend/src/       # React 18 + TypeScript + Vite
│   ├── App.tsx
│   ├── store/          # Zustand global state
│   ├── api/            # REST client + WS hook
│   ├── components/     # UI components
│   └── pages/          # Route-level pages
├── scripts/
│   ├── dev.py          # Development runner
│   └── package.py      # Production packaging (Stage 9)
└── docs/
    ├── architecture.md
    └── plugin-api.md
```

---

## Tech stack

**Backend:** Python 3.11, FastAPI, uvicorn, SQLModel (SQLite), Ollama  
**Frontend:** React 18, TypeScript, Vite, TailwindCSS, Zustand, TanStack Query  
**Research tools:** arxiv, semanticscholar, OpenAlex (httpx), duckduckgo-search  
**No admin required.** Everything runs under user-writable paths.