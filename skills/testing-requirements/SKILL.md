---
name: testing-requirements
description: >-
  Design or materially change a test strategy: what to verify before, during,
  and after a change, and what evidence a suite must provide before the change
  can land. Use for projects with automated tests; where no relevant automated
  coverage exists, use the project's applicable smoke checks.
---

# Testing Requirements

This is guidance for choosing adequate evidence, not a mandate to maximize the
number of tests. Match the strategy to the observable contract, credible
failure modes, and consequence of being wrong. Follow any stronger
repository-specific requirements.

## Start From The Contract

State what must remain observable at the boundary: successful behaviour,
failure behaviour, compatibility, side effects, and important non-functional
properties. Identify how the change could be wrong before choosing a test
layer.

Prefer the lowest-cost layer that can observe the property completely:

- unit tests for isolated logic and boundary cases;
- integration tests for component contracts and real dependency wiring;
- end-to-end tests for user-visible paths and packaging or deployment seams;
- compile, type, schema, or static checks for contracts owned by those tools;
- smoke checks for installation, configuration, or operational viability;
- performance measurements only when performance is part of the change's
  contract or credible risk.

Do not recreate a compiler, parser, package manager, database, or platform
oracle in a weaker test when the authoritative mechanism can be exercised
directly.

## Build Independent Evidence

Follow the project's test-first rule where one applies. At minimum, reproduce
the failure or establish a baseline before changing the code. A useful test
should be capable of failing for the defect it claims to catch and should not
merely repeat the implementation's assumptions.

Cover the applicable cases:

- representative success;
- important boundaries and state transitions;
- expected failures and malformed input;
- misuse that the public contract promises to reject;
- regression cases for discovered defects;
- behaviour across supported configurations when configuration changes the
  outcome.

For a new authoritative checker or gate, consider a deliberate mutation that
removes or corrupts expected work and prove the checker fails. Use this only
when false success is a material risk; it is not required for routine tests.

## Validate At Useful Times

During development, run the smallest relevant subset for fast feedback. Before
commit, run the repository-required suite and smoke checks for the affected
surface, with a 100% pass rate. Run a broader suite when project policy
requires it or when the change crosses boundaries that targeted tests cannot
cover.

If no relevant automated suite exists, use the project's real smoke checks
such as installation, syntax validation, configuration parsing, or a minimal
runtime exercise. Do not fabricate throwaway tests merely to satisfy a rule,
but do add durable automated coverage when the behaviour is important and a
repeatable oracle is available.

Document new test targets or catalogue entries only where the project requires
them. Keep test instructions focused on commands, fixtures, constraints, and
non-obvious rationale that the tests themselves cannot express.
