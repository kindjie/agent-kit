---
name: review-sheet
description: >-
  Builds a local keyboard-first sheet for a person to review many comparable
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
views. Reuse a shipped or repository preset when it fits; save a new preset
only for fields reusable across batches. A project component is executable
code: review its files, then trust its exact bundle digest with
`review-sheet component trust PATH`. Never let a description grant trust.

Keep media local. Build to an ignored, private output location and preserve
the page with its `review.resolved.json`. Spot-check a sample in Chrome
before handing it to the person. For synchronized frame comparisons, verify
the adapter's extracted source indices, crops, image fidelity, page size,
and browser memory on the actual review cases. The generic builder does not
extract video frames. Tell the person the page path, what they will judge,
estimated time, and where results download. Never publish a private page or
send its media to a third-party service.

The downloaded `review-results.json` is the handoff, not the browser draft.
Run `review-sheet import RESULTS --resolved PAGE.resolved.json
--check-current`, using `--require-complete` when required answers must be
present. Embedded media verification is separate from a decision's authority;
a verified visual preference is still only a preference. Treat stale,
invalid, inherited and unanswered entries as needing work. Check current
input currency and caller evidence against the project's own authority,
then put confirmed results in its catalogue or record. Remove temporary
review output only after that record and any needed evidence are preserved.
