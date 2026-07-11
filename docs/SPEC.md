# SpindleGraph — Product & Technical Specification

Status: **v0 draft** · Last updated: 2026-07-03

SpindleGraph is a standalone, local, GUI-driven control plane that orchestrates
Claude Code agents to build software from specs. It points at a target git
repository, models that repo's specs as structured records, derives a dependency
graph from which files each spec touches, lets the user run a spec-driven
workflow visually (generate specs, resolve decisions, build in parallel), and
keeps the specs consistent with reality through a post-build reconciliation loop.

**What it is not:** a Claude Code plugin. Plugins extend Claude Code from inside
its TUI and cannot render a GUI, run a server, or own a database. SpindleGraph is
a separate app the user clones and runs; it *drives* Claude Code from the outside
via the headless CLI (`claude -p`), migrating to the Claude Agent SDK in v2.

---

## 1. Confirmed decisions

| Decision | Choice |
|---|---|
| Product / repo name | **SpindleGraph** (this repo) |
| Frontend | React + Vite + TypeScript, graph canvas via **React Flow** |
| Backend | Python + **FastAPI**, **SQLite**, WebSockets for live logs |
| Agent execution | Claude Code **headless CLI** (`claude -p`) for v0/v1; Agent SDK in v2 |
| Workflow commands | **Bundled in this repo** (`commands/`), copied into target repos on onboarding |
| Executors | Pluggable **Executor** interface from v0 (per-spec model/agent choice); Claude Code is the only v0 backend. Codex CLI, local models (e.g. quantized Qwen3-Coder) plug in later |
| Build "success" (calibration signal) | Project checks pass **and** a PR opens — automatic, recorded per job |
| Success probabilities | Hand-set prior per executor, auto-updated from real build outcomes (Beta update); shown on graph **nodes** (v1) |
| Failed builds | Manual retry in v0 (inspect, relaunch with a different executor); no auto-escalation |
| Spec bodies | Markdown files in the target repo are canonical; DB is a synced projection + metadata |
| App data | Lives outside target repos (`~/.spindlegraph/`); never committed into a target repo |
| Deployment | Local web app first; packaging (pipx/npx launcher, desktop) deferred to v2 |

---

## 2. Glossary

- **Target repo** — the user's project that SpindleGraph operates on. Contains
  `specs/*.md` and a `CLAUDE.md`.
- **Spec** — one unit of intended work: a markdown file `specs/NNNN-slug.md` in
  the target repo, mirrored as a DB record with derived metadata.
- **Conflict edge** — an undirected graph edge between two specs whose
  `modifies_files` intersect. Conflicting specs must not build in parallel.
- **Wave** — a set of pairwise non-conflicting specs that can be built
  simultaneously in separate git worktrees.
- **Executor** — a (backend, model) pair that can run a build: e.g.
  Claude Code × Sonnet, Claude Code × Fable, later Codex CLI × GPT-5.5 or a
  local harness × quantized Qwen3-Coder. Each spec build is assigned one.
- **Success probability** — a per-node estimate of P(this spec builds
  successfully with its assigned executor). Distinct from edge *weight*:
  weight measures merge-conflict risk between two specs built in parallel;
  probability measures whether one spec's build lands at all.
- **Reconciliation** — the post-build pass that replaces a spec's *planned* file
  list with the *actual* git diff, re-derives the graph, and proposes edits to
  the remaining specs.

### A note on "dependency" vs "conflict"

File overlap does **not** imply a semantic dependency — it implies a *merge
conflict risk*. v0 therefore treats overlap edges as **conflict edges**
(undirected, "don't parallelize"), not ordering constraints. Ordering between
two conflicting specs defaults to ascending spec number. A separate, optional
`depends_on` field (manually set, or proposed by an agent) expresses true
ordering ("spec B needs spec A's API to exist"). The graph canvas renders both:
conflict edges (undirected, weighted) and dependency edges (directed).

---

## 3. Architecture

```
┌─────────────────────────────── SpindleGraph (this repo) ──────────────────────────────┐
│                                                                                       │
│  frontend/  React + Vite + React Flow          backend/  FastAPI + SQLite             │
│  ┌──────────────────────────────┐   REST/WS   ┌────────────────────────────────────┐  │
│  │ Spec board · Graph canvas    │◄───────────►│ API ─ Store ─ Importer ─ Graph     │  │
│  │ Runner · Batch composer      │             │ Engine ─ Orchestrator ─ Reconciler │  │
│  └──────────────────────────────┘             └───────────────┬────────────────────┘  │
│  commands/  bundled /triage /spec /build /build-batch          │ subprocess            │
└────────────────────────────────────────────────────────────────┼───────────────────────┘
                                                                 ▼
                        target repo (user's project)   +   ~/.spindlegraph/ (app state)
                        ├─ specs/NNNN-slug.md              ├─ spindlegraph.db
                        ├─ CLAUDE.md                       ├─ worktrees/<project>/<slug>/
                        └─ .claude/commands/*.md           └─ logs/<job_id>.ndjson
```

### Processes & ports

- **Dev:** two processes — `uvicorn` on `127.0.0.1:8787`, Vite dev server on
  `5173` proxying `/api` and `/ws` to 8787.
- **Non-dev:** FastAPI serves the built frontend from `frontend/dist` on 8787.
  Binds to localhost only; no auth (single local user is an explicit v0 assumption).

### Repo layout (this repo)

```
backend/
  spindlegraph/
    main.py            # FastAPI app factory, static serving, WS
    config.py          # state dir (~/.spindlegraph) + global config
    db.py              # SQLite schema, seeds, helpers
    events.py          # project-scoped event bus feeding the WS
    importer.py        # specs/*.md ⇄ Spec records
    graph.py           # edge derivation, waves, independent sets
    orchestrator/
      executors.py     # executor → argv, P estimate, cost math
      worktrees.py     # git worktree lifecycle + command sync
      jobs.py          # job state machine, runner, log fan-out
    reconcile.py       # v1
    api/routes.py      # REST routers (request models inline)
  requirements.txt     # plain venv + pip
  tests/               # unit + integration (fake_claude.py stands in for the CLI)
frontend/
  src/                 # React + TS: board, canvas, runner, composer, config
  package.json
commands/              # bundled slash-command templates (see §9)
docs/SPEC.md           # this file
README.md
```

### App state directory (`~/.spindlegraph/`)

- `spindlegraph.db` — the SQLite store (all projects).
- `worktrees/<project_slug>/<spec_slug>/` — build worktrees. **Never inside the
  target repo**, so they can't dirty it or get committed.
- `logs/<job_id>.ndjson` — raw agent event streams, kept after job completion.
- `config.json` — global settings (claude binary path, default model,
  max parallel builds).

---

## 4. Spec file format (canonical)

Path in target repo: `specs/NNNN-slug.md` — `NNNN` is a zero-padded number
unique per repo, `slug` is kebab-case. Both are assigned at creation (by the
`/spec` command or the GUI) and never renamed by SpindleGraph.

```markdown
---
title: Add rate limiting to the public API
status: draft            # draft | decided | building | built | stale
---

# Add rate limiting to the public API

## Summary
One or two paragraphs: what and why.

## Affected files
- `src/api/middleware.py` — add limiter middleware
- `src/config.py` — new RATE_LIMIT_* settings
- `tests/test_middleware.py` — new

## Decisions needed
- [x] Algorithm? → token bucket
- [ ] Counter store: redis or in-memory?

## Risk
- **Involvement:** Moderate — middleware + config, two areas
- **Review attention:** High — sits in the request path of every API call

## Implementation notes
Free-form guidance for the build agent.
```

### Parsing rules (importer contract)

Parsing is tolerant — hand-written specs predate SpindleGraph:

- **Frontmatter** (YAML) is optional. `title` falls back to the first `# H1`,
  then to a de-kebabed filename. `status` falls back to `draft`.
- **Number & slug** come from the filename, not the content.
- **Affected files**: the first heading (any level) matching
  `/^(affected|modified?|touched) files?$/i` or `/^files$/i`. Each list item
  yields one path: the first inline-code span if present, else the first
  whitespace-delimited token. Text after `—`/`--`/`:` is stored as the
  rationale. Paths are normalized to repo-relative, forward slashes. Globs are
  expanded against the repo tree at import time (expansion recorded, pattern
  kept). Paths that don't exist yet are kept verbatim and flagged `planned_new`.
- **Decisions needed**: checkbox list items under a heading matching
  `/^decisions? (needed|required)?$/i`. Checked = resolved; the answer is the
  text after `→` or `**Answer:**` if present.
- **Risk** (template v2, 2026-07-04): list items under `/^risks?$/i` carrying
  two axes — **Involvement** (Minimal | Moderate | Involved: how big/spread-out
  the change is) and **Review attention** (Low | Medium | High: how closely
  the author should supervise). Distinct axes: a large but well-isolated
  change can be Involved yet Low-attention; a tiny diff to core logic can be
  Minimal yet High. Rationale text after `—` is kept per axis. Missing/
  unparsed risk is treated as zero.
- Everything else is opaque body text, stored verbatim in `body_md`.

### Sync semantics

- The **file is the source of truth for content** (title, body, affected files,
  decisions). The **DB is the source of truth for derived/operational state**
  (edges, probabilities, job provenance).
- `status` lives in frontmatter so it survives outside SpindleGraph; the
  importer reads it, and SpindleGraph writes it back to the file on state
  changes (e.g. `building` → `built`). This is the one field SpindleGraph
  writes into spec files routinely.
- Import runs: on project add, on a manual **Refresh** action, and after any
  job that touched `specs/`. v0 has **no filesystem watcher** (explicit
  non-goal; Refresh is cheap). If a file changed on disk since last import, the
  file wins; DB-only fields are preserved.
- Deleting a spec file marks the record `archived` (not deleted) so provenance
  survives.

---

## 5. Data model (SQLite)

JSON columns are TEXT with JSON content; SQLite is accessed via the stdlib
`sqlite3` module (no ORM), with `PRAGMA foreign_keys=ON` and WAL mode.

```sql
CREATE TABLE project (
  id            INTEGER PRIMARY KEY,
  slug          TEXT NOT NULL UNIQUE,      -- derived from dir name, editable
  name          TEXT NOT NULL,
  repo_path     TEXT NOT NULL UNIQUE,      -- absolute path to target repo
  notes_doc_path TEXT,                     -- optional, for /triage
  default_branch TEXT NOT NULL DEFAULT 'main',
  settings_json TEXT NOT NULL DEFAULT '{}',-- per-project overrides (model, permission mode, max_parallel)
  created_at    TEXT NOT NULL
);

CREATE TABLE executor (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE,      -- "Sonnet", "Fable", "Qwen3-Coder (local)"
  backend       TEXT NOT NULL,             -- claude_code (only v0 value) | codex_cli | local_oss …
  model         TEXT,                      -- backend-specific model id (--model value for claude_code)
  prior_success REAL NOT NULL DEFAULT 0.8, -- hand-set starting estimate, editable in GUI
  prior_strength REAL NOT NULL DEFAULT 10, -- how many pseudo-builds the prior is worth
  successes     INTEGER NOT NULL DEFAULT 0,-- real recorded outcomes (see §10)
  failures      INTEGER NOT NULL DEFAULT 0,
  input_price_per_mtok  REAL,              -- USD per 1M input tokens; 0/NULL for local models
  output_price_per_mtok REAL,              -- USD per 1M output tokens
  avg_build_cost_usd REAL,                 -- running mean of actual per-build cost (see §10)
  enabled       INTEGER NOT NULL DEFAULT 1
);
-- Estimated P(success) = (prior_success·prior_strength + successes)
--                        / (prior_strength + successes + failures)      [Beta mean]
-- Calibration is global per executor in v1 (not per-project) — see D5.

CREATE TABLE spec (
  id            INTEGER PRIMARY KEY,
  project_id    INTEGER NOT NULL REFERENCES project(id),
  number        INTEGER NOT NULL,          -- NNNN from filename
  slug          TEXT NOT NULL,
  title         TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'draft',
                -- draft | decided | building | built | stale | archived
  file_path     TEXT NOT NULL,             -- repo-relative: specs/0001-foo.md
  body_md       TEXT NOT NULL,             -- verbatim file content at last import
  body_hash     TEXT NOT NULL,             -- to detect out-of-band edits
  files_planned_json TEXT NOT NULL DEFAULT '[]',
                -- [{path, rationale, planned_new, from_glob}]
  files_actual_json  TEXT NOT NULL DEFAULT '[]',
                -- [{path, change}] from git diff after build (v1 reconcile)
  decisions_json     TEXT NOT NULL DEFAULT '[]',
                -- [{text, resolved, answer}]
  depends_on_json    TEXT NOT NULL DEFAULT '[]',  -- [spec_id], manual/proposed ordering
  executor_id   INTEGER REFERENCES executor(id),  -- NULL → project default executor
  provenance_json    TEXT NOT NULL DEFAULT '{}',
                -- {branch, commit, pr_url, built_at, job_id}
  updated_at    TEXT NOT NULL,
  UNIQUE (project_id, number)
);

CREATE TABLE edge (            -- derived; wiped & recomputed per project on import/reconcile
  project_id    INTEGER NOT NULL REFERENCES project(id),
  spec_a        INTEGER NOT NULL REFERENCES spec(id),   -- spec_a < spec_b (canonical order)
  spec_b        INTEGER NOT NULL REFERENCES spec(id),
  shared_files_json TEXT NOT NULL,         -- [path]
  weight        REAL NOT NULL,             -- see §6
  overridden    INTEGER NOT NULL DEFAULT 0,-- user pinned weight; survives recompute
  PRIMARY KEY (project_id, spec_a, spec_b)
);

CREATE TABLE job (
  id            INTEGER PRIMARY KEY,
  project_id    INTEGER NOT NULL REFERENCES project(id),
  kind          TEXT NOT NULL,             -- triage | spec | build | build_batch | reconcile | scaffold
  spec_ids_json TEXT NOT NULL DEFAULT '[]',
  parent_job_id INTEGER REFERENCES job(id),-- build_batch spawns child build jobs
  status        TEXT NOT NULL,             -- queued | running | succeeded | failed | canceled
  executor_id   INTEGER REFERENCES executor(id),
  outcome       TEXT,                      -- build jobs only: success | failure per §10's
                                           -- definition (checks passed + PR opened); feeds calibration
  usage_json    TEXT NOT NULL DEFAULT '{}',-- accumulated from stream: {input_tokens, output_tokens,
                                           --  cache_read_input_tokens, cache_creation_input_tokens}
  cost_usd      REAL,                      -- computed at job end from executor pricing (see §10)
  command       TEXT NOT NULL,             -- the exact claude invocation, for audit
  worktree_path TEXT,
  branch        TEXT,
  pr_url        TEXT,
  exit_code     INTEGER,
  error         TEXT,
  log_path      TEXT NOT NULL,             -- ~/.spindlegraph/logs/<id>.ndjson
  started_at    TEXT,
  finished_at   TEXT,
  created_at    TEXT NOT NULL
);
```

---

## 6. Graph engine

Recomputed for a project after every import and reconcile. Pure functions over
Spec records — no I/O — so it's trivially unit-testable.

- **Effective file set** of a spec = `files_actual` if the spec is `built`,
  else `files_planned`.
- **Conflict edges:** for every pair with a non-empty intersection of effective
  file sets, emit an edge with:
  - `shared_files` — the intersection.
  - `weight = |A ∩ B| / min(|A|, |B|)` — overlap coefficient in `(0, 1]`.
    Chosen over Jaccard so a 2-file spec fully contained in a 40-file spec
    scores 1.0 (maximal conflict), not 0.05. Raw shared count is also returned
    for display.
  - Edges the user has `overridden` keep their pinned weight across recomputes
    (shared_files still refresh). Success *probabilities* are node-level, not
    edge-level — see §10.
- **Parallel-safe check** (batch composer): a selected set is safe iff it
  induces no conflict edges. The API returns the offending edges otherwise.
- **Wave suggestion:** repeatedly extract a maximal independent set from the
  conflict subgraph of the remaining selected specs, respecting `depends_on`
  (a spec can't be waved before its dependencies). Greedy pick order —
  **risk first**: `risk_score` (Involvement + Review attention ranks, 0–4)
  descending, so high-risk specs are scheduled earliest (their failures
  cascade into skipped conflicting neighbors, and the author's supervision is
  freshest at batch start); then lowest conflict degree, then lowest total
  conflict weight, then ascending spec number. Within a wave, specs are
  listed riskiest-first for review order. This is a heuristic, not optimal
  (max-independent-set is NP-hard); at realistic sizes (< 50 specs) it's fine,
  and v1's analyses can improve orderings.
- Specs with **zero effective files** (unparsed/empty "Affected files") are
  rendered as disconnected nodes flagged "unknown footprint" and are treated as
  conflicting-with-everything for batch safety (conservative default,
  overridable per launch).

---

## 7. Orchestrator

### Executors

Every build job runs under an **executor** — a named (backend, model) pair
(see `executor` table, §5). The runner is written against a small interface:

```python
class Executor(Protocol):
    def build_command(self, prompt: str, opts: JobOptions) -> list[str]  # argv
    def parse_events(self, stdout_lines) -> Iterator[AgentEvent]          # normalize to one event shape
```

- **Backends (v2, shipped 2026-07-04):**
  - `claude_code` — the claude CLI as a subprocess (invocation contract
    below). Executors differing only by `model` are just rows; the GUI seeds
    sensible defaults.
  - `claude_sdk` — the Claude Agent SDK, in-process (`pip install
    claude-agent-sdk`); events are normalized to the same stream-json shapes,
    so logs/WS/UI are backend-agnostic.
  - `local_cli` — any local coding agent invocable as a command line (aider,
    a codex wrapper, an ollama harness…): the executor's `command_template`
    runs with `{prompt}` substituted; plain-text output streams as raw log
    events; exit 0 = success. This is the "configurable local coding agent"
    from the v2 milestone — probabilities/cost calibrate per executor row as
    usual, which is exactly how weak-but-free local models earn their place.
- **Assignment:** each spec may pin an executor (`spec.executor_id`); unset
  falls back to the project's default executor. The batch composer shows and
  edits per-spec assignments before launch; the job records which executor
  actually ran (`job.executor_id`).
- **Failure handling (v0):** no auto-retry. A failed build keeps its worktree
  and logs; the GUI offers "retry", pre-opening the executor picker so the
  natural move — rerun with a stronger model — is one click. An auto-escalation
  ladder is a possible v2 batch option, deliberately out of scope now.

### Invocation contract (claude_code backend)

All agent work is `claude -p` run as a subprocess **with `cwd` = the target
repo** (or a worktree of it):

```
claude -p "<prompt>" \
  --output-format stream-json --verbose \
  --permission-mode acceptEdits \
  --allowedTools "<per-kind allowlist, see below>" \
  [--model <from the job's executor row>]
```

- **stream-json** gives an NDJSON event stream on stdout; the runner parses
  each line, appends it to the job's log file, and fans it out over WebSocket.
  The final `result` event carries the outcome text.
- **Permissions:** headless runs can't answer prompts, so each job kind ships a
  tool allowlist: builds get `Edit`, `Write`, and `Bash(git *)`, `Bash(gh *)`,
  plus the project's configured test/build commands; triage/spec get read tools
  + `Write(specs/**)`. A per-project setting can escalate to
  `--dangerously-skip-permissions` for fully-isolated worktree builds — off by
  default, surfaced in the GUI with a warning. **Open decision D2, §17.**
- Env passes through the user's environment (Claude Code auth comes from their
  existing login). `CLAUDE_PROJECT_DIR` etc. are left to the CLI.
- **Timeout** per job (default 30 min, configurable); on timeout or user
  cancel, the process tree is terminated, the job marked `canceled`, and the
  worktree left in place for inspection.

### Job kinds

| kind | prompt | cwd | result capture |
|---|---|---|---|
| `scaffold` | init pass drafting `CLAUDE.md`, creating `specs/` | repo | files created |
| `triage` | `/triage <notes_doc_path>` | repo | new `specs/*.md` → import |
| `spec` | `/spec "<idea text>"` | repo | new `specs/NNNN-*.md` → import |
| `build` | `/build specs/NNNN-slug.md` | **worktree** | branch, commits, PR URL |
| `build_batch` | decomposed by SpindleGraph into waves of `build` child jobs | worktrees | per-child |
| `reconcile` | v1, §10 | repo | proposed spec edits |

Note `build_batch` is **not** delegated to a `/build-batch` slash command:
SpindleGraph itself is the batch engine (it owns the graph), launching one
`build` job per spec, wave by wave, `max_parallel` (default 3) at a time. The
bundled `/build-batch` command file still exists for CLI-only users of the
workflow, but the GUI path doesn't use it.

### Worktree lifecycle (builds)

1. `git worktree add ~/.spindlegraph/worktrees/<proj>/<NNNN-slug> -b spec/<NNNN-slug> <default_branch>` .
2. Ensure `.claude/commands/` in the worktree has the bundled command files
   (copy if missing/outdated — they're committed to the target repo on
   onboarding, so normally present).
3. Run the `build` job there. The `/build` command instructs the agent to
   implement the spec, run the project's checks, commit, push, and open a PR
   via `gh` (skipped gracefully when no remote/`gh`; the branch is the
   deliverable then).
4. **PR URL capture:** parse the stream for `gh pr create` output /
   `https://github.com/.../pull/\d+` in the result; fallback
   `gh pr view --json url` in the worktree. Stored on job + spec provenance.
5. On success: spec `status` → `built` (written back to frontmatter, committed
   on the spec branch), worktree removed (`git worktree remove`), branch kept.
   On failure: worktree kept for inspection, GUI offers "clean up".

### Wave execution (build_batch)

- Wave N+1 starts only when every job in wave N reaches a terminal state.
- A failed build does not halt the batch, but any not-yet-started spec that
  conflicts with the *failed* spec is skipped (its assumptions may be wrong),
  reported as `skipped_due_to_conflict`.
- Because every build branches from `default_branch` (not from each other),
  waves reduce *merge-conflict risk among open PRs*, not literal rebase chains.
  Sequencing conflicting specs across waves assumes the user merges wave-N PRs
  before wave-N+1 builds run — the GUI states this and shows unmerged-PR
  warnings when launching a wave whose predecessors aren't merged.
  **Open decision D3, §17.**

---

## 8. WebSocket & API surface

REST under `/api`, WebSockets under `/ws`. Shapes are illustrative, not final.

```
POST   /api/projects                {repo_path, name?, notes_doc_path?}
GET    /api/projects
GET    /api/projects/{id}
POST   /api/projects/{id}/import            # re-scan specs/
POST   /api/projects/{id}/scaffold          # create specs/, draft CLAUDE.md (job)
GET    /api/projects/{id}/specs
GET    /api/specs/{id}
PATCH  /api/specs/{id}                      # edit body/status/decisions → writes file, reimports
GET    /api/projects/{id}/graph             # nodes + conflict/dependency edges
PATCH  /api/edges/{a}/{b}                   # override weight/probability
POST   /api/projects/{id}/graph/check       {spec_ids} → {safe, conflicts[], suggested_waves[][]}
GET    /api/executors                       # roster incl. calibrated rates
POST   /api/executors                       # add executor
PATCH  /api/executors/{id}                  # edit prior/model/enabled
POST   /api/jobs                            {project_id, kind, spec_ids?, prompt?, executor_id?, options?}
GET    /api/jobs?project_id=…
GET    /api/jobs/{id}                       # incl. log tail
POST   /api/jobs/{id}/cancel
WS     /ws/projects/{id}                    # project-scoped event bus:
                                            #   job.created/updated, job.log (NDJSON passthrough),
                                            #   spec.updated, graph.updated
```

One project-scoped WebSocket (not per-job) so the board, canvas, and runner all
stay live off a single connection; `job.log` events carry `job_id` for routing.

---

## 9. Bundled workflow commands (`commands/`)

Markdown command files, treated as templates: on onboarding (and before
any job) SpindleGraph copies them into the target repo's `.claude/commands/`
(committing on onboarding, with user consent). They defer to the target repo's
`CLAUDE.md` for stack conventions.

- **`triage.md`** — read the notes doc, cluster into candidate work items,
  emit a checklist of one-line candidates *and* a machine-readable
  ```json {"candidates":[…]}``` block (does *not* write specs). The GUI parses
  the block into a picker: the user checks candidates and fans out one `/spec`
  job each, or opens a `/spec-chat` on a candidate to develop it conversationally.
  (The notes doc often lives outside the repo, e.g. an Obsidian vault; the job
  gets read access to its directory via `--add-dir` rather than embedding the
  content, which would exceed the ~32K Windows command-line limit.)
- **`spec-chat.md`** — develop one spec back-and-forth. A turn-based
  conversation: each user reply resumes the same claude session server-side
  (`--resume <session_id>`, captured from the stream-json `init` event), so
  context carries without re-sending a growing transcript. The agent asks
  clarifying questions, then writes/updates `specs/NNNN-slug.md` when the key
  decisions settle, tagging the file with a `SPEC_FILE: <path>` marker so
  SpindleGraph links the chat to the spec. Persisted in `spec_chat` /
  `spec_chat_message`; each turn is a tracked job (kind `spec_chat`).
- **`spec.md`** — take one idea/bug; ground it in the codebase (search, read);
  write `specs/NNNN-slug.md` in the canonical format (§4) — next free number,
  explicit **Affected files** with rationale, explicit **Decisions needed** for
  anything genuinely ambiguous. Must not start implementing.
- **`build.md`** — take one spec path; refuse if unresolved decisions remain;
  implement it, honoring Affected files as a plan (deviations allowed but must
  be reflected by updating the spec's Affected files section in the same
  branch); run the project's checks; commit with the spec number in the
  message (`spec-0001: …`); push and open a PR via `gh` when available; report
  the PR URL on the final line.
- **`build-batch.md`** — CLI-convenience wrapper (topological/wave ordering by
  hand); the GUI does not use it (§7).

Exact command prose is authored during v0 implementation and reviewed like code.

---

## 10. Reconciliation & success probabilities (v1)

Trigger: a `build` job succeeds (or the user points at a merged PR/commit range).

1. **Capture reality:** in the worktree/branch,
   `git diff --name-status <default_branch>...HEAD` → `files_actual` (excluding
   the spec file itself). Provenance (branch, commit, PR) recorded.
2. **Re-derive:** graph recomputes with the built spec now using
   `files_actual` (§6). Any spec whose edge set *changed* (new conflict, or a
   planned conflict that evaporated) is marked **`stale`**.
3. **Agent pass:** one `reconcile` job per stale spec (bounded parallel):
   prompt = "spec X just landed with this actual diff; here is your spec;
   propose edits (updated Affected files, changed assumptions, or 'no change
   needed') as a unified diff of the spec file." Proposals are stored on the
   job, **not** applied.
4. **Review UI:** per stale spec, show proposal diff → Accept (write file,
   reimport, clear `stale`) / Reject (clear `stale`, keep file) / Edit-then-accept.

### Success probabilities

Semantics (settled 2026-07-03, resolving old D1): probability is a **node
property**, not an edge property. Each spec node displays
**P(build succeeds | assigned executor)** so the user can assign models
intelligently — strong/expensive executors where failure hurts, cheap/local
ones where the work is easy or low-blast-radius.

- **Success definition** (the calibration signal): a build counts as a
  *success* iff the project's checks pass in the worktree **and** a PR is
  opened (branch-only counts when the repo has no remote/`gh`). Recorded
  automatically on `job.outcome` the moment the job ends. Agent-process
  failures, timeouts, and cancellations count as *failure*; user-canceled jobs
  count as nothing.
- **Estimate**: Beta-mean per executor —
  `P = (prior_success·prior_strength + successes) / (prior_strength + successes + failures)`.
  The prior is hand-set per executor in the config panel (e.g. Fable 0.95,
  local Qwen 0.60) and every recorded outcome moves the estimate toward
  reality. Calibration is **global per executor** across projects in v1 (D5).
- **Not perplexity-based** — deliberately. Model perplexity doesn't track
  agentic build success (tool use, long-horizon planning, repo test friction
  dominate). The estimator is pluggable, so a signal-derived prior
  (benchmarks, perplexity) can be slotted in as an *initializer* later; the
  empirical update stays the backbone.
- **Difficulty scaling** (per-spec modifier: file count, `planned_new` files,
  unresolved decisions shrink P) is a planned refinement, not in the first v1
  cut — see D6.

**Node × edge analyses** these unlock (v1, canvas overlays):

- **Wave success**: P(all specs in a wave land) = Π node probabilities;
  expected number of manual interventions per wave.
- **Assignment advice**: flag high-degree/high-weight nodes carrying
  low-probability executors — a failure there also skips its conflicting
  neighbors (§7 wave rules), so its *effective* cost is amplified.
- **Monte-Carlo over orderings**: sample build outcomes from node
  probabilities across candidate wave orderings; report distribution of
  completed-spec counts and expected retries per ordering; recommend one.

### Cost display (resolves D7)

Cost sits next to probability everywhere an executor is assigned, making
assignment an explicit **cost-vs-confidence tradeoff**.

- **Actual cost (recorded):** the `claude_code` stream-json events carry
  `usage` (input, output, cache-read, cache-creation tokens); the runner
  accumulates them onto `job.usage_json` and computes at job end:
  `cost = (in·p_in + out·p_out + cache_read·0.1·p_in + cache_write·1.25·p_in) / 1M`
  using the executor's pricing. Local backends report usage without pricing →
  cost 0.
- **Pricing** lives on the executor row, seeded from published rates at setup
  (2026-07: Fable $10/$50, Opus $5/$25, Sonnet 5 $3/$15, Haiku $1/$5 per
  MTok) and **editable in the config panel** — prices change (e.g. Sonnet 5's
  intro pricing) and SpindleGraph must not hardcode them.
- **Estimated cost (pre-launch):** the executor's `avg_build_cost_usd` — a
  running mean of its recorded build costs, seeded with a hand-set guess the
  same way priors are. Difficulty scaling (D6) will refine this per spec
  later; v1 shows the executor average.
- **Expected cost to success:** `E = est_cost / P` — the expected total spend
  if you keep retrying on the same executor until it lands (geometric). This
  is the headline number in the composer: a $0 local model at P=0.6 and a
  $2 Sonnet at P=0.9 stop being incomparable — E surfaces the retry tax
  (though for local models the tax is your *time*, which the
  assignment-advice overlay already flags via skipped conflicting neighbors).
- **Display:** composer launch review gets per-spec columns
  `executor · P · est $ · E[$ to success]` plus batch totals; node tooltips
  show the same; the config panel shows each executor's live avg cost next to
  its calibrated success rate.

---

## 11. GUI (v0 scope)

Single-page app, project switcher in the header. Views:

1. **Spec board** — columns by status (draft / decided / building / built /
   stale). Card: number, title, file-count badge, unresolved-decision badge,
   PR link when built. Click → spec drawer: rendered body, editable markdown,
   decision checklist with inline resolve (writes the file via PATCH),
   affected-files list (planned vs actual once built).
2. **Graph canvas** — React Flow. Nodes colored by status; conflict edges with
   thickness ∝ weight, hover shows shared files; dependency edges as arrows.
   Node badge shows the assigned executor (and, from v1, its success
   probability). Selecting nodes drives the batch composer (below). Layout:
   dagre/ELK auto-layout, positions not persisted in v0.
3. **Runner** — command palette: Triage (needs notes doc), New Spec (idea text
   box), Build (spec picker). Job list with live status; clicking a job opens
   the **log pane** streaming `job.log` events (rendered as: assistant text,
   tool calls collapsed to one line, result). Cancel button.
4. **Batch composer** — multi-select specs (board or canvas); live safety
   check via `/graph/check`; canvas highlights conflicts in red and suggested
   waves by hue; per-spec **executor picker** in the launch review (defaults
   shown, editable in place) with `P · est $ · E[$ to success]` columns and
   batch totals (v1); launch → build_batch job; wave progress + per-spec PR
   links.
5. **Config** — global: claude binary path, max_parallel, and the **executor
   roster** (add/edit executors, set priors; v1 shows each executor's live
   calibrated rate and outcome counts); per-project: default executor,
   permission escalation toggle, default branch, notes doc path.

Non-goals for v0 GUI: auth, mobile, drag-to-create edges, spec creation by
hand in the GUI (use /spec — hand-authoring bypasses grounding), graph position
persistence, dark/light theming polish.

---

## 12. Onboarding a target repo

"Add project" flow:

1. User enters an absolute repo path → validated (exists, is a git repo, has a
   default branch).
2. Missing `specs/` → offer to create (empty dir + `.gitkeep`).
3. Missing `CLAUDE.md` → offer a `scaffold` job (Claude Code init pass drafting
   it for user review — shown as a diff before committing).
4. Copy bundled commands into `.claude/commands/` (diff-shown if files exist
   and differ; user confirms overwrite).
5. Offer to commit the scaffolding (`specs/`, `CLAUDE.md`, commands) as one
   commit on the default branch — or leave uncommitted.
6. Import specs, derive graph, land on the board.

Prereqs surfaced as a checklist with live checks: git repo ✓, `claude` on PATH
and authenticated (`claude --version` + a cheap `-p` ping) ✓, `gh` optional ✓,
notes doc optional.

---

## 13. Configuration

Precedence: job options > project settings > global config > defaults.

| Setting | Default | Scope |
|---|---|---|
| `claude_bin` | `claude` (PATH) | global |
| executor roster (backends, models, priors) | seeded Claude models | global |
| `default_executor` | Claude Code × CLI-default model | project |
| executor assignment | project default | spec / job |
| `max_parallel` | 3 | global / project |
| `job_timeout_min` | 30 | global / project |
| `permission_escalation` (`--dangerously-skip-permissions`) | off | project |
| `default_branch` | detected | project |

---

## 14. Security & safety

- Server binds `127.0.0.1` only. No auth in v0 (documented assumption).
- Nothing from the target repo (bodies, diffs, logs) is ever written inside
  SpindleGraph's own repo — only under `~/.spindlegraph/` and the target repo
  itself.
- Headless agents run with scoped allowlists by default (§7); the bypass toggle
  is per-project, off by default, and visually loud.
- SpindleGraph never force-pushes, never commits to the default branch except
  the consented onboarding commit, and never deletes branches it didn't create.
- Job records store the exact command line for audit.

---

## 15. Milestones & acceptance criteria

### v0 — data backbone + graph + run commands (shipped 2026-07-03)

Done when this demo path works end-to-end:

1. Add a project by path; onboarding scaffolds/verifies `specs/`, `CLAUDE.md`,
   commands.
2. Import ≥ 3 hand-written specs; board shows them; opening one shows parsed
   decisions and affected files.
3. Graph canvas renders nodes + conflict edges with correct shared files.
4. Run `/spec "<idea>"` from the Runner; live logs stream; the new spec file
   appears in the repo, auto-imports, and shows up on board + graph.
5. Run a single `/build` on a worktree with a chosen executor (Claude model
   picker); logs stream; branch (and PR when `gh` present) captured; spec
   flips to `built`; `job.outcome` recorded.
6. Batch composer: select 3 specs where 2 conflict; safety check flags the
   pair; launch waves; two parallel worktree builds then the third; PR links
   listed.

### v1 — reconciliation + probabilities (shipped 2026-07-04)

- Post-build reconcile: `files_actual` captured, graph re-derived, stale specs
  get agent proposals, review UI applies/rejects. Acceptance: build a spec that
  deviates from plan → a conflicting spec goes stale → proposal shown → accept
  rewrites its file.
- Success probabilities live: outcomes recorded, executor rates calibrating,
  node badges on the canvas, wave-success and assignment-advice overlays; edge
  weight editing. Monte-Carlo over orderings may slip to late v1.
- Cost display live: usage captured per job, executor pricing + avg cost
  calibrating, `P / est $ / E[$]` columns in the composer with batch totals.

### v2 — local agent + polish (shipped 2026-07-04)

- ✅ Executor backends: `claude_sdk` (Claude Agent SDK, in-process) and
  `local_cli` (any local coding agent via command template) beside
  `claude_code`; per-spec/per-build choice in the GUI, added/edited in the
  executor roster.
- ✅ Multi-project UX: switcher (v0) + remove-project (records only, repo
  untouched).
- ✅ One-command launcher: `pip install ./backend` → `spindlegraph` (serves
  API + bundled UI, opens browser). Desktop shell (Tauri) deliberately not
  pursued — the launcher covers the need.
- Spec template v2: `## Risk` section (Involvement × Review attention),
  parsed by the importer and driving wave ordering (§6) and board/canvas/
  composer badges.

---

## 16. Testing strategy

- **Importer:** golden-file tests — a fixtures directory of messy real-world
  spec markdown → expected parsed records.
- **Graph engine:** pure-function unit tests (overlap, waves, depends_on
  interaction, unknown-footprint conservatism).
- **Orchestrator:** unit-test the state machine with a **fake `claude`
  executable** (a script emitting canned stream-json) so CI never needs
  credentials; one opt-in integration test behind an env flag runs the real CLI.
- **Frontend:** component tests for board/composer logic; the canvas is
  smoke-tested only.
- **E2E demo path** (§15 v0) scripted against a throwaway fixture repo created
  by the test harness.

---

## 17. Open decisions

- **D1 — Probability semantics. RESOLVED 2026-07-03:** node-level
  P(build succeeds | executor), calibrated from real outcomes — see §10. (The
  user's Substack post may still refine the *analyses*; share when handy.)
- **D2 — Default permission posture for headless builds.** Current spec:
  `acceptEdits` + per-kind allowlists, opt-in bypass. Needs validation against
  real builds early in v0 (allowlists may prove too brittle for arbitrary
  target repos — if so, flip the default for worktree builds and say so loudly).
- **D3 — Cross-wave base branch.** Waves currently all branch from
  `default_branch`, assuming the user merges between waves. Alternative:
  wave N+1 branches from a wave-N integration branch. Deferred until batch
  builds are exercised for real; revisit with data.
- **D4 — Spec numbering collisions.** Two concurrent `/spec` jobs could both
  pick the next free `NNNN`. v0 mitigation: SpindleGraph serializes spec-kind
  jobs per project. Revisit if that's ever limiting.
- **D5 — Calibration scope.** Executor success rates are global across
  projects in v1 (more data, faster convergence) even though difficulty varies
  by repo. Alternative: per-project rates, or global with per-project offsets.
  Revisit once there's enough outcome data to see whether repos diverge.
- **D6 — Difficulty scaling.** Per-spec modifiers on P (file count, new files,
  unresolved decisions, LOC touched by conflicting built specs). Deferred past
  the first v1 cut; needs outcome data to fit against, not guesses.
- **D8 — Risk semantics. RESOLVED 2026-07-04:** the spec template's `## Risk`
  section (Involvement, Review attention — see §4) feeds a 0–4 `risk_score`
  that *leads* the wave-ordering sort: riskiest specs build earliest and list
  first within a wave. Chosen because a risky failure cascades (conflicting
  neighbors are skipped) and author supervision is freshest at batch start.
- **D7 — Cost awareness. RESOLVED 2026-07-03:** yes — per-executor pricing +
  token capture from the stream, estimated cost and expected-cost-to-success
  shown beside probability in the composer. See §10 → Cost display.

---

## 18. Limit-hit handling & auto-restart (planned)

Real builds hit two very different provider limits, and they need opposite
handling. Conflating them is the trap.

- **Spend cap** (Claude "monthly spend limit · raise it at claude.ai/settings/
  usage"): a hard cap you set. Does **not** clear on its own — resets monthly
  or when you raise it. Auto-retrying after N hours just fails again.
- **Rolling rate limit** (Claude's ~5-hour session window; `429`/overloaded):
  **does** clear on its own after the window.

### Shipped (2026-07-08): distinct limit state
`classify_limit(error)` tags a *failed* job as `spend_capped` | `rate_limited`
| `null` (regex over the error text; spend checked first). Surfaced on the job
as `limit_hit` and rendered as a distinct pill in the Runner (`$ spend cap` /
`⏳ rate limit`) so these read at a glance instead of buried in the error text.
Classification only — no retry yet.

### Planned: auto-restart on rate limit
- **State:** a rate-limited job moves to a `paused` state with a persisted
  `retry_at` (durable on the job row — must survive a server restart, since a
  wait can be hours; a startup sweep re-queues due jobs). A spend-capped job
  does **not** auto-retry — it surfaces for user action.
- **Probe, don't guess:** `retry_at` defaults to now + the executor's window
  (Claude ≈ 5h, configurable per executor), but the sweeper doesn't fire the
  expensive build blindly — it first sends a cheap `claude -p "ok"` ping and
  only relaunches once the ping succeeds, re-probing on an interval otherwise.
- **Resume, don't restart:** the failed build kept its worktree, so the retry
  resumes partial work rather than paying twice.
- **UI:** `⏸ paused — retrying ~HH:MM` with "retry now" / "cancel auto-retry".

### Planned: executor failover (often better than waiting)
Because executors are pluggable and calibrated independently, the higher-value
response to a rate limit is frequently **failover to a non-Claude executor**
(Codex, a local model) rather than waiting 5 hours — already observed working
when Claude was capped. Per-project policy: on rate-limit → *wait-and-retry* or
*failover to executor X* (or try failover first, fall back to waiting).

### Open decisions
- **D9 — retry vs failover default.** Which is the default on rate-limit:
  wait-and-retry, executor-failover, or failover-then-wait? Likely per-project
  config; default TBD from real use.
- **D10 — worktree hold.** A paused build holds its worktree for the whole wait
  (simple, resumes cleanly) vs. frees it and re-cuts later (tidier, loses
  partial progress). Leaning hold.
- **D11 — window source.** Is the retry window a hard per-executor default (5h
  for Claude) or read from a `retry-after`/reset hint if the CLI ever exposes
  one? Probe-gating makes the exact value non-critical.
