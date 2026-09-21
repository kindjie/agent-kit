---
name: design-documents
description: >-
  Decide whether a design document is warranted and which kind, before a
  substantial cross-cutting design, public contract, migration, persistence
  or wire format, concurrency mechanism, or other difficult-to-reverse
  architectural change. Also covers keeping design intent, current truth,
  and history distinct once the work lands.
---

# Design Documents

This is advisory guidance, not a required repository schema. Create a design
document only when resolving and communicating the design is likely to save
more ambiguity, rework, or risk than the document costs to create and maintain.
Use the shortest form that preserves the needed reasoning.

## Choose The Document By Purpose

- A problem brief or RFC asks what problem should be solved.
- A technical design document (TDD) explains how a substantial change should
  work and integrates several related decisions.
- An architecture decision record (ADR) preserves why one consequential choice
  was made and may later be superseded.
- A specification defines maintained, externally observable requirements.
- An architecture document describes how the implemented system works now.
- An implementation plan sequences temporary work; it is not an API or
  architecture reference.
- A runbook describes maintained operational procedures.

Do not create an ADR for an obvious implementation detail, a specification
when tests and a small API already state the contract adequately, or a TDD for
a localized change with no meaningful alternatives.

## When A TDD May Pay For Itself

Consider one for interacting components or ownership boundaries, a durable
public contract, persistence or wire formats, concurrency or distributed
behaviour, security or privacy architecture, major performance tradeoffs,
migrations or staged rollouts, or difficult-to-reverse choices. A short issue
note or ADR is normally enough for an intermediate change.

A proportionate TDD covers only applicable sections:

1. problem and observable consequence;
2. scope, non-goals, constraints, and current behaviour;
3. proposed responsibilities, data flow, state transitions, and interfaces;
4. success, failure, misuse, and compatibility contracts;
5. serious alternatives and tradeoffs;
6. migration, rollout, observability, and rollback where relevant;
7. verification and acceptance evidence;
8. risks, assumptions, and open questions.

Review while meaningful alternatives remain. State uncertainty instead of
writing speculative detail as settled design.

## Preserve Authority And History

Follow the project's declared document lifecycle. A proposal-style TDD usually
becomes historical design intent when implementation lands; some projects
instead maintain a design document as current truth. In the former case,
update maintained specifications and architecture docs, preserve lasting
rationale in ADRs, and mark the TDD implemented and historical. If
implementation diverges, update current truth, record the consequential
decision, and link from the TDD rather than rewriting history to make the
proposal appear prescient.

When authorized, remove temporary plans with no lasting value. Otherwise
recommend deletion or archive them according to project policy, after
extracting current behaviour and durable decisions. Each documentation area
should identify whether it is normative, maintained, historical, generated,
or temporary.
