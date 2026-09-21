---
name: pull-requests
description: >-
  Take a change through a pull-request workflow: scope and plan PR-bound work,
  publish or update a PR, verify its current head, address CI and review, merge
  when authorized, and verify the result. Use when asked to push, open, or
  update a pull request, when planning a change that will be submitted as one,
  when CI or review feedback lands, and before or after merging.
---

# Pull Requests

Apply this workflow proportionately. A small, reversible change needs less
ceremony than a public contract, migration, or release. This skill supplies
checks, not authority: do not create or switch branches or worktrees, push,
open or modify a PR, merge, or delete anything unless the user's request
authorizes that action.

## Before The Code

Verifying a premise before acting on it is an always-loaded rule; it applies
with particular force to a fix proposed in an issue or review.

For a nontrivial or difficult-to-reverse design choice, write a short plan
covering the verified premise, material options and tradeoffs, intended scope,
and open decisions. Surface it for review before implementation when a decision
is needed. Use `approach-review` for consequential approach selection.

Scope the branch to one coherent, reviewable outcome. Follow the project's
branch and worktree policy, and confirm the active worktree before building or
editing.

## Before Publishing

Inspect the complete diff against the intended base and the working tree. Keep
unrelated changes out of the branch and PR.

Satisfy the commit blockers your always-loaded instructions define, or those
in the `commit` skill where they define none, and the project's test strategy.
Use `testing-requirements` when designing or materially changing that
strategy.
Validate every materially affected supported configuration that can reasonably
run locally, and identify what CI must cover.

For a new high-authority gate, enforcement check, or negative test that could
pass without exercising its intended failure, demonstrate under a controlled
mutation that it fails for the right reason. Restore the change, rebuild, and
rerun the passing case.

Support behavioural and status claims with current evidence appropriate to the
claim. Exercise exact compatibility or failure cases when they are material;
do not infer them from a nearby example. Scope claims to the configurations and
conditions actually established.

## Publishing

Confirm the repository, remote, base, head branch, and whether the PR should be
draft or ready for review. Push only the intended commits. After pushing,
verify that the remote head is the locally reviewed commit.

In the title and description, state what changed and why, how it was verified,
and any material limitations or deferrals. Include rejected alternatives only
when they explain a non-obvious decision. Keep claims concise and scoped to
the evidence.

When a change alters something visual, such as UI, rendering, or assets, a
minimal before-and-after screenshot in the description or a comment often
explains the work better than prose; include one when it does and capturing it
is practical. For dynamic or interactive behaviour, a brief video or GIF can
serve the same purpose. Neither is required.

## Review And CI

Inspect conversation comments, submitted reviews, and inline threads. Give
each actionable item an explicit disposition: fix it, or decline it with
evidence. A non-actionable or superseded item need not receive a performative
reply unless its status is ambiguous.

Treat a factual correction as evidence that the same stale belief may appear
elsewhere. Check other claims derived from it rather than patching only the
reported occurrence.

Check for new review comments when your always-loaded instructions say to,
and otherwise once CI completes and again before merging; beyond that, refresh
after any push that can retrigger automation or review.
Confirm that required checks, approvals, and review state belong to the
current head; a head change can make earlier evidence stale. Classify a
failure before rerunning it.

Use an API that exposes thread-resolution state when the hosting platform has
one; a flat comments endpoint cannot prove that all threads are resolved.

## Merging

Merge only with explicit authorization. Before merging, confirm the PR is not
draft, the intended head and base are current, required checks and approvals
pass for that head, actionable threads are resolved, and no known scope blocker
remains.

Identify the exact ref and base context evaluated by a discrepant gate: branch
head, synthetic merge, or base comparison. Reproduce that context before
doubting the tool.

Use the project's merge method. Do not pass `--delete-branch` to `gh pr
merge` from a linked worktree: its local cleanup assumes a single-worktree
repository and fails or corrupts state when the base branch is checked out
elsewhere (cli/cli#13380). Merge, then delete branch and worktree explicitly
from the primary checkout. On an ambiguous transport error, poll for the
operation's outcome before retrying.

## After The Merge

Verify the PR reports merged and that the expected commit is reachable from
the target branch. Confirm any required post-merge workflow or deployment when
it is part of the change.

Update repository release notes, decisions, migrations, and other records in
the PR whenever the change makes them stale. After merging, update external
tracking state and facts that could only be known from the merge; use a
follow-up PR for newly required repository changes.

Delete branches and worktrees only when authorized, and only after verifying
the merge result.
