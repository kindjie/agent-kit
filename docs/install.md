# Installation and integrations

[Back to the introduction](../README.md).

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
- `tmux` — required only for `agent-dash`; `taskglance` is optional with
  `agent-dash --personal`. The default uses no model summaries.
- An authenticated `claude` or `codex` CLI for `agent-quota` to report on.
  It reports what it can observe and leaves the rest unknown.
- Speech is optional: `say` on macOS, `spd-say` or `espeak-ng` on Linux.
  Without one, `agent-speak.sh` falls back to a terminal bell.

`agent-speak.sh --bell --attention needs-you 'needs a decision'` uses a
terminal bell instead of speech. Attention reasons are `needs-you` (YOU),
`blocked` (BLOCKED), `work` (WORK: ready for work with no active delegates),
and `check` (CHECK: concrete evidence warrants investigation). Ordinary
messages default to `needs-you`. In tmux, a bell rings once when the reason
changes, through the exact pane's terminal even when tool output is captured
and the worker has no controlling terminal.
Mute suppresses audio while retaining the badge. No transcript silence or
turn completion is interpreted as a blocker or empty work queue.

`agent-speak.sh --clear` clears the current pane's badge. Configure
`agent-attention hook` on SessionStart and UserPromptSubmit to clear it when
the main session resumes (compact and identified subagent events are ignored).
The helper publishes `@agent_attention`, `@agent_attention_mark` and
`@agent_attention_kind` pane options; it preserves pane titles/window names.
Tmux formats can display these directly and loop over panes for window badges.
Outside tmux no pane is guessed or registered. Missing tmux fails silently.
Agents must clear resolved attention; a badge is a reported hand-off, not
proof of a running process. Dead panes are excluded from dashboard totals.

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
for source in "$PWD"/bin/*; do
  [ -f "$source" ] || continue
  target="$HOME/bin/agent-kit/${source##*/}"
  if [ -e "$target" ] || [ -L "$target" ]; then
    printf 'Existing entry, skipped: %s\n' "$target"
  else
    ln -s "$source" "$target"
  fi
done
```

Then add it to `PATH` in your shell profile:

```sh
export PATH="$HOME/bin/agent-kit:$PATH"
```

**Skills.** Link whole skill directories, so their `references/` travel with
them. Claude Code reads `~/.claude/skills`; Codex reads `~/.agents/skills`.

```sh
mkdir -p ~/.claude/skills ~/.agents/skills
# Select names; installing every skill is optional.
for name in profiling commit; do
  skill="$PWD/skills/$name"
  for directory in "$HOME/.claude/skills" "$HOME/.agents/skills"; do
    target="$directory/$(basename "$skill")"
    if [ -e "$target" ] || [ -L "$target" ]; then
      printf 'Existing entry, skipped: %s\n' "$target"
    else
      ln -s "$skill" "$target"
    fi
  done
done
```

**Check command discovery.** Help needs no account or model call:

```sh
agent-quota --help
agent-dash --help
```

Markdown preview is optional and needs `uv`; it starts a loopback server:
`md-preview README.md --no-open`. See the preview skill for asset-serving limits.

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

It writes [`AGENTS.snippet.md`](../AGENTS.snippet.md) into each file between
`BEGIN agent-kit` and `END agent-kit` markers, appending it when absent.
Read it first — it is short, and it asserts opinions about testing and
pushing that you may not share. Your own rules take precedence wherever
they conflict; the skills say so. By default it updates only the files of
the agents present (`~/.claude`, `~/.codex`), follows symlinks to the real
file, keeps its permissions, and replaces it atomically.

The task and changelog rules are included only when `agent-task doctor`
and `agent-changelog doctor` both pass at install time (see
[Agent records](records.md#agent-records)); agents then read whether the tools apply
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
