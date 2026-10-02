# agent-kit

Skills and command-line tools for coding agents, shared between
[Claude Code](https://claude.com/claude-code) and
[Codex](https://developers.openai.com/codex/cli/). Both discover a skill from
its YAML `name` and `description` and load it when the work matches, so the
same directory serves either.

Extracted from a personal dotfiles repository, where these are used daily.
Nothing here assumes that repository.

## What is in it

**Commands** (`bin/`)

| Command | Does |
| --- | --- |
| `agent-quota` | Reports Claude Code and Codex subscription quota, credits and pace as JSON, compact tables or a reset and run-out timeline |
| `agent-status` | Renders that report as a Claude Code status line or a tmux component |
| `claude-status.sh` | Claude Code `statusLine` entry point |
| `claude-ctx.sh` | tmux status entry point |
| `agent-speak.sh` | Spoken attention notification, with mute control |
| `md-preview` | Local GitHub-style Markdown preview served on loopback |
| `agent-id` | Derives or mints agent IDs and manages repository keys |
| `agent-task` | Creates, claims, hands off and closes task records |
| `agent-changelog` | Tracks persistent machine and repository state |
| `agent-kit-rules` | Installs or updates the always-loaded rules in agent instructions |
| `agent-records-hook` | Claude Code and Codex hook: records reminders and a session-start summary |

**Skills** (`skills/`)

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

## Requirements

- A POSIX shell. Developed on macOS; the shell scripts and Python target
  Linux equally, and platform-specific behaviour is gated rather than
  assumed.
- **Python 3.9+** for `agent-quota`, `agent-status` and their modules. No
  third-party packages.
- **[uv](https://docs.astral.sh/uv)** for `md-preview`, which is a PEP 723
  script: uv resolves its pinned dependencies per invocation. Without it the
  wrapper says so and exits 127.
- `git` — required for the records commands; optional for other tools.
- An authenticated `claude` or `codex` CLI for `agent-quota` to report on.
  It reports what it can observe and leaves the rest unknown.
- Speech is optional: `say` on macOS, `spd-say` or `espeak-ng` on Linux.
  Without one, `agent-speak.sh` falls back to a terminal bell.

## Install

Clone anywhere, then link the pieces you want.

```sh
git clone git@github.com:kindjie/agent-kit.git ~/src/agent-kit
cd ~/src/agent-kit
```

**Commands.** Link the whole `bin/` directory, not individual files.
`claude-ctx.sh`, `claude-status.sh` and `md-preview` locate their siblings
through the path they were invoked by, so a partial link fails at runtime
rather than at install time. The records commands also work when linked
individually because they resolve their Python modules through real paths.

```sh
mkdir -p ~/bin/agent-kit
find bin -maxdepth 1 -type f -exec ln -sf "$PWD"/{} ~/bin/agent-kit/ \;
```

Then add it to `PATH` in your shell profile:

```sh
export PATH="$HOME/bin/agent-kit:$PATH"
```

**Skills.** Link whole skill directories, so their `references/` travel with
them. Claude Code reads `~/.claude/skills`; Codex reads `~/.agents/skills`.

```sh
mkdir -p ~/.claude/skills ~/.agents/skills
for skill in "$PWD"/skills/*/; do
  ln -sfn "$skill" ~/.claude/skills/
  ln -sfn "$skill" ~/.agents/skills/
done
```

**Check it worked.** This needs no agent CLI and touches nothing:

```sh
agent-quota --help
md-preview README.md --no-open
```

## Always-loaded rules

Several skills defer to rules that bind regardless of which skill is
running: what blocks a commit, when to re-read review comments, what
reasoning effort to default to. Each skill states a usable default, so it
works with nothing installed. To make those rules bind everywhere rather
than only when a skill triggers, add them to your agent instructions with
`agent-kit-rules`:

```sh
agent-kit-rules --dry-run   # show what would change
agent-kit-rules             # ~/.claude/CLAUDE.md and ~/.codex/AGENTS.md
agent-kit-rules path/to/AGENTS.md   # or name the files
```

It writes [`AGENTS.snippet.md`](AGENTS.snippet.md) into each file between
`BEGIN agent-kit` and `END agent-kit` markers, appending it when absent.
Read it first — it is short, and it asserts opinions about testing and
pushing that you may not share. Your own rules take precedence wherever
they conflict; the skills say so. By default it updates only the files of
the agents present (`~/.claude`, `~/.codex`), follows symlinks to the real
file, keeps its permissions, and replaces it atomically.

The task and changelog rules are included only when `agent-task doctor`
and `agent-changelog doctor` both pass at install time (see
[Agent records](#agent-records)); agents then read whether the tools apply
rather than each running the checks. The doctors may open a git signing
prompt. Output says why the section was left out, and `--records on|off`
overrides the check. Re-run `agent-kit-rules` after configuring the
records tools or updating agent-kit.

Re-running is safe, and the snippet may be moved anywhere in the file: it is
replaced where it stands. The BEGIN marker records a hash of the installed
text, so a snippet you have edited is refused and left as it is (`--force`
replaces it; `--dry-run` shows the difference). Snippets appended by the
earlier README loop are recognised as unedited. Markers count only at the
start of a line and outside fenced code, so documentation that shows them is
left alone. Duplicate, missing or out-of-order markers are refused; fix them
by hand. So are files that are not UTF-8, symlink loops and dangling links,
and a file that changes while it is being updated. Line endings are kept,
CRLF included. The exit status is 1 when any file was refused.

## Records hooks

Always-loaded rules say when to record work and state; a hook makes the
moment hard to miss. `agent-records-hook` adds a one-line reminder after a
shell command that creates lasting state (a worktree, stash, new branch,
tool install or service), and at session start tells the agent its records
ID, the tasks it holds, open tasks and open changelog entries for the
repository. It only adds context, never blocks, and stays silent when the
records tools are not configured. Claude Code, in `~/.claude/settings.json`:

```json
"hooks": {
  "PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command",
    "command": "$HOME/bin/agent-kit/agent-records-hook post-tool"}]}],
  "SessionStart": [{"hooks": [{"type": "command",
    "command": "$HOME/bin/agent-kit/agent-records-hook session-start",
    "timeout": 20}]}]
}
```

Codex uses the same schema in `~/.codex/hooks.json`, with
`--provider codex` added to both commands so the agent ID matches
`agent-id show`.

## Status lines

**Claude Code** — in `~/.claude/settings.json`:

```json
"statusLine": {
  "type": "command",
  "command": "$HOME/bin/agent-kit/claude-status.sh",
  "refreshInterval": 2
}
```

**tmux** — in `tmux.conf`, using an explicit path. A `PATH` change does not
reach a tmux server that is already running, so a lookup by name leaves the
component blank until the server restarts:

```tmux
set -g status-right "#($HOME/bin/agent-kit/claude-ctx.sh #{window_id} #{client_width})"
```

## Agent records

Tasks and changelog entries live in separate, dedicated git repositories.
Configure their roots with `AGENT_TASKS_DIR` and `AGENT_CHANGELOG_DIR`, or
put a `records.json` in `${XDG_CONFIG_HOME:-~/.config}/agent-kit/`:

```json
{
  "tasks_dir": "/path/to/tasks",
  "changelog_dir": "/path/to/changelog",
  "machine": "example-machine",
  "push": false,
  "estimate_policy": "off",
  "repos": {"/path/to/checkout": "example-repo"}
}
```

`--dir` overrides each directory's configured path. `AGENT_MACHINE` overrides
the machine setting. Without either, the commands use `hostname -s`, which
can change with network configuration; pin a stable machine name. There are
no built-in records directories. Initialize each root once:

```sh
agent-task init /path/to/tasks
agent-changelog init /path/to/changelog
agent-task doctor
agent-changelog doctor
```

`init --adopt` accepts an existing records-only git repository. Other tracked
paths need a repeated `--allow PATH`; the marker records these exceptions.
Writers need both roots writable in an agent sandbox, including Git metadata,
lock and journal files. Readers open existing lock files read-only. `init`
creates them; run `doctor` after cloning records to provision missing locks.
`doctor` probes writability and tests git signing in a
temporary repository. It may open a signing prompt. The commands respect
the repository's signing settings and hooks; an executable commit or push
hook makes mutations refuse because it could change unjournaled files.

Task files are `T-0001-slug.md`, moving unchanged in name to `archive/` on
close. Their `key: value` header holds status, owner, claim expiry, helpers,
priority, severity, repository keys, review, links and related task IDs.
`## Known`, `## Plan`, `## Done when` and `## Log` follow. The four fixed
completion checks are merged, cleanup, docs and work-reviewed. A completed
task needs evidence for every applicable check. For work without repository
changes, create with `--no-changes` and mark inapplicable checks with
`check --na REASON`. `check` also takes an optional `--reason` to log;
checking an item again rewrites its evidence, and the owner can `uncheck
--reason` one ticked in error. The review field is separate from the
work-reviewed check. `agent-task --help` summarizes transitions; each
subcommand's `--help` describes its options.

Declare a prerequisite with `dependency add TASK PREREQUISITE`, or repeat
`new --depends-on PREREQUISITE` when creating a task. Revoke it with
`dependency remove TASK PREREQUISITE`. Dependencies are directional:
TASK depends on PREREQUISITE. Missing tasks, self-dependencies and cycles
are refused. These commands update only TASK and log the change; `--reason`
adds context. The owner may edit dependencies, or any agent when no live
claim exists. Helpers need the owner to make the change.

`agent-task dependency show TASK` lists its direct prerequisites and
dependents with their current status and title, including archived tasks.
Use `--json` for full task fields in `prerequisites` and `dependents` arrays.
`next` skips tasks until every declared prerequisite is `done`. Cancelled,
abandoned and superseded prerequisites remain unsatisfied; revoke or replace
those dependencies explicitly. Reopening a prerequisite makes it unsatisfied
again. Dependencies do not change task status or prevent explicit claims or
closure, so preparatory work can still proceed.
An invalid or missing prerequisite makes its candidate unavailable, with a
warning on stderr; other candidates remain eligible. A relationship view
with unreadable or malformed unrelated records is marked `UNVERIFIED`
(`authoritative: false` in JSON) because its dependent list may be incomplete.
Run `lint` to diagnose graph errors after reconciling records from other clones.

### Live task view

```sh
agent-task list --live
agent-task list --live --here
agent-task list --live --repo example --interval 1
```

The read-only terminal view refreshes verified local records every two seconds
by default (minimum interval 0.1 seconds). It includes live tasks and tasks
closed in the last hour; `--all` includes older closed tasks and `--archived`
shows closed tasks only. Existing status, owner and repository filters apply.
Without a usable input/output terminal it prints one plain snapshot and exits.
Interactive mode uses Python's standard-library curses on macOS and Linux.

| Keys | Action |
| --- | --- |
| `j` / `k`, Down / Up | Select next / previous task |
| `gg` / `G` | Select first / last task |
| Ctrl-d / Ctrl-u | Move half a page |
| `/` | Filter IDs, titles, repositories, owners and states without case |
| Enter | Keep the filter, or expand/collapse task details |
| Tab | Show progress, dependencies, evidence or observed waits |
| `[` / `]`, Ctrl-k / Ctrl-j | Scroll detail lines up / down |
| `f` | Toggle full-screen / split details |
| Mouse wheel / click | Scroll list or details / select a task row |
| `?` | Help; `?` or Esc closes it |
| Esc | Clear an edited filter, cancel a key sequence, or close details |
| `q` / `ZZ`, Ctrl-C | Quit |

The Observed waits tab reads the existing local agent cache and shows wait
conditions and observation age. Coverage is incomplete by nature; missing
observations never mean zero watchers. Use `--agent-cache-file PATH` with
`--live` to select another cache. No collector or session scan runs here.

For collapsible parent/subagent groups, use `agent-quota --agents --live`.
The live default is 100 parent groups with their observed subagents; groups
start collapsed. Rows lead with recorded session titles when available;
IDs, activity and descendant summaries occupy a separate column.
Click selects an agent row; click its fold marker to toggle its group.
Wheel over the list moves selection; wheel over details scrolls those details.
Space toggles a group, Left/Right navigate the hierarchy, and Enter shows
observation details. Rows distinguish ended turns, explicit waits, reasoning
and tool calls, with descendant activity visible even when collapsed.
See [agent-quota](bin/agent-quota.md) for the evidence and freshness limits.

Filter editing supports UTF-8, arrows, Home/End, Ctrl-a/e, Backspace,
Ctrl-w to remove a word and Ctrl-u to clear. Selection follows the task ID
across updates; selected titles wrap, with an ellipsis when space runs out.
Scrolling reserves the whole selected block, including wrapped titles and
its last recorded update time. At 101 columns or wider, an Updated column
shows local `HH:MM` for today and `YYYY-MM-DD` for earlier dates.
The wide layout places SP, Est tokens and Est time immediately after the ID;
these use the selected model only. Missing estimates show `—`, never zero. Details
retain the full timestamp and timezone. That timestamp comes from the task log (or
creation when no log exists), separately from snapshot verification. Colour
distinguishes states and warning flags; `NO_COLOR` disables it.
Tasks group by state, then priority and ID. Mutating taskglance keys are not
assigned: the dashboard cannot edit, claim, close, delete or undo tasks.

Changed rows are bold for ten seconds. Event badges last five minutes and
recent closures remain visible for one hour. The recent strip distinguishes
creation, completion, reopening, cancellation, supersession, rewritten logs
and prerequisites becoming satisfied. The initial snapshot establishes a
baseline rather than announcing every existing task as new. A disappeared
task is never counted as completed. Changes entirely undone between polls
cannot be observed. Session totals cover all repositories; health counts
follow CLI filters, and `/` filters only the list. Event history is bounded
and kept in memory, so restarting resets session counts.

The header shows recorded states, expired claims, owner holds, eligibility
under the existing `next` rule, recent completions and estimate coverage.
Owner holds remain separate from `next` eligibility, whose existing rule
does not interpret that field. A valid claim does not prove an agent is
running; an expired one does not prove work stopped. The last-record age
measures recorded activity, not productive progress. Completion checks show
counts and recorded evidence, not a percentage of effort or freshly queried
CI results. Dependency details include records outside the selected scope;
only `done` satisfies a prerequisite. Missing dependencies stay unsatisfied.

Time estimates are for the selected model's participation, not a task finish
forecast. Zero is a known estimate; absent estimates remain unknown. The view
does not add overlapping model estimates, turn points into hours, infer
remaining work from elapsed time, or invent completion ETAs. An owner hold
is labelled explicitly. Details include the recorded reason for unknown time.

Refresh errors keep the last good snapshot behind a `STALE` banner with its
verification time. The reader never repairs pending journals, writes cache or
record files, synchronizes Git, scans transcripts or calls models. Locks are
held only during snapshots, with a short bounded wait. `--unlocked`, `--json`
and `--with-changes` cannot be combined with `--live`. Keyboard input, screen
and cursor are restored on normal exit and SIGINT, SIGTERM or SIGHUP.

Record estimates against exact model IDs:

```sh
agent-task --agent helper estimate set T-0001 provider/model-v1 \
  --wall-seconds 600 --tokens 20000
agent-task --agent helper estimate set T-0001 provider/model-v1 --tokens 25000
agent-task --agent helper estimate remove T-0001 provider/model-v1 \
  --reason 'no longer considered'
agent-task --agent helper set T-0001 --storypoints 5
```

The optional `estimates` header is a JSON object keyed by model ID, with
`wall-seconds` (finite, nonnegative elapsed seconds) and `tokens`
(nonnegative integer total tokens), or nonblank `wall-seconds-unknown` and
`tokens-unknown` reasons in place of numeric values. At least one metric or
unknown reason is required for `set`; omitted metrics and other models are preserved. `remove` revokes the
model's whole estimate. These are manual planning estimates, not measured
usage. Estimate edits have the same permissions as dependency edits.
`storypoints` accepts only 1, 2, 3, 5, 8, 13 or 20 through `new` or `set`;
an empty field means unspecified. Storypoints edits follow the existing
owner-only field editing policy for claimed tasks. Older records without
these optional headers remain valid; no migration is required.
Use `agent-task --agent <id> log TASK 'rationale or scope change'` for brief
estimation context.
The `estimate-agent-work` skill provides sizing and decomposition guidance;
it does not require estimating the existing backlog. Executable pieces are
ordinary tasks. Associate them with
`agent-task --agent <id> link TASK --related OTHER_TASK`. Declare real blockers
with `agent-task --agent <id> dependency add TASK PREREQUISITE`, adding
`--reason 'result needed'` to explain the prerequisite.

Set the per-machine `estimate_policy` in `records.json` to `off` (default),
`warn` or `require`. At claim/extension, handoff, helper registration and
resumption into `in-progress`, `warn` prints missing execution estimates;
`require` refuses without changing
the task. Creation and closure remain unaffected, with no backlog migration
or storypoint gate. Select the intended model when claiming or handing off:

```sh
agent-task --agent helper estimate set T-0001 provider/model-v1 \
  --wall-seconds 600 --tokens-unknown 'context size not yet known'
agent-task --agent helper claim T-0001 --model provider/model-v1
agent-task --agent helper claim T-0002 \
  --model-unknown 'runtime does not expose its model ID'
```

Both metrics must have a numeric value or a nonblank unknown reason for the
selected model. Use `--wall-unknown REASON` and `--tokens-unknown REASON` on
`estimate set`; each replaces its numeric value, and a later numeric estimate
replaces that reason. Unknown values are never converted to zero. A recorded
unknown model reason acknowledges that neither metric can be keyed reliably.
These reasons are explicit exceptions, not verified predictions.

The optional `execution-model` and `estimate-model-unknown` task headers store
the selection. Renewals by the same recorded owner reuse it, even after expiry; takeovers and
handoffs need a fresh selection or explanation. Estimates for unrelated
models do not satisfy the check and are never used to infer a selection.
Warnings show commands for recording resources and choosing the model.

For a new task, combine creation, estimates and ownership in one transaction:

```sh
agent-task --agent helper new --title 'Example task' --storypoints 3 \
  --claim --model provider/model-v1 --wall-seconds 600 --tokens 20000
```

`new --claim` accepts `--hours` (default 2, maximum 24). Without `--claim`,
startup options are rejected. Missing required estimates or invalid input
leave no task, allocation or commit behind. Use `--model-unknown REASON` alone
when the model is unavailable. For a known model, the same numeric or unknown
metric options as `estimate set` are available. Existing `claim` accepts those
options too; same-owner renewals can update metrics for their retained model.
Omitted metrics are preserved, and takeovers never inherit a model selection.
Helper registration remains explicit and separate from owner selection.

Register each helper's intended model separately, after recording its
per-model estimates:

```sh
agent-task --agent owner estimate set T-0001 provider/worker-v1 \
  --wall-seconds 300 --tokens 5000
agent-task --agent owner helper add T-0001 helper-id \
  --model provider/worker-v1
```

`helper add` also accepts `--model-unknown REASON`. Repeating it reuses the
helper's previous selection unless a new selection is supplied. The optional
`helper-models` JSON header maps helper IDs to `{"model":"ID"}` or
`{"unknown":"reason"}`; it never changes the owner's selection. Estimates
remain task-wide per-model entries, not separate per-helper budgets. Removing
a helper clears its selection; release, takeover, handoff and closure clear
all helper registrations and selections. Same-owner renewal after expiry keeps
helpers and their selections unless another agent has taken ownership.
The policy checks these task transitions, not whether arbitrary work outside
the tool actually started, and does not validate estimate accuracy.
Implicit claim heartbeats on ordinary task edits are not rechecked; this is
a start/resume check, not continuous enforcement. Other machines can use a
different policy. Invalid policy configuration is refused by all commands.

Like all header values in `show --json` and `list --json`, `estimates` is
returned as a string; decode that string as JSON to read its model entries.

Changelog entries are `entries/YYYY-MM-DD-HHMM-scope-slug.md`; mistakes are
`mistakes/YYYY-MM-DD-slug.md`. Their headers record the event, location,
reason, cleanup plan, repository keys and associated task IDs. The `tasks:`
field is the association authority: tasks do not duplicate it. Open records
must be closed, mitigated or transferred before their last task closes.
Records document state but never authorize deleting it.

Give each writer an explicit identity. `agent-id show` derives one from a
session variable; `agent-id new helper` mints and stores one for a delegated
agent. The delegating agent can pass this prompt fragment:

```text
Your agent ID is helper-0123456789abcdef. Work on T-0001.
Pass --agent helper-0123456789abcdef to every records mutation.
Use agent-task --agent helper-0123456789abcdef log T-0001
  --to all 'Progress update' for coordination.
```

Agents sharing a task may use `helper add`, `log --to`, `show --after` and
`watch --for` to exchange messages. `watch` prints a cursor to resume from;
after a log rewrite, scan the full log it prints before resuming. Every
mutation requires `--agent` or `AGENT_ID`; inherited session variables do
not silently grant the parent's identity. `--force REASON` is only for the
human owner's explicit instruction, quoted or cited in the reason. It never
bypasses a completion gate; close as `cancelled` or `superseded` with a reason
when completion is not appropriate.

Exit codes are 0 success, 1 refusal with records verified unchanged,
2 usage or configuration error, 3 committed locally but push failed,
4 watch timeout, and 5 recovery needed or git operation in progress. A
mutation commits with explicit paths. `push` defaults to false, including
for `sync`. It may be set per directory, for example
`"push": {"changelog": true}` to push changelog records while tasks stay
local. When pushing, pushes hold the records lock, so readers and
watch polls can wait behind them. A failed rebase is aborted and reported;
resolve conflicts manually. If two machines allocate the same task ID and
`.next-id` conflicts, renumber the local task file and its `id:`, `related:`
references and changelog `tasks:` values, then run both `lint` commands.
Never use a records entry itself as permission to remove a worktree or file.

Ordinary reads and watches never invoke recovery or signing. A pending journal
makes them exit 5 without changing records; use an explicitly authorized
`agent-task --agent ID recover` or `agent-changelog --agent ID recover`.
Mutations retain automatic recovery before applying their own transaction.
`--unlocked` permits a diagnostic read of pending state marked `UNVERIFIED`
(`authoritative: false` in JSON); it cannot establish completion or ownership.
A live writer may still hold a lock: readers wait up to `--wait` rather than
inspect its in-flight transaction. Do not delete its lock or launch competing
recovery operations. `doctor` and explicit recovery are mutating operations.

## What it reads, and what leaves the machine

`agent-quota` scans the Claude Code and Codex transcript directories on this
machine to attribute token activity. That scan uploads nothing.

`agent-quota --agents` additionally labels threads, and a label is produced
by sending a bounded excerpt of the thread to a model. By default the
eligible providers include the one that did not produce the thread, so a
Claude Code excerpt can be sent to Codex and the reverse, spending that
provider's quota or credits.

- `--no-cross-provider-summaries` keeps each excerpt with its own provider.
- `--no-summaries` updates observations and makes no model calls.
- `--cached` skips the transcript scan entirely.
- `AGENT_QUOTA_SUMMARIZER=1` in the environment suppresses labelling without
  a flag.

`md-preview` serves the document's own directory on loopback. Passing
`--repository-assets` widens that to the enclosing repository, which makes
every sibling readable by any local client for as long as the server runs.
Version-control directories are never served.

## Tests

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t .
```

No third-party packages are needed. The `md-preview` suite skips unless
`cmarkgfm` and `nh3` are importable by the interpreter running it; to
exercise it, supply them:

```sh
PYTHONDONTWRITEBYTECODE=1 \
  uv run --with cmarkgfm==2025.10.22 --with nh3==0.3.6 \
  python3 -m unittest tests.test_md_preview
```

Model calls are mocked or use local fake executables, and speech backends
are shadowed by stubs, so running the suite spends nothing and stays quiet.
[`tests/AGENTS.md`](tests/AGENTS.md) describes what each suite covers, and
[`bin/agent-quota.md`](bin/agent-quota.md) is the schema contract the quota
tooling must preserve.

## Licence

[MIT](LICENSE). Copying a skill or a command into your own setup is the
expected use; keep the copyright notice with it.

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

### Cooperative resource admission

```sh
agent-resource run --resource gpu --wait 600 --timeout 1800 -- command args
agent-resource run --resource gpu --resource build -- command args
```

Use agreed resource names among competing workers. Names are case-sensitive.
Acquisition is exclusive and ordered by name to avoid deadlocks. This is
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
to `$HOME/.local/state/agent-kit/resources`. They contain no commands or task
text. Never delete lock files to break a live lock: that creates two independent
locks. Idle files may remain indefinitely and cost no running process. The
wrapper does not write task records or claim resource ownership for other
agents. It runs only the explicitly supplied command.

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
