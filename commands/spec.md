---
description: Turn one idea or bug into a grounded, structured spec file
---

Turn the following idea into a spec file: $ARGUMENTS

You are writing a **spec**, not implementing anything. Follow this process:

1. **Ground the idea in this codebase.** Search and read the files the change
   would plausibly touch. Defer to CLAUDE.md for stack conventions. If the
   idea references behavior, find where that behavior lives before writing.
2. **Pick the filename.** Specs live in `specs/` as `NNNN-slug.md`. Use the
   next free zero-padded number (check existing files in BOTH specs/ and specs/implemented/) and a short kebab-case
   slug.
3. **Write the spec** in exactly this shape:

   ```markdown
   ---
   title: <one-line imperative title>
   status: draft
   ---

   # <same title>

   ## Summary
   One or two paragraphs: what and why, grounded in what you found in the code.

   ## Affected files
   - `path/to/file.py` — why it changes
   - `path/to/new_file.py` — new

   ## Decisions needed
   - [ ] <a genuinely ambiguous question the implementer cannot answer alone>

   ## Risk
   - **Involvement:** Minimal | Moderate | Involved — <short rationale>
   - **Review attention:** Low | Medium | High — <short rationale>

   ## Implementation notes
   Concrete guidance for the build agent: entry points, existing patterns to
   follow, tests to add.
   ```

Rules:
- **Affected files must be real, specific repo-relative paths** you verified
  (or explicitly new files). This list drives conflict detection between
  specs — err on the side of listing every file the change will touch,
  including tests and config.
- **Decisions needed** is only for questions that genuinely change the
  implementation (algorithm choice, user-facing behavior, data migration).
  Do not pad it; an empty section is fine — but then set `status: decided`.
- **Risk** has two axes, one bullet each, each with a short rationale:
  **Involvement** (Minimal | Moderate | Involved) — how big/spread-out the
  change is (files and areas touched); **Review attention** (Low | Medium |
  High) — how closely the author should supervise it. Keep these distinct: a
  large but well-isolated change can be low-danger yet still Involved (lots to
  review), while a tiny diff to core logic can warrant High review. Call out
  what specifically drives the rating (prod migration, carry-forward/backfill,
  auth, open-ended scope, new pillar, etc.). SpindleGraph schedules
  higher-risk specs earlier in build batches.
- Do NOT modify any other file. Do NOT start implementing.
- End your final message with the path of the spec file you wrote.
