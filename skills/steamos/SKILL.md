---
name: steamos
description: >-
  Works on a SteamOS device (Steam Deck, Steam Machine) shared with other
  agents and people: check its status, take and release the device lease,
  and hand off what needs a person at the device. Use before deploying,
  launching, benchmarking or changing anything on such a device, and when
  a task mentions a Steam Deck, Steam Machine or SteamOS target.
---

# SteamOS devices

Use the `steamos` command (see `steamos.md` beside it in agent-kit's
`bin/`). It reaches devices over ssh and manages the lease, its log,
launch-window records, and, when asked, Valve's helpers in `~/devkit-utils`,
uploaded titles in `~/devkit-game`, screenshots and MangoHud logging.
`wake` sends a UDP broadcast.
If it is not configured, say so and ask the user for the device's name
or address rather than guessing.

## Order of work

1. `steamos status` (add `--device NAME` when several are configured).
   Read the SteamOS version, build and channel, free space, power and the
   lease. Devices may run different channels and versions on purpose; do
   not "fix" that, and note the version with any result you report.
2. `steamos lease take 'PURPOSE'` before anything that changes the device
   or depends on it being quiet: deploys, launches, benchmarks, settings.
   Make the purpose specific (`deploy build 1234 for audio check`).
3. Do the work. Re-run `steamos lease check` before each later mutating
   step; it fails if your lease expired or was taken over. Renew long work
   with `steamos lease renew`.
4. `steamos lease release` when done, including when you stop early.

On a shared device, hold a lease only for the device steps themselves; a
build or benchmark running on the device is a device step. Release it before
long work that does not use the device (local builds, reviews, CI waits) and
take it again when you next need it. Build SteamOS bundles in the project's
supported build environment; where that includes an available SteamOS device,
building there usually beats emulating x86-64 on a host of another
architecture.

A subagent shares its parent's session, so the holder would default to the
parent. Start each Bash call with `export AGENT_ID=<your agent ID>;` (the
variable does not persist between calls, and a one-off prefix such as
`AGENT_ID=x steamos lease take y && steamos deploy` covers only the first
command, so the deploy would run as the parent). `--holder <id>` works
on every `steamos` command too, not only `lease`: deploy, title, devkit,
frametimes and bench check the lease as the same holder. `steamos lease
show` confirms it.

Taking or renewing a lease prevents sleep/idle by default until its expiry. The
transient user unit `agent-kit-steamos-lease-inhibit` runs `systemd-inhibit`
outside the ssh session scope. Take/renew stop the old unit before starting
another; release, break and reclaim stop it with `systemctl --user stop`. Its
timed sleep ends at expiry. Normal sleep policy applies afterwards. This avoids
waking into a PIN screen during leased display/performance work.
`lease.prevent_sleep: false` opts out in the machine's device config. Missing
`systemd-run`, `systemd-inhibit` or a user manager is skipped with
`inhibit=unavailable`; `lease show --json` and the `lease` object in `status
--json` report `sleep_inhibited` as true, false or null (unavailable). Active
requires both an active unit and its `agent-kit-steamos` logind entry; startup
checks both after 0.5 seconds. Inhibition failure is best-effort:
take/renew/reclaim still succeed, with `inhibit=failed`, a stderr warning and
`sleep_inhibited: false`. Inspect `systemd-inhibit --list` after ssh closes and
after release/expiry when validating device behavior. No inhibitor PID files or
PID signalling are used.

Reading status needs no lease. When `status` says Valve's
`devkit-utils` are missing or not at the pinned commit and the work needs
them, run `steamos devkit install` while holding the lease; it replaces
the helpers another agent's title may be using.

## Titles and wake

- For projects with a root `steamos.json`, use `steamos stage` to verify
  the built bundle locally, then `steamos deploy` with the lease and pinned
  helpers. Use `--project PATH` from another directory. The config describes
  title, bundle, inventory, executable, args, runtime and retention; see
  `bin/steamos.md`. Version-1 SHA256 inventories accept a `required` list
  directly. Never infer inventory contents from the checkout.
- When the project declares `build`, use `steamos build --dry-run` to inspect
  its resolved inputs, then `steamos build` to run its command and stage the
  resulting bundle. Machine-specific input paths belong in the per-user
  overlay described in `bin/steamos.md`. A missing required path stops before
  the build; a successful build prints the verified version ID.
- Use `steamos doctor --project PATH` for read-only local and project checks.
  Add `--device NAME` or `--all` for device checks; no selector means no
  device connection. Warnings identify unavailable evidence and failures
  identify unmet requirements. Doctor never obtains a lease or changes a
  device. Benchmark-only projects need no `steamos.json`.
- Deploy uploads and verifies every hash in a new version, then atomically
  replaces `current`; it preserves current on pre-publication failure.
  `steamos deploy --list` is read-only and reports current/retained/running
  versions. `steamos status` in the project compares current with local stage.
  `steamos deploy --rollback` needs the lease and pin, verifies the previous
  retained version and publishes it. Repeated rollback walks farther back
  through retained history. Neither list nor rollback needs a build.
  `runtime_files` permits named runtime paths in existing device versions;
  globs match path components, and a matched directory covers descendants.
  Patterns cannot cover inventory members or their parents, or start with `*`.
  Check `ignored_runtime_files` in deployment JSON for paths skipped by
  verification. Other unlisted files make verification fail. After a crash,
  inspect `deploy --list` for locks and partials. With the lease,
  `deploy --abort-stale` removes direct partial directories and only a lock
  older than ten minutes whose recorded lease holder is gone. It refuses
  live or unknown locks and preserves symlinks.
- Benchmark-only projects use `steamos bench` and need no deploy config.
- For ad-hoc titles, with the lease, use
  `steamos title register Demo1 DIR --start ./run.sh`. This
  uploads DIR and registers a Steam shortcut. Names must contain only ASCII
  letters and digits. Repeat `--arg ARG` (use `--arg=--flag` for flags).
  Runtime defaults to `slr4`; `--runtime none` clears the compatibility tool.
  Never use title register on a versioned deploy title: it mirrors the whole
  title directory and would remove versions and their publication record.
- `steamos title list` is read-only and needs no lease. It also reports
  invalid leftovers that block registration and Steam re-sync. Never delete
  another title to work around these; refer the named leftovers to the owner.
- `steamos title launch Demo1 --json` needs the lease and Steam running in
  Game Mode. Its `device_time` is the device epoch immediately before launch;
  it appends that time, NAME and holder to `~/.agent-kit-steamos-launches`.
  Use `steamos logs NAME` for that window. Check `launched` and exit status.
- `steamos title remove Demo1` needs the lease and deletes only that title
  and its sibling argv/env/settings files. Check the reported Steam re-sync
  status: exit 1 may mean files were removed but re-sync failed.
- Title commands refuse helpers without the configured pinned commit.
- `steamos wake --wait 60` needs configured `mac`, sends a broadcast to
  port 9, then waits for ssh (exit 0 awake, 3 unreachable). Optional
  `broadcast` defaults to `255.255.255.255`; on macOS, subnet broadcast may
  work where unicast fails, and Python needs Local Network permission.
  A Steam Deck on Wi-Fi does not wake this way.

## Logs, screenshots and frametimes

- `steamos logs [NAME] --lines 200 --json` needs no lease. It reads the
  user journal from the last recorded launch of NAME (or any title) to
  device now. Use `--since EPOCH` when no launch is recorded; `--until EPOCH`
  bounds the end. If journalctl is unavailable it reads timestamped Steam
  `console-linux.txt` entries. NAME selects a window, not a title filter;
  other titles/services may appear. JSON gives source, epochs and lines.
- `steamos capture --out ./capture.png` needs Game Mode, but no lease.
  It uses Valve's numeric gamescope xprop request, waits up to 10 seconds
  for a complete PNG, copies it with scp, and deletes only its device temp
  file. Default output is `./steamos-capture-<device>-<utc>.png`. Missing
  gamescope or a timeout is a refusal. It preserves existing screenshots.
  SIGHUP/SIGTERM clean up the PID/time capture lock; locks older than
  60 seconds or with a gone PID are replaced on the next capture. A fresh
  live lock is refused. Inspect any leftover temp file before cleanup.
  If download and cleanup both fail, the download error is primary and
  cleanup failure is a stderr warning.
- `steamos frametimes start` / `stop` require your active lease and use
  `mangohudctl set log_session true` / `false`. MangoHud must already be
  active on the running game; successful control alone proves no frame data.
- A device woken over the network shows its lock screen; a title launched
  behind it may never be displayed. Ask the user to unlock it before
  visual or performance work, and check with `steamos capture`.
- `steamos frametimes pull --out ./frametimes` needs no lease and copies the
  newest session from the device home, using either member's mtime. It pulls
  `mangoapp_<stamp>.csv` and its `_summary.csv` companion even when their mtimes
  differ. A missing summary refuses the pull: logging may still be active, so
  run `frametimes stop` first. Use `--partial` explicitly to allow a base-only
  session; a missing base is always refused. It preserves device logs and
  ignores symlinks and unsafe names. Inspect the CSV and measured scenario
  before reporting performance; no quiet-machine or hardware acceptance is
  implied.

Logs, capture and frametimes do not require pinned Valve helpers. Downloads
use the configured ssh key/options and address fallback; scp is needed
locally. Start and stop logging before pulling a completed session.

## Benchmarks

With your active lease, use `steamos bench run [--pin-governor]
[--require-power] [--cpus LIST] [--perf-stat] [--thermals SECONDS_INTERVAL]
[--out DIR] -- COMMAND [ARGS...]`. COMMAND runs on the device as literal
argv, initially in the ssh user's home; provide a wrapper for another cwd
or environment. Benchmark stdin is closed. Paths are the caller's
responsibility. The lease already inhibits sleep.
Overlapping bench runs are refused until the first supervisor finishes
cleanup. Its file lock releases automatically; never delete `run.lock`
to bypass an active run.

- `--require-power` requires confirmed external power in the status fields.
- `--pin-governor` uses only `sudo -n /etc/agent-kit/steamos-governor CPU
  GOVERNOR`. If denied, report the exact refused command and the optional
  root-owned helper/sudoers example in `bin/steamos.md`. Never install it
  automatically, prompt for a password or silently drop requested pinning.
  Without the flag, bench needs no sudo. Every CPU's original governor is
  saved, restored and checked, including after command failure, device
  HUP/INT/TERM, host interruption or transport loss.
- `--cpus` uses taskset; `--perf-stat` uses CSV perf stat when available
  (otherwise warns and records the omission); `--thermals N` samples hwmon
  and thermal-zone temperatures at a positive finite interval.

Results remain under `~/.agent-kit-steamos-bench/run-<utc>-<unique-id>/`
and are copied to a new local `--out` directory, default
`./steamos-bench-<device>-<utc>`. JSON reports the original argv/status,
device times, governor snapshots, power, collectors, restoration and paths;
stdout/stderr and requested CSVs are retained. Inspect the workload and
thermal trace before claiming performance acceptance.

Exit 4 means COMMAND failed, with its original status in `summary.json`;
exit 1 means setup or restoration failed. After exit 3 from a disconnect,
device heartbeat cleanup stops the command group and restores governors;
inspect the named run's summary and current governors before retrying or
releasing the lease. SIGKILL, power loss or device failure cannot execute
cleanup. Do not infer restoration from the host exit alone.

## When the lease is held

- Held by someone else and active: wait, or tell the user who holds it
  and for what. Never break it on your own judgment.
- Expired: it stays with its holder for a grace period, then your next
  `take` reclaims it and the device log records that.
- `break --reason` is for the user's explicit instruction; quote it in the
  reason. A configuration may restrict who can break a lease at all.
- `busy`: another agent is changing the lease this moment; retry
  shortly. `gone`: it changed while your command ran; run
  `steamos lease show` and decide again.
- Exit 3 after a dropped connection means the effect is unknown: run
  `steamos lease show` before retrying anything.
- An older project lock, reported by `status`, `lease show` and a refused
  `lease take`, blocks the lease too. Report it with its contents; remove
  it only with that project's own tool and the user's agreement.

## Needs a person at the device

Stop and ask rather than work around these:

- Judging how a game plays, looks or sounds.
- Pairing: Devkit pairing needs **Settings > Developer > Pair new host**
  on the device before the request is sent.
- Any `sudo` prompt. Never store, request or type the user's password;
  give them the exact command to run in their own terminal (a real
  terminal; prompts cannot be answered through an agent's shell).
- Steam Input templates, Touchscreen Native Support and other per-game
  Steam settings.

## Operating notes

- Verify against fetched device state, and treat an empty result as a
  failure, not as success.
- SteamOS updates can reset ssh and system settings; if a device that
  worked becomes unreachable, check Developer Mode and its devkit service
  before anything else.
- A device asleep cannot be reached. Wired devices may support
  Wake-on-LAN once it is enabled on them; a Steam Deck on Wi-Fi generally
  does not wake from sleep this way.
- Read-only means read-only: Valve's `steamos-get-status` also changes
  wireless power management on a Steam Deck, so do not use it for status.
