# Reading agent activity

[Back to the introduction](../README.md#follow-activity-and-quota).

![Expanded brewing parent and two helpers beside profiling and chair-path
  sessions](assets/agent-activity.png)

*Follow parent sessions and delegated agents, with observation details and
freshness information. Synthetic Moss & Mugs demo data.*

Space folds a parent group; Left/Right move through the tree. Enter opens
observed activity details, and `?` opens scrollable controls and state help.
Descendant summaries stay visible when a group is collapsed.

IDLE means an observed turn ended. It does not establish that an agent has
no work. WAITING represents a recognized outstanding wait; its target or
condition may be unknown. THINKING and TOOL describe observed reasoning or
calls, not a heartbeat. Old or incomplete evidence remains UNKNOWN.

Explicit attention badges are separate reports from an agent. They can
signal an owner decision, blocker, readiness for more work, or concrete
reason to investigate. They are not inferred from transcript silence.

See the [canonical activity contract](../bin/agent-quota.md), the
[attention setup](install.md#requirements), and the
[reproducible isolated scenario](demo/README.md).
