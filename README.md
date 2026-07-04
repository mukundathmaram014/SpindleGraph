# SpindleGraph

A standalone, local, GUI-driven control plane that orchestrates Claude Code
agents to build software from specs. Point it at a target git repo; it models
the repo's `specs/*.md` as structured records, derives a conflict/dependency
graph from which files each spec touches, runs a spec-driven workflow visually
(triage → spec → build, in parallel where safe via git worktrees), and keeps
specs consistent with reality through a post-build reconciliation loop.

It is **not** a Claude Code plugin — it's a separate app that drives Claude
Code from the outside via the headless CLI (`claude -p`).

**Status:** specification phase. The full product & technical spec lives at
[`docs/SPEC.md`](docs/SPEC.md).

## Planned stack

- **Frontend:** React + Vite + TypeScript, graph canvas via React Flow
- **Backend:** Python + FastAPI + SQLite, WebSockets for live agent logs
- **Agents:** Claude Code headless CLI now; Claude Agent SDK later
