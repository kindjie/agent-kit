# Command and skill catalogue

[Back to the introduction](../README.md).

## What is in it

**Commands** (`bin/`)

| Command | Does |
| --- | --- |
| `agent-dash` | Opens activity, shared tasks, and quota timing in tmux; optional personal tasks |
| `agent-quota` | Reports Claude Code and Codex subscription quota, credits and pace as JSON, compact tables or a reset and run-out timeline |
| `agent-status` | Renders that report as a Claude Code status line or a tmux component |
| `claude-status.sh` | Claude Code `statusLine` entry point |
| `claude-ctx.sh` | tmux status entry point |
| `agent-speak.sh` | Spoken attention notification, with mute control |
| `agent-attention` | Explicit tmux attention badges and prompt reset hook |
| `md-preview` | Local GitHub-style Markdown preview served on loopback |
| `agent-id` | Derives or mints agent IDs and manages repository keys |
| `agent-task` | Creates, claims, hands off and closes task records |
| `agent-resource` | Serializes cooperating foreground jobs with scoped resource locks |
| `agent-scheduler` | Records explicit one-use grants and independent operational release receipts |
| `agent-release-check` | Checks supplied release evidence and shadow decisions without changing reservations |
| `agent-efficiency` | Reads local usage and coordination observations without provider/model calls |
| `agent-changelog` | Tracks persistent machine and repository state |
| `agent-kit-rules` | Installs or updates the always-loaded rules in agent instructions |
| `agent-records-hook` | Claude Code and Codex hook: records reminders and a session-start summary |

## Skills

| Skill | Use when |
| --- | --- |
| `approach-review` | Choosing an approach for work with real impact or irreversibility |
| `commit` | Reviewing, validating and staging a commit |
| `delegation` | Deciding whether to delegate, to which agent, at what effort |
| `delivery-evidence` | Deciding what CI evidence should gate delivery |
| `design-documents` | Deciding whether a design document is warranted |
| `preview-markdown` | Previewing and visually validating Markdown |
| `profiling` | Collecting or reading a performance profile, CPU, GPU or Wasm |
| `pull-requests` | Taking a change through a pull-request workflow |
| `agent-records` | Tracking work and lasting machine state with `agent-task` and `agent-changelog` |
| `estimate-agent-work` | Sizing tracked work and identifying useful decomposition |
| `repository-records` | Creating changelogs, decisions, ADRs or incident records |
| `testing-requirements` | Designing or changing a test strategy |
| `verification-systems` | Judging whether a green check means the work happened |
