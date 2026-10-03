# Try the Moss & Muggs demo

<img class="guide-float" src="../assets/moss-and-muggs/scenes/tea-cottage.png" width="180" alt="">

Explore the dashboard through a fictional woodland tea-shop game. You can
see how a parent agent works with helpers, inspect shared tasks, and read a
quota timeline without loading your own projects or accounts.

## Ask your agent to help

<img src="../assets/moss-and-muggs/characters/hedgehog-point-down-right.png" width="88" alt="">

```text
Help me try agent-kit's Moss & Muggs demo. Read docs/demo/README.md and
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

Reproduce terminal output with Freeze v0.2.2 and an installed font:

```sh
python3 docs/demo/run.py --capture docs/demo
for view in dashboard task-details quota-timeline agent-activity; do
  uv run tools/render_captures.py "docs/demo/$view.ansi" \
    "docs/assets/$view.png" --font-family 'Berkeley Mono'
done
python3 docs/demo/run.py --scenario growing --capture build/growing-capture
uv run tools/render_captures.py build/growing-capture/dashboard.ansi \
  docs/assets/growing-dashboard.png --font-family 'Berkeley Mono'
```

Captures use the agent-kit charcoal surface (`#202B36`), inverse text
(`#F3F5F7`) and orange attention accent (`#FF882B`), with softer supporting
status colours. Only SVG colour attributes change; terminal text, geometry
and the saved ANSI remain intact. These are themed output captures.

The rasterizer verifies the requested font with Fontconfig and uses pinned
CairoSVG with local Cairo. On macOS, Cairo may need
`DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`. It refuses a missing font
rather than silently substituting one. Berkeley Mono is optional and
licensed separately; font files are never distributed. Use an installed
font of your choice for your own captures. Freeze produces a temporary SVG;
Cairo renders the final PNG using the installed font.

The larger scenario adds fictional projects, agent groups and account
observations. It uses the same production views and a 140-column, 60-row
dashboard; it is a workflow illustration, not a capacity benchmark.

The renderer inputs use **2026-10-02 12:00 UTC** throughout. The overview is a
110-column, 42-row three-pane dashboard; task details and agent activity are
captured after resizing the real panes to 28 and 24 rows respectively. The
quota pane retains the overview's 110-column geometry. The launcher determines
pane sizes using the same code as normal operation.

Capture method: `tmux capture-pane -p -e` retains production output and ANSI
status colours. Only unused trailing empty rows are omitted; all content and
ANSI attributes are retained. For the overview, the actual stacked pane captures are joined
in pane order with full-width, labelled separators representing tmux's pane
borders and one empty row between apps. The capture tool rasterizes that output on an
opaque dark background; this is a terminal-output rendering, not a desktop
screenshot. Freeze's ANSI rendering does not reproduce inverse selection
backgrounds; the production `>` selection marker remains visible. The rasterizer uses installed symbol fonts for spinner and fold glyphs
that Berkeley Mono lacks. The saved ANSI is the authoritative
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
