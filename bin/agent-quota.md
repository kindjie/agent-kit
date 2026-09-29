# Agent Quota Contract

This is the maintained contract for `agent-quota` schema version 3. The
implementation and fixture tests are the current truth; this document defines
the meanings consumers must preserve.

## What it reads, before anything else

Reporting scans the Claude Code and Codex transcript roots on this machine
to attribute token activity. Nothing is uploaded by that scan.

`--agents` additionally labels threads, and a label is produced by sending a
bounded excerpt of the thread to a model. By default the eligible providers
include the one that did not produce the thread, so a Claude Code transcript
excerpt can be sent to Codex and the reverse. That call spends the chosen
provider's quota or credits.

- `--no-cross-provider-summaries` keeps each excerpt with its own provider.
- `--no-summaries` updates observations and makes no model calls.
- `--cached` skips the transcript scan entirely.

Set `AGENT_QUOTA_SUMMARIZER=1` in an environment to suppress labelling
without passing a flag. Ordinary quota queries never start summarization.

## Output modes

- Default output is indented canonical JSON.
- `--compact` emits the same JSON on one line.
- `--brief` emits compact quota and credit tables, constraints first, plus
  one token-activity line. Fresh observations share a checked-time footer;
  stale/unknown observations are marked individually. Errors and warnings
  remain visible. Ended quota periods show unknown remaining capacity.
- `--verbose` (also accepted with `--brief`) emits detailed text including
  observation timestamps, projections, bindings, and velocity. It cannot
  be combined with `--compact`, `--models` or `--timeline`.
- `--timeline` lists upcoming events grouped by day in local time, in
  aligned columns: when, time, label, quota, account and a note. Labels
  are `RESET`, `EXHAUSTED` (empty now) and `BURN` (a quota or credits
  projected to run out at recent burn, listed only when that comes before
  the reset and only for fresh observations of the checked accounts).
  Quotas are named `Claude`, `Claude 5h`, `Fable`, `Codex` and so on;
  weekly is the default window and goes unnamed. Notes say what each event
  means: `N% left` for the checked accounts, `was N%` (last known,
  unverified) for other accounts, `→ 100%` (`→ ~100%` for other accounts)
  on a reset that restores an exhausted bucket or frees a blocked account,
  `blocked` on a reset while spent account-wide buckets still block the
  account, and `stale` when a checked account's data is not fresh. Events
  with the same moment, type, account and meaning merge
  (`Claude, Fable … was 0% / 56% → ~100%`). 5h items appear only when low
  (under 10%), exhausted, running out, or freeing an account.
  On a terminal the timeline is styled: `EXHAUSTED` bold red reverse,
  `BURN` bold red within 24 hours and bold yellow later, `RESET` bold
  green, restoring resets highlighted in bold green, quota names bold,
  each account in its own colour, and unverified or stale lines dim italic.
  Piped output, `NO_COLOR` and `TERM=dumb` get plain text.
- `--timeline --compact` emits the same events as one-line JSON, unmerged
  and unfiltered (5h items included), for tools that must not parse text:
  `{"schema_version": 1, "generated_at": ..., "events": [...]}`. Each event
  has `at`, `type` (`reset`, `burn`, `exhausted`), `provider`, `account`,
  `active`, `quota`, `limit_id`, `short_window`, `remaining_percent` (or
  `remaining_credits` with `unit` `credits`), `unit`, `restores`,
  `blocked`, `stale` and `rate_per_hour`. Events are in time order.
- `--live` keeps `--timeline` or `--agents` on screen, redrawing every
  `--interval` seconds (default 30, minimum 5) on the terminal's alternate
  screen, and restores the terminal on exit. `q`, a lone Escape or Ctrl-C
  quits; arrow and other key sequences do not. Between redraws a dim dot at
  the start of the status line moves once a second, showing the loop is
  alive. It re-reads caches each redraw but queries the services only when
  the cached quota is over five minutes old, and never with `--cached`; in
  agents mode it starts the background summary worker at most every five
  minutes. A status line shows the interval and the quota data's age. Rows
  that are new or changed since the previous redraw are shown reversed: a
  timeline row by its moment, type, account and quota (relative times
  ticking do not count), an agent row by its state (its current step changes
  too often to mark). Agents mode adds a header per provider: the account
  bucket every model draws on (the binding one, or the one with least left),
  its run-out or reset, `<model> blocked` for each spent model bucket, and
  the share of the last 15 minutes' tokens by the busiest agents. Each line
  fits the width: the whole header switches to the table's compact IDs, then
  lists fewer agents. When output is not a terminal, `--live` prints one
  frame and exits. It requires `--timeline` or `--agents` and cannot be
  combined with `--compact`, `--brief` or `--models`.
- `--notify` (with `--live`) rings the terminal bell and posts a desktop
  notification (`osascript` on macOS, `notify-send` on Linux) once per
  event: a quota reset passing, an inactive account likely available, a
  BURN within an hour, an agent turning stalled, or one agent using over
  70% of its provider's last 15 minutes of tokens (at most hourly).
- `--color {auto,always,never}` colours the text views (`--brief`,
  `--verbose`, `--timeline`, `--agents`, `--models --brief`). `auto`, the
  default, colours an interactive terminal unless `NO_COLOR` is set or
  `TERM=dumb`; `always` and `never` override that, so
  `watch -c -n 60 agent-quota --cached --timeline --color always` works.
  One palette throughout: exhausted bold red reverse, recent burn too high
  bold red, behind red, blocked and stale yellow, surplus blue, likely
  available and recent activity green, on-pace and ended dim, headers bold.
  Styling never changes the visible text or alignment. `--color` with a
  JSON output is an error.
- Both text modes list archived accounts when any exist; see
  [Accounts not checked now](#accounts-not-checked-now).
- `--cached` reads and reevaluates the derived cache without querying a
  service.

An option that cannot affect the chosen mode is an error, not silently
ignored: agent-only options (`--agent-days`, `--agent-limit`,
`--no-summaries`, `--no-cross-provider-summaries`) require `--agents`;
`--models` rejects `--cached`, `--no-cache`, `--strict`, `--cache-file` and
`--timeout`; and `--timeout` and `--claude-timeout` cannot be combined with
`--cached` outside `--agents`, which queries nothing. Abbreviated option
names are recognized.

Renderers consume JSON or the derived cache. They must never parse `--brief`.
An unsupported `schema_version` is an error, not a best-effort input.

The tmux component shows each bucket's weekly window. It shows the bucket's
5h window and reset instead while that period is current, below 10%
remaining, and below the weekly window's remaining percentage; a weekly
window as low or lower is the longer outage and stays in view. Recent burn
alone does not swap them, since it can project exhaustion hours ahead. A
service in the report with no weekly limit to show, such as after a failed
refresh, appears as `?` rather than disappearing.

## Semantics

The root `generated_at` is when the report evaluated its observations. A
service identifies both its vendor (`provider_id`) and product
(`service_id`), records the latest refresh attempt separately from retained
data, and has one of these data states:

- `complete`: the latest collection produced valid recognized data;
- `partial`: some records were rejected, or last-good data survived a failed
  refresh;
- `unavailable`: no usable observation exists.

`refresh.status` is `succeeded`, `not_needed`, or `failed`. `not_needed`
means the provider cache was inside its refresh interval. A failed refresh
records its own attempt and error without changing the timestamps on retained
observations.

Every limit has an opaque `limit_id`, neutral bucket metadata, a window, an
optional availability observation, and a `last_observation`. Scoped buckets
such as Fable and Spark are separate allocations. Their percentages must not
be added to an account bucket or to each other.

Percentages are finite numbers from 0 through 100. `remaining_percent` always
means percentage remaining, and equals `100 - used_percent`. Invalid values
are rejected with a warning; they are never clamped.

An observation keeps its percentage, observation time, reset time, and source
from one source snapshot. Data from separate snapshots must not be combined.
Timestamps are RFC 3339 UTC values.

`freshness` and `period_relation` describe independent facts, evaluated at
`generated_at`:

| Field | Values | Meaning |
|---|---|---|
| `freshness` | `fresh`, `stale`, `unknown` | Whether `fresh_until` has passed |
| `period_relation` | `current`, `ended`, `unknown` | Reset-period state |

A stale current-period observation is usable best-effort data and must be
marked stale. An ended observation is historical evidence only: current
remaining usage is unknown. When the reset is unknown, consumers must not
claim that the observation describes the current period. Cache consumers
recompute both states from the timestamps rather than trusting stored derived
labels indefinitely.

Availability is separate from utilization. A blocked event must not fabricate
`100% used` or overwrite a measured percentage.

## Codex account identity and switching

Live collection reads `account/read`, quota, token activity, and `account/read`
again in one app-server process. If the account or plan changes during the
collection, the snapshot is rejected. No credentials are read or stored by
this tool. These are the CLI's credentials; a desktop app using a different
login may report different usage.

The Codex service carries an `account` object with `key`, `label` (email),
`plan`, `observed_at`, and `source`, or null when identity is unknown. The key
is a SHA-256 digest of the case-folded login email, not a credential and not
an anonymization guarantee. Brief and verbose output show the email, plan,
short key, and check time. These identify the account **last checked**;
cache-only readers do not verify the current login. Tmux keeps its compact
quota display without account identifiers. Run a normal quota refresh after
switching; the status refresh will also pick it up.

The documented API currently exposes no workspace ID. Email identity is used
only for recognized personal plans. Other plans retain live measurements and
a display label, but have a null key and partial status: workspace-specific
history and background summaries cannot be trusted without that identity.
A failed identity lookup similarly leaves fresh quota usable as partial data,
without carrying forward history or last-good balances.

The private derived cache has an additive `codex_accounts` map of at most
eight account snapshots, keeping those most recently checked. Only
`services.codex` represents the account just collected; archived snapshots
are not active allowances and are never summed or used for provider selection.
Switching back restores that account's quota and credit histories, subject to
normal history expiry; text output lists them meanwhile. A plan change starts new history. Failed quota reads
retain last-good data only for the same verified account and plan. Legacy
unlabelled history is not assigned to the first account encountered.

Local transcript totals remain observations of sessions on this machine.
A thread continued across accounts is not attributed to either subscription.
The tool does not sign in, sign out, save alternate logins, or switch accounts.

## Claude Code account identity and switching

Claude collection checks `claude auth status --json` before and after reading
usage. The documented command reports authentication status; its observed
JSON fields supply the email, organization ID, and subscription type. Each
check waits at most 15 seconds: it normally answers in under one, but a
shorter cap failed under memory pressure. See the
[Claude CLI reference](https://code.claude.com/docs/en/cli-reference).
The service's `account` uses the same display fields as Codex, with
`source: claude.auth/status`. Its key hashes the case-folded email together
with the organization ID so different organizations do not share history.
Neither credentials nor raw organization/account UUIDs enter the report.

Usage still comes from the undocumented `cachedUsageUtilization` state.
Its `accountUuid` must match the local `oauthAccount.accountUuid`, whose email
and organization must match the CLI identity. A changed account, changed
plan, legacy unlabelled report, or changed source-cache content forces a
`/usage` refresh even inside the normal refresh interval. The
`usage_cache_digest` binds reusable cache contents to the verified report;
it is not a credential. If refresh fails, only the same verified account
and plan can retain previous observations. A changed or unverified login
cannot inherit another account's quota. An unknown cache owner is rejected.

`claude_accounts` holds at most eight last-known snapshots, independently of
`codex_accounts`, and is displayed on the same terms. Switching back restores
that account's retained history;
plan changes restart burn history. Cached reports show the identity last
checked, not a fresh authentication result. Local transcript totals remain
machine-wide observations and are not assigned to an account.

Only the normal first-party subscription login is supported for identity
attribution. API-key, bearer-token, OAuth-token, named-profile, and separate
credential-store environment overrides leave identity unverified instead of
assuming that saved profile metadata describes the effective credential.
The cache format is not a provider guarantee; concurrent clients sharing
that file can still race with collection. This tool never switches accounts.

## Accounts not checked now

`--brief` and `--verbose` list every archived snapshot whose account is not
the one its service just checked, newest check first, under `Other accounts
(last checked; not verified now)`. Each account shows its label, short key,
plan, and check time; each of its buckets shows the last known remaining
percentage and its recorded reset: `(in <duration>)` while it is ahead,
`reset time UNKNOWN` when none was recorded, and, once the reset is behind
the report's `generated_at`, `RESET <time> (<duration> ago)` followed by
either `likely 100% now (was N%)` or `was N%, blocked`.

An account is `LIKELY AVAILABLE` when at least one bucket has reset since
its check and no account-wide bucket is still at 0%. Both text modes then
lead with a `Likely available (reset since last check; not verified):` line
naming it, since that usually means full quota on the other account. This is
the strongest claim available without signing in: the account is not
queried, and switching back is what confirms a reset.

Model-scoped buckets, such as Fable, are sub-limits of the account: their
use also counts against the account-wide buckets. While an account-wide
bucket is at 0%, a model bucket that still has capacity reads `Blocked` in
`--brief` and `blocked` in archived snapshots and `--timeline`.
`--timeline` also lists an unqueried account's resets since its check
(`<duration> ago`), but only when the account is likely available, and
highlights them. For an account still blocked by spent account-wide
buckets it instead highlights the reset of the last of them, when the
account becomes usable, and marks earlier resets `blocked`.

Each archived snapshot carries `ready_at`, the moment its account becomes
likely available under the same rule: the reset of the last spent
account-wide bucket when one is at 0%, otherwise the first reset of a
bucket below 100%; `null` when a blocker's reset is unknown or nothing is
below 100%. It depends only on the snapshot, so it is fixed when the cache
is written.

The tmux component appends, per service, `alt ready` (`alt✓` when narrow,
green) when an archived account's `ready_at` has passed since it was last
checked, which usually means full quota on that account. Otherwise it
shows the soonest weekly reset among those archived accounts when it falls
within 36 hours, as `alt reset <time>` (`alt↻<time>` when narrow). It names
no account, and it skips snapshots already at 100% remaining, since that
reset restores nothing.

An account only appears here if it was checked at least once while signed in.
Legacy unlabelled history is never attributed to an account, so quota
observed before per-account tracking existed is not shown.

## Pace

Every limit carries a derived `pace` object, recomputed whenever the report
is evaluated, including `--cached` reads of a cache written before pace
existed:

- `reset_in_seconds`: seconds from `generated_at` to the reset; null unless
  the period is current.
- `window_share_remaining_percent`: share of the window still ahead at
  `generated_at`.
- `projected_unused_percent`: allocation left at the reset if the observed
  average burn continues; negative means exhaustion before the reset.
- `state`: `behind` (projection below 0), `surplus` (at or above 25),
  `on_pace`, `early`, or `unknown`.
- `reset_soon`: reset within 3600 seconds.

The projection is anchored at the observation time, when the percentage was
true, so re-evaluating a stale observation later never changes it; only
`reset_in_seconds` and the window share move. `early` means less than 5% of
the window had elapsed at the observation, which is too little to project.
`unknown` covers ended or unknown periods and windows of unknown length.
Projections are never clamped.

Each service carries `binding_limit_id`: the limit with the lowest
projection, ties broken by lower remaining percent, or null when no limit
projects. Neither provider reports which models draw on which scoped
bucket, so the report does not filter by model: readers apply the account
bucket plus any bucket named for the model in use. A guessed match would
hide a binding bucket whenever a new model broke the naming pattern.

Verbose output labels binding as period-average and separately names each
current-period bucket whose recent burn projects exhaustion before reset,
including its freshness. A surplus binding is not permission to accelerate
when another applicable bucket is constrained. The delegation skill owns
throughput decisions; the report supplies observations and projections.

## History, burn, and velocity

Each limit keeps a `history` list of raw observations (`observed_at`,
`reset_at`, `used_percent`) carried forward from the derived cache. A new
observation is appended when its time differs from the last entry and either
its percentage changed or at least 300 seconds passed. Entries from earlier
reset periods are kept so velocity can span resets; entries older than 90000
seconds are pruned, and 1000 entries is the backstop cap. `--no-cache` has no
previous report, so it carries no history and derives no burn or velocity.

History is the one place separate snapshots sit side by side. They are never
merged into a percentage; the derived `burn` object is a rate across them:

- `rate_percent_per_hour` and `span_seconds`: from the oldest and newest
  entries inside the trailing 10800-second window, requiring at least two
  entries at least 900 seconds apart, else null.
- `exhausts_at`: when the remaining percent runs out at that rate; null when
  the rate is not positive.
- `exhausts_before_reset`: true when `exhausts_at` precedes the reset. This
  is the current signal; `pace.state` stays the period-average view.

The derived `velocity` object reports consumption over trailing windows
ending at `generated_at`, keyed `1h`, `5h`, and `24h`. Each window carries:

- `used_percent`: percentage points of this bucket consumed in the window.
  Within one reset period only increases count; when consecutive entries
  belong to different periods (reset times more than 60 seconds apart), the
  newer entry's usage counts as consumption in the new period. A bucket that
  is exhausted and refilled keeps counting, so a value can exceed 100.
- `rate_percent_per_hour`: `used_percent` divided by the covered span.
- `span_seconds`: the covered span, from the baseline entry to the newest
  entry. The baseline is the last entry at or before the window start when
  it lies within 600 seconds of it, else the earliest entry inside the
  window. A span shorter than the window means the history does not cover
  all of it.

All three are null when fewer than two entries, or a span under 900 seconds,
fall in the window. Velocity is evidence of demand, not a projection; it is
derived for ended and unknown periods as well as current ones, and verbose
output prints it with the covered span whenever it is shorter than the
window.

## Codex credits

Codex services optionally carry a `credits` list, one observation per
reported bucket, separate from `limits`. Each entry contains `bucket`,
`has_credits`, `unlimited`, and `balance`, plus `observed_at`, `fresh_until`,
`freshness`, and `source`. Balance is a nonnegative decimal string preserving
provider precision, in credits, not currency or percent. Missing or invalid
fields are null (unknown); missing credit details do not mean zero credits.
Brief output rounds balances to two decimal places. Credit balances from
different buckets must not be added; they may describe the same funds.

Credits do not change subscription percentages, binding limits, quota pace,
or exit status. Failed
collection retains last-good credits with their original timestamps;
successful responses that omit credits clear the old credit observation.
Cached reads recompute freshness, and older schema-v3 caches without credit
details remain readable.

Each credit entry has its own `history` of timestamped decimal balances and
derived `pace`: `rate_credits_per_hour`, `span_seconds`, `exhausts_at`, and
`seconds_until_empty`. Pace uses observations in the trailing three hours,
requiring at least 15 minutes of coverage. A balance increase starts a new
estimate, so a top-up cannot appear as negative spending. History uses the
same retention, sample spacing, and entry cap as quota history. Unknown or
unlimited balances clear history and have unknown pace. `--no-cache` cannot
derive a rate from its single observation.

Exhaustion is projected from the latest history point using the unrounded
rate; cached reads move the countdown without moving the exhaustion date.
Zero observed burn has no exhaustion estimate. An elapsed estimate has a zero
countdown, not a claim that the observed balance is now zero. Brief output
shows credits/hour and time until empty; insufficient history is explicit.
These are estimates from net balance changes: purchases or adjustments
between samples can hide consumption. Credits have no reported reset or
expiry here, so they receive no `behind`/`surplus` judgment.

The source is the `credits` field of the documented
[Codex app-server rate-limit response](https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt).

## Token activity

Codex's optional `token_usage` observation comes from the documented
`account/usage/read` endpoint. Brief output adds one line with average
tokens/hour, tokens/day, and tokens/week over seven consecutive provider
calendar dates ending at the latest reported date. These are activity
averages, not measured hourly usage, rolling 24h/168h totals, or a forecast.
The latest date may be incomplete or delayed; the end date is always shown.
Provider dates are used as given without assuming a local timezone.

`period_start`, `period_end`, and `average_tokens` retain the exact period
and unrounded averages in JSON. Brief output abbreviates thousands, millions,
and billions as K/M/B. Missing dates are not assumed to be zero: fewer than
seven consecutive valid daily totals leave averages unknown. Duplicate dates,
invalid counts, and malformed dates are rejected. `status` is `complete`,
`partial`, or `unavailable`. Collection time and freshness are separate from
the dates represented by the data; cached reads recompute freshness.

Token activity is account-wide. It does not affect quota, credits, or exit
status. The optional lookup has a timeout of at most five seconds; failure
records an error under `token_usage` without failing quota collection or
carrying forward an old token summary.

## Model lineup

`--models` reports the live model and effort lineup as a separate document
(`"document": "models"`) and never refreshes quota. Claude Code effort levels
come from the `--effort` line of `claude --help`; model aliases are not
enumerated there, so consult the Agent tool's model list. Codex models come
from its undocumented models cache (`~/.codex/models_cache.json`, override
with `--codex-models-file`); hidden entries are skipped and unrecognized
shapes produce warnings instead of guesses. The exit status is 0 when either
tool's lineup was readable.

## Local agent view and work summaries

`agent-quota --agents` displays recent local Codex and Claude Code sessions
in a separate table. `--agent-days N` selects the activity window (default
one day, maximum 30); `--agent-limit N` limits the rows (default 20, maximum
100). Children are grouped beneath displayed parents. The view reports
observed token totals, cached-input share, model, effort, last transcript
activity, and a short Work label. Width comes from `COLUMNS`, then the
terminal when output is a terminal. Redirected or piped output is
unconstrained, so labels are shown whole rather than fitted to a terminal
that is not there; this is the usual case for another program reading the
table. Otherwise Work takes whatever width the other columns leave, up to
120 characters, shows the brief label when that is too narrow, and
truncates only when neither fits. Activity is not proof that a process is
running. Model and effort are the latest the transcript records. Model names
are shortened by rule, never by table, so a new model still reads correctly:
`claude-`, `gpt-` and `codex-` prefixes and date suffixes go, and a trailing
version joins its name (`opus5.5`, `6-sol`, `auto-review`); `--compact` and
`--verbose` list every one seen. Missing metadata is unknown, never inferred
from current configuration.

Each row also shows where the agent is, derived from its transcript without
model calls:

- **State**: `working` (mid-turn; with the turn's age), `stalled` (mid-turn
  but no transcript activity for 20 minutes), `waiting` (its turn ended;
  with time since), `idle` (waiting over an hour), `done` (a finished
  subagent) or `aborted`. `(long)` marks a turn running over three times the
  agent's median turn, and at least ten minutes. A turn starts at a user
  prompt (Claude Code) or `task_started` (Codex) and ends at an end-turn
  stop or `task_complete`.
- **Claimed tasks**: when agent-kit's `agent-task` is configured, Work leads
  with the tasks an agent holds (`T-0007 · …`, or `T-0007+2` for three) and
  `--compact` lists them as `tasks`. An agent is matched by the records ID
  `agent-id` derives from its session variable: the provider and the first
  16 hex digits of the SHA-256 of its session or thread ID. A Claude
  subagent shares its parent's session, so its tasks appear on the parent.
  The list is read once per view with `agent-task list --json`; nothing is
  shown when agent-task is absent, unconfigured or slow, and `--cached`
  skips it, since it starts no processes.
- **Dir**: the directory the session started in (`cwd` in `--compact`),
  shown by its last segment, `~` for home, with parent segments added only
  where two directories would otherwise look alike (`git/app`, `work/app`).
  Long names are clipped in the middle, never at the end, since sibling
  worktrees share a start. Later `cd`s are ignored: they wander into scratch
  and subdirectories, while the start names the project or worktree.
- **Now**: for a working agent, plan progress such as `2/5 Run tests` when
  the agent keeps a Claude Code `TodoWrite` list or a Codex `update_plan`
  plan, otherwise its pending tool call (`Bash: make test`, or the tools a
  Codex script calls). Between tool calls it is the step just finished
  (`after Bash: …`), or `thinking` at the start of a turn. Commands, paths
  and URLs are clipped from the left of the detail (`Bash: …&& git push`),
  since their end says most; multi-line commands show their first line.
- **15m**: uncached tokens in the last 15 minutes. Rows are ordered busiest
  first, then by state and recency; children stay beneath their parents.

The transcript cannot show a process that died mid-turn: it reads as
`working` until it becomes `stalled`.

Internal sessions, such as Codex's automatic reviewers (`guardian`), are
many, short-lived and never summarized, so the table shows one row per
provider and label: its busiest member's state, the group's total tokens,
and a count in Work (`5 guardian sessions`). `--verbose` and `--compact`
list each session. When Now is folded into Work, it keeps only a step in
progress with a real detail; `after …` steps and bare tool names such as
`exec: write_stdin` leave the room to the label. The State legend keeps each
glyph with its meaning when wrapped.

When a terminal width applies and the table would leave Work under 30
columns, it compacts one step at a time, in this order, stopping as soon as
it fits: drop Cache; drop Seen (State's age covers it); show State as a
glyph and compact age (`▸` working, `⬥` waiting, `∙` idle, `!` stalled, `✓`
done, `✗` aborted, `+` for a long turn) with a generated legend; shorten
agent IDs to the shortest unique provider prefix and ID suffix (`cl:9cf6`,
growing on collision); clip the middle of Dir to 16 columns, keeping its
first segment and its end (`gameproj…defects`); drop lifetime Tokens;
shorten efforts to their shortest unique prefix among the rows shown
(`Eff`); fold Now into Work; shorten model names with an ellipsis; drop Dir.
Short forms are derived from the values shown, never from a fixed table, so
new models, efforts, states or providers appear in full rather than mis-
abbreviated. Glyphs are Unicode width N or Na without emoji forms. Piped
output is unconstrained and never compacted. The footer wraps to the width.

The agent cache keeps work summaries across cache-version changes; only the
parsed sessions are rebuilt.

`--agents --verbose` adds full IDs, parent IDs, labels, model/effort/speed
sets, token breakdowns, parser warnings, and summary state. `--agents
--compact` emits the separate `document: agents`, schema-version-1 JSON
document carrying both labels, plus `status` (the parsed state, turn start,
last event, pending action, plan progress and recent turn durations) and
the derived `state`, `turn_age`, `long_turn`, `now` and `recent_tokens`; it
omits source paths, token events and message excerpts used as summary
input.
`--cached` neither scans
transcripts nor starts model calls. `--no-cache` scans without reading or
writing caches and does not generate summaries. `--no-summaries` updates
observations without starting model calls. Ordinary quota queries never
start summarization.

The collector reads undocumented local JSONL formats. Claude messages are
deduplicated by message ID, retaining the maximum observed counters across
repeated fragments. Claude input includes uncached input, cache reads, and
cache writes; Codex input already includes cached input. Reasoning counts
are subsets of output, not extra tokens. Codex cumulative counters are
differenced, excluding pre-fork observations and handling counter resets.
Claude rows belonging to another session are excluded from a copied root
transcript. Totals are per-agent observations, not family rollups or billing
records. Oversized (>4 MiB), malformed, and incomplete records are skipped
and reported; such sessions may have incomplete counts.

The private `agents-v1.json` cache sits beside the quota cache and is
separately locked and atomically written with mode 0600. It stores parsed
observations, bounded user text, and summaries. Unchanged source files are
not reread. Deleted sources and sources untouched for 30 days are pruned,
along with their summaries. One scan processes at most the 100 most recently
modified candidate transcripts and reports truncation. Root overrides are
`--codex-sessions-dir` and `--claude-projects-dir`; `--agent-cache-file`
overrides this cache without moving the quota cache.

Work falls back to a recent message excerpt or agent label immediately.
Changed text schedules a detached refresh, never blocking table display.
Requests batch up to three threads assigned to the same provider, with a
36,000-byte batch prompt limit and unchanged per-thread input limits. IDs
are validated before any result is cached; missing, duplicate, or unknown
IDs fail the batch and preserve old labels until the normal retry.
At most two CLI calls run concurrently across workers (one cache lock),
and each pass covers all displayed sessions due for a summary. Each agent
has a five-minute attempt cooldown, including failures. A new owner message
refreshes a label at once. Changed agent activity or task-list step
refreshes it only when the label is at least 15 minutes old, so a busy
agent is relabelled at most about four times an hour. Unchanged inputs reuse
summaries indefinitely. Every displayed session with owner messages or
activity is eligible, regardless of inactivity. Old
summaries survive failures and quota deferrals; verbose output marks a
summary outdated when its input hash differs. Attempts are saved before
calling a model, so a worker crash does not cause an immediate retry storm.

Summary input combines four signals, because the owner's latest message
may be a question or aside rather than the work in progress:

- **Owner messages:** at most the latest six real user messages, each
  limited to 1,000 characters, with a 4,000-character total budget
  allocated newest-first. Interruption markers and bare acknowledgements
  (`yes`, `ok`, `Restarted`) are dropped.
- **Agent activity:** the agent's last 12 steps, oldest first: each tool
  call as the agent described it (a Bash `description`, a message
  `summary`) or else its action, the first sentence of each reply, and
  Codex reasoning headings. Each is capped at 300 characters, which covers
  99.5% of observed replies; task-list writes are left to the next signal.
- **Task list:** the current `TodoWrite` or `update_plan` step and count.
- **Claimed tasks:** up to five `agent-task` tasks the agent owns or helps
  with (in progress, in review or blocked), the strongest sign of its
  assignment.

The prompt asks for the work in progress rather than a side question or a
finished step. In all three, images become `[image attached]`, runs of 120
or more base64-like characters become `[data]`, and image bytes and
attachment paths or URLs are omitted. Tool steps can name ordinary files and
commands. Over the byte cap, the oldest activity is dropped first, then the
oldest messages. Recognized skill injections, environment/instruction
blocks, tool results, and subagent reports are excluded. The previous
summary is included for continuity. The serialized UTF-8 prompt is capped at
12,000 bytes. One call returns two labels: a summary budgeted at 60
characters and a brief at 28. Models overrun character budgets, so the
summary is retained whole up to 120 characters and the brief doubles as the
repair when the summary will not fit the table. A missing, invalid, or
overrun brief is dropped rather than shown truncated; the row degrades
without failing, and the fuller summary is truncated instead. The prompt
schema is part of the cached input hash, so a schema change refreshes stored
labels once. This is bounded text extraction, not a general secret detector.

By default, either provider may summarize either tool's transcript. Among
providers passing all quota gates, choose the one with the most remaining
percentage in its tightest applicable bucket; ties prefer the transcript's
original provider. Unavailable subscription authentication falls back to
the next eligible provider. Use `--no-cross-provider-summaries` to restrict
new calls to the original provider. This does not erase existing cached
summaries. Summary provenance is recorded in JSON and verbose output; `~`
marks fallback excerpts in the table.

Codex calls use Luna at low effort; Claude calls use Sonnet at low effort.
CLI calls use ephemeral
sessions and isolated temporary working directories; Claude tools are
disabled, and Codex uses read-only sandboxing with shell, multi-agent, skill
discovery, and web search disabled. Calls have a 45-second timeout; a timed
out process group is killed. Summary sessions are not persisted, preventing
recursive summarization. Claude also receives a $0.05 per-call budget.

Before summary calls for either provider, the worker checks the account against
its quota snapshot, refreshing quota if the login differs. It checks again
before each batch and defers on mismatch or unknown identity. This reduces
the switching race but cannot prevent a login change after the check.

Automatic calls require verified subscription authentication and fresh,
complete quota with at least 3% remaining in every applicable account/model
bucket; a spent account bucket therefore blocks its model limits too. Pace
and recent burn do not gate labels, which are cheap: they are deferred only
when no provider qualifies. Missing, stale, or failed quota defers the
summary. Purchased credit balance never overrides this gate. The background
worker refreshes missing or stale quota before choosing a provider. A fresh, explicitly unused
Claude five-hour window (zero use, no reset, inactive) does not block calls;
the weekly and applicable model limits still apply. These checks are
advisory, not a provider-enforced guarantee against credit spending during
concurrent work. Authentication or model-call failures retain the fallback.

## Claude local token activity

The Codex token line is explicitly labeled Codex. Claude Code has a separate
line showing hourly/daily/weekly averages from the trailing seven days of
**observed local transcript usage**. It is not an account-wide measurement
and is not directly comparable to Codex's provider-calendar-day totals.
Remote/deleted/unscanned sessions are absent. The collector considers seven
days of Claude files even when the agent table uses a shorter window.
Message IDs are deduplicated across copied
sessions; cached tokens are included once in total input. The same line
reports the trailing wall-clock hour and how many agents produced it, which
is where current spend is visible; per-agent rates are not shown because
nearly every row is idle within that hour.

Ordinary quota queries refresh these local observations without any model
calls; cached queries recalculate the window and freshness from the agent
cache. JSON exposes this under Claude's `token_usage` with
`scope: observed_local_sessions` and a `recent_hour` object holding that
window's deduplicated token total and distinct agent count; it does not
affect quota exit status.
The inventory cap and parser omissions can make this a partial measurement.

## Sources and cache

Claude Code data comes from its advisory `cachedUsageUtilization` state,
refreshed with the CLI's `/usage` command without session persistence. That
cache is undocumented, so schema changes fail visibly and last-good derived
data is retained. `fetchedAtMs` is milliseconds since the Unix epoch.

Codex data comes from the documented app-server
`account/rateLimits/read` method. Source identifiers are stable strings and
never expose local filesystem paths.

`agent-quota` exclusively owns provider refresh, normalization, refresh
locking, and atomic writes to its private derived cache. A failed refresh does
not replace or retimestamp last-good observations. Status renderers only read
the cache and start non-blocking background refreshes, and none while a
refresh already holds the cache lock. They skip limits whose bucket or window
is not an object instead of failing the whole line.

## Exit status

Normal execution succeeds when at least one requested service has a usable
current-period observation. `--strict` additionally requires every requested
service to be `complete`. Historical-only or unavailable reports fail even
though their observations remain in the output for diagnosis.
