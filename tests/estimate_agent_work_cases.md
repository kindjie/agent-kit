# Estimation behavior checks

Run these synthetic cases against `estimate-agent-work` with no task writes.
For routing cases, first show only its name and description. Then provide
the body for applicable cases. Judge decisions, not exact words or points;
several adjacent sizes can be reasonable. These checks require an agent,
not a keyword-matching test.

| Request | Expected behavior |
| --- | --- |
| Create a tracked task to correct one help example; verify the example runs. | Invoke; choose a small size, keep one task, no separate report. |
| Estimate a bug fix: a cancelled import leaves a temporary file; the cause is known and a regression test is needed. | Invoke; include the test in the same estimate and task. |
| Estimate replacing a renderer backend; compatibility with required devices is unknown. | Invoke; avoid pretending implementation is bounded. Propose investigation with a question, evidence and stopping condition. |
| Size an experiment comparing two particle update strategies; either could be slower. | Invoke; size producing reproducible measurements, not achieving a speedup. |
| Refine work integrating a mocap importer and visual preview. The file contract is known; exporter and importer edits overlap. | Invoke; include integration and visual acceptance, without assuming parallel edits are safe. |
| Estimate a coherent 13-point change with one shared interface and one end-to-end acceptance test. | Invoke; consider splitting, but do not manufacture children to satisfy a threshold. |
| Split a format migration: consumers need the producer's finalized version contract. | Invoke; each executable piece has observable acceptance; declare only the actual prerequisite and account for integration. |
| Suggest a size for this backlog item; do not change it. | Invoke; return advice without writing a record. |
| A task estimated at 3 took longer because the build queue stalled; its scope is unchanged. | No automatic re-estimation or new tickets to match elapsed time. |
| Summarize status of an unchanged task already estimated at 5. | Do not invoke. |
| Continue implementing an unchanged task already estimated at 5. | Do not invoke. |
| Before starting this tracked task, it has no estimate. | Invoke; estimate proportionally, without a backlog-wide pass. |
| Compare token usage by model for two completed runs. | Do not invoke merely for resource analysis; do not convert tokens to points. |

Review also checks that examples contain no private calibration and that
task writes, when explicitly requested, follow existing records permissions.
