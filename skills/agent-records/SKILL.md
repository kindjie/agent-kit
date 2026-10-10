---
name: agent-records
description: >-
  Tracks work and lasting machine state with agent-task and agent-changelog.
  Use when starting, claiming, handing off, coordinating on or closing a
  task; when delegating to another agent; when creating state that outlives
  the session (worktrees, stashes, kept branches, large scratch output,
  installs, services); before cleaning anything up; and when recording a
  serious mistake.
---

# Agent records

`agent-task` holds the shared work queue; `agent-changelog` holds state on
this machine that outlives a session, and mistakes. Records help agents remember
cleanup and let a successor safely finish it after an interruption. They are
not approval gates or obstacles to cleaning up your own finished work. Both are
git-backed and shared by every agent here. Run `--help` on any command for its
options; `agent-task --help` also prints the status transition table.

## Identity

Every writing call takes `--agent <id>`. Use the ID you were assigned when
given one; otherwise `agent-id show`: it is stable for your session and
derived from it. A delegated agent, fork or fresh,
gets its own from `agent-id new <label>`; a subagent shares your session,
so without its own ID it writes as you.

Records writes take `--agent`, else `AGENT_ID`; the session is never used.
Implicit lookups (`agent-id show`, the `steamos` holder) take `--holder`
(steamos), else `AGENT_ID`, else the session. A subagent therefore prefixes
every shell command with `AGENT_ID=<id>` (it does not persist between
calls) and passes `--agent <id>`. `AGENT_ID` is honoured only in the session
that minted the ID, so start nested `claude -p`/`codex exec` with
`env -u AGENT_ID`.

## Tasks

- **Before starting:** `agent-task list --here` (this repository) or
  `agent-task list`. If a task covers the work, claim it; if another agent
  holds it, coordinate instead of duplicating. `agent-task next` offers the
  highest-priority unclaimed task.
- **Start:** use `agent-task new --title ... --claim --model MODEL
  --wall-seconds N --tokens N` to create, estimate and claim in one commit.
  Use `--wall-unknown` / `--tokens-unknown` for unavailable metrics, or
  `--model-unknown REASON` when the model is unknown. Plain `new` still creates
  an unclaimed task. Existing tasks use `claim T-NNNN`, which also accepts
  inline estimates. Claims expire; claim again to extend.
  When estimate policy is enabled, select `--model MODEL` or explain
  `--model-unknown REASON`; same-owner renewals reuse the selection and helpers even after expiry,
  provided nobody else has taken ownership.
- **Estimate:** when creating or materially refining tracked work, or before
  starting unestimated work, use `estimate-agent-work` and record its points
  and available per-model wall-time/token estimates. Briefly explain metrics
  left unset. Follow that skill's guidance; do not re-estimate unchanged work.
- **Progress:** `agent-task log T-NNNN 'what changed'` at milestones, not
  every step. Record changed decisions, blockers and results once; reference
  that entry in peer messages instead of copying the same narrative. Routine
  reads, waits and acknowledgments need no log entry. `status` moves between
  in-progress, in-review and blocked.
- **Planning:** `dependency add TASK PREREQUISITE` declares a dependency;
  `dependency remove TASK PREREQUISITE` revokes it. `dependency show TASK`
  lists direct prerequisites and dependents with status. `next` skips tasks
  whose prerequisites are not `done`; explicit claims and closure remain
  available. `estimate set TASK MODEL --wall-seconds N --tokens N` records
  manual per-model estimates (either metric may be omitted);
  `--wall-unknown REASON` or `--tokens-unknown REASON` records honest unknowns.
  `estimate remove TASK MODEL` removes them. `new` and `set` accept
  `--storypoints` of 1, 2, 3, 5, 8, 13 or 20.
- **While you hold a claim, watch it:** others may address you on the task
  with `log --to`. Where your tool can wait in the background (Claude
  Code's background Bash re-invokes you when it exits), keep
  `agent-task watch T-NNNN --for <your id> --until message` running and
  re-arm it from the cursor it prints. Otherwise read
  `agent-task show T-NNNN --after <cursor>` at each milestone, and always
  before you close, release or hand off.
  For several tasks, use `agent-task events T-0001 T-0002` and resume with
  its JSON cursor via `--after`. It returns one batch per wake; `--for ID`
  filters messages while preserving state changes. Exit 4 is a timeout,
  not completion; retain the returned cursor. Exit 5 requires inspection.
  Add `--actionable` to ignore routine lease renewals while retaining observed
  expiry, ownership and other header changes. Keep that mode consistent across
  cursors; changing modes produces a fresh header event. Watcher lifetime and
  the tool's blocking/yield interval are separate (see `delegation`).
- **Finish:** tick the completion checks (`check T-NNNN merged --evidence
  ...`, or `--na 'reason'`), then `close T-NNNN done --reason ...`. The
  review check wants a real review. If open records must continue on another
  live task, add `--transfer T-NNNN`; its owner inherits them, so agree that
  with them first. Otherwise close or transfer those records first.
- **Stop without finishing:** `release T-NNNN --note ...`, or
  `handoff T-NNNN --to <id> --note ...` to pass the claim.

Your session plan (`TodoWrite`, `update_plan`) is separate: it tracks steps
within one session. Tasks are for work other agents or later sessions need
to see.

## Access and recovery

Verify required access once when the execution environment changes. Writers
need the records roots and their Git metadata; readers need readable existing
lock files. Use the normal sandbox when it permits the operation. Do not
request escalation for every records command by habit.

`show`, `list`, `next`, relationship views, lint and watches never repair a
pending journal. Exit 5 means recovery is needed; an authorized writer uses
`agent-task --agent ID recover` or `agent-changelog --agent ID recover`.
An explicit `--unlocked` diagnostic read may show incomplete state and is
marked unverified. Do not use it to establish a gate or ownership decision.
`doctor` is a write/signing probe, not a routine status read; it also provisions
missing lock files in newly cloned records repositories. If signing or recovery
fails, retain the error, coordinate one recovery owner, and avoid parallel
retries. Continue independent work where possible.

## Correcting records

Every task correction adds a log line, so the record keeps what
happened; where a command takes no reason, `log` one. Changelog entries
have no log: say why in an entry's `--notes`, or for a mistake, in a
`log` line on its task.

| Mistake | Correction |
| --- | --- |
| Wrong evidence or n/a reason | `check` the item again; `--reason` says why |
| Item ticked in error | `uncheck T-NNNN <item> --reason ...` (owner only) |
| Task closed too early | `reopen T-NNNN --reason ...` |
| Wrong status or owner | `status`, `release` or `handoff` |
| Wrong title, priority, severity or repository | `set ... --reason ...` |
| Wrong added check | `set --remove-check N`, then `--add-check` |
| Wrong PR or reference link | `link --demote` or `--remove`, `--reason` |
| Wrong log message | `log` a correction; the log is append-only |
| Wrong changelog entry field | `agent-changelog update ... --notes ...` |
| Wrong mistake record field | `agent-changelog mistake update ...` |

## Working with other agents on a task

When several agents share an ongoing task, coordinate through it so the
exchange stays with the work:

- the owner adds collaborators with `helper add T-NNNN <id> --model MODEL`
  or `--model-unknown REASON`; record that model's estimates first when
  policy requires them. Helper selection is separate from the owner's.
  An agent asks with `log T-NNNN '...' --request helper`;
- address a message with `log T-NNNN '...' --to <id>[,<id>]`;
- read what is new with `show T-NNNN --after <cursor>`, or wait with
  `watch T-NNNN --for <id> --until message`.

This is for collaboration on an ongoing task. A quick question or heads-up
to another session can still go as a direct cross-session message.

When delegating, create or pick the task, add the delegate as a helper,
give it its ID and the task ID, and tell it to pass `--agent <its id>` on
every records write. A delegate in the other tool, or in a background
process, cannot receive cross-session messages, so the task is its only
channel back and forth. For example:

```text
Your agent ID is helper-0123456789abcdef. Work on T-0001.
Pass --agent helper-0123456789abcdef to every records write.
At each milestone, read new messages with
  agent-task show T-0001 --after <cursor>
and report progress or blockers with
  agent-task log T-0001 --to <my id> '...'.
```

The delegating agent waits with `agent-task watch T-0001 --for <its id>
--until message` (or `--until closed`) rather than polling.

## Lasting state

Record anything that outlives your session as you create it:
`agent-changelog new --scope ... --slug ... --kind worktree|stash|branch|
scratch|asset|backup|tool|service|other --location ... --why ...
--cleanup-when ... --cleanup-how ...`, with `--task` and `--repo` where
they apply. Update it as things change (`update`), and `close <entry>
--what ...` once cleaned up. State you remove in the same session needs no
entry.

Entries are for state that will need cleanup. A change meant to stay (a
committed tool, a deliberate config grant) belongs where it lives: the
commit, or a comment beside the setting. When temporary state is adopted
as permanent, close its entry with `--what` naming the owner's decision
and where the record now lives, so the open list keeps meaning "still to
clean up".

To combine kinds, pass one quoted value with spaces around `+`, for example
`--kind 'backup + scratch'`. The compact form `backup+scratch` is invalid.

Consult relevant cleanup records with `agent-changelog list --open --machine`
(add `--here` for this repository), especially when taking over another agent's
state. An entry explains why state exists; it never authorizes deleting it. An
open task or entry alone does not require retention or prior closure. Missing
or stale records do not prohibit cleanup when current evidence establishes
safety; reconcile records after the cleanup.

Clean up state after it serves its purpose. The creator does it; otherwise
the delegating agent or the next agent on the task or entry does. Verify that
retained work reached its intended integration branch (usually the default
branch) by searching its symbols and introducing commit, or account for an
intentionally discarded experiment; no unique uncommitted, unpushed, or untracked work remains; and no
process or active agent uses it, and no actual retention need remains. When
removing a directory, consult relevant records and preserve or account for each
file they name; never infer contents from one pattern. Remove exactly the
verified state, then close or update its records with the evidence. A records
write failure does not undo safe cleanup or require keeping disposable state;
retain the verification evidence and reconcile the records when writes work.
Clean up agent-created worktrees and branches safely one by one when they have
served their purpose, including corresponding remote branches where the
repository's policy allows. Verify each item separately before removing it; do
not batch-delete from names, age, or expired claims. Follow the user's or
repository's cleanup policy: where it grants standing authorization, these
cleanups need no further approval; otherwise ask first. Leave anything
uncertain in place and say why.

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
