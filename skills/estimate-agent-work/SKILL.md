---
name: estimate-agent-work
description: >-
  Estimate story points when creating or materially refining tracked work,
  before starting unestimated work, or when asked for sizing or useful
  decomposition. Skip unchanged scope and routine status updates.
---

# Estimate agent work

When scope can be sized, choose one value: **1, 2, 3, 5, 8, 13 or 20**.
Unset means unestimated.
Size relative work through accepted completion, including relevant
investigation, implementation, tests, integration and review fixes. Points
do not measure fixed minutes, tokens, importance or failure impact; keep
wall-time and per-model token estimates separate.

Use available local reference tasks without treating their timings as fixed
conversions or their provisional calibration as precise. Otherwise use a
rough progression: 1 trivial, 2 small, 3 bounded, 5 moderate, 8 substantial,
13 large, 20 work-package sized. Judge scope and verification needs, not
domain labels. Usually provide just the size; add a sentence when a material
assumption or uncertainty changes how it should be understood.

Include known discovery work. When a major unknown prevents useful sizing,
leave implementation unestimated and propose a bounded investigation:
question, evidence deliverable and stopping condition. Size experiments by
the work needed to produce evidence, without promising a successful result.

Split only when it improves independent verification, isolates a genuine
prerequisite or major unknown, or enables safe parallel work. Keep ordinary
implementation and its tests together. Give each executable piece an
observable completion condition and account for integration and end-to-end
acceptance. Larger sizes prompt consideration, not mandatory splitting.
Parallel work needs compatible interfaces, edits and resources. Dependencies
represent actual blocking requirements; explain the result each needs.
Child estimates need not sum to a parent; do not count both in a total.

Use existing task records for estimates and rationale when a material
assumption or split needs explanation; see the tool's documentation for
commands and permissions. Propose splits unless task creation is authorized.
Do not create incidental tasks
to assign points, require reports or checklists, or re-estimate unchanged
scope. Update for material scope changes with a brief explanation, not to
match elapsed time. Label retrospective estimates. Advisory requests do
not authorize backlog edits. Keep private calibration out of public examples.
