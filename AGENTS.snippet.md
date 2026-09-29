<!-- BEGIN agent-kit -->
## Commit blockers

These bind every commit, however it starts — a slash command, a request in
prose, or the agent's own initiative.

1. Relevant tests pass, with no exceptions. Where a project has no test
   suite, run its documented smoke checks instead; never write throwaway
   tests to satisfy this rule.
2. No secrets or credentials are committed.
3. Staging is deliberate. Never `git add -A` over unrelated working-tree
   changes, and inspect the staged diff before committing.
4. Nothing is pushed, branched, or merged unless asked.

## Pull requests

Check for new review comments once CI completes and again before merging,
and address every actionable unresolved thread.

## Delegation

Use `high` reasoning effort unless the work is mechanical. Anything above
`xhigh` needs a task that `xhigh` has already demonstrably failed on, and
the owner's agreement.

## Verifying before acting

Verify a premise against authoritative evidence before acting on it. A
report may identify a real problem while prescribing the wrong remedy.
Before judging branch state — merging, branching, or calling work unlanded
— fetch and compare against the remote: a squash merge leaves a branch's
commits unreachable from the default branch, so "not an ancestor of main"
does not mean "not landed".

## Session plans

For work with several steps, keep your tool's plan current (Claude Code's
`TodoWrite`, Codex's `update_plan`), marking each step in progress and
completed as you go; `agent-quota --agents` shows it as progress.
<!-- agent-kit:records -->

## Task and state records

`agent-task list` is the shared work queue; claim a task before working on
it. Record state that outlives your session (worktrees, kept branches,
large scratch output, tool installs) with `agent-changelog new` as you
make it, saying why and when and how to clean it up, and close it when
done. Record serious mistakes (lost work, bad deletions, unintended
publication) with `agent-changelog mistake new` at once. Read
`agent-changelog list --open --machine` before cleaning up; entries never
authorize deletion. Pass `--agent $(agent-id show)` on every call that
writes a record, and give each delegated agent its own ID from
`agent-id new <label>`.
<!-- /agent-kit:records -->
<!-- END agent-kit -->
