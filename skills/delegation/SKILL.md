---
name: delegation
description: >-
  Decides whether to delegate work and at what model and effort, when spawning
  a subagent, running `claude -p` or `codex exec`, or sizing an independent
  review. Also use when a task needs capabilities available in the other
  agent. Covers capability routing, quota, effort, and delegated context.
---

# Delegation

Use `high` reasoning effort by default, or `medium` for mechanical work.
Anything above `xhigh` requires a task that `xhigh` has demonstrably failed
on and the owner's agreement. Security relevance or high stakes alone do not
establish that failure. This skill helps choose and brief a delegate within
those boundaries.

## Whether to delegate

Delegate to cut wall-clock time, context noise, or token usage: a parent
re-sends its context every turn, so in-thread exploration is paid again on
each one. Skip it when briefing costs as much as doing. A fork inherits
context. Prefer a compact fresh brief when it supplies enough evidence; fork
only when inherited context materially helps. A fresh agent needs the question,
constraints, files, and expected deliverable, and should return findings or a
patch, not narration.

Where `agent-task` and `agent-changelog` are in use, give each delegate,
fork or fresh, its own ID from `agent-id new <label>` and the task it works
on; a fork shares your session and would otherwise write as you. Tell it to
pass `--agent <id>` on every records write; the agent-kit README has a
ready brief.

A delegate in the other tool (`codex exec` from Claude Code, `claude -p`
from Codex) or in a background process cannot receive cross-session
messages, so for anything beyond a single exchange the task is the channel.
Add the delegate as a helper (`agent-task helper add T-NNNN <id>`), brief
it to read `agent-task show T-NNNN --after <cursor>` at milestones and to
report progress and blockers with `agent-task log T-NNNN --to <your id>`,
and wait with `agent-task watch T-NNNN --for <its id> --until message` (or
`--until closed`) instead of polling. Its final result still comes back as
its output.

## Coordination and waiting

Send a message when it changes a decision, ownership, blocker, required action,
or completed result. Address only affected agents. Do not acknowledge routine
status, echo it back to the sender, or relay unchanged updates. Acknowledge a
handoff or safety-critical stop when confirmation is needed. Put durable
milestone evidence on the task once and reference it in messages.

Use completion notifications or a task watch for known waits. Check at a
meaningful milestone when background waiting is unavailable; do not repeatedly
wake the model to ask whether anything changed. Respect higher-priority update
and tool-wait limits. A waiting worker is not stalled merely because it has no
new transcript activity; inspect its current operation before interrupting it.

A tool call's blocking/yield limit is separate from a watcher's lifetime.
When background completion notifications are supported, let the watcher remain
alive across short tool yields; do not turn every yield limit into a watcher
timeout and a fresh model turn. Retain its cursor when rearming after an event
or timeout. If the runtime requires periodic updates, keep those wakes small;
a longer watcher cannot waive that requirement. For task batches, prefer
`agent-task events --actionable` to suppress routine lease-renewal wakes while
retaining observed expiry and other state changes.

Store a full handoff or verification receipt once on its owning task. Send
affected peers the task ID and log reference plus the decision or action they
need, rather than copying the entire receipt onto each task. Keep resource
quiescence/release handling responsive during result review; changing release
authority still requires an explicitly agreed workflow and verified safety
checks.

For a centrally reserved run, send one operational release request with its
exact grant/run reference, terminal session status, scoped process-quiescence
evidence, and explicit end-of-run intent. Label the result and remaining review
separately. The coordinator checks operational release first, records it, then
continues semantic review. A failed test need not retain a reservation after
independently verified cleanup; uncertain process scope does. Release grants
no permission to retry or resume. Existing grant requirements and compatibility
exceptions still apply.

For requested ongoing monitoring, establish wake conditions, a fallback cadence
and a bounded scope per pass. Prefer a watcher or scheduler to a continuously
active model where available. Unchanged state needs no new work or report; a
monitoring request does not authorize creating an unattended service. Preserve
any persistent goal and its pause/completion rules.

Keep coordinators focused on decisions, ownership, blockers and evidence
references. At a phase boundary, use a verified handoff if a fresh context would
avoid repeatedly carrying implementation history. Preserve authorization,
unresolved findings, exact source/evidence references and the next action.
Assign one coordinator per shared resource; use an existing lease or queue
rather than negotiating every transition among several coordinators. Distinguish
quiet-machine performance measurements from ordinary functional validation.

Where workers need local admission and no project lease already owns it,
`agent-resource run --resource NAME --timeout SECONDS -- command` bounds
cooperating commands. Resources default to one credit; configure a budget with
`agent-resource capacity --resource NAME --credits N`. Request a weight with
`--resource NAME:N`, or the entire budget for exclusive measurements. Agree
names, budgets and workload weights once; it neither establishes machine quiet
nor constrains unwrapped work. Use foreground commands; see its README limits.
For repeatable local usage audits, `agent-efficiency --since ISO --until ISO`
reports deduplicated activity without model calls. Check coverage diagnostics;
raw token totals and tool counts do not establish cost or avoidable overhead.

### Cleanup after delegated work

Tell a delegate which state it may create and that it should remove what it no
longer needs before handing back. When the work returns, the delegating agent
owns what is left: worktrees, branches, scratch output, temporary assets, traces
or test deployments. Once the result and that state pass the always-loaded
records rule's cleanup checks, remove exactly that state and close its changelog
entry with what was verified. A later agent on the same task or entry inherits
the same duty. Leave anything uncertain in place and say why; do not leave
verified-disposable state for the user to clear.

## Routing between Codex and Claude Code

Before declaring a task unsupported, check the current session's tools and
whether the other agent can supply the missing capability. Tool access varies
between desktop, CLI, and individual sessions; a CLI delegate need not inherit
the desktop app's plugins. An installed integration is not proof of working
authentication. Verify the target's access before handing off.

- **Raster image generation and editing:** route concept art, sprites,
  textures, portraits, and UI illustrations to Codex's image-generation tool
  when available. Include reference images, dimensions, style, and intended
  asset paths. Claude's image understanding and SVG generation do not replace
  a raster image generator. See [Codex image generation][image-generation].
- **Semantic code navigation and diagnostics:** consider Claude Code's
  language-server integrations, such as clangd for C/C++ engine code and
  Pyright for Python tools, when enabled. Check its [tool reference][tools];
  either agent can also use local compiler and analysis commands.
- **iOS interaction testing:** consider Claude Code Desktop's dedicated
  [simulator tools and pane][simulator] when available. Check its platform
  requirements; do not assume a CLI session has the same interface.
- **Service integrations:** inspect the available connectors for tasks such
  as Sentry crash triage or Context7 SDK lookup. Consult configuration rather
  than maintaining a duplicate plugin inventory here.

Blender/3D MCP, browser and desktop automation, interactive HTML/SVG,
document tooling, builds, debugging, and profiling are not inherently
exclusive to either agent. Check the configured tools. For generated music,
sound effects, or video, look for an actual generator integration rather than
inferring support from the provider's other products.

If the needed tool is available only in the other app, provide a concise
handoff with the task, inputs, output paths, and acceptance criteria, and
coordinate through the task as described under whether to delegate. Routing
does not expand task authorization, data-sharing permissions, or spending
limits; the quota guidance below still applies.

[image-generation]: https://learn.chatgpt.com/docs/image-generation
[tools]: https://code.claude.com/docs/en/tools-reference
[simulator]: https://code.claude.com/docs/en/desktop-ios-simulator

## Quota

Run `agent-quota --brief` outside the sandbox when required for a live check:
at the start of substantial autonomous work, and before substantial review or
second-opinion delegation for the delegate's model. Routine in-process spawns
skip it. `--cached` is historical. Check every applicable bucket's remaining
capacity, reset, pace, and recent burn; percentage alone is insufficient.
Use `--agents` when thread usage or work context affects the decision.

Choose model, effort, tier, and concurrency for useful throughput while
preserving required quality, validation, and authorization. If recent burn is
too high or an applicable bucket is behind pace, reduce optional consumption
and assess other suitable allocations. If capacity is surplus, increase
only useful authorized work; do not invent tasks. Reassess at meaningful
checkpoints, not every turn. An alternate account's availability remains
unverified until a live check after the owner switches; never switch accounts
or credentials automatically.

Purchased credits are separate from subscription quota. Consider balance,
recent burn, and estimated time until empty before continuing credit-backed
work. Unknown runway is not unlimited capacity. Preserve necessary capability
and testing; ask before substantial discretionary spend or work outside the
user's budget. Detailed pace, multiple-account, credit, and per-thread
interpretation is in [quota interpretation](references/quota.md).

## Batching and concurrency

For repeated small, independent tasks such as labels or extraction, consider
small batches to amortize CLI startup and repeated context. Compare latency,
usage, and output quality on representative inputs before adopting batching.
Keep per-item IDs and input bounds, validate the complete response mapping,
and retain prior results when a batch fails. Bound concurrent calls and retry
work to avoid multiplying costs. Keep tasks separate when they need different
permissions, context, or independent review. Batching does not authorize new
work or broader data sharing.

## Knobs

- In-process subagents: use the current tool's supported model and effort
  controls. Claude Code definitions use `effort` (otherwise session effort);
  Codex definitions use `model_reasoning_effort`. Do not assume that choosing
  a different model preserves the parent's effort; set it explicitly where
  supported.
- CLI: `claude -p --model <model> --effort <level>`;
  `codex exec --model <model> -c model_reasoning_effort=<level>`.
  Select the same exact model used for the quota check; omitted flags can
  inherit local defaults. A fresh CLI session needs the task brief and
  working directory. For Codex use `-C <repo>` and `--sandbox read-only`
  for reviews, or `--sandbox workspace-write` for authorized edits.
  Run `codex exec` with stdin from `/dev/null` when nothing is piped in:
  with an open stdin it waits for more input and never starts, which in a
  background job looks like a hang. `-o <file>` saves the final message.
- `agent-quota --models --brief` lists each tool's live effort levels and
  the Codex models with defaults and tiers. A model your tool lacks may be
  reachable through the other.

## Effort

Effort tracks reasoning difficulty, not code volume.

- `low`: search, extraction, formatting, an already-decided edit.
- `medium`: well-specified implementation, localized fixes, tests, docs.
- `high` (default): ambiguous or multi-file work, unfamiliar code,
  root-cause debugging, design decisions.
- `xhigh`: deeper investigation likely changes the answer: concurrency,
  performance, subtle correctness, broad migrations.

## Model

Fast model for bounded search and repetitive edits; balanced model for
well-specified implementation; strongest for ambiguous or cross-cutting
work. Upgrade model or effort, not both, then reassess. Classify a new model
from its vendor naming and documentation at use time; keep no catalogue.

<example>
Task: Compare six documented CLI flags with their implementation.
Choice: Fast model, medium effort; the files and expected table are bounded.
Handoff: "Read these six flags in the CLI and docs. Return mismatches with
file and line evidence. Do not edit files or infer undocumented behavior."
</example>

<example>
Task: Implement an approved parser change with existing tests.
Choice: Balanced model, high effort; behavior spans several modules.
Handoff: "Change the parser contract in these modules, add focused tests,
run the named suite, and return the diff plus any remaining edge cases."
</example>

<example>
Task: Explain a GPU frame-time regression with a captured trace.
Choice: Strongest model, xhigh effort; diagnosis may change the proposed fix.
Handoff: "Inspect this trace and the render path. Separate measured costs
from hypotheses, suggest the next discriminating capture, and make no edits."
</example>

## Escalation and review

Escalate when an agent finds no plausible solution, repeats incomplete work,
uncovers materially more complexity, or cannot verify. Hand its findings to
the stronger agent; do not restart. Review scales with risk: parent review
for mechanical changes; independent `medium` or `high` for normal work;
`high` or `xhigh` for correctness, concurrency, security, performance, API,
or architecture. Give an adversarial reviewer the requirements and the
change, not the implementer's reasoning, unless that is under review.
