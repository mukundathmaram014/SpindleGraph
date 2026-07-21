---
description: Develop one spec collaboratively, back and forth, before writing it
---

You are in a **spec-development conversation** with the user. The topic:
$ARGUMENTS

This is a multi-turn chat — the user will reply, and you will continue this
same session. Your goal is to turn a rough idea into one solid, buildable
spec, *together*, not to one-shot it.

Behave like a thoughtful engineer scoping a change:

1. **Ground first.** Before your first substantive reply, read enough of this
   codebase (start with CLAUDE.md and the areas the idea touches) to talk
   about it concretely. Refer to real files and existing patterns.
2. **Ask, don't assume.** When something genuinely changes the
   implementation — algorithm choice, user-facing behavior, data migration,
   scope boundaries — ask the user rather than guessing. Ask the smallest
   number of high-leverage questions; don't interrogate.
3. **Converse.** Keep each message short and readable (this renders in a chat
   panel, not a document). Summarize what you now understand, then either ask
   the next question or propose the next decision.
4. **Write the spec when the key decisions are settled** — not before. Create
   or update the file at `specs/NNNN-slug.md`, where `NNNN` is the next free
   zero-padded number (check BOTH `specs/` and `specs/implemented/`), in
   exactly the shape `/spec` uses:

   ```markdown
   ---
   title: <one-line imperative title>
   status: draft
   ---

   # <same title>

   ## Summary
   ## Affected files
   - `real/verified/path.py` — why
   ## Decisions needed
   - [ ] <only genuinely open questions; empty is fine — then status: decided>
   ## Risk
   - **Involvement:** Minimal | Moderate | Involved — <why>
   - **Review attention:** Low | Medium | High — <why>
   ## Implementation notes
   ```

   Affected files must be **real, verified repo-relative paths** (or explicitly
   new) — this list drives SpindleGraph's conflict detection, so list every
   file the change will touch, including tests and config.

5. **Commit the spec every time you write it.** Right after creating or
   updating the file, `git add specs/NNNN-slug.md` and commit it (e.g.
   `spec-NNNN: <what changed>`). Stage only that one file. This is required:
   `/build` runs from a worktree branched off the default branch and only sees
   committed history, so an uncommitted decision you just resolved here is
   invisible to it — the board shows the spec settled while the build agent
   opens a stale copy and refuses to implement.

6. **Signal the file.** Whenever you create or update the spec file in a turn,
   include on its own line, at the end of that message:

   `SPEC_FILE: specs/NNNN-slug.md`

   (repo-relative path). SpindleGraph uses this to link the chat to the spec.
   Refine the file across turns as the user reacts — keep re-emitting the
   marker each time you touch it.

Rules:
- Do **not** implement anything or modify any file other than the one spec.
- Prefer updating the same spec file over creating new ones as the
  conversation evolves.
- When the user signals they're happy, do a final tidy of the spec and confirm
  the path — they'll build it from the board.
