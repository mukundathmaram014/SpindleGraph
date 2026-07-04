# SpindleGraph User Guide

SpindleGraph turns a pile of ideas into reviewed PRs by driving coding agents
through a spec-first loop: **triage → spec → resolve decisions → build (in
parallel waves) → reconcile**. This guide walks the whole loop.

## Setup

Prerequisites: git, Python 3.11+, Node 20+, [Claude Code](https://claude.com/claude-code)
installed and authenticated (`claude --version` works), and optionally the
`gh` CLI (enables automatic PRs).

```sh
# one-time
cd frontend && npm install && npm run build && cd ..
cd backend && python -m venv .venv && .venv/Scripts/pip install -e .

# every time (either)
backend/.venv/Scripts/spindlegraph        # serves :8787 and opens a browser
# or: backend/.venv/Scripts/python -m uvicorn spindlegraph.main:app --port 8787
```

The header shows the detected `claude` version; a red warning means the CLI
isn't on PATH (fix it or point "claude binary" at it in Config). All
SpindleGraph state lives in `~/.spindlegraph/` — never in your repos.

**Try it without spending tokens:** point "claude binary" in Config at
`python <repo>/backend/tests/fake_claude.py`. Every command then runs against
a canned agent — the full loop works offline.

## 1. Add a project

Click **+ Add project** and paste the absolute path of a local git repo you
own. SpindleGraph will create `specs/` if it's missing, copy the four
workflow commands into the repo's `.claude/commands/`, detect the default
branch, and import any existing specs. Commit those additions when you're
happy with them.

Optional: set a **notes doc** path (Config tab) — a freeform ideas/TODO file
that `/triage` can mine.

## 2. Get specs

- **Runner → `/spec "your idea"`** — an agent reads your codebase, grounds
  the idea, and writes `specs/NNNN-slug.md`. It auto-imports on completion.
- **Runner → `/triage notes doc`** — mines your notes file into a ranked list
  of candidate specs (report only; you pick which to `/spec`).
- Hand-written specs work too — drop `specs/NNNN-slug.md` files in the repo
  and hit **↻ Refresh from repo** on the Board. The parser is tolerant, but
  the canonical shape (SPEC.md §4) parses best:

```markdown
---
title: Add rate limiting to the public API
status: draft
---

# Add rate limiting to the public API

## Summary
What and why.

## Affected files
- `src/api/middleware.py` — add limiter
- `src/config.py` — settings

## Decisions needed
- [ ] Counter store: redis or in-memory?

## Risk
- **Involvement:** Moderate — middleware + config
- **Review attention:** High — sits in every request path
```

**Affected files matters most** — it drives conflict detection between specs.
**Risk** has two deliberately distinct axes: *Involvement* (Minimal /
Moderate / Involved — how big/spread-out the change is) and *Review
attention* (Low / Medium / High — how closely you should supervise). Riskier
specs are scheduled earlier in batches and badged (⚑) across the UI.

## 3. Board — read, edit, resolve

Cards are columned by status (`draft → decided → building → built`; `stale`
surfaces in the draft column). Click a card to open the drawer:

- **Decisions** — each unresolved ❓ blocks building. Click **Resolve**, type
  the answer; it's written back into the markdown file itself.
- **Executor** — pin which agent/model builds this spec (empty = project
  default).
- **Markdown** — edit the raw spec; **Save to file** writes to the repo and
  re-imports.
- **▶ Build** — build just this spec (disabled until decisions are resolved).

## 4. Graph — compose a batch

Every spec is a node; a **red edge** means two specs touch the same files and
must not build in parallel (thickness = overlap; hover the pill for the file
list). The layout is meaningful: **distance ≈ independence** — conflicting
specs cluster, independent ones drift apart. Drag to rearrange; ↺ re-layout
resets.

Click nodes to select a batch. The composer then shows:

- conflicts inside your selection, and the suggested **waves** — colored
  rings + W1/W2 tags on the nodes match the table groupings. Waves run one
  after another; everything within a wave runs in parallel worktrees.
  Within the ordering, riskier specs go earliest.
- per spec: executor picker, **P** (that executor's calibrated success rate),
  **est $** (its average build cost), and **E[$ to success]** (est ÷ P — the
  retry tax priced in). Wave and batch roll-ups: P(all land), expected
  retries, total expected cost.
- **assignment advice** (◆): a low-P executor on a spec that conflicts with
  others is flagged — a failure there also skips its neighbors.

**Launch batch** creates one worktree + branch (`spec/NNNN-slug`) + PR per
spec. Merge a wave's PRs before the next wave runs for conflict-free merges.

## 5. Runner — watch it happen

Every job (spec, build, batch, triage, reconcile) appears in the list with
live status and cost. Click one to stream its log: agent text, tool calls
(→), and the final result with the PR link. Cancel kills the process tree.
Failed builds keep their worktree on disk for inspection — retry from the
drawer with a different executor.

## 6. Reconcile — keep specs honest

After a build lands, SpindleGraph replaces that spec's *planned* file list
with the **actual git diff** and re-derives the graph. Any other spec whose
conflict picture changed goes **stale**, and an agent drafts a proposed
rewrite. The **⟳ Reconcile** badge in the header opens the review drawer:
a line diff per proposal, **Accept** (rewrites the spec file) or **Reject**
(keeps it, clears stale). Nothing is ever auto-applied.

## 7. Config

- **Global**: claude binary path, max parallel builds (default 3), job
  timeout.
- **Project**: notes doc, default branch, **Remove project** (SpindleGraph
  records only — your repo is untouched), and the
  `--dangerously-skip-permissions` toggle — off by default; only enable it if
  headless builds stall on permission prompts and you trust the isolation of
  worktree builds.
- **Executor roster**: each row is an agent you can assign to specs —
  backend, model or command template, hand-set prior P, prices per MTok, live
  calibrated P and win/loss record, average build cost. Three backends:
  - `claude_code` — the claude CLI (default).
  - `claude_sdk` — Claude Agent SDK in-process (`pip install
    claude-agent-sdk` into the backend venv first).
  - `local_cli` — **any local coding agent** runnable as a command. Template
    gets `{prompt}` substituted, e.g. `aider --yes -m "{prompt}"`; exit 0
    counts as success. Pair a free local model with a low prior and let its
    real record earn (or lose) trust.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| "claude CLI not found" in header | Install Claude Code or set the binary path in Config |
| Build hangs, then times out | Agent stalled on a permission prompt: add allowlist entries to the target repo's `.claude/settings.json`, or enable the per-project bypass toggle |
| Build succeeded but no PR link | No git remote or no `gh` — the branch is the deliverable; push/PR manually |
| Spec shows wrong files | Check the `## Affected files` list parses (repo-relative paths, one per bullet); globs expand against the repo tree |
| Spec stuck in `building` after a crash | Restart the server; re-run the build (queued jobs don't survive restarts) |
| Board out of date after editing files outside the app | **↻ Refresh from repo** |
