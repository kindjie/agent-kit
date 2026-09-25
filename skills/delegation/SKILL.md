---
name: delegation
description: >-
  Decide whether to delegate work and at what model and effort, when spawning
  a subagent, running `claude -p` or `codex exec`, or sizing an independent
  review. Also use when a task needs capabilities available in the other
  agent. Covers capability routing, quota, effort, and delegated context.
---

# Delegation

Advisory calibration. Your always-loaded instructions win where they set a
default. Where they set none: `high` effort unless the work is mechanical,
and anything above `xhigh` only after `xhigh` has failed and the owner
agrees.

## Whether to delegate

Delegate to cut wall-clock time, context noise, or token usage: a parent
re-sends its context every turn, so in-thread exploration is paid again on
each one. Skip it when briefing costs as much as doing. A fork inherits
context. A fresh agent needs the question, constraints, files, and expected
deliverable, and should return findings or a patch, not narration.

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
handoff with the task, inputs, output paths, and acceptance criteria. Routing
does not expand task authorization, data-sharing permissions, or spending
limits; the quota guidance below still applies.

[image-generation]: https://learn.chatgpt.com/docs/image-generation
[tools]: https://code.claude.com/docs/en/tools-reference
[simulator]: https://code.claude.com/docs/en/desktop-ios-simulator

## Quota

Maximize completed useful work over time within the available quota and
reset windows, while preserving required quality and authorization limits.
Choose model, effort, service tier, and concurrency for sustained throughput.
Account for retries and rework: a stronger model can finish more work per
unit of quota than a cheaper model that needs repeated attempts.

Run `agent-quota --brief` outside the sandbox (approval if required, so
Codex can write its SQLite state): before substantial review or
second-opinion delegation, for the delegate's model; at the start of
substantial autonomous work, for your own. Use the plain command for a live
check; `--cached` only reads existing observations. Routine in-process
spawns skip it. Buckets are separate allocations: a model draws on its
provider's account buckets plus any bucket named for it, such as a
model-scoped weekly limit. The report cannot map models to buckets, so
judge that from the names. The compact table shows remaining
capacity, resets, and pace; `--verbose` adds the binding limit and detailed
velocity/burn evidence. Use remaining quota, time to reset, and recent burn
together; percentage alone is insufficient.
Check all applicable buckets for recent-burn exhaustion, even when another
bucket is binding. Surplus in one bucket does not cancel scarcity in another.

- `Recent burn too high` in the table, or EXHAUSTS BEFORE RESET in verbose
  output: treat as BEHIND whatever the pace label says; it is the more
  current signal.
- BEHIND: slow consumption of that allocation while keeping useful work
  moving. Route suitable work through available independent allocations;
  avoid optional duplicate reviews and quota-expensive speed tiers. Use
  cheaper models or lower effort where they can reliably meet the task's
  requirements. Reduce concurrency when it would exhaust the allocation
  prematurely; serializing the same work alone does not save total quota.
  Preserve capacity for required validation and difficult work. If no
  adequate route remains, explain the constraint and let the owner choose
  whether to switch, wait, or change scope. Prefer other useful authorized
  work while a short window is about to reset.
- SURPLUS: increase useful throughput from that allocation, especially as
  its reset approaches, when every other applicable bucket permits it.
  Choose the combination that helps the task: Codex Fast tier
  (`-c service_tier=priority`) when supported, independent parallel work,
  or deeper reasoning and review when they avoid rework. Claude fast mode
  bills usage credits, so quota surplus alone does not authorize it.
  `xhigh` may be worthwhile when it plausibly improves completion, a lower
  bar than "likely" in the effort guidance. Never exceed the existing
  approval boundary above `xhigh`, invent work to consume quota, or expand
  the authorized scope.
- ON_PACE, EARLY, or UNKNOWN: no quota-driven change.

Decide at task start; reassess at meaningful checkpoints during long work,
after a reset, or when burn or scope changes materially. Do not poll each
turn. Stale or unknown observations do not justify accelerating consumption.

### Multiple accounts

The owner may have more than one ChatGPT or Claude account and switches
between them manually. Quota and purchased credits are per account, so a
constrained active account does not establish that any other is exhausted.
Do not assume how many accounts exist or which is signed in; a live report
names the one in use.

Check the account email and plan in a live `agent-quota codex --brief` or
`agent-quota claude --brief` report before any account-specific capacity
decision. A cached report identifies the login last checked, not necessarily
the current one. The Codex report queries the CLI, and the desktop app may
be signed in as someone else; `claude auth status --json` covers Claude
login diagnostics. After a switch, refresh quota before resuming capacity
decisions.

When the active account is running low, say which one is constrained and
suggest switching if another is available. Treat archived readings as
historical: do not promise capacity until a live check after switching, and
do not claim an account exists that has not been observed. Preserve a
concise handoff if work must pause. Never switch accounts or manipulate
saved credentials automatically; existing authorization for necessary
credit-backed work still applies.

Local chats and transcripts can span accounts. Their presence and lifetime
token counts are machine-wide observations: they do not identify the active
subscription, establish any account's usage, or stand in for per-account
billing.

### Purchased credits and token activity

Exhausted subscription quota does not mean the provider cannot continue:
purchased credits are separate capacity for authorized work. Read the
Credits table's balance, credits/hour burn, and estimated time until empty
alongside subscription pace. An unknown burn or depletion time means
insufficient observations, not unlimited runway. A positive balance does not
restore subscription percentages, prove that a particular model can spend
it, or justify increasing spend beyond the task's authorization. When quota
is exhausted, weigh available credit runway against other suitable providers
before stopping or rerouting work. Preserve capability for the task.

Use credits to complete an active authorized task, resolve a blocker, or
retain a model capability the task needs when subscription quota is spent.
Prefer another provider's subscription headroom when it can do the work
equally well. Do not compromise necessary testing or review to save credits.

Let `agent-quota` enforce its own background-summary quota gate; this does
not call for manually disabling its summaries. Avoid credit spending on
other background labels, redundant reviews, speculative
exploration outside the task, unnecessary retries, or premium speed without
a time-sensitive need. As estimated depletion approaches, reduce optional
work and concurrency; keep enough capacity for required validation and
completion. Unknown runway calls for bounded work, not an assumption that
credits are unlimited. Ask before a substantial discretionary spend or a
spend outside the user's stated budget; ordinary calls within an authorized
task do not each need renewed permission. An explicit user budget or
provider preference takes precedence.

Token lines are labeled by provider: Codex account activity and Claude Code
locally observed transcripts have different coverage. Hour/day/week values
are averages over the reported seven-day window, not instantaneous rates;
check freshness and truncation. Cached input is included in token totals,
so raw tokens alone do not measure cost, productivity, or credit efficiency.

### Per-thread observations

Use `agent-quota --agents` when investigating token-heavy threads, possible
duplicate work, or which thread has relevant context. It lists IDs, work
labels, model/effort, observed token totals, cache share, and last activity.
Totals are per agent, not parent-plus-children. Seen is transcript activity,
not proof a process is running; work summaries may be stale. Confirm the
underlying conversation before taking action or treating work as complete.

Normally use plain `--agents` with summaries enabled. It generates cached
labels asynchronously with cheap models at low effort and automatically
defers calls when quota is too scarce or pace is constrained to protect
productive work. An exhausted provider alone is not a reason to disable
summaries: the tool can select another eligible provider.

Use `--agents --no-summaries` for an explicit no-model-call requirement or
when diagnosing summary generation, not as a routine quota precaution.
`--agents --cached` also skips transcript scans. `--verbose` shows summary
provenance; `--compact` gives JSON. Use `--agent-days` or `--agent-limit` to
adjust the recent-thread view. Local inventory can be truncated.

Automatic labels have their own conservative quota gate: they skip when
neither provider has more than 5% in every applicable bucket or when freshness
or pace checks fail. Purchased credits do not override this gate. This is a
background-label policy, not a rule to stop authorized credit-backed work.

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

## Escalation and review

Escalate when an agent finds no plausible solution, repeats incomplete work,
uncovers materially more complexity, or cannot verify. Hand its findings to
the stronger agent; do not restart. Review scales with risk: parent review
for mechanical changes; independent `medium` or `high` for normal work;
`high` or `xhigh` for correctness, concurrency, security, performance, API,
or architecture. Give an adversarial reviewer the requirements and the
change, not the implementer's reasoning, unless that is under review.
