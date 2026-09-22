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
  be combined with `--compact` or `--models`.
- Both text modes list archived accounts when any exist; see
  [Accounts not checked now](#accounts-not-checked-now).
- `--cached` reads and reevaluates the derived cache without querying a
  service.

Renderers consume JSON or the derived cache. They must never parse `--brief`.
An unsupported `schema_version` is an error, not a best-effort input.

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
JSON fields supply the email, organization ID, and subscription type. See the
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
percentage and its recorded reset, marked `(PASSED)` once that reset time is
behind the report's `generated_at`, `(in <duration>)` while it is ahead, and
`reset time UNKNOWN` when none was recorded.

`(PASSED)` means only that the recorded period ended, which is the strongest
claim available without signing in: the account is not queried, and switching
back is what confirms a reset. Snapshots are re-evaluated against
`generated_at` in JSON output too, so `period_relation` is never a stale
`current`. `--for MODEL` narrows archived buckets exactly as it narrows live
ones, and selecting one provider drops the other's archive.

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
projects. `--for MODEL` keeps account buckets plus scoped buckets whose id or
name matches the model slug, recomputes bindings on the kept set, records
`model_filter` at the root, and leaves the exit status of the unfiltered
report unchanged.

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
or exit status. The model filter applies to their buckets too. Failed
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

Token activity is account-wide and is not narrowed by `--for`. It does not
affect quota, credits, or exit status. The optional lookup has a timeout of
at most five seconds; failure records an error under `token_usage` without
failing quota collection or carrying forward an old token summary.

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
running. Mixed models or effort levels are shown as mixed; missing metadata
is unknown, never inferred from current configuration.

`--agents --verbose` adds full IDs, parent IDs, labels, model/effort/speed
sets, token breakdowns, parser warnings, and summary state. `--agents
--compact` emits the separate `document: agents`, schema-version-1 JSON
document carrying both labels; it omits source paths and message excerpts
used as summary input.
`--for` matches observed model names in this view. `--cached` neither scans
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
has a five-minute
attempt cooldown, including failures. Unchanged inputs reuse summaries
indefinitely. Every displayed session with user messages is eligible for
generation or refresh when its input changes, regardless of inactivity. Old
summaries survive failures and quota deferrals; verbose output marks a
summary outdated when its input hash differs. Attempts are saved before
calling a model, so a worker crash does not cause an immediate retry storm.

Summary input contains at most the latest six real user messages, each
limited to 1,000 characters, with a 4,000-character total budget allocated
newest-first. An older message is shortened or dropped before a newer one.
Images become `[image attached]`; image bytes, attachment URLs, and paths
are omitted. Recognized skill injections, environment/instruction blocks,
tool results, and subagent reports are excluded. The previous summary is
included for continuity. The serialized UTF-8 prompt is capped at 12,000
bytes. One call returns two labels: a summary budgeted at 60 characters and
a brief at 28. Models overrun character budgets, so the summary is retained
whole up to 120 characters and the brief doubles as the repair when the
summary will not fit the table. A missing, invalid, or overrun brief is
dropped rather than shown truncated; the row degrades without failing, and
the fuller summary is truncated instead. The prompt schema is part of the
cached input hash, so a schema change refreshes stored labels once. This is
bounded text extraction, not a general secret detector.

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
complete quota for every applicable account/model bucket, more than 5%
remaining, no behind-pace projection, and no recent-burn exhaustion. An
early window is eligible; an unknown pace is not.
Missing, stale, failed, or constrained quota defers the summary. Purchased
credit balance never overrides this gate. The background worker refreshes
missing or stale quota before choosing a provider. A fresh, explicitly unused
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
the cache and start non-blocking background refreshes.

## Exit status

Normal execution succeeds when at least one requested service has a usable
current-period observation. `--strict` additionally requires every requested
service to be `complete`. Historical-only or unavailable reports fail even
though their observations remain in the output for diagnosis.
