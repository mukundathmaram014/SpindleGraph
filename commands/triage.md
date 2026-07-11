---
description: Mine a notes/ideas document into a list of candidate specs
---

Triage the following ideas/notes document: $ARGUMENTS

The document's content is provided inline above (SpindleGraph read it for you —
it may live outside this repo, so do NOT try to open it as a file). Read enough
of this codebase (start with CLAUDE.md and the top-level layout) to judge each
idea against reality.

Produce a **triage report** — do NOT write any spec files and do NOT modify
any code. For each distinct work item you find in the notes:

- One line: `- [size: S/M/L] <imperative candidate title> — <one-sentence
  grounding in the codebase (which area it touches, or "new surface")>`
- Cluster duplicates and overlapping ideas into a single candidate; note what
  you merged.
- Flag items that are too vague to spec with `[needs clarification: <what>]`.
- Flag items that appear already done in the code with `[already exists?]`.

Order the list by your judgment of value-for-effort, best first. End with a
short "Suggested next" line naming the 1–3 candidates you'd spec first.

Then, as the **very last thing in your final message**, emit the same
candidates as a machine-readable block so SpindleGraph can offer them as a
one-click "create specs" picker. Use exactly this fenced shape — a single
JSON object with a `candidates` array, best-first (same order as the report):

```json
{"candidates": [
  {"title": "<imperative candidate title>", "size": "S|M|L",
   "grounding": "<one-sentence grounding>", "flag": null},
  {"title": "...", "size": "L", "grounding": "...", "flag": "needs_clarification"},
  {"title": "...", "size": "S", "grounding": "...", "flag": "already_exists"}
]}
```

Rules for the block:
- Include **every** candidate from your report, in the same order.
- `flag` is `null` for clean, ready-to-spec candidates; `"needs_clarification"`
  for vague ones; `"already_exists"` for ones that look already done. The UI
  pre-selects only the `null`-flag candidates.
- `title` is what gets handed to /spec, so make it a self-contained imperative.
- Output nothing after this block.
