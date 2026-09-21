---
name: verification-systems
description: >-
  Define or review the semantics and completeness of manifests, inventories,
  repository checkers, generated-documentation checks, or test catalogues,
  and judge whether a green result means the expected work happened.
---

# Repository Verification Systems

This is advisory guidance, not a reason to add manifests or custom checkers.
Introduce one only when a concrete omission, drift, or false-success risk is
material and existing authoritative tools cannot address it more simply.

## Inventories And Manifests

Use a manifest when omission itself is dangerous: public surfaces, package
contents, migrations, permissions, registrations, compatibility fixtures, or
measured workload coverage. Do not restate a directory that is already an
adequate source of truth.

A justified manifest normally checks both directions:

- every implementation item has exactly one valid classification;
- every manifest entry resolves to an implementation item;
- ambiguity, unknown values, and stale exemptions fail;
- examining zero relevant items is not success.

Prefer deriving consumers from the manifest over maintaining duplicate lists.
If the authoritative semantics belong to a compiler, package manager, schema
engine, or other mature tool, use that tool rather than recreating its parser.

## Check The Checker

A green status is meaningful only if expected work occurred. Fail or report
clearly when test discovery is empty, filters match nothing, benchmark results
are missing, files were not scanned, compilation commands are absent, or a
required job was skipped. Test configuration failures and important negative
cases, not only successful findings.

Keep repository logic in locally runnable, deterministic, tested programs.
Let CI provision the environment, invoke those programs, preserve evidence,
and aggregate outcomes; avoid burying complex policy in workflow expressions
or shell blocks.

## Tests And Executable Documentation

Keep verified behaviour in tests. Add companion test-rationale notes only when
they preserve traps, chosen constants, deliberately narrow claims, shared
fixture constraints, or environmental limitations that the source cannot show
clearly. If catalogued separately, enforce test-to-note and note-to-test
synchronization.

When documentation makes executable claims, prefer snippets extracted from
compiled sources, outputs checked against real programs, and versions or links
verified from authoritative inputs. A generated artifact should identify its
inputs, command, ownership, authority, regeneration trigger, and whether human
edits are allowed.

## Adoption Check

Name the plausible silent failure, its observable consequence, why a simpler
check is insufficient, and how the new checker itself will be tested and kept
complete. If that case cannot be made, do not add the mechanism.
