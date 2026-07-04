---
description: Turn one idea or bug into a grounded, structured spec file
---

Turn the following idea into a spec file: $ARGUMENTS

You are writing a **spec**, not implementing anything. Follow this process:

1. **Ground the idea in this codebase.** Search and read the files the change
   would plausibly touch. Defer to CLAUDE.md for stack conventions. If the
   idea references behavior, find where that behavior lives before writing.
2. **Pick the filename.** Specs live in `specs/` as `NNNN-slug.md`. Use the
   next free zero-padded number (check existing files) and a short kebab-case
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
- Do NOT modify any other file. Do NOT start implementing.
- End your final message with the path of the spec file you wrote.
