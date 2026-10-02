# Try the Moss & Mugs demo

Explore the dashboard through a fictional woodland tea-shop game. You can
see how a parent agent works with helpers, inspect shared tasks, and read a
quota timeline without loading your own projects or accounts.

## Ask your agent to help

```text
Help me try agent-kit's Moss & Mugs demo. Read docs/demo/README.md and
check the needed tools. Use the supplied runner and fictional inputs,
keeping real accounts, transcripts and task stores out of the demo.
Help me launch it in a terminal, or give me the command if you cannot
open an interactive terminal. Explain the three views and how to exit.
Do not query providers or enable model summaries.
```

## What to look for

- **Agents:** a brewing parent waiting on two active helpers, alongside
  other work. Expand or fold the group to see the relationship.
- **Agent Tasks:** dependencies, completed work and an overnight-save task
  with its review still outstanding. Open details to inspect the evidence.
- **Timeline:** an upcoming quota reset and projected run-out. These are
  fictional observations, not your account's allowance.

All records are authored for this example, not anonymized copies of real
work. Completion checks illustrate recorded evidence; they do not claim
real tests or CI ran. The profiling task has no invented bottleneck or result.

## Technical reference for agents and manual use

The commands and capture details below support the setup prompt above.

From the repository root, with Python 3, Git and tmux installed:

```sh
python3 docs/demo/run.py --help
python3 docs/demo/run.py
```

The second command opens the shipped `bin/agent-dash`, copied unchanged into a
temporary directory beside fixture adapters. Exit or detach to return; the
runner kills only its isolated tmux socket and removes its temporary directory.
It never changes the production tmux server, user task store, or machine clock.

Reproduce the terminal output and PNGs with Freeze v0.2.2:

```sh
python3 docs/demo/run.py --capture docs/demo
for view in dashboard task-details quota-timeline agent-activity; do
  freeze --execute "cat docs/demo/$view.ansi" \
    --output "docs/assets/$view.png" --background '#17191f' \
    --font.size 16 --padding 20 --margin 0 --window=false
done
```

The renderer inputs use **2026-10-02 12:00 UTC** throughout. The overview is a
110-column, 42-row three-pane dashboard; task details and agent activity are
captured after resizing the real panes to 28 and 24 rows respectively. The
quota pane retains the overview's 110-column geometry. The launcher determines
pane sizes using the same code as normal operation.

Capture method: `tmux capture-pane -p -e` retains production output and ANSI
status colours. Only unused trailing empty rows are omitted; all content and
ANSI attributes are retained. For the overview, the actual stacked pane captures are joined
in pane order with full-width, labelled separators representing tmux's pane
borders and one empty row between apps. Freeze rasterizes that output on an
opaque dark background; this is a terminal-output rendering, not a desktop
screenshot. Freeze's ANSI rendering does not reproduce inverse selection
backgrounds; the production `>` selection marker remains visible. The spinner
may lack a glyph in Freeze's default font. The saved ANSI is the authoritative
capture of those terminal attributes.

Isolation: a fresh temporary HOME and XDG directories, temporary records/cache
paths, an environment allowlist, and a unique tmux socket. No transcripts,
credentials, provider requests, model summaries, personal tasks, or real record
roots are loaded. Task records are materialized in disposable Git repositories and read through
the production loader; activity loader seams supply the synthetic fixture;
quota inputs are converted using the production observation constructor.
Production lint and closure gates validate the records before launch. The
overnight save has three of four fixed checks recorded, with work review
outstanding; the completed rain task passes the real closure gate. The
runner rejects unsupported adapter commands and live collection fallbacks.

The capture run drives real keys and checks folding/expansion, task filtering,
selection and evidence details before cleanup. Automated coherence/environment
checks run with:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_readme_demo
```

Capture baseline: `e8b71ce` plus the README refresh working changes, including
its portable launcher. The final delivery commit records the complete source.
No font files, cache directories, or failed captures are included.
