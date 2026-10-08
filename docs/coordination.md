# Coordination and efficiency reference

[Back to the introduction](../README.md).

This is an agent-facing reference for event waits, resource admission and
usage reports. For the human overview and setup prompt, start with the
introduction. These mechanisms support chosen workflows; installing the kit
does not start a scheduler or grant permission to run new work.

## Coordination and efficiency

Three local tools reduce model-driven polling and repeated audit scripts.
They use the Python standard library on macOS and Linux; no service is installed.

### Multi-task event batches

`agent-task events T-0001 T-0002` returns a JSON snapshot cursor immediately.
Pass that exact `cursor` object as `--after '<json>'` on the next call to wait
for a batch of new log entries or header changes. `--for ID` filters log
messages, including messages to `all`; header changes remain visible. Cursors
advance over suppressed messages too. A batch contains all changes observed
in one locked snapshot; it does not delay an actionable event to fill a batch.

The same task set is required on resume. `--timeout 540 --interval 5` bounds
local polling without model turns; exit 4 means no matching event before the
deadline and still returns a usable cursor. Exit 5 with `rewritten` task IDs
means log history changed: inspect those tasks before using the replacement
cursor. Missing tasks and pending recovery fail explicitly. Reads never repair
records and `--unlocked` is refused. The version-1 JSON contains `events`,
`cursor`, and `rewritten`; each cursor stores a log prefix count/hash and a
header hash. Store cursors privately, outside source control. Do not confuse
an empty batch with completed work.

Use `--actionable` to suppress ordinary lease-renewal timestamp changes.
It still emits addressed messages and all other header changes, and detects
an active claim becoming expired on a later poll even without a file write.
It observes current state, not every transition between polls; it never
releases a claim or a resource. Full fields remain in emitted header events.
Keep the mode consistent when resuming: switching modes causes a fresh header
event. A shorter active renewal does not wake this mode until observed expiry;
use the default feed when exact lease deadlines matter.

The watcher lifetime is independent of a host tool's blocking/yield limit.
With background completion notifications, let the command keep waiting across
tool yields instead of starting a fresh model turn every minute. Host-required
updates still apply. `--agent ID` is accepted before or after an `agent-task`
subcommand; if both are given, the later value wins. Use `--` to protect
literal message arguments beginning with an option name.

### Cooperative resource admission

```sh
agent-resource run --resource gpu --wait 600 --timeout 1800 -- command args
agent-resource capacity --resource gpu --credits 2 --wait 60
agent-resource run --resource gpu:1 -- command args
agent-resource run --resource gpu:2 -- command args
agent-resource capacity --resource build --credits 2
agent-resource run --resource gpu:1 --resource build:2 -- command args
```

Use agreed resource names among competing workers. Names are case-sensitive.
Each resource defaults to one credit. Configure its machine-local budget with
`capacity --resource NAME --credits N`; budgets and requests are integers
from 1 to 64. Plain `--resource gpu` requests one credit; `gpu:2` requests
two. Configure each resource before requesting more than its default budget.
A two-credit GPU admits two one-credit jobs or one two-credit job. Request
the entire configured budget for an exclusive run, such as a performance
measurement. Credits are admission units, not enforced memory or compute
allocations; choose weights based on the workload.

Capacity changes wait until no cooperating job holds that resource, then
replace its budget atomically. A wait timeout leaves the old budget intact.
Existing callers pick up the budget without changing their commands. Older
tool versions still exclude all new callers through the original gate lock,
so mixed versions remain safe but old callers cannot share capacity.
Requests above the budget fail immediately (exit 1). Request each resource
only once. Acquisition is ordered by name to avoid deadlocks; all credits
for one resource are acquired together or released before waiting. This is
cooperative admission, not a capacity monitor or proof that the machine is
quiet. It provides no FIFO/fairness guarantee. A waiter may hold a subset
while acquiring another resource, so use only resources the command needs.
Existing project leases still apply; do not invent a competing authority.

Admission expires after `--wait` seconds (exit 75, command never started).
Execution defaults to a one-hour timeout (exit 124). Normal command exit
codes pass through. Interrupts and timeouts terminate the foreground process
group, then kill remaining members after a short grace period. Descendants
must not detach: the wrapper also cleans up the group when its leader exits.
Lock descriptors are inherited by the command so supervisor death alone
cannot release its locks. Programs that close inherited descriptors or escape
the group can defeat this safeguard; do not use this wrapper for daemons or
as a security boundary. Admission remains held until the entire process group disappears. If cleanup
cannot be confirmed within five seconds, a warning is printed and the wrapper
keeps waiting with its locks held. An unkillable child can retain admission
beyond the command timeout.

Stable lock files live under `$XDG_STATE_HOME/agent-kit/resources`, defaulting
to `$HOME/.local/state/agent-kit/resources`. Budget files are named
`NAME.capacity`; `NAME.lock` is the compatibility and configuration gate, and
`NAME.slots/` holds credit locks. They contain no commands or task text. Never
delete lock files to break a live lock: that creates two independent locks.
Idle files may remain indefinitely and cost no running process. The wrapper
does not write task records or claim resource ownership for other agents. It
runs only the explicitly supplied command.

### Structured scheduler pilot

`agent-scheduler` records exact one-use grants and separates operational release
from result acceptance. It requires an explicit private `--state` directory and
coordinator; it does not replace existing live grants or overlap policy.
Automatic release is opt-in for cooperative, trusted foreground process groups.
Crashes and uncertain cleanup retain a recovery hold. See the maintained
[workflow and CLI reference](../bin/agent-scheduler.md) for manual-first adoption,
shadow receipts, failure handling and the live cutover boundary.

### Advisory release evidence

`agent-release-check` checks a supplied manual-grant evidence packet without
reading live reservations, running process checks, or writing task records.
It separates workload outcome from recorded terminality, reaping, ended intent
and a successful scoped observation. Optional coordinator decisions support
shadow comparison. Consistent records do not prove their provenance or actual
process quiescence, and the result grants no release or retry authority.
See the [contract and examples](../bin/agent-release-check.md).

Keep a [compact coordinator handoff](../bin/agent-scheduler.md#compact-coordinator-handoff)
with authoritative references, unresolved holds and the next action, instead of
copying implementation history into every scheduling turn.

### Offline usage report

```sh
agent-efficiency --since 2026-01-01T00:00:00Z \
  --until 2026-01-01T06:00:00Z > /path/to/private/report.json
```

The default window is the last six hours. `--provider codex|claude|both`,
`--codex-dir`, and `--claude-dir` select local JSONL inputs. The command reads
transcripts without contacting providers, invoking models, or writing caches.
Output contains identifiers and usage, but no prompts, commands, source paths,
or tool arguments. Treat even this metadata as private. Exit 1 means detected
incomplete coverage; the partial report still explains the diagnostics.

Counts are observed activity, not account billing, credit spend, useful work,
or proven waste. Cached input is included in input and total tokens. Claude
cache creation is counted once, and reasoning is not added to output twice.
Request IDs deduplicate globally, including copied transcripts and streaming
updates. Earliest observed request time determines the half-open window
`[since, until)`. Codex per-request records take precedence over cumulative
counters for a thread. When both formats occur, the report marks coverage
unverified: it does not prove that request records cover every cumulative
observation, even though it avoids double-counting them. Legacy counters use the previous observation as their
baseline; an unknown first baseline is omitted and diagnosed. Decreases start
a new counter epoch. A transition to per-request records within a thread can
leave older activity uncovered; earlier cumulative observations in the window
are diagnosed as incomplete rather than silently omitted.

Groups split provider, thread, model, internal guardian activity and accounting
method. Guardian groups are included in totals. `tool_calls` identifies busy
tool categories; it does not attribute model tokens to those tools or infer
which instructions caused them. `compaction_agent_seconds` sums recorded
compaction durations and may exceed wall time under concurrency. Unknown
models stay unattributed. Missing files, unsupported schemas and incomplete
transcripts limit coverage; a successful parse cannot prove a complete history.
Diagnostics describe all scanned files, including historical baseline material.
Assistant activity without any recognized usage is explicitly incomplete.
Use repeated comparable windows to decide where deeper trace inspection is
worthwhile, and validate improvements against completed useful work.

`coordination` reports supported completed shell attempts, explicit failures,
empty event returns (bootstrap and timeout separately), and proven renewal-only
wakeups. Exit 4 from a recognized event wait is a timeout, not a failure.
Exact running-cell envelopes can be joined to same-thread wait results; pending
work is not counted as completed. Unsupported result reasons distinguish
missing status, mismatched envelopes and truncation. Plain stdout without a
reliable exit status remains unknown, even when its text looks successful.
These are partial observations, not counts of successful record mutations.
Recognition is deliberately limited to literal shell calls and supported tool
result envelopes; dynamic scripts and missing results remain unclassified.
The existing `complete` flag describes detected usage-ledger coverage issues;
it does not certify complete coordination-event coverage.
Renewal-only evidence requires a continuous observed feed with a prior full
header, only an increasing expiry timestamp, and no delivered log messages.
Unknown baselines cannot establish renewal overhead. No transcript code is
executed, and no command or message text is included in the report.

`compaction_count`, `compactions_by_thread` and
`compaction_unknown_duration_count` distinguish completed compactions with
known timing from those without it. Transcript-derived release-to-confirmation
latency remains unavailable; free-form task prose is not a timing contract.
Use structured lifecycle receipts for the separate measurements below.

For the scheduler's structured lifecycle evidence, use
`--scheduler-receipts FILE` (repeatable). Add `--receipts-only` to skip transcript
scanning. The separate `scheduler_lifecycle` section reports terminal-to-release
and quiescence-to-release delays, preserving missing timestamps as unknown and
rejecting conflicting exports. `complete` also becomes false when a requested
receipt export is invalid. See the [receipt contract](../bin/agent-scheduler.md).
