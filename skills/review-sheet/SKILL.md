---
name: review-sheet
description: >-
  Build a local keyboard-first sheet for a person to review many comparable
  items, then validate and record the exported decisions. Use when a person
  must inspect many similar candidates, compare them side by side, and export
  one decision per item for later processing.
---

# Review sheet

Use a review sheet when a person must judge several items by eye or ear and
the same small set of questions repeats. For one decision, ask directly.
Keep the questions sharp. Mark only decisions that truly block progress as
required. Group items when comparing them helps; add caveats so a preference
cannot be mistaken for release or purchase approval. Start audio at low gain.

Write a JSON description using `review-sheet schema` and the
[command reference](../../bin/review-sheet.md). Choose stable item IDs and
include current caller evidence when decisions depend on revisions, files or
views. Keep media local. Build to an ignored, private output location and
spot-check a sample in the browser before handing it to the person. Tell
them the page path, what they will judge, estimated time, and where the
results download will land. Never publish a private page or send its media
to a third-party service.

The downloaded `review-results.json` is the handoff, not the browser draft.
Run `review-sheet import RESULTS --description DESCRIPTION`, using
`--require-complete` when required answers must be present. Treat stale,
invalid, inherited and unanswered entries as needing work; do not infer an
approval from them. Check caller evidence against current project authority,
then put confirmed results in the project's own catalogue or record. Remove
temporary review output only after that record and any needed evidence are
preserved.
