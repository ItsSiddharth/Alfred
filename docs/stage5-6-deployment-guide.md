# C10 Deployment & Validation Guide — Stages 5 & 6

## Stage Summary
Stage 5 adds the Hypothesis Validator: a 5-phase research loop (query generation → broad sweep → citation snowball → web sweep → synthesis/scoring) triggered by the LLM after clarifying questions, with results displayed as a scorecard with per-score citations.
Stage 6 adds the Experiment Setup agent: a multi-turn collaborative dialogue that proposes and approves a structured experiment plan after a minimum of 3 conversational turns.

---

## Subsection 1 — File placement table

| File path (from repo root) | Action | Notes |
|---|---|---|
| `backend/alfred/agents/hypothesis.py` | Create (new file) | HypothesisAgent — 5-phase loop, score persistence, re-run on rejection |
| `backend/alfred/agents/setup.py` | Create (new file) | SetupAgent — multi-turn dialogue, plan proposal, approval handling |
| `backend/alfred/api/hypothesis_router.py` | Create (new file) | `GET /scores` and `POST /start` endpoints for the hypothesis agent |
| `backend/alfred/main.py` | Replace entirely | Version bumped to 0.5.0; mounts hypothesis_router; adds stage routing for hypothesis/setup/plain; adds `_START_RESEARCH_MARKER` pattern; lifespan runs `citations_json` column migration |
| `backend/alfred/models/db_models.py` | Add to existing | Add `citations_json: str = Field(default="[]")` field to `Score` model |
| `frontend/src/api/client.ts` | Add to existing | Add `HypothesisScore` interface and `hypothesisApi` object after the experiments section |
| `frontend/src/components/chat/ApprovalCard.tsx` | Replace entirely | Add `CitationsList`, enhanced `ScoreMeter` with citations, `ScorecardView` with landscape, `rerunMutation` |
| `backend/tests/test_stage5.py` | Create (new file) | Tests for compact_paper_list, dedup, score persistence, S1Sub sequence, GET /scores |
| `backend/tests/test_stage6.py` | Create (new file) | Tests for _strip_fences, _check_plan_ready, _get_or_create_experiment, handle_approved_plan, S2Sub sequence, MIN_TURNS guard |

---

## Subsection 2 — Installing dependencies

No new Python or npm dependencies are introduced in Stages 5 and 6. All research tools (arxiv, semanticscholar, httpx, ddgs) were already installed in Stage 4. All frontend packages were already present.

If you skipped Stage 4's dependency install, run these from the repo root:

```bash
conda activate nk_sid
pip install arxiv semanticscholar httpx duckduckgo-search
```

---

## Subsection 3 — How to run

From the repo root, with the `nk_sid` conda environment active:

```bash
conda activate nk_sid

# Start both servers (backend :8000 and frontend :5173)
python scripts/dev.py
```

**What "ready" looks like:**

Backend terminal will show:
```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

Frontend terminal will show:
```
  VITE v5.x.x  ready in xxx ms
  ➜  Local:   http://localhost:5173/
```

**To run the tests only:**
```bash
conda activate nk_sid
cd backend
pytest tests/test_stage5.py -v
pytest tests/test_stage6.py -v
```

---

## Subsection 4 — Validation checklist

```
[ ] Check 1: Backend starts without error
    Exact command: python scripts/dev.py
    ✓ Success: Both "Application startup complete." (backend) and "VITE ... ready" (frontend) appear
      with no Python traceback or import error.
    ✗ If it fails: Most likely a syntax error in main.py or a missing import. Run
      `cd backend && python -c "from alfred.main import app"` to surface the traceback directly.

[ ] Check 2: Hypothesis router is mounted
    Exact command: curl -s http://localhost:8000/api/projects/1/hypothesis/scores
    ✓ Success: Returns [] (empty JSON array) — project 1 may not exist yet, but a 404 from FastAPI
      means the router IS mounted. The response will be {"detail":"Project not found"} rather than
      {"detail":"Not Found"} (generic 404), confirming the router resolved.
    ✗ If it fails: If you get a generic "Not Found", the router is not mounted. Check that
      `app.include_router(hypothesis_router)` appears in backend/alfred/main.py.

[ ] Check 3: citations_json column migration
    Exact command (after the app has started at least once):
      sqlite3 ~/alfred-workspace/alfred.db ".schema score"
    ✓ Success: The schema output includes `citations_json TEXT NOT NULL DEFAULT '[]'`
    ✗ If it fails: The lifespan migration did not run. Check that `add_column_if_missing(
      "score", "citations_json", "TEXT NOT NULL DEFAULT '[]'", db_path)` is called inside the
      `@asynccontextmanager async def lifespan` function in main.py.

[ ] Check 4: Create a project and start hypothesis validation (API path)
    First create a project:
      curl -s -X POST http://localhost:8000/api/projects/ \
        -H "Content-Type: application/json" \
        -d '{"name":"Test Stage 5"}' | python -m json.tool
    Note the "id" field (assume it is 1). Then start the research:
      curl -s -X POST http://localhost:8000/api/projects/1/hypothesis/start \
        -H "Content-Type: application/json" \
        -d '{"hypothesis":"Sparse attention reduces transformer memory usage","model":"llama3.2:3b"}'
    ✓ Success: Returns {"status":"started","project_id":1}
    ✗ If it fails with 500: The agent instantiation failed. Check that Ollama is running
      (`curl http://localhost:11434/api/tags`) and the model name matches an installed model.

[ ] Check 5: Scores appear after research completes
    Wait ~30–120 seconds for the 5-phase loop to complete, then:
      curl -s http://localhost:8000/api/projects/1/hypothesis/scores | python -m json.tool
    ✓ Success: Returns a JSON array with exactly 3 objects, each having "kind" (one of
      "novelty", "gap", "publishability"), "value" (integer 0–100), "rationale" (non-empty
      string), and "citations" (array, may be empty if no papers found).
    ✗ If it fails (returns []): The agent may still be running. Check backend logs for
      "[HypothesisAgent]" entries. If it errored, look for "ERROR" lines in the terminal.

[ ] Check 6: Browser — scorecard renders in the chat UI
    Open http://localhost:5173 in a browser. Select the "Test Stage 5" project. Send a chat
    message describing a research hypothesis (e.g. "I want to test whether sparse attention
    mechanisms can reduce memory usage in large transformers without significant accuracy loss").
    ALFRED will respond with clarifying questions. Answer them. Eventually ALFRED will include
    [START_RESEARCH] in its response (invisible in the UI — stripped before display).
    ✓ Success: A progress strip appears showing "Generating queries...", "Sweeping sources...",
      "Snowballing...", "Web sweep...", "Analyzing..." phases in sequence. After completion,
      an ApprovalCard appears in the chat with three ScoreMeter bars (Novelty, Gap, Publishability).
      Each meter has an expand arrow that reveals the rationale and cited papers.
    ✗ If the scorecard does not appear: Open browser DevTools → Network tab → WS connection.
      Check for `progress` and `approval_request` WS events. If events arrive but no card,
      check ApprovalCard.tsx for a TypeScript compile error (Vite will show it in the terminal).

[ ] Check 7: Approve scorecard → advance to setup stage
    In the browser, click "Approve" on the scorecard. The chat stage badge should change from
    "hypothesis" to "setup".
    ✓ Success: The project's current_stage changes to "setup" in the DB:
      curl -s http://localhost:8000/api/projects/1 | python -m json.tool | grep current_stage
      → "current_stage": "setup"
    ✗ If it fails: The state machine transition likely threw. Check that `_create_setup_approval`
      in main.py has its imports (S2Sub, Stage, etc.) at the TOP of the function body, not inside
      a method call's argument list.

[ ] Check 8: Setup agent — multi-turn dialogue before plan proposal
    In the browser with the project in "setup" stage, send 2 short messages (e.g. "I want to
    train ResNet-18 on CIFAR-10", then "Use standard augmentation"). After each, ALFRED should
    respond conversationally without proposing a plan.
    ✓ Success: After 2 messages, no ApprovalCard appears. The responses are prose questions
      or observations, not a structured plan.
    ✗ If a plan is proposed before turn 3: Check MIN_TURNS_BEFORE_PROPOSAL in setup.py equals 3,
      and that `_load_history()` correctly counts assistant messages in the DB.

[ ] Check 9: Setup agent — plan proposed at turn 3+
    Send a 3rd setup message (e.g. "Baseline is a vanilla CNN, success is >85% validation
    accuracy after 20 epochs"). ALFRED should propose a structured experiment plan.
    ✓ Success: An ApprovalCard appears with plan fields: objective, toy_dataset, architectures,
      baselines, metrics, success_criteria, first_iteration_spec.
    ✗ If no plan appears after turn 3: The silent plan-readiness check returned {"ready": false}.
      This is expected behaviour if the LLM decides more info is needed. Send 1–2 more messages
      with specific details. The check is re-run every turn from turn 3 onward.

[ ] Check 10: Reject plan → re-runs hypothesis with feedback
    On the plan ApprovalCard, click "Reject" and enter feedback (e.g. "Also test EfficientNet").
    ✓ Success: The backend logs show "[HypothesisAgent] Re-running with feedback: Also test
      EfficientNet" and the 5-phase progress strip reappears. After completion, an updated
      scorecard appears.
    ✗ If the re-run does not start: Check `rerunMutation` in ApprovalCard.tsx calls
      `hypothesisApi.start(projectId, hypothesis, model, feedback)` where `isScorecard` is true.

[ ] Check 11: Score rows in the database
    sqlite3 ~/alfred-workspace/alfred.db \
      "SELECT project_id, kind, value, substr(rationale,1,40), substr(citations_json,1,60) FROM score;"
    ✓ Success: Exactly 3 rows per project, one for each of novelty/gap/publishability. The
      citations_json column contains a JSON array (may be [] or [{...}]).
    ✗ If 6 rows exist: _save_scores() did not delete old rows before inserting. Check the
      DELETE statement at the start of `_save_scores` in hypothesis.py.

[ ] Check 12: Run the test suites
    conda activate nk_sid
    cd backend
    pytest tests/test_stage5.py tests/test_stage6.py -v --tb=short
    ✓ Success: All tests show PASSED with no ERRORs or FAILUREs.
    ✗ If ImportError on alfred.agents.setup: The file was not created or has a syntax error.
      Run `python -c "from alfred.agents.setup import SetupAgent"` to isolate the import error.
```

---

## Subsection 5 — What "done" looks like

When Stages 5 and 6 are fully deployed, the ALFRED chat UI shows a project that flows through two visible stages. In the **hypothesis** stage, the user types a research hypothesis into the chat bar; ALFRED responds with a few clarifying questions (all in natural prose), and after the user answers, a progress strip at the top of the chat window cycles through five phases — "Generating queries", "Sweeping sources", "Snowballing", "Web sweep", and "Analyzing" — each with a spinner and elapsed time. When research completes, an ApprovalCard appears with three animated score meters: Novelty, Gap, and Publishability, each showing a 0–100 bar with color coding (green / amber / red). Clicking the expand arrow on any meter reveals the LLM's rationale and a list of clickable cited papers. Clicking "Approve" transitions the project to the **setup** stage. In the **setup** stage, ALFRED engages in a minimum of three conversational turns asking about dataset, architecture, baselines, and success criteria. Once ALFRED has enough information, a second ApprovalCard appears showing the full structured experiment plan (objective, toy and scale datasets, architectures, baselines, metrics, and first-iteration spec). The user can approve — advancing the project to **run** stage — or reject with written feedback, which automatically re-runs the 5-phase research loop and incorporates the feedback before proposing again. Throughout both stages, every tool call, progress event, and approval gate is visible in the browser's Show Work panel and in the backend terminal logs.
