# steamos

Share and inspect SteamOS devices, such as a Steam Deck or a Steam
Machine, from agents and people working on the same machine. It talks to
each device over ssh, with optional installation of Valve's helpers and
title uploads. Wake-on-LAN uses a UDP broadcast.

```sh
steamos status                      # device facts and who holds it
steamos --device NAME status --json
steamos lease take 'deploy build 12' [--hours N]
steamos lease renew [--hours N]
steamos lease show | check | release
steamos lease break --reason 'why'
steamos devkit install              # Valve's helpers, at a pinned commit
steamos title register Demo1 ./build --start ./run.sh --arg=--verbose
steamos title launch Demo1 --json
steamos title list
steamos title remove Demo1
steamos logs [Demo1] [--since EPOCH] [--until EPOCH] [--lines N] [--json]
steamos capture [--out FILE]         # gamescope screenshot; Game Mode
steamos frametimes start | stop      # change MangoHud logging; lease needed
steamos frametimes pull [--out DIR]  # download newest CSV(s); no lease
steamos wake [--wait N]              # broadcast, then wait for ssh
```

All commands accept `--device NAME` and `--json`, before the command or
after its action. Exit codes: 0 success; 1 refused (including helper or
Steam re-sync failure); 2 usage or configuration; 3 unreachable or an
unusable device reply. A lost connection after a command starts has an
unknown effect; inspect the device before retrying.

## Setup

1. On the device, turn on Developer Mode and its devkit service.
2. Pair this computer, either with Valve's SteamOS Devkit Client or by
   any ssh key the device accepts. Devkit pairing needs the device in
   pairing mode first: **Settings > Developer > Pair new host**.
3. Describe your devices in `$XDG_CONFIG_HOME/agent-kit/steamos.json`
   (usually `~/.config/agent-kit/steamos.json`).

```json
{
  "default": "deck",
  "devices": {
    "deck": {"name": "steamdeck"},
    "box": {"address": "192.0.2.20", "name": "steammachine"}
  }
}
```

A device needs only `name`, its network name as the device advertises
it. With an `address`, that is tried first and the name (`NAME.local`)
is the fallback. Optional per device: `user` (default `deck`) and `key`
(default `~/.config/steamos-devkit/devkit_rsa`, the Devkit Client's key,
when that file exists; otherwise ssh's own configuration applies).
For wake, configure `mac` as six colon-separated hexadecimal bytes,
and optionally `broadcast` as an IPv4 address (default
`255.255.255.255`). On multi-homed hosts, set `broadcast` to the subnet
broadcast address: `255.255.255.255` leaves only via the primary interface.

```json
{"devices": {"box": {"name": "steammachine",
  "mac": "02:00:00:00:00:01", "broadcast": "192.0.2.255"}}}
```

## The lease

One lease per device, shared by every project, so agents and people do
not deploy over or benchmark against each other. It is advisory: it
protects only work that checks it. The lease is a directory in the
device's home created with one `mkdir`, so two takers cannot both
succeed, and the device's clock judges expiry.

- `take` succeeds when the lease is free or already yours (it then
  renews). An expired lease stays with its holder for a grace period,
  then the next `take` reclaims it and the device's
  `~/.agent-kit-steamos-lease.log` records that.
- `break` removes a lease someone else holds and records the reason in
  the same log.
- The holder is `STEAMOS_LEASE_HOLDER` when set, then `holder` in the
  configuration, then `agent-id show` when agent-kit records are
  installed, then `user@host`. On macOS the host part is the stable
  local host name, which does not change with the network.

Policy, all optional:

```json
{
  "lease": {
    "hours": 4,
    "grace_minutes": 30,
    "prevent_sleep": true,
    "breakers": ["you@your-computer"],
    "legacy_locks": [".project-deck-lock"]
  }
}
```

`breakers` limits who may break a lease; when it is absent or empty,
anyone may. `legacy_locks` names lock paths, relative to the device's
home, that older project tools create; while one exists, `take` refuses
and `status` shows its record, so both kinds of lock are honoured until
those tools move to this lease.

`prevent_sleep` defaults to `true`. Taking, refreshing or renewing a lease
starts `systemd-inhibit --what=sleep:idle --mode=block` for the seconds left
until expiry, with `--who=agent-kit-steamos` and the holder in its reason.
This prevents an idle sleep and the PIN screen on waking from interrupting
display or performance work. Release, break and reclaim stop the recorded
inhibitor before removing the lease; expiry ends its own `sleep`, with no
watcher or cleanup command needed. Without a lease, normal sleep policy
applies. Set `prevent_sleep` to `false` to opt out; a later take or renew
also stops an existing inhibitor when this option is disabled.

The inhibitor uses `nohup` and `setsid`, with stdin/stdout/stderr redirected,
to survive ssh session hangup without keeping the connection open. Its PID,
start epoch and Linux process start ticks live in
`~/.agent-kit-steamos-lease/inhibit`. Before signalling it, the tool checks
the recorded ticks and `/proc/PID/cmdline` for its exact inhibitor identity.
An unrelated or stale PID is never intentionally signalled. Missing
`systemd-inhibit`, detach tools or procfs are skipped without stderr warnings;
output reports `inhibit=unavailable`. An installed inhibitor that fails to
start makes take/renew fail; check the lease before retrying.

`lease show --json` reports `sleep_inhibited`: `true` for a verified live
inhibitor, `false` when absent, stopped, expired or disabled, and `null` when
support is unavailable. `status --json` includes it under `lease`; text
output includes `inhibit=active|inactive|disabled|unavailable`. These are
process checks, not a query of logind's inhibitor table. During live
validation, run `systemd-inhibit --list` on the device after the taking ssh
connection has closed, and again after release or expiry. Session hangup is
covered locally; logind configured with `KillUserProcesses=yes` can still
kill detached session processes and requires device-side verification.

## Status

`status` is read-only. It reports the SteamOS version, build and update
channel, the model, free space in the device home, power, whether
Valve's `devkit-utils` are present, and the lease. Devices may run
different SteamOS channels and versions; `status` reports each device's
rather than assuming one. Valve's `steamos-get-status` is not used,
because on a Steam Deck it also turns off wireless power management.

## Valve's device helpers

Valve's SteamOS Devkit Client keeps small helper scripts on each device
in `~/devkit-utils` (registering a title with Steam, launching it,
listing and deleting titles). `steamos devkit install` puts them there
without the Devkit Client: it fetches Valve's repository at a pinned
commit with `git`, which checks the content against that commit, keeps
it in `$XDG_CACHE_HOME/agent-kit/steamos-devkit/COMMIT` (usually under
`~/.cache`), and copies `client/devkit-utils` to the device with rsync,
replacing what was there, so it needs the lease and refuses a
`~/devkit-utils` that is a symlink. It records the commit in
`~/devkit-utils/.agent-kit-pin`, and `status` reports whether a device
has the pinned commit. Only the first install of a commit needs to reach
Valve's GitLab. To use another commit or a mirror:

```json
{"devkit": {"commit": "<40-character commit>", "source": "<git URL>"}}
```

Facts found while proving this path, which these helpers do not check:

- A title's game ID must not contain `-`; Steam rejects it with only
  `missing/invalid arguments`.
- A title's output goes to the user journal (`journalctl --user`), not to
  a per-launch log file.
- Deleting a title makes Steam re-register every folder in
  `~/devkit-game` that has configuration files, so one invalid leftover
  makes that whole step fail.

## Titles

`title register NAME DIR --start PATH [--arg ARG ...]
[--runtime slr4|none]` uploads the contents of DIR and registers a Steam
shortcut using the pinned helpers. NAME accepts only ASCII letters and
digits; invalid names fail before any device call. `--start` is required,
must name a file inside DIR, and is passed as the first command argument
(for example `./run.sh`). Repeat `--arg` for each argument; use
`--arg=--flag` for values beginning with a dash. Arguments are preserved
as an array, not interpreted as a shell command.

The default runtime is Steam Linux Runtime 4 (`SteamLinuxRuntime_4`),
with Steam Play disabled. `--runtime none` clears the compatibility-tool
setting. Registration requires your lease and refuses invalid existing
title folders with sibling `<id>-argv.json` or `<id>-settings.json`
files, because they can break Steam re-sync. Nothing removes those
leftovers automatically. Symlinked title destinations are refused.
Re-registering mirrors DIR exactly with rsync `--delete` inside the prepared
`~/devkit-game/NAME/` directory; other titles and sibling files are preserved.

`title list` needs no lease. It reports Valve's title list and any invalid
leftovers; JSON contains `games` and `invalid_leftovers`.

`title launch NAME` requires your lease and Steam running in Game Mode.
It records `date +%s` on the device immediately before calling Valve's
launch RPC. JSON contains integer `device_time`, `launched`, `output`
and `stderr`, including when the RPC fails after the time was captured.
It also appends that epoch, NAME and holder (tab-separated) to
`~/.agent-kit-steamos-launches` before calling the RPC, including failed
launch attempts. If the record cannot be written, the launch is refused.
Use `steamos logs NAME` to read that device-clock window.

`title remove NAME` requires your lease and refuses named invalid leftovers
before deletion, just as registration does. It calls Valve's delete helper
for that title, then removes only its three sibling argv, env and settings
JSON files even when the helper exits non-zero. Other titles are preserved.
It reports Steam re-sync status (`steam_sync` in JSON); a sync failure
exits 1 even though the files were
removed. The helper exit code is the primary failure signal; its sync
warning is a secondary hint. It never resets Steam or requests deletion of
all titles.

`launch` and `remove` check the lease once at start. `register` re-checks
before each prepare, copy and shortcut-registration step.

All title commands require the device's recorded helper commit to match
the configured pin. If it does not, run `steamos devkit install` with the
lease before retrying.

## Logs

`logs [NAME] [--since EPOCH] [--until EPOCH] [--lines N]` is read-only
and needs no lease or Valve helpers. By default it starts at the last
recorded launch of NAME, or any title when NAME is omitted, and ends at
the device's current epoch. If there is no matching launch record, supply
`--since`. Epochs are integer seconds on the device clock; `--lines`
defaults to the last 200 lines (allowed range 1 to 1000000).

It reads `journalctl --user --since @A --until @B -o short-iso --no-pager`.
If journalctl is missing or fails, it reads
`~/.local/share/Steam/logs/console-linux.txt`, filtering its timestamped
entries and continuation lines to the window using the device's local
timezone. A working journal with no entries returns an empty result.
JSON is `{source, since, until, lines: [...]}`, with source `journalctl`
or `console-linux.txt`. NAME selects the time window; output may include
other titles and user services. Steam combines title stdout/stderr in
these sources; there are no per-launch log files.

## Capture

`capture [--out FILE]` requests a gamescope screenshot in Game Mode,
without a lease or Valve helpers. It uses Valve's `DISPLAY=:0 xprop`
protocol: `GAMESCOPECTRL_DEBUG_REQUEST_SCREENSHOT`, format `32c`, mode
`1` (baseplane). The property takes a numeric mode, not a filename;
gamescope creates a `/tmp/gamescope*.png` asynchronously. The command
preserves existing screenshots, waits up to 10 seconds for a newly
created complete PNG, then moves it into a private device temp directory.

It downloads with scp using the same key, ssh options and address fallback
as other commands, then removes its device temp file and directory,
including after a failed copy. Downloads replace local output only after
a successful transfer. The default output is
`./steamos-capture-<device>-<UTC YYYYMMDDTHHMMSSZ>.png`; `--out` requires
an existing parent directory. JSON reports `device` and `output`.
Missing gamescope reports that Game Mode is required; xprop failures
and timeouts exit 1. Concurrent agent-kit captures are refused by a
short-lived capture lock, separate from the device lease. An interrupted
connection may leave that lock or a temp file: inspect before removing
them; never remove another capture's files or run broad gamescope cleanup.

## Frametimes

`frametimes start` and `frametimes stop` require your active device lease.
They run `mangohudctl set log_session true` or `false`, matching Valve's
PerfOverlay controls. They change logging only; MangoHud must already be
available on the device and attached to the running game. A successful
control command does not prove that the game produced frame data.

Not yet verified on a device. In a first test (SteamOS 3.8.27, beta
Steam client, Game Mode) logging produced no CSV, with the performance
overlay on or off; the device had just woken and its screen was locked,
so the title may never have been displayed. Steam writes mangoapp's
configuration itself (`MANGOHUD_CONFIGFILE`), with no log folder set.
Treat `frametimes` as experimental until a run with the screen unlocked
produces data.

`frametimes pull [--out DIR]` needs no lease or Valve helpers. Valve
downloads `mangoapp_*.csv` from the device user's home; this command copies
the newest regular matching file(s), including ties in modification time,
to DIR (default: current directory). It ignores symlinks and unsafe names,
preserves device files, and refuses when no CSVs exist. It creates DIR as
needed and uses scp with the same ssh options/address fallback. JSON gives
`device` and `files`; start/stop instead give `device` and `logging`.

```sh
steamos lease take 'Demo1 frametime check'
steamos title launch Demo1 --json
steamos frametimes start
# Let the title run through the scenario being measured.
steamos frametimes stop
steamos frametimes pull --out ./frametimes
steamos logs Demo1 --lines 100 --json
steamos capture --out ./capture.png
steamos lease release
```

## Wake

`wake` sends three magic packets about 100 ms apart to the configured MAC
via UDP broadcast on port 9, then polls ssh for up to 60 seconds.
`--wait N` changes the wait
(0 to 3600 seconds; 0 sends the packets and checks once). It needs no lease.
It exits 0 when ssh answers and 3 if the device stays unreachable; JSON
contains `awake`. Wake-on-LAN must already be enabled on a wired device.
A Steam Deck on Wi-Fi does not wake this way.

## Notes

- On macOS, a Python interpreter may be blocked from reaching the local
  network by the Local Network privacy setting while system tools are
  not. Title and lease commands use `ssh`. Wake uses Python UDP; a subnet
  broadcast address has worked where unicast failed. Configure the subnet
  broadcast if needed, and check Local Network permission for Python.
