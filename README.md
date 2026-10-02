<picture>
  <source media="(prefers-color-scheme: dark)"
    srcset="docs/assets/brand/agent-kit-logo-dark.svg">
  <source media="(prefers-color-scheme: light)"
    srcset="docs/assets/brand/agent-kit-logo-light.svg">
  <img src="docs/assets/brand/agent-kit-logo-light.svg"
    alt="agent-kit — a friendly toolbox" width="340">
</picture>

# agent-kit

**A practical toolkit for working with coding agents.**

Follow Claude Code and Codex sessions, monitor quota, and coordinate shared
work. Give your agents reusable skills for profiling, planning, testing,
review, and handoffs. Start with the pieces you need.

[Quick start](#quick-start) · [Profiling](#investigate-performance-with-evidence) ·
[Activity and quota](#follow-activity-and-quota) ·
[Shared tasks](#coordinate-work) · [Skills](#build-your-own-toolkit) ·
[Install](#install) · [Privacy](#privacy-and-limits)

Want just the performance guidance? The [profiling skill works on its own](
#profiling-only-installation), without the dashboard or task records.

![Dashboard with a brewing parent and two helpers, shared tea-shop tasks,
  and quota timing](docs/assets/dashboard.png)

*Building Moss & Mugs, a fictional woodland tea-shop game. Agent activity,
shared tasks, and quota timing shown with synthetic demo data.*

## Quick start

The commands need a POSIX shell and **Python 3.9+**. Live quota also needs
an authenticated Claude Code or Codex CLI; install only the provider you use.
Quota commands contact provider services and maintain a local cache.
No account is needed to read help or run the [isolated demo](docs/demo/README.md).

Clone the public repository, then try a command directly:

```sh
git clone git@github.com:kindjie/agent-kit.git
cd agent-kit
./bin/agent-quota --help
```

The SSH example assumes GitHub SSH access. You can also
[download a ZIP](https://github.com/kindjie/agent-kit/archive/refs/heads/main.zip)
and work from the extracted directory.

With a provider CLI signed in, see quota, resets, credits, and recent pace:

```sh
./bin/agent-quota --brief
```

Missing or unavailable observations remain unknown. To follow local agent
sessions without generating model summaries:

```sh
./bin/agent-quota --agents --live --no-summaries
```

This reads local transcripts and can still query quota services.
`--no-summaries` suppresses model calls; it does not mean offline.

For the combined view, add **tmux** and run:

```sh
./bin/agent-dash
```

It opens Agents, Agent Tasks, and Timeline. Without configured records,
the task pane explains setup; launching never initializes records for you.
The dashboard defaults to no model summaries. Add `--summaries` only when
comfortable with the [summary data flow](docs/privacy.md).
[Taskglance](https://github.com/kindjie/taskglance) is optional:
`./bin/agent-dash --personal` adds your own task list.

**Just the skills?** Skip these command dependencies and
[install one skill](#profiling-only-installation), or pick from the
[catalogue](docs/tools.md#skills).

## Investigate performance with evidence

The [profiling skill](skills/profiling/SKILL.md) helps your coding agent decide
what to measure before changing code. It connects symptoms to useful signals,
guides capture with platform tools, and separates what measurements show from
what still needs testing. Use it on its own; agent quota and task records are
separate tools.

| Investigating | What the guidance covers |
| --- | --- |
| CPU work | Apple Silicon/macOS and x86 Linux; computation versus waiting, allocations, code generation, memory behavior, and scaling |
| GPU work | Frame/pass timing, submission, synchronization, and GPU memory; Metal, RADV/SteamOS, and NVIDIA tooling |
| Browser and graphics layers | WebAssembly tiering, workers, JS↔Wasm boundaries, and backend-aware guidance for sokol, raylib, OpenGL, and browser graphics |
| Parallel scaling | Work partitioning, contention, locality, NUMA, controlled workloads, and interpretation limits |

These are reference workflows, not a bundled profiler. Tools, target hardware,
and capture permissions must be checked on the host. Some steps can be run
and analyzed by an agent; others produce a capture for you to inspect or need
an interactive profiler. Unavailable hardware is a limit to report.

Example requests, illustrating how to start an investigation:

```text
Use the profiling skill to investigate uneven frame pacing in Moss & Mugs
when the tea shop is full. Establish a reproducible workload, check the
available tools, and choose evidence that distinguishes CPU work, GPU work,
and waiting. Do not optimize from source inspection alone.
```

```text
Our simulation stops getting faster as we add workers. Use the profiling
skill to distinguish work partitioning, contention, and memory/topology
limits. Explain what the available measurements can and cannot establish.
```

Start with [CPU diagnosis](skills/profiling/references/diagnosis.md) or
[GPU diagnosis](skills/profiling/references/gpu-diagnosis.md), then the
[interpretation guide](skills/profiling/references/interpretation.md).
[WebAssembly](skills/profiling/references/wasm.md) and
[NUMA](skills/profiling/references/numa.md) cover those specific investigations.
Moss & Mugs is a demonstration scenario; no performance result is claimed.

### Profiling-only installation

From the checkout, link the **whole directory** into one agent's skill folder.
Its `references/` must stay with it. This needs neither tmux nor records,
and installing guidance does not install profiling tools.

For Codex:

```sh
mkdir -p "$HOME/.agents/skills"
destination="$HOME/.agents/skills/profiling"
[ ! -e "$destination" ] && [ ! -L "$destination" ] && \
  ln -s "$PWD/skills/profiling" "$destination"
```

For Claude Code, use `$HOME/.claude/skills` for the destination instead.
These commands refuse an existing entry; inspect it before deciding whether
to replace it. Keep the checkout where the link points.

Ask your agent to **use the profiling skill**. Skill selection depends on the
agent and request; installation does not guarantee automatic invocation.
The supported local discovery paths and symlink behavior are documented by
[Codex](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)
and [Claude Code](https://code.claude.com/docs/en/skills#choose-where-skills-load).

## Follow activity and quota

The agent tree groups parent sessions and delegated agents. Collapse groups
to see the overview, or open details to inspect the observed state and waits.
It distinguishes ended turns, reasoning, tool calls, and explicit waits while
keeping observation freshness visible. See the
[activity close-up](docs/activity.md) and
[quota/activity contract](bin/agent-quota.md).

```sh
./bin/agent-quota --agents --live --no-summaries
./bin/agent-quota --timeline --live
```

![Quota timeline showing an upcoming reset and projected run-out before
  reset](docs/assets/quota-timeline.png)

*Upcoming resets and projected run-out times. Synthetic data; projections
follow recent usage, not a guaranteed schedule.*

Subscription quota and purchased credits are reported separately. Forecasts
need adequate recent observations; missing evidence stays unknown.
For attention handoffs, `agent-speak.sh` can ring or speak and publish a tmux
badge. Agents explicitly report and clear these reasons; silence is not a
stall detector. [Attention and status setup](docs/install.md#requirements).

## Coordinate work

<a id="agent-records"></a>
<a id="live-task-view"></a>

`agent-task` is the shared agent work queue. Track ownership, leases, helpers,
dependencies, per-model estimates, and completion evidence across sessions.
`agent-changelog` records lasting machine state and recovery information.
Both use dedicated Git repositories you choose and initialize explicitly.

After [records setup](docs/records.md#agent-records):

```sh
./bin/agent-task list --live
```

![Shared tasks with Save the shop overnight selected and its recorded
  completion evidence open](docs/assets/task-details.png)

*Inspect ownership, dependencies, and recorded completion evidence. Synthetic
demo records; the view is read-only.*

Enter opens details; Tab switches progress, dependencies, evidence, and
observed waits. `f` expands details, and `?` opens grouped, scrollable help.
Dependency readiness uses real task status: only `done` satisfies a
prerequisite. Recorded evidence is not a fresh CI check, and estimates are
not completion forecasts.

Keep your own decision list separate: [taskglance](
https://github.com/kindjie/taskglance) holds work needing you. The dashboard's
optional My Tasks pane does not replace the agents' shared queue.
[Records reference](docs/records.md) covers lifecycle, estimates, messages,
recovery, and the read-only boundary.

## Build your own toolkit

Skills are small sets of working instructions and references. Adopt them
individually; neither agent product nor the entire command suite is required.
They supply defaults and respect your instructions.

| When you want to… | Start with |
| --- | --- |
| Choose a sound approach | [approach-review](skills/approach-review/SKILL.md), [design-documents](skills/design-documents/SKILL.md) |
| Plan and share work | [estimate-agent-work](skills/estimate-agent-work/SKILL.md), [delegation](skills/delegation/SKILL.md), [agent-records](skills/agent-records/SKILL.md) |
| Validate and deliver a change | [testing-requirements](skills/testing-requirements/SKILL.md), [commit](skills/commit/SKILL.md), [pull-requests](skills/pull-requests/SKILL.md) |
| Judge evidence and preserve decisions | [verification-systems](skills/verification-systems/SKILL.md), [delivery-evidence](skills/delivery-evidence/SKILL.md), [repository-records](skills/repository-records/SKILL.md) |
| Preview Markdown locally | [preview-markdown](skills/preview-markdown/SKILL.md); its command needs `uv` |

For larger workflows, [coordination tools](docs/coordination.md) include
multi-task event waits, cooperative resource admission, explicit scheduler
grants, and offline usage reports. The scheduler is an opt-in pilot with
manual recovery boundaries, not an unattended scheduling service.

## Install

Link only the commands and skill directories you want. Keep required sibling
modules together; use the [installation guide](docs/install.md) for commands,
optional rules, status lines, attention badges, and records hooks.
Always-loaded rules are a separate opt-in: read them before installing them.
No setup command in this introduction rewrites your global agent settings.

## Privacy and limits

<a id="what-it-reads-and-what-leaves-the-machine"></a>

Activity scanning reads local transcripts; the scan itself uploads nothing.
Quota reporting contacts provider services. Model-generated thread summaries
send bounded excerpts to a provider and may use the other provider by default.
`--no-summaries` disables those model calls; `--cached` skips new transcript
scans. The dashboard's default is no summaries. Review the
[privacy reference](docs/privacy.md) before enabling additional data flow.

Observed activity is not proven productivity. Claim expiry does not prove
work stopped, a recorded check does not re-run CI, and quota forecasts are
not guarantees. The screenshots use only isolated synthetic records and
accounts; they contain no private projects or measured profiling results.

## Documentation

<a id="what-is-in-it"></a>
<a id="requirements"></a>
<a id="always-loaded-rules"></a>
<a id="records-hooks"></a>
<a id="status-lines"></a>
<a id="coordination-and-efficiency"></a>

- [Command and skill catalogue](docs/tools.md)
- [Installation, rules, hooks, and status lines](docs/install.md)
- [Shared task records and live controls](docs/records.md)
- [Coordination and efficiency](docs/coordination.md)
- [Quota schema, activity, and summary contract](bin/agent-quota.md)
- [Synthetic demo and capture reproduction](docs/demo/README.md)

## Tests and contributions

<a id="tests"></a>

Focused fixes, documentation, and tests are welcome. Discuss substantial
behavior changes before building them. Follow the
[focused test instructions](tests/AGENTS.md); the full suite is:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t .
```

## Licence

[MIT](LICENSE).
