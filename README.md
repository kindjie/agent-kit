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
than only when a skill triggers, append them to your agent instructions.

[`AGENTS.snippet.md`](AGENTS.snippet.md) holds them, delimited by markers so
re-running the append is safe:

```sh
for f in ~/.claude/CLAUDE.md ~/.codex/AGENTS.md; do
  grep -q 'BEGIN agent-kit' "$f" 2>/dev/null ||
    { printf '\n'; cat AGENTS.snippet.md; } >> "$f"
done
```

It appends only when the marker is absent, leaves existing content alone,
and creates the file if it does not exist. Read it first — it is short, and
it asserts opinions about testing and pushing that you may not share. Your
own rules take precedence wherever they conflict; the skills say so.

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
Both roots must be writable in an agent sandbox, including their lock and
journal files. `doctor` probes writability and tests git signing in a
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
`check --na REASON`. The review field is separate from the work-reviewed
check. `agent-task --help` summarizes transitions; each subcommand's
`--help` describes its options.

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
for `sync`. With `push: true`, pushes hold the records lock, so readers and
watch polls can wait behind them. A failed rebase is aborted and reported;
resolve conflicts manually. If two machines allocate the same task ID and
`.next-id` conflicts, renumber the local task file and its `id:`, `related:`
references and changelog `tasks:` values, then run both `lint` commands.
Never use a records entry itself as permission to remove a worktree or file.

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
