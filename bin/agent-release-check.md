# Advisory release evidence check

`agent-release-check FILE` checks a supplied versioned JSON release packet.
It reads that file only. It never inspects processes, dereferences evidence
references, reads scheduler state, signals, launches, posts messages or changes
reservations. Existing manual grants remain under their coordinator's authority.
It does not import prose or turn manual grants into scheduler grants.

The output is `kind: agent-release-advisory`, with `advisory_only: true`,
`authority: coordinator`, `quiescence_verified: false` and `evidence_status`:

- `recorded-evidence-consistent`: supplied records satisfy this contract.
- `insufficient`: required records are absent, invalid or inconsistent.

A consistent packet is **not proof of actual quiescence or authorization to
release**. The coordinator must independently establish that the referenced
records are authentic, immutable and cover the agreed process scope. Distinct
observer labels and record references cannot prove observer independence.
Neither release nor this check accepts a workload result or authorizes a retry.
See [the scheduler reference](agent-scheduler.md) for those boundaries.

## Input contract, version 1

All fields in the example below are required except `coordinator`, which may
be omitted completely. Unknown fields, versions and statuses fail validation.
References and labels are opaque nonempty strings of at most 1,024 UTF-8 bytes,
without control characters. SHA-256 references are allowed; no reference format
is required or fetched. Use references to immutable records, not mutable files
whose contents can change underneath a request. Keep packets private.

- The exact `identity` includes the authority reference, grant reference, lane,
  run ID and worker. Every evidence record repeats it exactly. The references
  identify the existing manual authority and grant; no scheduler IDs are
  invented.
- `grant.scope` is `scoped-process-observation` or `trusted-foreground-group`.
  The former refers to an externally agreed observation scope; the latter refers
  to the scheduler's cooperative foreground-group contract. Both remain
  advisory.
  `scope_ref` identifies the immutable scope definition. The quiescence record
  must match both fields exactly.
- `execution.status` is `exited` with `exit_code` (integer 0–255), or `signaled`
  with `signal` (integer 1–255). Supply exactly the matching terminal field.
  A failed workload exit can have consistent operational release evidence.
- `intent.status` must be `ended`, an explicit end-of-run declaration.
- `quiescence` records a separate completed check, with integer `exit_code: 0`,
  `result: quiescent`, a distinct observer and a distinct execution/check record
  reference. A failed, running or unknown check is insufficient, even if the
  workload exited successfully. The referenced check must cover the complete
  agreed scope; PID disappearance alone is not that evidence.
- Timestamps are observed UTC `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`, not estimates.
  Grant ≤ terminal ≤ reaped ≤ check start ≤ check completion; explicit intent
  must be observed at or after reaping. Equality is allowed. Incorrect clocks
  can make otherwise authentic records insufficient.

The JSON file must be a regular UTF-8 file of at most 64 KiB. Duplicate keys,
nonfinite numbers, integers over 64 digits, invalid JSON and unreadable files
are rejected. Booleans are not substitutes for statuses, observations or numeric
exit codes. Diagnostics
contain fixed field names and reasons, never input paths, references or values.

## Synthetic example

```json
{
  "schema_version": 1,
  "kind": "agent-release-evidence",
  "identity": {
    "authority_ref": "authority-demo", "grant_ref": "grant-demo",
    "lane": "lane-demo", "run_id": "run-demo", "worker": "worker-demo"
  },
  "grant": {
    "scope": "scoped-process-observation", "scope_ref": "scope-demo",
    "granted_at": "2026-01-01T00:00:00Z", "evidence_ref": "grant-record"
  },
  "execution": {
    "identity": {
      "authority_ref": "authority-demo", "grant_ref": "grant-demo",
      "lane": "lane-demo", "run_id": "run-demo", "worker": "worker-demo"
    },
    "observer": "supervisor-demo", "status": "exited", "exit_code": 7,
    "terminal_at": "2026-01-01T00:00:01Z",
    "reaped_at": "2026-01-01T00:00:02Z",
    "evidence_ref": "execution-record"
  },
  "intent": {
    "identity": {
      "authority_ref": "authority-demo", "grant_ref": "grant-demo",
      "lane": "lane-demo", "run_id": "run-demo", "worker": "worker-demo"
    },
    "status": "ended", "observed_at": "2026-01-01T00:00:03Z",
    "evidence_ref": "intent-record"
  },
  "quiescence": {
    "identity": {
      "authority_ref": "authority-demo", "grant_ref": "grant-demo",
      "lane": "lane-demo", "run_id": "run-demo", "worker": "worker-demo"
    },
    "observer": "observer-demo",
    "scope": "scoped-process-observation", "scope_ref": "scope-demo",
    "status": "completed", "exit_code": 0, "result": "quiescent",
    "started_at": "2026-01-01T00:00:03Z",
    "completed_at": "2026-01-01T00:00:05Z",
    "evidence_ref": "quiescence-record"
  },
  "coordinator": {
    "identity": {
      "authority_ref": "authority-demo", "grant_ref": "grant-demo",
      "lane": "lane-demo", "run_id": "run-demo", "worker": "worker-demo"
    },
    "decision": "released", "decided_at": "2026-01-01T00:00:08Z",
    "evidence_ref": "decision-record"
  }
}
```

```sh
agent-release-check "$private_evidence"
```

## Read-only shadow comparison

Optional `coordinator` records an **actual** decision (`released` or `held`),
matching identity, observed decision time and immutable evidence reference.
Its time must be at or after the grant. Do not populate it with a proposed
decision. The checker cannot authenticate that this decision occurred.

`shadow.comparison` describes the supplied records without judging other policy:
`held-with-consistent-evidence`, `held-with-insufficient-evidence`,
`released-with-consistent-evidence`, `released-before-evidence` or
`released-with-insufficient-evidence`. A hold can be justified by other policies
even with consistent evidence. These labels are not release recommendations.
Malformed coordinator metadata produces `shadow: null` and insufficient status.

For consistent evidence, `evidence_ready_by_decision` compares the decision time
with the latest of reaping, explicit intent and check completion. It is null
when evidence is insufficient. `release_delay_seconds` is populated only for a
supplied release at or after that threshold: the example yields 3 seconds.
It is a wall-clock difference, not proven avoidable delay or process safety.
The output never fabricates scheduler lifecycle receipts or a `released_at`
event.

Exit 0 means recorded evidence is consistent, including an early shadow release
or a hold; inspect the shadow label separately. Exit 1 means insufficient
contract evidence. Exit 2 means invalid/unreadable input or incorrect
invocation.
No exit status establishes lane availability.
