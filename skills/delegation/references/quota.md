# Quota interpretation

Maximize completed useful work over time within the available quota and
reset windows, while preserving required quality and authorization limits.
Choose model, effort, service tier, and concurrency for sustained throughput.
Account for retries and rework: a stronger model can finish more work per
unit of quota than a cheaper model that needs repeated attempts.

Run `agent-quota --brief` outside the sandbox (approval if required, so
Codex can update its cache): before substantial review or
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
  current signal. When another account is likely ready, see *When
  another account is likely ready* below before slowing down.
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

#### When another account is likely ready

The brief report lists other accounts as *Likely available* when their
windows have reset since they were last checked. Treat that as unverified
until a live check after a switch, but let it change pacing. Each account's
weekly allowance is lost at its own reset whether or not it was used, so
with a ready alternate, active allowance left unspent at its reset is
waste:

- **Spend down before the reset.** If a ready alternate exists, an active
  bucket marked BEHIND or *Recent burn too high* need not slow work:
  running it out before its reset wastes nothing when the next account
  can carry on. Keep its remaining capacity in use for authorized work as
  its reset approaches, rather than letting it lapse unused. A ready
  alternate's short window still limits how fast it can carry on.
- **Use the allowance that expires first.** Compare reset times. Where the
  ready alternate's window resets sooner than the active one's, its unused
  allowance lapses first; suggest switching to it now, before the active
  account is exhausted, rather than at exhaustion. Where the active
  account resets sooner, keep using it; the alternate's allowance keeps.
- **Do not manufacture work.** Faster consumption still means authorized,
  useful work: parallel independent tasks, deeper review that avoids rework,
  or a higher effort level, within the approval boundary above, where it
  plausibly helps. Credits are separate and still need their own
  justification.
- **Say what you are assuming.** When pacing depends on an alternate, name
  it with its plan and reset time from the report, and ask the owner to
  switch when the active account runs out or the alternate's window is
  the one about to lapse. You cannot switch accounts yourself.

Without a likely-ready alternate, the BEHIND and SURPLUS rules above apply
unchanged.

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

Avoid credit spending on redundant reviews, speculative exploration outside
the task, unnecessary retries, or premium speed without a time-sensitive
need. As estimated depletion approaches, reduce optional
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

Use `--agents --no-summaries` for an explicit no-model-call requirement or
when diagnosing summary generation.
`--agents --cached` also skips transcript scans. `--verbose` shows summary
provenance; `--compact` gives JSON. Use `--agent-days` or `--agent-limit` to
adjust the recent-thread view. Local inventory can be truncated.
