---
name: estimate-agent-work
description: >-
  Estimates points, wall time and tokens for new or materially refined tracked
  work, before starting unestimated work, or when asked for sizing or useful
  decomposition. Skip unchanged scope and routine status updates.
---

# Estimate agent work

When scope can be sized, choose one value: **1, 2, 3, 5, 8, 13 or 20**.
Unset means unestimated.
Size relative work through accepted completion, including relevant
investigation, implementation, tests, integration and review fixes. Points
do not measure fixed minutes, tokens, importance or failure impact; keep
wall-time and per-model token estimates separate.

For tracked work, record the chosen storypoints on the task. Record rough
wall-time and token estimates against the intended exact model ID. State
material assumptions briefly. If a metric cannot be estimated usefully,
leave it unset and explain
why; do not invent precision. Advisory sizing requests still do not authorize
record edits.

Wall time covers elapsed work through accepted completion, including expected
testing and review fixes. Note substantial external waits separately. For
multiple models, estimate each model's participation; their wall times may
overlap and must not be summed as task elapsed time. If a task-level elapsed
estimate is useful, put it in the existing description or log.

Tokens mean input plus output, including cached input. State exclusions when
delegate or reviewer usage is not covered. Estimate separately by model ID;
do not treat raw token totals as billing cost or convert them into points.

Use available local reference tasks without treating their timings as fixed
conversions or their provisional calibration as precise. Otherwise use a
rough progression: 1 trivial, 2 small, 3 bounded, 5 moderate, 8 substantial,
13 large, 20 work-package sized. Judge scope and verification needs, not
domain labels. Keep the result compact: points and available resource
estimates, with a sentence only when a material assumption or uncertainty
changes how they should be understood.

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
