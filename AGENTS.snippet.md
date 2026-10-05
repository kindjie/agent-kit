<!-- BEGIN agent-kit -->
## Commit blockers

These bind every commit, however it starts: a slash command, a request in
prose, or the agent's own initiative.

1. Relevant tests pass, with no exceptions. Where a project has no test
   suite, run its documented smoke checks instead; never write throwaway
   tests to satisfy this rule.
2. No secrets or credentials are committed.
3. Staging is deliberate. Never `git add -A` over unrelated working-tree
   changes, and inspect the staged diff before committing.
4. Nothing is pushed, merged, or branched without approval. Do not create,
   switch, or delete branches unless asked.

## Pull requests

Check for new review comments once CI completes and again before merging,
and address every actionable unresolved thread.

## Delegation

At the start of substantial autonomous work and before delegating, consult
`delegation` for routing, effort, quota, and a bounded handoff. Use `high`
reasoning effort unless work is mechanical. Anything above `xhigh` needs a
task that `xhigh` has demonstrably failed on and the owner's agreement.
Security relevance or high stakes alone do not establish that failure.

## Verifying before acting

Verify a premise against authoritative evidence before acting on it. A
report may identify a real problem while prescribing the wrong remedy.
Before acting on branch or worktree state, including a merge, branch,
commit plan, or landed judgment, fetch and compare against the remote: a
squash merge leaves branch commits unreachable from the default branch.
Search for landed symbols and their introducing commit with `git log -S`;
neither ancestry nor diff size alone proves work is missing.

## Coordination

Send actionable changes and milestone results to affected agents; avoid
acknowledgment loops and unchanged status relays. Prefer completion
notifications or task watches during known waits. The `delegation` skill
covers monitoring cadence and compact handoffs.

## Session plans

For work with several steps, keep your tool's session plan current (Claude
Code's `TodoWrite`, Codex's `update_plan`). `agent-quota --agents` shows it
as progress.

<!-- agent-kit:records -->
## Task and state records

Use `agent-task` for shared work and `agent-changelog` for lasting state and
mistakes; the `agent-records` skill has the commands. Before starting, check
`agent-task list --here` for overlapping work. Claim or create a task and log
milestones. Use `estimate-agent-work` before starting unestimated work. Pass
`--agent $(agent-id show)` on every records write. Watch claimed tasks for
messages. Use `agent-changelog new` for state that outlives the session and
record serious mistakes. Before cleanup, read
`agent-changelog list --open --machine`; an entry never establishes deletion
authority. The skill covers handoffs, verification, and cleanup details.

Clean up state once it has served its purpose: worktrees, branches, stashes,
scratch, temporary assets, traces, and test deployments. Its creator does
it; otherwise the delegating agent does, or the next agent on the task or
entry. First verify the work landed on the default branch by searching its
symbols and introducing commit, not by inference. Verify that no unique
uncommitted, unpushed, or untracked work remains; nothing running uses it;
and no open entry or active agent still needs it. Before removing a directory,
read every open entry located inside it and preserve or account for each file
it names; never infer contents from one pattern. Remove exactly that state
and close its entry with what was verified. Verified cleanup needs no owner
approval unless the user requires it. Leave anything uncertain and say why.
<!-- /agent-kit:records -->
<!-- END agent-kit -->
