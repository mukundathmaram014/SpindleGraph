# SpindleGraph Architecture (as built)

This describes the system as it exists today (v2). [`SPEC.md`](SPEC.md) is the
design record — decisions, rationale, and milestones; this document is the
map of the running code. When they disagree, this file wins.

## The one-paragraph version

SpindleGraph is a local web app: a **FastAPI backend** (SQLite store, job
orchestrator, WebSocket event bus) serving a **React frontend** (spec board,
graph canvas, runner, config). It points at *target git repos* you own,
mirrors their `specs/*.md` files into structured records, derives a conflict
graph from file overlap, and drives coding agents — Claude Code or any local
CLI agent — in isolated git worktrees to build specs in parallel waves, one
branch + PR per spec. Every agent event streams to disk and to the browser
live. Nothing SpindleGraph stores ever lives inside a target repo.

## Components

```
frontend/ (React + Vite + React Flow)        backend/ (FastAPI + stdlib sqlite3)
┌────────────────────────────┐   REST /api   ┌──────────────────────────────────┐
│ App shell (project switch) │◄─────────────►│ api/routes.py    one router      │
│ Board · SpecDrawer         │   WS /ws      │ importer.py      md ⇄ records    │
│ GraphView · composer       │◄─────────────►│ graph.py         edges, waves    │
│ Runner · log pane          │               │ reconcile.py     stale + props   │
│ Config · executor roster   │               │ events.py        in-proc bus     │
│ Reconcile drawer           │               │ orchestrator/                    │
└────────────────────────────┘               │   executors.py   backend → argv  │
                                             │   worktrees.py   git worktrees   │
commands/ (bundled /triage /spec             │   jobs.py        job engine      │
           /build /build-batch)              │ db.py · config.py · main.py      │
                                             └──────────────────────────────────┘
```

### Storage — two sources of truth, deliberately split

| What | Lives in | Owner |
|---|---|---|
| Spec content (title, body, files, decisions, risk) | `specs/NNNN-slug.md` in the target repo (git-versioned) | The file. Re-import always wins for content. |
| Operational state (edges, jobs, probabilities, costs, proposals) | `~/.spindlegraph/spindlegraph.db` | The DB. Never written into the repo. |

The importer (`importer.py`) is the sync point: tolerant markdown parsing
(frontmatter optional, heading regexes, glob expansion against the repo tree),
upsert by `(project, number)`, archive-on-file-delete. Two exceptions to
"file wins": `built` and `stale` statuses are operational states SpindleGraph
owns — a re-import never silently downgrades them (a spec built on an unmerged
branch still reads `draft` in the default-branch file).

The app state dir (`~/.spindlegraph/`, override `SPINDLEGRAPH_HOME`) holds the
DB, `config.json`, `logs/<job>.ndjson`, and `worktrees/<project>/<spec>/` —
worktrees are intentionally *outside* the repo so they can't dirty it.
`db.init_db()` applies additive column migrations on startup (see
`db.MIGRATIONS`).

### Graph engine (`graph.py`)

Pure functions, no I/O:

- **Conflict edges**: for each spec pair, intersect *effective files*
  (`files_actual` once built, else `files_planned`). Weight = overlap
  coefficient `|A∩B| / min(|A|,|B|)` — a small spec fully inside a big one
  scores 1.0. Edges are conflicts ("don't parallelize"), **not** dependencies;
  the optional `depends_on` list carries true ordering.
- **Wave suggestion**: repeated greedy maximal-independent-set, respecting
  `depends_on`. Pick order: **risk score descending** (Involvement +
  Review-attention ranks, 0–4 — riskiest specs build earliest because their
  failures cascade), then lowest conflict degree, weight, spec number. Specs
  with no parseable file list conservatively conflict with everything.
- Edge recompute runs after every import/reconcile; user-pinned (overridden)
  weights survive.

### Orchestrator (`orchestrator/`)

A job is one row in the `job` table plus one asyncio task. Kinds: `triage`,
`spec`, `scaffold` (run in the repo, re-import on success), `build` (runs in a
worktree), `build_batch` (pure orchestration — no agent of its own), and
`reconcile` (v1).

**Executor backends** (`executors.py`) — an executor row = name + backend +
model/template + calibration + pricing:

| Backend | Runs as | Events |
|---|---|---|
| `claude_code` | `claude -p "<prompt>" --output-format stream-json` subprocess, `--permission-mode acceptEdits` (or `--dangerously-skip-permissions` when the project opts in) | NDJSON per line |
| `claude_sdk` | Claude Agent SDK in-process (`jobs._exec_sdk`), guarded import | SDK messages normalized to the same dict shapes |
| `local_cli` | any command template with `{prompt}` substituted | plain text lines → `raw` events; exit 0 = success |

Because all three normalize to the same event shapes, the log file, WebSocket
channel, and UI are backend-agnostic. Every event is appended to the job's
NDJSON log *and* published on the in-process bus (`events.py`), which fans out
to `/ws/projects/{id}` subscribers. PR URLs are regex-captured from the
stream; usage/cost come from the final `result` event (or are computed from
executor pricing: cache reads 0.1×, cache writes 1.25× input price).

**Build lifecycle**: semaphore (`max_parallel`) → spec marked `building` →
`git worktree add -b spec/NNNN-slug` under the state dir → bundled commands
copied into the worktree → agent runs `/build specs/…` → on success: capture
`files_actual` via `git diff <default>...HEAD`, mark `built`, record
provenance + executor outcome (Beta calibration + running avg cost), remove
worktree, then trigger reconciliation; on failure: roll status back, keep the
worktree for inspection.

**Batches**: waves run sequentially; within a wave, one child build job per
spec in parallel. A failure marks later conflicting specs
`skipped_due_to_conflict`. All builds branch from the default branch — the UI
warns that wave N+1 assumes wave N's PRs are merged (open decision D3).

**Reconciliation** (`reconcile.py`, v1): after a successful build, re-derive
the graph with actual files; any spec whose conflict set changed goes `stale`
and gets a `reconcile` job whose prompt embeds the actual diff + the stale
spec. The agent's proposed rewrite lands in `reconcile_proposal` (never
auto-applied); the Reconcile drawer accepts (write file + re-import) or
rejects (restore prior status).

### Probability & cost model

Node-level, per executor: `P = (prior·strength + wins) / (strength + wins +
losses)` — a hand-set prior Beta-updated by real outcomes, where success =
job succeeded (checks pass + PR opens, per the /build contract). Cost:
per-job actuals from the stream; per-executor running average feeds the
composer's `est $` and `E[$ to success] = est / P` columns, plus wave/batch
roll-ups (`P(all land) = Π p`, expected retries `Σ(1−p)`).

### Frontend

Single-page React app; state lives in `App.tsx` (projects, specs, executors,
proposals) and refreshes on WebSocket events (`specs.updated`,
`graph.updated`, `job.updated`, `job.log`, `proposals.updated`). The graph
canvas is React Flow with a custom deterministic force layout
(`views/layout.ts` — springs on conflict edges, repulsion otherwise, so
*distance ≈ independence*) and center-anchored floating edges
(`views/FloatingEdge.tsx`). Dragged node positions persist across data
refreshes but not reloads.

## Request flow, end to end

```
Add project ──► POST /api/projects: validate git repo, create specs/ if
                missing, copy commands/, detect default branch, import
Import ───────► parse specs/*.md → upsert records → recompute edges
                → WS specs.updated + graph.updated
Compose ──────► POST graph/check {spec_ids} → {safe, conflicts, waves}
Launch ───────► POST /api/jobs {kind: build_batch, waves} → child build jobs
                → worktrees → agents → job.log WS stream → PRs → built
                → reconcile jobs → proposals → review drawer
```

## Trust & safety boundaries

- Server binds `127.0.0.1`; no auth (single local user by design).
- Agents run with `acceptEdits` + the target repo's own permission config
  unless the project explicitly enables the bypass flag (off by default,
  loudly labeled).
- SpindleGraph never force-pushes, never commits to the default branch except
  consented onboarding, never deletes branches it didn't create, and removing
  a project deletes only SpindleGraph's records.
- Every job row stores the exact command line for audit; raw event logs are
  kept after completion.

## Known limitations

- Queued jobs don't survive a server restart (in-process asyncio tasks).
- Spec markdown renders as monospace text, not rich HTML.
- Wave sequencing assumes you merge earlier waves' PRs before later waves run.
- The `claude_sdk` backend is exercised against a stubbed SDK in tests; live
  runs require `pip install claude-agent-sdk`.
