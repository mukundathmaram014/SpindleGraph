---
description: Implement several specs in dependency order (CLI convenience; the SpindleGraph GUI orchestrates batches itself)
---

Implement these specs, in a safe order: $ARGUMENTS

Note: when driven from the SpindleGraph GUI, batching is handled by the app
(one isolated worktree per spec, parallel waves). This command is the manual,
sequential fallback for CLI-only use.

1. Read every listed spec. Refuse any spec with unresolved "Decisions needed"
   items — report and continue with the rest.
2. Compare their "Affected files" sections. Specs whose file lists intersect
   MUST be built sequentially, in ascending spec-number order; independent
   specs may be built in any order.
3. For each spec, in the order you determined:
   a. Create a branch `spec/NNNN-slug` off the default branch.
   b. Follow the full /build procedure for that spec (implement, update spec,
      run checks, commit, PR via `gh` when available).
   c. Return to the default branch before starting the next spec.
4. Finish with a table: spec number, title, branch, PR URL (or failure
   reason), one row per spec.
