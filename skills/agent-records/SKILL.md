---
name: agent-records
description: >-
  Track work and lasting machine state with agent-task and agent-changelog.
  Use when starting, claiming, handing off, coordinating on or closing a
  task; when delegating to another agent; when creating state that outlives
  the session (worktrees, stashes, kept branches, large scratch output,
  installs, services); before cleaning anything up; and when recording a
  serious mistake.
---

# Agent records

`agent-task` holds the shared work queue; `agent-changelog` holds state on
this machine that outlives a session, and mistakes. Both are git-backed and
shared by every agent here. Run `--help` on any command for its options;
`agent-task --help` also prints the status transition table.

## Identity

Every writing call takes `--agent <id>`. Use `agent-id show`: it is stable
for your session and derived from it. A delegated agent, fork or fresh,
gets its own from `agent-id new <label>`; a subagent shares your session,
so without its own ID it writes as you.

## Tasks

- **Before starting:** `agent-task list --here` (this repository) or
  `agent-task list`. If a task covers the work, claim it; if another agent
  holds it, coordinate instead of duplicating. `agent-task next` offers the
  highest-priority unclaimed task.
- **Start:** `agent-task new --title ...` for new work, then
  `agent-task claim T-NNNN`. Claims expire; claim again to extend.
- **Progress:** `agent-task log T-NNNN 'what changed'` at milestones, not
  every step. `status` moves between in-progress, in-review and blocked.
- **Finish:** tick the completion checks (`check T-NNNN merged --evidence
  ...`, or `--na 'reason'`), then `close T-NNNN done --reason ...`. The
  review check wants a real review.
- **Stop without finishing:** `release T-NNNN --note ...`, or
  `handoff T-NNNN --to <id> --note ...` to pass the claim.

Your session plan (`TodoWrite`, `update_plan`) is separate: it tracks steps
within one session. Tasks are for work other agents or later sessions need
to see.

## Working with other agents on a task

When several agents share an ongoing task, coordinate through it so the
exchange stays with the work:

- the owner adds collaborators with `helper add T-NNNN <id>`; an agent
  asks with `log T-NNNN '...' --request helper`;
- address a message with `log T-NNNN '...' --to <id>[,<id>]`;
- read what is new with `show T-NNNN --after <cursor>`, or wait with
  `watch T-NNNN --for <id> --until message`.

This is for collaboration on an ongoing task. A quick question or heads-up
to another session can still go as a direct cross-session message.

When delegating, create or pick the task, give the delegate its ID and the
task ID, and tell it to pass `--agent <its id>` on every records write, for
example:

```text
Your agent ID is helper-0123456789abcdef. Work on T-0001.
Pass --agent helper-0123456789abcdef to every records write.
Report progress with agent-task log T-0001 --to <my id> '...'.
```

## Lasting state

Record anything that outlives your session as you create it:
`agent-changelog new --scope ... --slug ... --kind worktree|stash|branch|
scratch|asset|backup|tool|service|other --location ... --why ...
--cleanup-when ... --cleanup-how ...`, with `--task` and `--repo` where
they apply. Update it as things change (`update`), and `close <entry>
--what ...` once cleaned up. State you remove in the same session needs no
entry.

Before cleaning anything up, read `agent-changelog list --open --machine`
(add `--here` for this repository). An entry explains why state exists; it
never authorizes deleting it.

## Mistakes

Record a serious mistake as soon as you notice it (lost or overwritten
work, a bad deletion, corrupted state, unintended publication):
`agent-changelog mistake new` with summary, impact, cause, detection,
cleanup options, prevention and severity. Tell the owner.

## Hooks

Where the agent-kit hook is installed, a reminder follows commands that
create lasting state, and each session starts with your ID, your tasks,
open tasks and open entries here. The reminders are prompts, not proof:
record the state yourself.
