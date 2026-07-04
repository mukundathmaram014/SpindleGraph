---
description: Implement one spec on the current branch and open a PR
---

Implement the spec at: $ARGUMENTS

Preconditions — check these before writing any code:
1. Read the spec file. If its "Decisions needed" section has any unresolved
   (unchecked) items, STOP and report them instead of implementing.
2. Read CLAUDE.md for stack conventions, and read every file listed under
   "Affected files".

Implementation:
3. You are already on a dedicated branch in an isolated worktree — do not
   create or switch branches.
4. Implement the spec. Treat "Affected files" as the plan: deviating is
   allowed when reality demands it, but then **update the spec's Affected
   files section in this same branch** so it reflects what you actually
   touched.
5. Update the spec's frontmatter to `status: built`.
6. Run the project's checks (tests, linter, build — whatever CLAUDE.md or the
   repo's config defines). Fix failures you introduced. If checks fail for
   pre-existing reasons, say so explicitly in the PR body.

Ship:
7. Commit everything with a message starting `spec-NNNN: ` (the spec's
   number). Small logical commits are fine; at least one is required.
8. If a remote and the `gh` CLI are available: push the branch and open a PR
   titled with the spec title, whose body summarizes the change, links the
   spec file, and notes check results. If there is no remote or no `gh`, skip
   this — the branch is the deliverable.
9. The LAST line of your final message must be the PR URL if one was created,
   otherwise `BRANCH: <branch-name>`.
