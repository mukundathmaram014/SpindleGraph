# SpindleGraph

A standalone, local, GUI-driven control plane that orchestrates Claude Code
agents to build software from specs. Point it at a target git repo; it models
the repo's `specs/*.md` as structured records, derives a conflict graph from
which files each spec touches, runs a spec-driven workflow visually
(triage → spec → build, in parallel where safe via git worktrees), and tracks
per-executor success probability and cost.

It is **not** a Claude Code plugin — it's a separate app that drives Claude
Code from the outside via the headless CLI (`claude -p`).

**Docs:**

- [`docs/USER-GUIDE.md`](docs/USER-GUIDE.md) — how to use it, tab by tab, with the full workflow loop
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the system as built: components, data flow, job lifecycle
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) — dev loop, test suites, conventions, adding executor backends
- [`docs/SPEC.md`](docs/SPEC.md) — the design record: decisions, rationale, milestones

## Prerequisites

- git, Python 3.11+, Node 20+
- [Claude Code](https://claude.com/claude-code) installed and authenticated
  (`claude --version` should work)
- `gh` CLI (optional — enables PR creation from builds)

## Run

Backend (serves the API on `127.0.0.1:8787`, and the UI too once built):

```sh
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Windows; use bin/ on unix
.venv/Scripts/python -m uvicorn spindlegraph.main:app --port 8787
```

The backend keeps all its state — your added projects, the SQLite DB, job logs,
and build worktrees — under `~/.spindlegraph/`. Set `SPINDLEGRAPH_HOME` to use a
different directory, and start the backend with the **same** value every time:
launch it against a fresh location and it comes up with no projects. After
changing backend code, stop and restart it by hand — don't use `uvicorn
--reload`, whose graceful shutdown hangs on SpindleGraph's long-lived WebSocket
and job tasks.

Frontend — either build once and let the backend serve it:

```sh
cd frontend && npm install && npm run build
# → open http://127.0.0.1:8787
```

…or run the dev server with hot reload (proxies /api and /ws to :8787):

```sh
cd frontend && npm run dev
# → open http://localhost:5173
```

…or install the one-command launcher (v2; build the frontend first):

```sh
pip install ./backend        # or pipx install ./backend
spindlegraph                 # serves API + UI on :8787 and opens a browser
```

Then **Add project** with the absolute path of any local git repo. SpindleGraph
creates `specs/` if missing, copies the workflow commands into
`.claude/commands/`, imports existing specs, and derives the graph.

## The loop

1. **Runner** tab — `/spec "your idea"` grounds the idea in the repo and writes
   `specs/NNNN-slug.md`; `/triage` mines a notes doc into candidates.
2. **Board** tab — read specs, resolve their "Decisions needed" items (builds
   are blocked until a spec's decisions are resolved).
3. **Graph** tab — specs whose file lists overlap are joined by red conflict
   edges. Click nodes to compose a batch: the composer splits the selection
   into parallel-safe waves, shows each executor's success probability and
   cost, and launches one isolated git-worktree build + PR per spec.
4. **Config** tab — claude binary, parallelism, per-project settings, and the
   executor roster (models, priors, prices — calibrated by real outcomes).
   Executors run on one of three backends: `claude_code` (the CLI),
   `claude_sdk` (Claude Agent SDK, in-process), or `local_cli` — any local
   coding agent invocable as a command template with `{prompt}` substituted.

Specs carry a `## Risk` section (Involvement × Review attention); riskier
specs are scheduled earliest in build batches and badged across the UI.

All app state lives under `~/.spindlegraph/` (override with
`SPINDLEGRAPH_HOME`); nothing is ever written into a target repo except specs,
commands, and the branches you asked for.

## Tests

```sh
cd backend && .venv/Scripts/python -m pytest tests -q
```

Integration tests drive a real git fixture repo through
`tests/fake_claude.py`, a stand-in that emits canned `stream-json` events — no
credentials needed. Point `claude binary` in Config at
`python backend/tests/fake_claude.py` to demo the whole app offline.

### End-to-end (browser)

```sh
cd frontend && npm run build && npx playwright test
```

Playwright boots a private backend on :8788 with a generated demo repo and the
fake CLI, then drives the real UI through the whole demo path: onboarding,
decision resolution, graph clusters, node dragging, wave-colored batch launch,
live log streaming, and executor calibration. Screenshots land in
`frontend/e2e-artifacts/`.
