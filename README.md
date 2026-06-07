# ALFRED — Local-First AI Research Agent

ALFRED is a local-first AI research agent that takes you from a raw hypothesis
all the way through literature review, experiment design, sandboxed code
execution, live metric streaming, automatic error fixing, result interpretation,
iterative refinement, comparison dashboards, and paper export — entirely on your
own hardware.

No data leaves your machine. All LLMs run locally via [Ollama](https://ollama.ai).

---

## What it does

### Stage 1 — Hypothesis Validator
Deep multi-source literature sweep across arXiv, Semantic Scholar, OpenAlex,
and DuckDuckGo. Uses snowball sampling and fuzzy deduplication to surface
the most relevant prior work, then scores your hypothesis on **novelty**,
**gap**, and **publishability** (0–100 each) with verifiable citations.

**Configurable depth:** set `research_num_queries` in `alfred_config.json` to
`1` (fast pipeline test) or `5` (thorough, production-grade review). Default
is `1` so you can iterate quickly.

**Skip option:** If you already know the literature or just want to start
hacking, use the **"Skip research → Jump to experiment design"** button on the
empty chat screen to bypass Stage 1 entirely.

### Stage 2 — Experiment Setup
Collaborative multi-turn dialogue to design a concrete experiment plan.
ALFRED proposes options and trade-offs; you decide. Produces a structured
plan card (objective, architecture, hyperparams, dataset, seed, version mode)
gated behind a human approval step. Plan cards show a **runtime estimate** based
on the median of your past runs on this hardware.

**Rejection → auto-refinement loop:** If you reject a plan with feedback,
ALFRED immediately incorporates your feedback and streams a revised proposal —
no need to re-type anything. A new approval card appears automatically once the
revised plan is ready. Enable **Show Work** to see the LLM's refinement reasoning
in real time.

### Stage 3 — Run & Iterate
End-to-end experiment execution loop:

- **Code generation** — coder role writes a full Python script; unified diff shown for approval
- **Conda sandbox** — every subprocess runs inside `conda run -n <env>`; path jail enforced
- **Live streaming** — stdout/stderr streamed line-by-line; `ALFRED_METRIC`, `ALFRED_PHASE`,
  and `ALFRED_PLOT` markers parsed in real time
- **Auto error-fix** — classifies errors (missing module → `conda install`; generic → fixer role
  with optional DuckDuckGo context); diffs shown in the thinking tab; capped at 3 attempts
- **Plotting** — PNG plots base64-encoded and shown inline; ASCII fallback for the thinking tab
- **Interpretation** — interpreter role streams a plain-language analysis of metrics + plots
- **Git versioning** — automatic commit after each successful run; rollback via sidebar
- **Next-iteration loop** — collaborator role proposes the next experiment (changes, rationale,
  version mode); user approves or stops; supports `modify` (edit-in-place) and `branch`
  (new subfolder) versioning strategies

### Stage 8 — Comparison Dashboard & Export
- **Dashboard panel** — side-by-side metric charts (Recharts line overlays across all iterations)
  and a comparison table (runtime, metrics, git commit, version mode). Accessed via the
  "Dashboard" nav item in the sidebar.
- **Compute budget estimate** — before you approve a plan, the card shows the estimated runtime
  based on past completed runs on your machine.
- **Paper export** — one click generates a structured Markdown + LaTeX research note:
  hypothesis assessment, methodology from plans, results tables, discussion stub. Download
  as `.md` for immediate editing. Clearly labelled as DRAFT.

### Supporting systems
| System | Description |
|--------|-------------|
| **Memory engine** | Per-project memory store with mistake capture, fact/preference/dataset_ref types, LLM compression, and 1 200-token context injection |
| **Tool bus** | Dynamic tool dispatch (arXiv, Semantic Scholar, OpenAlex, DuckDuckGo); Show Work mode surfaces every call |
| **Dataset cache** | Content-hash cache for HuggingFace, HTTP, and local datasets; symlink → hardlink → copy into experiment folder |
| **ExperimentStateMachine** | Full substage tracking (WRITING_CODE → AWAITING_APPROVAL → SETTING_UP_DATA → PREPROCESSING → TRAINING → EVALUATING → INTERPRETING → AWAITING_NEXT); WS transparency events per transition |
| **Ollama health monitor** | Progress strip polls `/api/models/health` every 20 s; shows "Ollama offline" badge if the server goes away mid-run; keepalive pings prevent model unloading during long experiments |
| **Quick / Manual mode** | Toggle per project (⚡ Quick / ⚡ Manual button in the sidebar). Quick mode skips clarifying questions and auto-approves all plan/code diffs. Manual mode pauses at every approval gate for human review |

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

## Configuration

Edit `alfred_config.json` to tune pipeline behaviour:

```json
{
  "workspace_path": "~/alfred-workspace",
  "default_model": "",
  "auto_approve_default": false,
  "research_num_queries": 1
}
```

| Field | Default | Notes |
|-------|---------|-------|
| `research_num_queries` | `1` | Number of search queries per hypothesis run. `1` = fast test, `5` = full sweep |

---

## Typical workflow

```
1. Create a project
2. (Optional) Skip research → describe your idea and jump straight to experiment design
   OR describe your hypothesis and let ALFRED run the literature review first
3. Review the scorecard (novelty / gap / publishability scores with citations)
4. Discuss and refine the experiment design (multi-turn)
5. Review and approve the generated plan card (shows compute estimate)
6. In the sidebar: set conda env name + experiment folder path → Save
7. Send a message like "Run the experiment"
8. Review the generated code diff → Approve
9. Watch live logs, metrics, and plots stream in
10. Read the interpretation, then approve or modify the next-iteration proposal
11. Repeat from step 8 as needed; roll back via the git history panel at any time
12. Open Dashboard → see metric charts across all iterations + export a research note
```

---

## Example project: Continual Learning via PDF Manifold Repair

This end-to-end example walks through using ALFRED exactly as it's meant to be
used — from a novel research idea to experimental validation.

### The idea

> "What if we can do continual learning by repairing the approximated PDF manifold
> exactly in the area that corresponds to the new knowledge that was supposed to be
> injected into the model?"

The intuition: instead of fine-tuning on new data (which causes catastrophic
forgetting) or replaying old data (which is expensive), we identify the region
of the latent-space manifold that encodes the to-be-learned concept and surgically
update only that region's probability density.

---

### Step 1: Create a project and enter the hypothesis

Open ALFRED. Create a new project: **"Manifold Repair CL"**.

Type your hypothesis into the chat bar:

> "My hypothesis is: we can perform continual learning without catastrophic
> forgetting by identifying the submanifold of the model's internal representation
> that encodes a specific piece of knowledge, then repairing only that region of
> the approximated probability density function. New knowledge injection becomes a
> local manifold update rather than a global weight perturbation."

ALFRED will ask one or two clarifying questions before starting the literature sweep:

> "What model family are you targeting — a generative model (VAE/flow), a
> discriminative classifier, or a language model? And what datasets were you
> thinking for initial validation?"

You: "Targeting a VAE on MNIST first, then scaling to CIFAR-10. Eventually
language models but that's later."

ALFRED detects it has enough context and fires the `[START_RESEARCH]` trigger.

---

### Step 2: Literature review (Show Work mode)

The progress strip shows each research phase. If you enable **Show Work**, you
see every tool call inline:

```
› [arxiv_search] "continual learning catastrophic forgetting manifold" — 12 results
› [semantic_scholar] "probability density estimation neural network repair" — 8 results
› [openalex_search] "latent space geometry continual learning" — 6 results
› [semantic_scholar expand] paperId:abc123 — references + 14 citations
› [web_search] "continual learning manifold repair github" — 5 results
```

After ~2–3 minutes (with 1 query / fast mode), ALFRED presents a scorecard:

```
Novelty          ████████░░  82/100
Gap realness     ███████░░░  71/100
Publishability   ██████░░░░  63/100
```

**Landscape summary (collapsible):** ALFRED found that EWC (Elastic Weight
Consolidation), PackNet, and several VAE-based continual learning methods exist,
but none explicitly frame the problem as manifold geometry repair. The "PDF
repair" angle is novel framing. Gap is real — the open question is whether the
local manifold can be identified and repaired efficiently without disturbing
neighbouring regions.

**Citations** (clickable, linked to arXiv/Semantic Scholar):
- [2017] *Overcoming catastrophic forgetting in neural networks* (PNAS) — EWC
- [2018] *Variational Continual Learning* (ICLR)
- [2020] *Continual learning with hypernetworks* (ICLR)
- [2022] *Geometric continual learning in Riemannian spaces* (NeurIPS)

You click **Approve** → ALFRED advances to Stage 2.

> **Alternative:** If you already knew all this and just want to start coding,
> you would have clicked **"Skip research → Jump to experiment design"** at the
> start and gone straight to Step 3.

---

### Step 3: Experiment design dialogue

ALFRED opens the setup dialogue as a collaborative researcher:

**ALFRED:** "Great — let's design the first iteration. I'd suggest starting with
a VAE on MNIST: it's fast to train (< 5 min), gives us a clean 2D latent space
to visualise, and failure modes are immediately obvious. For the manifold repair
mechanism I have two initial hypotheses:

1. **Density patching**: train a small correction network that adjusts the
   decoder's local density for the new class without touching global weights.
2. **Latent region masking**: identify the latent region corresponding to the
   new class via a density estimator, then fine-tune only layers whose activations
   overlap that region.

Which direction interests you more? I'd suggest option 2 as the first attempt —
it's closer to existing EWC intuitions and easier to ablate."

**You:** "Let's go with option 2. Use a Gaussian mixture model to identify the
latent region."

**ALFRED:** "Perfect. Here's the proposed plan:

- **Objective**: Validate whether selective fine-tuning based on GMM-identified
  latent regions reduces catastrophic forgetting vs baseline full fine-tuning
- **Baseline**: Full fine-tuning on new class, measure forgetting on old classes
- **Proposed method**: GMM fit on new-class latent codes → identify affected
  region → fine-tune only layers with high activation overlap
- **Dataset**: MNIST (10 classes, sequential introduction)
- **Architecture**: Convolutional VAE, latent dim=16
- **Metrics**: Accuracy on all seen classes after each new class introduction;
  BWT (Backward Transfer) and FWT (Forward Transfer)
- **Success criterion**: BWT < −0.05 vs baseline BWT < −0.15
- **Version mode**: modify (edit in place, git tracks changes)
- **Seed**: 42"

You edit the latent dim from 16 to 32, then click **Approve**.

The plan card shows: **~4 min on your GPU** (compute estimate from a past VAE run).

---

### Step 4: First run

Set your conda environment in the sidebar: `nk_sid` | `/home/you/projects/manifold-cl`.

Send: "Run the experiment."

ALFRED generates a full Python script. The diff appears for review — you skim
it, see the logging is thorough, and click **Approve**.

The live log streams:

```
[ALFRED_PHASE] setup
[INFO] Loading MNIST: 60000 train, 10000 test
[ALFRED_PHASE] train_vae
[INFO] VAE Epoch 1/20 — recon_loss 142.3 kl_loss 8.1
[INFO] VAE Epoch 5/20 — recon_loss 89.4 kl_loss 12.3
...
[ALFRED_METRIC] loss=45.2
[ALFRED_PHASE] fit_gmm
[INFO] Fitting 2-component GMM to class-0 latent codes
[ALFRED_PHASE] sequential_cl
[INFO] Introducing class 1 — selective fine-tune...
[ALFRED_METRIC] accuracy_all=0.847
[ALFRED_METRIC] bwt=-0.031
[ALFRED_PLOT] results/vae_latent_space.png
[ALFRED_PLOT] results/accuracy_curve.png
```

Plots appear inline: a 2D PCA of the latent space coloured by class (showing the
GMM regions), and an accuracy-vs-class-introduction curve comparing baseline vs
proposed method.

**ALFRED interpretation:**

> "Results are promising. BWT of −0.031 vs the baseline's −0.147 is a 4.7×
> reduction in catastrophic forgetting. The latent space plot shows clean class
> separation, and the GMM regions are visually distinct. However, forward
> transfer (FWT = +0.012) is only marginal — the selective fine-tuning may be
> too conservative, not helping future classes benefit from earlier learning.
> I'd suggest two next experiments: (1) widen the fine-tuned region (increase
> GMM sigma), (2) add an orthogonality loss to prevent the correction from
> disturbing already-learned regions."

---

### Step 5: Second iteration

ALFRED proposes Iteration 2:

```
Proposed changes:
+ Increase GMM sigma by 1.5× to allow wider region influence
+ Add orthogonality regularisation term (λ=0.01) on weight updates

Rationale: FWT is low; the region may be too tight to allow beneficial
cross-task transfer. Orthogonality prevents global disruption.

version mode: modify
```

You click **Run next iteration**. Git commits iter 1, starts iter 2.

After 4 minutes: BWT = −0.019, FWT = +0.041. Better on both axes.

---

### Step 6: Dashboard comparison

Open the **Dashboard** panel from the sidebar.

**Charts tab** — line charts show `loss`, `accuracy_all`, `bwt`, `fwt` overlaid
for iterations 1 and 2. You can visually confirm the BWT improvement and the
FWT improvement with the wider GMM region.

**Table tab:**

| iter | status | runtime | commit | loss | accuracy_all | bwt    | fwt   |
|------|--------|---------|--------|------|--------------|--------|-------|
| 1    | done   | 4.1m    | a3f8d1 | 45.2 | 0.847        | -0.031 | 0.012 |
| 2    | done   | 4.3m    | c7b2e9 | 44.8 | 0.861        | -0.019 | 0.041 |

Compute estimate: **~4.2 min on your hardware** (median of 2 runs).

---

### Step 7: Export research note

Click **Export** in the Dashboard panel. ALFRED generates and downloads
`manifold_repair_cl_research_note_20260607.md` containing:

- Hypothesis assessment (novelty 82, gap 71, publishability 63 with cited papers)
- Methodology section for each iteration (pulled from approved plan cards)
- Results tables (auto-inserted from parsed metrics)
- Discussion stub labelled DRAFT
- Optionally a `.tex` skeleton for LaTeX typesetting

You open the Markdown, fill in the discussion, add a conclusion, and have a
workshop paper draft in ~10 minutes of editing.

---

### Show Work mode — verbose transparency

Toggle **Show Work** at any time to see:

- Every research tool call (query string, sources queried, result count)
- Model used and memory tokens injected for each response
- Generated code diffs before execution
- Fix loop diffs and DuckDuckGo queries used when errors occur
- ALFRED's interpretation prompt and raw model output

This is the "verbose mode" that lets you audit every decision ALFRED makes.

---

## Development commands

```bash
# Boot both servers (recommended)
python scripts/dev.py

# Backend only
cd backend && uvicorn alfred.main:app --reload --port 8000

# Frontend only
cd frontend && pnpm dev

# Run all tests
cd alfred && conda run -n <your-env> python -m pytest backend/tests/

# TypeScript type check
cd frontend && pnpm tsc --noEmit

# Lint Python
ruff check backend/ && black --check backend/

# Format Python
ruff check --fix backend/ && black backend/
```

---

## Directory layout

```
alfred/
├── backend/alfred/
│   ├── main.py                 # FastAPI app; chat routing per project stage
│   ├── config.py               # Workspace config (alfred_config.json)
│   ├── db.py                   # SQLite engine + migration shim
│   ├── models/
│   │   └── db_models.py        # SQLModel tables: Project, Experiment, Metric,
│   │                           #   RunLog, Message, Score, MemoryItem, ...
│   ├── api/
│   │   ├── projects_router.py  # CRUD + skip-hypothesis endpoint
│   │   ├── experiments_router.py
│   │   ├── messages_router.py
│   │   ├── hypothesis_router.py
│   │   ├── runner_router.py    # /bind, /git/log, /git/rollback, /runs, /metrics
│   │   ├── dashboard_router.py # Stage 8: /dashboard, /compute-estimate, /export
│   │   ├── memory_router.py
│   │   └── tools_router.py
│   ├── ws/
│   │   └── __init__.py         # ConnectionManager; send/broadcast helpers
│   ├── agents/
│   │   ├── base.py             # Role enum, LLMClient (chat/chat_raw/chat_silent)
│   │   ├── hypothesis.py       # 5-phase research loop (configurable query count)
│   │   ├── setup.py            # Collaborative plan dialogue
│   │   └── runner.py           # Full execution loop (generate→run→fix→interpret→iterate)
│   ├── state_machine/
│   │   └── machine.py          # ExperimentStateMachine; Stage/S1Sub/S2Sub/S3Sub enums
│   ├── memory/
│   │   ├── store.py            # CRUD + capture hooks
│   │   ├── compress.py         # LLM compression (critic role)
│   │   └── context.py          # build_memory_block() with 1 200-token budget
│   ├── tools/
│   │   ├── base.py             # AlfredTool ABC, ToolRegistry
│   │   ├── dispatch.py         # ToolDispatcher: LLM decision loop + WS events
│   │   ├── web_search.py       # DuckDuckGo
│   │   ├── arxiv_search.py
│   │   ├── semantic_scholar.py
│   │   └── openalex_search.py
│   └── services/
│       ├── ollama.py           # Health, list, pull (streaming), stream_chat
│       ├── gpu.py              # Hardware detection (pynvml / nvidia-smi / Apple Silicon)
│       ├── conda.py            # CondaExecutor: run_script, install_package, snapshot_env
│       ├── dataset_cache.py    # Content-hash cache; HF + HTTP + local sources
│       ├── git_service.py      # init, commit, log, rollback
│       └── plotting.py         # STANDARD_PREAMBLE, png_to_ascii, emit_plot_event
├── frontend/src/
│   ├── store/
│   │   └── index.ts            # Zustand: messages, streams, logs, plots, approval gate,
│   │                           #   activeProjectStage (synced for skip-hypothesis button)
│   ├── api/
│   │   ├── client.ts           # Typed REST client for all routers incl. dashboardApi
│   │   └── useWebSocket.ts     # WS hook; dispatches all server events to store
│   └── components/
│       ├── chat/
│       │   ├── ChatThread.tsx  # Main thread: bubbles, thinking tab, plots, approval card,
│       │   │                   #   SkipHypothesisAction (shown in hypothesis stage)
│       │   ├── ChatBar.tsx     # Message input + model picker
│       │   ├── ApprovalCard.tsx# Plan cards with compute estimate badge (Stage 8)
│       │   ├── DiffView.tsx    # Unified diff viewer (+/- coloring, collapse)
│       │   └── PlotView.tsx    # PNG plot card with ASCII toggle
│       ├── experiment/
│       │   ├── DashboardPanel.tsx       # Stage 8: metric charts + comparison table + export
│       │   ├── ProjectBindingPanel.tsx  # Conda env + experiment folder binding
│       │   └── GitHistoryPanel.tsx      # Git log with per-commit rollback
│       └── sidebar/
│           ├── Sidebar.tsx     # Nav: Memory, Tools, Find models, Dashboard
│           ├── FindModelsPanel.tsx
│           ├── MemoryPanel.tsx
│           └── ToolsPanel.tsx
├── backend/tests/
│   ├── test_stage2.py   # State machine, messages, experiments (32 tests)
│   ├── test_stage3.py   # Memory store, compress, context (32 tests)
│   ├── test_stage4.py   # Tool bus, dispatch, routers (26 tests)
│   ├── test_stage5.py   # Hypothesis agent, scoring (12 tests)
│   ├── test_stage6.py   # Setup agent, plan approval (8 tests)
│   ├── test_stage7.py   # Conda, cache, git, runner, next-iter loop (38 tests)
│   └── test_stage8.py   # Dashboard, compute estimate, export, skip-hypothesis (24 tests)
└── scripts/
    └── dev.py           # Boots backend + frontend concurrently
```

---

## Tech stack

**Backend:** Python 3.11, FastAPI, uvicorn, SQLModel (SQLite), asyncio  
**Frontend:** React 18, TypeScript, Vite, Tailwind CSS, Zustand, TanStack Query, react-markdown, Recharts  
**LLM runtime:** Ollama (all models run locally)  
**Research tools:** arxiv, semanticscholar, OpenAlex (httpx), duckduckgo-search  
**Execution:** conda subprocess jail, PYTHONUNBUFFERED streaming, git via subprocess  
**No admin required.** Everything runs under user-writable paths.

---

## Key design invariants

- **Conda jail** — every script subprocess goes through `conda run -n <env> --no-capture-output`; never raw `python`
- **Path jail** — every file write calls `assert_within(experiment_folder, target)` before writing
- **Approval gates** — code diffs and next-iteration proposals always pause for human review (auto-approve toggle available)
- **Error-fix cap** — automatic fix loop capped at 3 attempts; stuck message emitted on exhaustion
- **Memory recording** — every error+fix pair persisted via `memory.store.capture_mistake()`
- **Git commit** — only on exit code 0; failed runs are never committed
- **No fabricated citations** — hypothesis agent only cites papers returned by the actual tool calls
- **Draft labelling** — all exported research notes are clearly marked as DRAFT
- **Binding guard** — ALFRED will not generate or run experiment code unless a conda environment and experiment folder are configured; messages that ask to run/build without a binding set are blocked with a clear setup prompt
