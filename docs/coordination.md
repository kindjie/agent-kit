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

Use agreed resource names among competing workers. Names are folded to lower
case, because case-insensitive filesystems would alias their lock files.
Requesting two names that map to the same credit variable (`a.b` and `a-b`)
is refused.
Each resource defaults to one credit. Configure its machine-local budget with
`capacity --resource NAME --credits N`; budgets and requests are integers
from 1 to 64. Plain `--resource gpu` requests one credit; `gpu:2` requests
two. Configure each resource before requesting more than its default budget.
A two-credit GPU admits two one-credit jobs or one two-credit job. Request
the entire configured budget for an exclusive run, such as a performance
measurement. Credits are admission units, not enforced memory or compute
allocations; choose weights based on the workload.

A `cpu` resource with one credit per logical core (at most 64) lets agents
share compilation and test parallelism. Configure it once, then request no
more credits than the budget:

```sh
cores=$(python3 -c 'import os; print(min(os.cpu_count() or 1, 64))')
agent-resource capacity --resource cpu --credits "$cores"
agent-resource run --resource cpu:4 --wait 3600 -- cmake --build build -j4
```

Agree locally which work takes CPU credits. A suggested default: a process
expected to use more than roughly 30% of the machine's CPU for an extended
period, or more than about 90% even briefly, such as parallel builds,
`ctest -jN`, sanitizer suites, benchmarks and heavy model work. Request credits
matching its parallelism. The child receives
`AGENT_RESOURCE_<NAME>_CREDITS` for each resource; names are upper-cased with
non-alphanumeric characters replaced by `_`. A wrapper script can use
`-j"$AGENT_RESOURCE_CPU_CREDITS"` for its granted parallelism. Never wrap quick
or single-threaded commands such as cat, git, grep, editors, reviews or docs
checks; they must not wait on admission.

On macOS, resource admission requires macOS 10.12+ for the shared uptime
clock, with Python 3.9+ unchanged. Ticket order, ages and priority deadlines
use the system-wide uptime clock even on Python 3.9. Older process-relative
priority timestamps have unknown mixed-version timing; their flocks still
preserve exclusion and are never overridden.

An exclusive timing measurement requests the whole budget (`cpu:<all>`).
A waiting request holds no partial credits for that resource. New callers
register private live tickets under the admission turnstile. Registration
orders them and starts each resource's backfill age. The oldest live ticket
wins immediately when its whole weight fits. Otherwise younger fitting jobs
may backfill until that head's original registration plus 30 seconds (adjust
with `--backfill SECONDS`), then new admissions stop until the head fits.
An already-aged successor receives no fresh window. Ticket flocks prove
liveness; timeout, interruption or death cannot leave a ticket holding a turn.
Active jobs continue until they finish; draining does not interrupt them or
reserve idle slots. Backfill jobs may run longer than the arrival window.
This bounds repeated overtaking opportunities among participating callers,
not admission time or strict admission FIFO. A small job may wait behind a
large head even with free credits. Running jobs retain their requested weight,
including serial phases. Do not nest `agent-resource run` for the same
resource: the inner call waits on its parent's credits until `--wait` expires.
Still confirm machine quiet after admission: non-cooperating processes are
not governed. If timing work cannot start while unrelated load remains busy,
release admission and reconsider or retry later within its decision/evidence
budget. Choose `--wait` from the expected queue duration; the
600-second default suits short steps, not long build queues. Give optional
measurements a decision/evidence budget: when a wait expires, reconsider
whether the result still changes a decision before retrying with more time.

Capacity changes wait until no cooperating job holds or is waiting for that
resource, then replace its budget atomically. A wait timeout leaves the old
budget intact. Existing callers pick up the budget without changing their
commands. Older tool versions still exclude all new callers through the
original gate lock, so mixed versions remain safe but old callers cannot share
capacity.
Requests above the budget fail immediately (exit 1). Request each resource
only once. Acquisition is ordered by name to avoid deadlocks. Each attempt
under a resource's short admission turnstile acquires its complete requested
weight or immediately releases every slot collected. Timeout, interrupt or
supervisor death releases the waiting priority token, so stale token contents
cannot reserve capacity. Earlier fully granted resources remain held while
acquiring a later resource; acquisition is atomic per resource, not across
the whole bundle. Use only resources the command needs. Mixed versions keep
their capacity and legacy gate safety, but old callers do not participate in
ticket ordering. Pre-backfill weighted versions can still retain partial
credits while holding the turnstile. This is cooperative
admission, not a capacity monitor or proof that the machine is quiet.
Existing project leases still apply; do not invent a competing authority.

Admission expires after `--wait` seconds (exit 75, command never started).
Execution defaults to a one-hour timeout, or 120 seconds with `--interactive`
(exit 124). The execution budget starts at the post-launch child wait, so
admission wait, spawn overhead, and cleanup are outside it. Normal command exit
codes pass through. Interrupts and timeouts terminate the foreground process
group, then kill remaining members after a short grace period. Descendants
must not detach: the wrapper also cleans up the group when its leader exits.

The controller observes child completion without reaping it until the last
group signal has been sent. The unreaped child reserves the numeric identity;
a lost or already-reaped child does not authorize another group signal. After
reaping, cleanup only observes group absence. Admission and the deferred
interrupt handlers remain in place until that absence is verified. An
observer error, failed diagnostic, or missing proof does not release credits
or trigger another numeric cleanup attempt. A nonzero workload result survives
later cleanup, history, handler-restoration, and stream-flush errors; a cleanup
failure after a successful workload makes the run fail.

Before a run creates resource state or spawns its child, it requires default
`SIGCHLD` handling and a single-threaded controller. Linux uses Python's
non-reaping `waitid` interface. macOS uses that interface when available or a
checked 64-bit Darwin ABI; an unavailable interface refuses the run. These
requirements do not change resource names, capacities, or admission order.

Lock descriptors are inherited by the command so supervisor death alone
cannot release its locks. Programs that close inherited descriptors or escape
the group can defeat this safeguard; do not use this wrapper for daemons or
as a security boundary. Admission remains held until the entire process group
disappears. If cleanup cannot be confirmed within five seconds, a warning is
attempted and the wrapper keeps waiting with its locks held. An unkillable
child or an unreaped descendant on a host that does not reap orphans can retain
admission beyond the command timeout. Neither a timeout nor a failed warning
is permission to release credits or signal an unowned numeric identity.

### Interactive runs

Use `--interactive` for short resource-intensive viewers, captures, and UI
checks. Build and prepare inputs before taking admission; keep the actual
workload in the admitted foreground process group. Do not use a launcher
that detaches the application or delegates it to a desktop service.

```sh
agent-resource run --resource gpu:1 --interactive -- ./viewer
agent-resource run --resource gpu:1 --interactive --timeout 300 \
  --interactive-reason 'planned multi-step capture' -- ./viewer
```

With `--interactive`, an omitted `--timeout` means 120 seconds; an explicit
finite positive value up to 120 is accepted. Above 120, supply an explicit
`--timeout` and nonblank `--interactive-reason`. The reason acknowledges the
longer planned run; it does not authorize work outside the task or a grant.
A reason without `--interactive`, without an explicit timeout above 120, or
containing only whitespace is invalid. Invalid combinations exit 2 before
creating resource state, recording history, taking admission, or starting a
child. Ordinary runs retain their 3600-second default and explicit overrides.
`--wait` remains a separate admission budget for both profiles.

Reason text is neither echoed nor journaled. It is still visible in process
arguments and potentially shell history, so never include private data.
The supervisor enforces the execution deadline even if the agent is blocked
in a UI call. Timeout initiates cleanup, not unconditional credit release:
cleanup must establish that the process group is gone. An unkillable member
can retain admission longer than the timeout. The existing supervisor-death
and escaped-process limitations still apply; this is no application sandbox.

For UI work, record the exact owned process identity from the admitted launch
and inspect current UI state. Use only a documented operation that cannot
launch or relaunch an application, targeting that existing owned process.
This guarantee must hold for every subsequent operation, including calls
that resume after the process exits. A presence check before a launch-capable
name/path selector is insufficient: the process may exit between the check
and the call. If the tool offers no guaranteed attach-only path, stop the UI
step and report the capability gap. Do not attach an unrelated application or
substitute another instance. The CLI cannot enforce this policy on UI tools.

Discard the UI handle once the managed process exits or times out. A needed
retry must take new admission with an explicit runtime bound; a stale handle
must not recreate the workload. End successful interactions promptly and
wait for wrapper cleanup and release. Do not hold credits across builds,
review, or idle time. A stalled UI call can outlive the managed workload;
process-group timeout evidence alone does not prove safe UI attachment.

### Local state and observations

Stable lock files live under `$XDG_STATE_HOME/agent-kit/resources`, defaulting
to `$HOME/.local/state/agent-kit/resources`. Budget files are named
`NAME.capacity`; `NAME.lock` is the compatibility and configuration gate,
`NAME.turnstile` serializes short admission attempts, `NAME.priority` holds
the waiting token deadline, `NAME.slots/` holds credit locks, and
`NAME.queue/` holds ephemeral private tickets. Only an unlocked ticket can be
pruned, under the admission turnstile; a status read never prunes tickets.
They contain no commands or task text. Never delete lock files to break a live
lock: that creates two independent locks.
Read queue health on demand:

```sh
agent-resource status --resource cpu --resource gpu
```

The JSON snapshot groups participating waiters by credits and shows their
current ages/order, oldest head weight and backfill/drain state, configured
capacity and reserved/free credits. Reserved credits are lock reservations,
not measured CPU/GPU utilization. Status creates or prunes no files and uses
a short bounded turnstile attempt. Before a configured resource has any
turnstile, exclusive ownership of its existing compatibility gate proves an
idle snapshot without creating state; busy, unsafe or unconfigured state is
unknown. The top-level policy identifies the observer and new participating
callers, not every live wrapper. Legacy queue/priority coverage remains
unknown, so an empty participating queue does not prove nobody is waiting.
`eta_seconds: null` means unknown: ongoing ages and historical completed waits
cannot predict remaining durations. Completed wait medians/tails are useful
historical context only, with sample counts and ongoing ages alongside them.

A CPU/GPU request prints one notice after 300 seconds of waiting (configure
with `--notice-after SECONDS`). It includes requested weights, elapsed wait,
compact queue counts/head/reserved/free state and the status command. The
threshold is an operator reminder, not a latency promise. Check available VMs,
alternative build machines or SteamOS devices for compatible portable work.
Verify access, capacity, lease, storage, cost and target/evidence requirements
before moving work. No work is rerouted automatically; target-specific
evidence stays on its required target.
Custom-resource-only requests and immediate admissions emit no host reminder.

`admission.jsonl` records request, admitted and released events with an opaque
request ID, timestamp, requested credits, wait seconds, held seconds and exit
code. Schema 2 adds the participating policy, request backfill budget,
per-resource wait/acquire/release stages with configured capacity and captured
shared uptime/monotonic times, and terminal reasons. Request events also include
`profile` (`standard` or `interactive`), resolved `timeout_seconds`, and
`extended_timeout` (true only for an interactive timeout above 120 seconds).
These are additive schema 2 fields; readers ignore unknown fields. No command,
reason text, application identity, path, screenshot, or UI contents are logged.
`admission_timeout` distinguishes a prestart exit 75 from `child_exit` with exit
75. Resource wait starts before its compatibility gate; acquired/released
stages expose earlier complete bundle
grants. Capture precedes logging, outside the admission turnstile. Captured stage rows
and their admitted/released event share one bounded journal-lock attempt, so
lock contention before child start does not add a wait per resource. Whole-bundle
held duration starts once every resource is admitted and ends after group
cleanup and lock release. Waiting failures have no admitted event and zero
whole-bundle held seconds, even if an earlier resource was temporarily held.
Events are best effort: write failure prints a warning but does not change
admission, and abrupt supervisor death may leave unmatched history. Request
telemetry runs before the `--wait` budget starts; recorded wait includes that
attempt. Short writes are completed where possible; incomplete trailing records
are discarded before the next append. No command arguments, working
directories, agent IDs, task text or reminder payloads are recorded. The
private mode-0600 file is capped at 1 MiB, retaining only whole recent records
and leaving space for the next row. History is diagnostic, not an ownership,
forecast or acceptance authority. There is no daemon or utilization sampler.

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
