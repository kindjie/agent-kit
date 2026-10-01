# Focused Tests

## Estimation Skill

For changes to `skills/estimate-agent-work/SKILL.md`, use the synthetic
behavior and routing cases in
[estimate_agent_work_cases.md](estimate_agent_work_cases.md).
Have an independent agent evaluate decisions, including near-misses; these
are behavioral checks, not automated wording assertions. Also run the rules
and privacy suites when changing the skill's always-loaded integration.

Run everything from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t .
```

`test_md_preview.py` skips unless its dependencies are importable by the
interpreter running the suite; the recipe below supplies them.

## Quota Snapshot Identity

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_quota_snapshot_identity
```

These tests use synthetic provider snapshots to cover namespaced quota-ID
hashes without raw-ID persistence, same-email workspace isolation and return,
identity-source upgrades, failed reads, malformed or absent IDs, routing
changes, summary rechecks, and partial daily coverage without zero filling.

## Agent Quota

Run from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_agent_quota
```

`test_agent_quota.py` covers schema-v3 normalization, strict value and
timestamp handling, stale/reset state, scoped buckets, last-good retention,
pace projection anchored at observation time (behind, on-pace, surplus, early,
unknown, reset-soon), binding-limit selection, pace added to older cached
reports, observation-history merging across reset periods, trailing-window
velocity (1h, 5h, 24h consumption summed across resets, coverage spans, and
brief output), recent-burn exhaustion (including nonbinding buckets and
stale/ended observations in brief constraint summaries), the `--models` lineup
document, agent-readable output, private caching, and Codex app-server
failures. Account-switch coverage includes A→B→A quota/credit isolation,
same-account failure retention, legacy-cache migration, unknown identity, plan
changes, bounded account snapshots, changes during collection, and a
single-process RPC fixture with an optional usage timeout. Identity-upgrade
regressions cover older 82%/1% readings labelled as other identities before
the current heading in brief and verbose output, a new identity without
quota readings, and fresh 100% readings without inherited history. Timeline
coverage distinguishes same-email plans and keys without changing JSON
emails or merging archived identities' simultaneous resets. Archived accounts
are covered for elapsed and live resets, newest-check ordering, exclusion of
the account last checked, absence when only one account is known, and
narrowing by provider selection. Claude coverage also verifies organization
isolation, cache-owner matching, forced refresh across switches, and rejection
of credential overrides.

Credit coverage includes balances separate from exhausted subscription quota,
unknown/zero/unlimited and invalid values, freshness and failed-refresh
retention, and credit burn/exhaustion estimates with top-ups, duplicate
samples, zero burn, and insufficient or expired history. Token activity tests
cover seven-calendar-day averages, compact formatting,
missing/invalid/duplicate daily data, cached freshness, optional lookup
failure isolation, and app-server method selection. Compact table tests cover
constraints first, recent-burn precedence, credit history warm-up, stale/ended
observations, and visible refresh errors. Detailed output assertions exercise
the verbose renderer. Timeline tests cover quota names without the default
weekly window, day grouping in time order with aligned columns, notes
(`N% left`, burn rates, credits, `stale`), BURN details added in priority
order as width allows, resets read from history (early ones with what was
due, scheduled ones, a fresh window rolling forward not counted, an hour's
visibility, one alert and none on expiry), exhausted quotas and the reset
that restores them, merged simultaneous events, inactive accounts (the
reset that frees a blocked account highlighted, earlier ones `blocked`,
past resets only when likely available), hidden routine 5h items and
noteworthy ones shown, colour that only adds escape codes (urgency,
per-account colours, dim lines, plain output for pipes, `NO_COLOR` and
`TERM=dumb`), the unmerged JSON events, and the CLI paths. Archived-account
tests cover the `Likely available` line and explicit `RESET … ago` in the
text modes, accounts still blocked by a spent account-wide bucket, and
model buckets blocked by a spent account bucket.

Flag-validation tests cover agent-only options without `--agents`
(including abbreviated and `=` forms), options `--models` cannot use, query
timeouts with `--cached`, and meaningful combinations that must still parse
with their defaults.

Colour tests cover `--color` resolution (`always` over `NO_COLOR`,
`never` over a terminal), styled tables that keep their alignment, pace
colours and alerts in `--brief`, keyword highlighting in `--verbose`, the
models brief, and `--color` rejected for JSON output; each checks that
removing the escape codes leaves the plain output unchanged.

The timeline, live and `ready_at` tests live in their own file, which
imports fixtures from `test_agent_quota.py`:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_agent_quota_timeline
```

Live tests drive `run_live` with a fake clock and key-returning wait: the
alternate screen entered and restored, the spinner stepping once a second
between frames, `q` and a lone Escape quitting while key sequences do not,
later frames marking new rows, alerts only with `--notify`, quota queried
only when stale and not `--cached`, alert rules, screen fitting,
notification commands per platform, the `--live` flag rules, and one plain
frame when output is not a terminal.

Run the local agent collector and summary-cache suite:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_agent_quota_agents
```

This covers per-agent token normalization and deduplication, copied/forked
history, model and effort metadata, image omission and newest-first text
limits, local seven-day token averages, source-cache reuse and deletion,
quota/authentication gates, summary cooldown and failure retention, two-call
concurrency across all displayed rows, batches of three with exact ID matching
and per-thread bounds, background quota refresh, unused Claude session
windows, bounded CLI responses, and cache-only CLI behavior. Model calls in
this suite are mocked or use local fake executables; no credits are spent.
Account mismatch defers both providers at selection and before each batch.
Provider-selection tests cover tightest-bucket ranking, cross-provider
provenance, the same-provider opt-out, and initial summaries for idle agents.
Label tests cover the summary/brief pair from one call, briefs that are
missing, overlong, or the wrong type, and the prompt-schema hash that
refreshes stored labels once. Live-helper tests cover stall and dominance
alerts and the per-provider quota header, including its fallback bucket.
The live header reports an account without quota readings and never falls
back to a previous identity's allowance.
Status tests cover Claude Code turns, pending tool calls and `TodoWrite`
progress, Codex turns, script actions, `update_plan` progress, aborts and per-
event token deltas, derived stalled/idle/long states, the uncached 15-minute
rate, busiest-first ordering, and the State/Now/15m columns with colour.
Renderer tests cover width resolution (`COLUMNS`, terminal, unconstrained
pipe), the measured Work width, the brief substituted for a label that will
not fit, ellipsis only when neither fits, and trailing-hour totals in the
footer.

`ReadyAtTest` checks that the recorded `ready_at` agrees with the likely
available rule at several moments (blocked, two blockers, open, a spent
model bucket), stays null for unknown blockers and full snapshots, and is
written by the account cache.

Compaction tests render at every width from 200 down and require the applied
steps to be a prefix of the ladder, no line over the width from 60 columns
up, derived short forms that grow on collision (efforts at least three
characters, the width of their heading), single-width glyphs, unknown
states, models and efforts in full, and the legend only with glyphs. A cache
test keeps summaries across a version change. Action tests keep the end of
commands and paths, and the start of other details; parser tests show the
latest model and effort and the starting directory; Dir labels grow a parent
segment only on collision. A terminal reporting zero columns falls back to
120. Gate tests defer summaries only under 3% left. Summary-input tests
cover activity from Claude and Codex (descriptions, reply first sentences,
reasoning headings), dropped noise messages, objective-only extraction from
automatic Codex goal continuations, scrubbed and bounded activity with the
oldest dropped first, the 15-minute activity refresh, and Now
showing the step just finished. Display tests cover rule-based model names
and middle-clipped Dir, internal sessions sharing one row unless verbose,
folded Now skipping quiet steps and following the label only in spare
room, and a live header that keeps the account
headline, names blocked models, lists each provider's own agents, and fits
the width with one ID style. Claimed-task tests check records IDs against
agent-id's own derivation, owner and helper matching, the Work prefix, and
the claimed_tasks summary input.

Run the status-renderer suite from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_agent_status
```

`test_agent_status.py` covers Claude's native row (session and weekly
windows), wide and narrow tmux quota grouping, warning colours, the per-bucket
pace glyph (behind, surplus, on pace, unknown, burn exhaustion, stale
periods), stale and historical markers, reset formatting, a 5h window
replacing its bucket's weekly one when about to run out (below 10%, not at 10%
or on burn alone, weekly lower, ended period), the soonest inactive-account
weekly reset within 36 hours (excluding the active, full, mismatched, passed,
and session entries), `alt ready` once an inactive account's `ready_at` has
passed since its check (outranking an upcoming reset, never naming the
account), skipping limits with malformed buckets, `?` for a
present service with no limits (and omission of an absent one), no background
refresh while one holds the cache lock, and schema rejection.

## Markdown Preview

Run from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 \
  uv run --with cmarkgfm==2025.10.22 --with nh3==0.3.6 \
  python3 -m unittest tests.test_md_preview
```

Keep the dependency versions in this test recipe synchronized with the
PEP 723 metadata in `bin/md-preview.py`.

`test_md_preview.py` covers path resolution, safe GFM rendering, forced
themes, verified stylesheet caching, repository-relative assets, response
security headers, and live-reload state.

## Attention Notifications

Run from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_agent_speak
```

`test_agent_speak.py` covers option handling with every speech backend
shadowed by a stub, so a regression is recorded in a file rather than read
aloud. It asserts that an unrecognised option exits 2 without speaking, that
a notify payload is never spoken, that `--` still allows a message starting
with a dash, and that mute round-trips. Lock tests cover the private lock
directory, reaping a dead holder, speaking after the timeout
without releasing a live holder's lock, and skipping the lock when its
directory is not a real one owned by the user.

## Always-Loaded Rules

Run from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_agent_kit_rules
```

`test_agent_kit_rules.py` installs `AGENTS.snippet.md` into temporary files:
appending with one blank line, a no-op re-run, replacing a moved snippet in
place, recognising the released unhashed snippet, refusing an edited one
unless forced, refusing duplicate, missing or out-of-order markers without
writing, the records section following both doctors, dry runs, and keeping
symlinks and permissions. Regression tests keep CRLF line endings, refuse
symlink loops, dangling links, non-UTF-8 files and a file changed mid-
update, and ignore marker examples in fenced or inline code. Doctors are
stubbed scripts; nothing touches real instruction files.

## Records Hook

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_agent_records_hook
```

`test_agent_records_hook.py` covers which shell commands count as lasting
state (worktree, stash, new branch, tool install, service) and which do
not (listing, cleanup, quoted or echoed text), finding the command in
Claude Code and Codex tool inputs, the PostToolUse context JSON, silence
without records configuration or on bad input, and the session-start
summary through a stubbed `agent-task` and `agent-changelog`.

## Agent Records

Run the records suites from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests \
  -p 'test_agent_records_*.py' -t .
```

The suites launch the three command scripts in subprocesses against temporary
git repositories. A class fixture initializes a tasks/changelog pair once,
then each test copies it to a fresh temporary directory. Tests use a temporary
global git config with signing off and disable system git config. The setup,
lifecycle, records, commands, transitions and watch suites cover identity,
initialization, task claims, permissions, help, checklist corrections
(re-checking, owner-only unchecking, and edits confined to the checklist),
closing and watch cursors.
The planning suite covers dependency declaration/revocation, graph validation,
prerequisite and dependent status views, `next` readiness across closure and
reopening, per-model estimate updates/removal, storypoints, permissions,
invalid inputs without writes, lint rejection and legacy records. Run it alone:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_agent_records_planning
```

The estimate-policy suite covers default compatibility, warnings, strict
refusal without writes, execution-model selection, partial/unknown estimates,
replacement of unknowns, takeovers, handoffs, resumption, separate helper
selection, registration/removal/closure, actionable warnings, invalid config
and closed tasks. Run it alone:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_agent_records_estimate_policy
```

The efficiency suite covers atomic create/estimate/claim, invalid startup
without ID allocation, inline claim estimates, expired same-owner renewal
versus takeover, read-only lock access, pending-journal refusal across readers,
and explicitly unverified diagnostic reads:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_agent_records_efficiency
```

The recovery and cross suites exercise real git signing children, index
locks, staged deletions, journals, and cross-repository races. The privacy
suite scans tracked and new files;
`AGENT_RECORDS_PRIVACY_DENYLIST` can name a private newline-delimited list
of additional strings. Keep lock and timeout fixtures short so the full
suite stays fast.

For a faster records-only run, the optional standard-library runner executes
independent test classes in parallel. Its worker count defaults to
`os.cpu_count()` and it uses the same temporary fixtures:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/run_agent_records_parallel.py
```
