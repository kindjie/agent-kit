# Focused Tests

Run everything from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t .
```

`test_md_preview.py` skips unless its dependencies are importable by the
interpreter running the suite; the recipe below supplies them.

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
single-process RPC fixture with an optional usage timeout. Archived accounts
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
the verbose renderer.

Run the local agent collector and summary-cache suite:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_agent_quota_agents
```

This covers per-agent token normalization and deduplication, copied/forked
history, model and effort metadata, image omission and newest-first text
limits, local seven-day token averages, source-cache reuse and deletion,
quota/authentication gates, summary cooldown and failure retention, two-call
concurrency across all displayed rows, batches of three with exact ID
matching and per-thread bounds, background quota refresh, unused
Claude session windows, bounded CLI responses, and cache-only CLI behavior. Model calls
in this suite are mocked or use local fake executables; no credits are spent.
Account mismatch defers both providers at selection and before each batch. Provider-selection
tests cover tightest-bucket ranking, cross-provider
provenance, the same-provider opt-out, and initial summaries for idle agents.
Label tests cover the summary/brief pair from one call, briefs that are
missing, overlong, or the wrong type, and the prompt-schema hash that
refreshes stored labels once. Renderer tests cover width resolution
(`COLUMNS`, terminal, unconstrained pipe), the measured Work width, the
brief substituted for a label that will not fit, ellipsis only when neither
fits, and trailing-hour totals in the footer.

Run the status-renderer suite from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_agent_status
```

`test_agent_status.py` covers Claude's native row (session and weekly
windows), wide and narrow tmux quota grouping, warning colours,
the per-bucket pace glyph (behind, surplus, on pace, unknown, burn
exhaustion, stale periods), stale and historical markers, reset
formatting, a 5h window replacing its bucket's weekly one when about to run
out (threshold, burn, weekly lower, ended period), the soonest
inactive-account weekly reset within 36 hours (excluding the active, full,
mismatched, passed, and session entries), and schema rejection.

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
with a dash, and that mute round-trips.
