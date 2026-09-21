---
name: repository-records
description: >-
  Create or materially change persistent engineering records — changelogs,
  decisions, ADRs, experiments, incidents, audits, calibration history,
  release or migration records — or redraw the boundary between current and
  historical documentation.
---

# Repository Records

This is advisory guidance, not a requirement to add logs or folders. Introduce
a record only when it prevents meaningful information loss, recurring
coordination cost, or unsupported repetition, and when its expected value
justifies its maintenance and release burden.

## Separate Records By Authority

Do not make one document serve incompatible purposes. Distinguish:

- maintained current truth: specifications, architecture, and runbooks;
- rationale: decisions and supersession links;
- historical intent: completed or abandoned design documents;
- evidence: experiments, incidents, audits, and calibration;
- temporary coordination: plans and task lists;
- adopter communication: release notes and migration guides.

Give each area a named authority and lifecycle. Do not keep completed plans or
historical proposals synchronized as if they were current reference material.

Before storing incidents, audits, raw evidence, or migration records, establish
their audience, visibility, retention, and redaction rules. Do not copy secrets,
credentials, personal data, or private infrastructure details into a more
broadly visible record. Link to access-controlled evidence when the detail must
be retained, and preserve forensic or compliance material under the controls
that govern it rather than casually summarizing or deleting it.

## Append-Heavy Shared Records

Consider per-change fragments when concurrent branches regularly edit the same
changelog, decision chronology, or experiment log, or when conflict resolution
could silently lose entries. Prefer direct edits while they remain simpler.

When fragments are justified:

- put one independently reviewable record in each file;
- make its contents the final rendered form where practical;
- validate name, shape, and content in the change that adds it;
- provide deterministic check and preview commands;
- assemble transactionally so invalid input writes and deletes nothing;
- consume fragments only at the defined consolidation point;
- archive consolidated history before size makes it costly to use.

The useful pattern is: append independently, validate early, compact
transactionally.

## Decisions And Experiments

An ADR should state status, context, decision, consequences, serious
alternatives, and supersession. Use one only when future maintainers benefit
from knowing why the choice was made.

An experiment record should state hypothesis, controlled change, environment,
method, evidence location, result, decision, limitations, and the condition for
reconsideration. Record rejected, deferred, and inconclusive work when doing so
can stop repeated dead ends. Do not manufacture an experiment log for routine
work or preserve raw data without an expected future use.

## Adoption Check

Before adding a record system, ask:

1. What information or coordination failure does it prevent?
2. Why are existing code, tests, issues, or docs insufficient?
3. What is the smallest useful form?
4. Who or what validates, consolidates, archives, and eventually retires it?
5. What observable signal would show that it is earning its cost?
