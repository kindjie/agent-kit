# Short interactive resource runs

Status: implemented and independently reviewed; historical design intent.
The [interactive-run contract](coordination.md#interactive-runs) and CLI help
own current behavior. Local activation and process tests establish the CLI
contract; external UI tools must separately satisfy the attachment policy.

## Problem and boundary

A foreground viewer or capture may keep resource credits while its agent is
blocked in a UI call. The ordinary one-hour command timeout is too long for
these short interactions. Separately, selecting an application through a UI
API can launch another process outside the admitted command.

`agent-resource` already supervises a new process group independently of the
agent turn. Its timeout triggers group cleanup; inherited lock descriptors
and cleanup-before-release protect cooperating jobs. `--wait` bounds admission
only. Reuse this supervisor instead of adding another watchdog.

The intended result is a short default deadline for explicitly interactive
runs, deliberate acknowledgement of longer work, and no implicit UI relaunch.
This is cooperative process-group supervision, not an application sandbox or
an unconditional upper bound on credit occupancy. No change to resource
weights, capacity, admission ordering, backfill, or existing project leases
is proposed. Ordinary builds and benchmarks retain their existing behavior.

## Command contract

Add two options to `agent-resource run`:

```sh
agent-resource run --resource gpu:1 --interactive -- ./viewer
agent-resource run --resource gpu:1 --interactive --timeout 300 \
  --interactive-reason 'planned multi-step capture' -- ./viewer
```

- `--interactive` selects a 120-second execution timeout when `--timeout` is
  omitted. An explicit finite positive timeout up to 120 seconds is allowed.
- A timeout above 120 seconds requires both an explicit `--timeout` and a
  nonblank `--interactive-reason`. The reason is a deliberate acknowledgement,
  not authorization for work outside the user's task or existing grants.
- Reject the reason without `--interactive`, without an explicit timeout, or
  when the timeout is at most 120 seconds. Reject a whitespace-only reason.
  Do not echo or persist reason text; it can still be visible in process
  arguments or the invoking shell's history, so never include private data.
- Without `--interactive`, omitted timeout remains 3600 seconds and explicit
  timeouts retain their current behavior. Admission `--wait` remains separate
  and unchanged; waiting does not consume the execution budget.
- Reject invalid option combinations with exit 2 before creating state,
  registering a ticket, acquiring credits, recording history, or launching a
  child. Do not infer an interactive profile from an executable name.

Resolve the effective timeout after argument validation and before admission.
The existing child wait enforces it without another agent message, UI result,
heartbeat, or renewal. Timeout starts at the existing post-launch child wait;
spawn overhead and cleanup are outside that budget. Timeout returns 124,
interruption and normal child exit retain their existing statuses.

On timeout, cancellation, or leader exit, terminate the process group, escalate
as needed, and confirm the group is gone before closing admission handles.
Keep the existing warning and retained admission if cleanup cannot establish
quiescence. Never turn deadline expiry into unconditional release. An
unkillable member can therefore exceed 120 seconds. Supervisor death and
processes that detach or close inherited descriptors retain the documented
limitations; this profile adds no guarantee against those cases.

## UI attachment policy

Put the agent-facing policy in the resource section of the
[delegation skill](../skills/delegation/SKILL.md), with the operational detail
in the coordination reference and a short README example:

1. Build and prepare inputs before admission. For short resource-intensive
   viewers, captures, and UI checks, use `--interactive` and keep the actual
   workload in the admitted foreground process group. Do not wrap a launcher
   that hands the application off to a desktop service or detaches it.
2. Record the exact owned process identity from the launch and inspect current
   UI state. Attach only with a documented operation that cannot launch or
   relaunch an application, targeting the already running owned process.
   Checking that an app exists before calling a launch-capable selector is
   insufficient: the app can exit between those steps.
3. If the UI API can auto-launch by name/path, or its no-launch behavior is
   unknown, do not call it for this workload. Use a supported attach-only
   path or stop the UI step and report the capability gap. Do not substitute
   another instance or attach an unrelated user's application.
4. Once the managed process exits or times out, discard its UI handle. Any
   needed retry is a new explicitly bounded admitted run; a stale handle or
   application selector must never recreate the workload outside admission.
5. End a successful interaction promptly and let the wrapper finish cleanup
   and release. Do not hold credits across builds, review, or idle time.

The CLI cannot enforce the UI policy against external tools. UI attachment
must independently satisfy it; a passing process test is not proof of safe UI
behavior. A blocked UI tool may outlive the workload deadline, but the managed
workload still receives timeout cleanup while the supervisor is alive.

## Minimal observability

Reuse the existing best-effort private admission journal. Add only the
resolved profile (`interactive` or `standard`), `timeout_seconds`, and an
`extended_timeout` boolean to run request events. Existing request IDs and
terminal reasons correlate timeout with release and distinguish it from an
admission timeout. Keep schema 2 with additive fields; old readers must ignore
unknown fields. No command, reason, application identity, path, screenshot,
or UI contents belong in telemetry. History failure must not affect admission
or cleanup. No new service, heartbeat, state file, or status mode is needed.

## Alternatives and verification

A global 120-second default would break ordinary long jobs. Requiring every
caller to remember `--timeout 120` leaves the same omission easy to repeat.
A second watchdog or a lease that releases on elapsed time would add ownership
races or release credits before cleanup. Automatic application discovery and
termination would widen process authority without reliable ownership proof.
The additive profile plus attach-only policy is the smallest bounded change.

Add tests before implementation, using temporary private state and harmless
foreground processes only:

- Verify 120/3600 defaults and explicit shorter/longer values without waiting
  for long deadlines. Assert the effective value passed to child wait, then
  independently exercise a short explicit interactive timeout in a subprocess.
- Cover every invalid combination, nonfinite/nonpositive timeouts, and blank
  reasons. Assert exit 2, no child marker, and no state directory or mutations
  to a pre-existing state's byte snapshot.
- With a stalled controller providing no further interaction, verify timeout
  kills a real managed child and a TERM-resistant in-group descendant. Use an
  independent contender and process-liveness checks to prove credits become
  usable only after cleanup. Preserve cancellation and leader-exit coverage.
- Verify long override acceptance with an immediately exiting child, normal
  exit propagation, admission-timeout distinction, and payload-free journal
  fields. Include a distinctive reason sentinel and prove it is absent from
  stdout, stderr, and the journal.
- Run the existing resource suite for weighted admission, backfill, supervisor
  death, cleanup, and telemetry regressions; run the public privacy suite.
  Update test instructions and check documentation links and whitespace.

Review the UI policy against the selected tool's actual documented semantics.
Do not launch a native UI or claim UI enforcement merely to test this CLI.

## Local rollout

After independent proposal review, change the installed source checkout's CLI,
focused tests, and linked delegation policy. Verify the runtime command and
skill links resolve to those exact tested files; use temporary state for the
installed-command smoke. Run no installer unless link structure changes.
Do not modify capacities, existing reservations, running jobs, or scheduling
configuration. Already running wrappers retain their original behavior; new
invocations read the changed source. Publication is a separate decision.

For rollback, restore only these owned edits after checking for concurrent
changes. No persisted state migration is required. Mark this proposal
implemented and historical only after independent implementation review and
local validation; report process-group evidence separately from UI capability.
