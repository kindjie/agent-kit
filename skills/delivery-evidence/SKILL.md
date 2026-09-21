---
name: delivery-evidence
description: >-
  Decide the CI placement and authority of delivery evidence, or materially
  change CI tiers, advisory or blocking gates, performance calibration and
  thresholds, compatibility fixtures, or release evidence.
---

# Delivery And Evidence

This is advisory guidance, not a mandate for elaborate CI or release process.
Add a tier, gate, calibration system, or compatibility fixture only when a
concrete delivery risk or credible claim requires it and the expected evidence
is worth the ongoing latency and maintenance cost.

## Put Evidence At The Useful Boundary

Choose checks by when their evidence changes a decision:

- local checks give the fastest defect feedback;
- pull-request checks protect the changed surface;
- main-branch checks provide broad integration backstops;
- scheduled checks detect drift and run expensive analysis;
- release checks verify the exact artifact and commit being published;
- advisory checks collect uncertain or noisy signals without blocking work.

Run the complete suite when it remains cheap and reliable. Introduce
classification and tiers only when cost makes them valuable, fail closed on
uncertain classification by running the broader applicable checks, and retain
a broader backstop. This does not require failing a change merely because its
classification is uncertain. Document what may be skipped so a list of CI jobs
is not mistaken for evidence that every job ran.

## Gates, Telemetry, And Calibration

Separate gates that answer "may this proceed?" from telemetry that answers
"what should we investigate?" Do not let an unreliable evidence pipeline block
unrelated correctness when its result is not needed for the merge decision.

Introduce noisy or heuristic checks in shadow or advisory mode. Promote them
only after representative evidence establishes useful sensitivity and an
acceptable false-positive rate. A threshold should record its population,
environment, method, uncertainty, headroom rationale, and recalibration
condition. Label bootstrap limits honestly; the existence of a number does not
make it calibrated.

Start diagnosis with evidence closest to the mechanism: deterministic state or
work counters before paired timing, paired timing before profiling. Separate
detection of a change from attribution of its cause; stronger causal claims
need controls, interleaving, bisects, or direct instrumentation.

## Authority And Release Evidence

For every report, dashboard, plot, or generated summary, state what it informs
and what it may control. Keep authoritative policy distinct from calibration
evidence and explanatory presentation.

Exact-commit release evidence and immutable compatibility fixtures are useful
when publishing a stable library, protocol, package, CLI, persistence format,
or other distributed contract. They may include tested commit and artifact,
tool versions, installed-consumer builds, historical fixtures, public-surface
snapshots, and required-job results. Ordinary internal applications usually do
not need this machinery.

## Adoption Check

State the decision the mechanism informs, the credible failure it catches, the
cost of running and maintaining it, its authority, the evidence required before
it can block work, and its simplification or retirement condition. Prefer the
smallest system that answers the actual delivery question.
