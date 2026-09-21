---
name: approach-review
description: >-
  Choose the conceptual approach and authority boundary for work with
  nontrivial impact, uncertainty, irreversibility, boundary crossing, or
  behavioural variety — a new abstraction, cache, parser, gate, or policy.
  Use while selecting the approach, again when scope expands, before granting
  a mechanism authority, and when evidence contradicts the working model.
---

# Engineering Approach Review

This is advisory guidance, not a required checklist. Use only the lenses that
help address a concrete project need or credible risk, and keep the response
proportionate to the consequence of being wrong.

Use this review proportionately when impact, uncertainty, irreversibility,
boundary crossing, or behavioural variety is nontrivial. Typical triggers
include new abstractions, caches or derived state, parsers or interpreters,
gates or policies, migrations, concurrency or distributed behaviour, and
mechanisms claiming safety, correctness, compatibility, or completeness.

Apply it while selecting the approach. Reapply it when scope expands, before a
mechanism becomes authoritative, and when findings expose a new conceptual
dimension or repeatedly contradict the model.

First establish the **Contract**: what externally observable behaviour,
including failure behaviour, is intended, and what is the consequence if it is
wrong? Surface it to the user when it affects the decision they are making.

Use the applicable principles as lenses, not verdicts:

- **DRY — Don't Repeat Yourself:** Who owns the relevant knowledge? Are we
  independently encoding semantics already authoritative elsewhere?
- **End-to-End Argument:** Where is sufficient context available to implement
  or verify the property completely?
- **Ashby's Law of Requisite Variety:** Which material distinctions within the
  intended scope can change the outcome, and can the design represent them?
  Which distinctions are deliberately unsupported?
- **Rule of Least Power:** What is the least expressive mechanism adequate to
  the task? Can controlled inputs be constrained instead of interpreted?
- **YAGNI / KISS:** Which generality and machinery are required now? Is this
  the simplest adequate design?
- **No Silver Bullet:** Is essential complexity being handled, or merely
  hidden, displaced, or recreated in weaker machinery?
- **Law of Leaky Abstractions / Hyrum's Law:** Which underlying or observable
  behaviours remain relevant, and what might consumers depend upon?
- **Goodhart's Law:** Is a metric, check, status, or representation still
  evidence for the outcome, or has it become the target?
- **Dijkstra's testing maxim:** What do passing tests establish, and what do
  they leave unproved? Is the oracle independent of the implementation's
  assumptions?
- **Coupling and cohesion:** Does shared fate — result, lifecycle, timeout,
  transaction, or failure state — reflect shared semantics?

Do not force every lens to apply. A signal becomes an actionable finding only
when it identifies:

1. a plausible failure;
2. an observable consequence;
3. supporting evidence or reasoning;
4. confidence;
5. a proportionate mitigation or evidence request.

This review supplements rather than replaces specialist security, privacy,
performance, accessibility, concurrency, migration, and operational review.
