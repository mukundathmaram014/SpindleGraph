---
description: Revise an already-built spec on its existing branch, addressing user feedback
---

You previously implemented a spec on this branch, and it's under review. The
user has feedback: $ARGUMENTS

You are already on the spec's existing branch in an isolated worktree — the
prior implementation is here and there may be an open PR for it. Do NOT create
or switch branches; your commits must land on this branch so they ride the
existing PR.

1. Read the spec file (in `specs/` or `specs/implemented/`) and CLAUDE.md, and
   look at what this branch already changed (`git log`, `git diff` against the
   default branch) so you understand the current implementation.
2. Address the feedback. It usually reports a bug or a gap in what was built —
   investigate the actual cause in the code before changing anything; don't
   just pattern-match the symptom.
3. If the fix changes which files the spec touches, update the spec's "Affected
   files" section to match.
4. Run the project's checks. Fix failures you introduce.
5. Commit with a message starting `spec-NNNN: ` describing the revision (the
   spec's number). At least one new commit is required.
6. Push the branch. The PR updates automatically; if there is no PR yet and
   `gh` is available, create one. Report the PR URL on the last line, else
   `BRANCH: <branch-name>`.
