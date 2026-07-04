# SpindleGraph Development Guide

## Layout

```
backend/spindlegraph/     FastAPI app (see docs/ARCHITECTURE.md for the map)
backend/tests/            pytest suites + fake agents
frontend/src/             React app (views/ per tab)
frontend/e2e/             Playwright suite + env setup
commands/                 workflow command templates copied into target repos
docs/                     SPEC (design record) · ARCHITECTURE · USER-GUIDE · this file
```

## Dev loop

```sh
# backend, terminal 1  (restart by hand after backend changes)
cd backend && .venv/Scripts/python -m uvicorn spindlegraph.main:app --port 8787

# frontend, terminal 2 (hot reload; proxies /api + /ws to :8787)
cd frontend && npm run dev        # http://localhost:5173
```

The backend loads its projects and DB from `~/.spindlegraph/` (or
`SPINDLEGRAPH_HOME` if set) — start it with the **same** home each time or it
comes up with no projects. Use a throwaway `SPINDLEGRAPH_HOME` while developing
so you don't pollute your real state: `SPINDLEGRAPH_HOME=/tmp/sg-dev uvicorn …`.

Don't use `uvicorn --reload`: its graceful shutdown hangs on the app's
long-lived WebSocket bus and job tasks, wedging the port. Restart the backend
by hand after changing backend code.

## Testing

Three layers, all offline, no credentials:

1. **Backend** (`cd backend && .venv/Scripts/python -m pytest tests -q`)
   - Pure-unit: importer golden strings, graph engine (edges, waves, risk
     ordering), executor math.
   - Integration: FastAPI TestClient + a real git fixture repo, with
     **`tests/fake_claude.py`** standing in for the claude CLI — it emits
     canned stream-json per prompt kind (`/build` commits + prints a PR URL,
     `/spec` writes a spec file, reconcile prompts echo a revised body).
     `FAKE_CLAUDE_FAIL=1` / `FAKE_CLAUDE_TOUCH=<path>` steer failure and
     plan-deviation cases. `tests/fake_local.py` plays a `local_cli` agent;
     the `claude_sdk` backend is tested by stubbing `sys.modules["claude_agent_sdk"]`.
2. **E2E** (`cd frontend && npm run build && npx playwright test`)
   - Boots a private backend on :8788 with a generated demo repo
     (`e2e/setup.mjs`) and drives the real UI in headless Chromium through
     the full demo path. Screenshots land in `frontend/e2e-artifacts/`.
   - Gotcha baked into the config: Playwright starts the webServer **before**
     globalSetup, so env prep runs inside the webServer command chain — don't
     move it.
3. **Type-check/build**: `cd frontend && npm run build` (tsc + vite).

Run all three before pushing; they're the definition of green.

## Conventions & gotchas

- **DB**: stdlib `sqlite3`, WAL, JSON-in-TEXT columns (`*_json`), converted
  via `db.row_to_dict`. Schema changes to existing tables must be **additive**
  and registered in `db.MIGRATIONS` (checked with `PRAGMA table_info` at
  startup) — the CREATE TABLE in `SCHEMA` only covers fresh databases.
- **Events**: anything that changes specs/graph/jobs must publish on
  `events.bus` (`specs.updated`, `graph.updated`, `job.updated`, `job.log`,
  `proposals.updated`) — the UI refreshes purely off these.
- **Jobs** must always reach a terminal state (`succeeded/failed/canceled/
  skipped`); `_run` has a defensive catch, but don't rely on it.
- **Graph engine stays pure** (no I/O) — that's what keeps it unit-testable.
- **Importer is tolerant by contract** (SPEC §4): don't make parsing stricter
  without a golden test showing hand-written specs still import.
- The **spec file is canonical for content**; SpindleGraph writes into spec
  files only via explicit user actions (status write-back, decision resolve,
  drawer save, accepted proposals). Never write repo files from background
  logic.

## Adding an executor backend

1. `orchestrator/executors.py`: add the name to `BACKENDS`; teach
   `build_argv()` (subprocess backends) or add an in-process path in
   `jobs.py` modeled on `_exec_sdk` (normalize events to the stream-json dict
   shapes: `system` / `assistant` / `result` / `raw`).
2. `describe_command()` must return an audit string for the job row.
3. Validation in `api/routes.py` (`create_executor` / `patch_executor`).
4. UI: `BACKENDS` list in `views/Config.tsx` picks it up; add any
   backend-specific field the way `command_template` is handled.
5. Tests: a fake agent script (subprocess) or a module stub (in-process),
   plus one end-to-end build through the API.

## Packaging

`backend/pyproject.toml` exposes the `spindlegraph` console script
(`spindlegraph/cli.py` → uvicorn + browser). The server serves the UI from
`frontend/dist` (repo clone) or `spindlegraph/static` (installed package) —
to ship a self-contained wheel, copy `frontend/dist/*` into
`backend/spindlegraph/static/` before `python -m build`.

## Releasing changes to the workflow commands

`commands/*.md` are copied into every target repo's `.claude/commands/` on
onboarding and refreshed before each job when they differ. Changing a
template therefore propagates on next use — keep the templates in lockstep
with what the importer parses (especially `## Affected files`, `## Decisions
needed`, `## Risk`).
