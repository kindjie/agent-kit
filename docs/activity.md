# Reading agent activity

[Back to the introduction](../README.md#follow-agents).

Agent-kit works best when the agents run on one machine. This view reads
local activity; agents and delegated work on other machines may be absent
or stale. Treat it as an incomplete overview of a multi-machine setup.

![Expanded brewing parent and two helpers beside profiling and chair-path
  sessions](assets/agent-activity.png)

*Follow parent sessions and delegated agents, with observation details and
freshness information. Synthetic Moss & Muggs demo data.*

<!-- site:wrap -->

<img class="ak-illustration ak-illustration--right ak-illustration--character" src="assets/moss-and-muggs/characters/hedgehog-look-down.png" width="73" alt="">

Space folds a parent group; Left/Right move through the tree. Enter opens
observed activity details, and `?` opens scrollable controls and state help.
Descendant summaries stay visible when a group is collapsed.

The default sort puts working agents and groups with working descendants
first, preserving collector activity order within each tier. Uncertain
`Working?` observations stay in the remaining tier. Press `s` to cycle
working-first, collector activity, newest update and title sorts. Families
stay together, and selection and fold choices survive sorting. The Agent
Tasks view defaults to newest update first.

<!-- /site:wrap -->

Details start with **Tasks**: matching IDs, titles, status and owner/helper
roles. **Activity**, **Session** and **Evidence** follow. Use `[`/`]` to
scroll. Task matches use observed records identities and current claims;
unsupported commands, missing identities or unavailable records can leave
the list empty. This is association evidence, not a watch count or proof
that an agent has no work.

`Working` covers activity, reasoning and tool calls. `Waiting` means a
foreground wait was observed. `Idle` means a turn ended; `Stopped` means it
was aborted. Neither proves what the agent's process is doing now.
`Unknown` means no usable status observation.

Old working/waiting evidence keeps its status with `?` and age, such as
`Waiting? · 25m ago`. Ended and stopped turns retain their status and age.
Enter shows the recorded event, timestamp, pending tools, wait timeout and
reason for uncertainty. The existing total shows `N+ agents` when discovery
is incomplete; details explain why. No additional header or footer rows
are needed.

Explicit attention badges are separate reports from an agent. They can
signal an owner decision, blocker, readiness for more work, or concrete
reason to investigate. They are not inferred from transcript silence.

See the [canonical activity contract](../bin/agent-quota.md), the
[attention setup](install.md#requirements), and the
[reproducible isolated scenario](demo/README.md).
