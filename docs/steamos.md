# Work with SteamOS devices

Use `steamos` when agents and people share a Steam Deck or Steam Machine for
game builds, title runs and measurements. The command reads device facts over
ssh and coordinates changes through a device lease. The
[SteamOS skill](../skills/steamos/SKILL.md) guides the agent through the
lease, device checks and work that needs a person at the device. The
[command reference](../bin/steamos.md) has every option and the detailed
failure and recovery rules.

## Set up a device

On the device, enable Developer Mode and its devkit service. Pair the host
with Valve's SteamOS Devkit Client, or use another ssh key the device accepts.
For Devkit pairing, first select **Settings > Developer > Pair new host** on
the device. Confirm ssh works before trying a title or deployment.

Describe each device in `$XDG_CONFIG_HOME/agent-kit/steamos.json` (normally
`~/.config/agent-kit/steamos.json`):

```json
{
  "default": "deck",
  "devices": {
    "deck": {"name": "steamdeck"},
    "box": {"name": "steammachine", "address": "192.0.2.20"}
  }
}
```

The address is tried first, then the device name with `.local`. The Devkit
Client's key is used when present; otherwise ssh's configuration applies.
Use `steamos --device box status` to inspect another device. Status reports
its own SteamOS build and update channel. Your devices may deliberately run
different channels, so check each one rather than assuming they match.

Valve's device helpers are needed for titles and deployments. Once you hold
the lease, run `steamos devkit install` if status reports that the helpers
are missing or differ from the configured pin. It fetches Valve's source at
a pinned commit and copies the helpers to the device. Installation replaces
the device's existing `~/devkit-utils`, so coordinate with anyone using it.

## Share the device

Check `steamos status` or `steamos lease show` before changing the device.
Take a lease for the work, then release it when that work is finished:

```sh
steamos lease take 'Demo1 deployment'
steamos lease show
# Run the planned device work.
steamos lease release
```

The lease is shared across projects. It is advisory, so every cooperating
tool must check it. While held, it normally prevents idle sleep until the
lease expires; the status output shows whether inhibition is active. An
unexpired lease held by someone else remains theirs. Follow the
[lease rules](../bin/steamos.md#the-lease) for renewal, expiry, legacy
project locks and owner-directed breaks.

## Build and deploy a project

`steamos build` runs the repository's build command with your environment.
Use it only on projects you trust.

Put `steamos.json` at the project root. It names the title, bundle,
inventory and executable, plus optional arguments, runtime and retention.
Keep machine-specific build input paths in the per-user overlay under
`$XDG_CONFIG_HOME/agent-kit/steamos-projects/`. The
[project configuration](../bin/steamos.md#project-stage-and-deploy) defines
the accepted fields and inventory format.

```json
{
  "title": "Demo1",
  "bundle": "build/bundle",
  "inventory": "bundle.json",
  "start": "run"
}
```

```sh
steamos build --dry-run   # if the project declares a build command
steamos build             # optional: build and stage
steamos stage             # verify an existing bundle locally
steamos lease take 'Demo1 deployment'
steamos deploy
steamos deploy --list
steamos lease release
```

`stage` checks the local inventory and needs no device. `deploy` uploads
and verifies a version, then switches the current version atomically. If a
published version needs to be reverted, take the lease and use
`steamos deploy --rollback`; it verifies a retained prior version. Use
`deploy --list` to inspect current and retained versions, running versions,
locks and partial uploads. After an interrupted deploy, inspect that state
before retrying. `deploy --abort-stale` has narrow lease and age checks;
see the reference before using it.

For a one-off title, use
`steamos title register Demo1 ./build --start ./run.sh`, then
`steamos title launch Demo1`. `title list` needs no lease;
register, launch and remove do. Registration mirrors the title directory,
so do not use it on a title managed by versioned deploys. Title launch
requires Steam running in Game Mode.

## Inspect a run

`steamos logs Demo1` reads the latest recorded launch window from the user
journal, with a Steam log fallback. The name selects a time window, so the
output can include other services. Use `--since EPOCH` and `--until EPOCH`
to set the device-clock window; if no launch is recorded, `--since` is
required. `steamos capture --out ./capture.png`
copies a gamescope screenshot in Game Mode. Neither needs a lease.

With the lease, use `steamos frametimes start` and `stop` around a MangoHud
scenario, then `steamos frametimes pull --out ./frametimes` to copy the
newest completed CSV session. MangoHud must already be active for the game;
successful logging control alone does not prove that frames were recorded.
Inspect the CSV and workload before drawing a performance conclusion.

A device woken over the network can show its PIN lock screen. Ask someone
at the device to unlock it before visual checks or performance work, and
check the display with a capture.

## Benchmarks, wake and checks

`steamos bench run -- COMMAND` runs a workload on the device under your
lease and saves its output and measurements. Optional controls include power
confirmation, CPU affinity, perf statistics, thermal sampling and governor
pinning. The lease normally inhibits sleep. Governor pinning is optional
and uses a root-owned helper at `/etc/agent-kit/steamos-governor` with a
narrow sudoers rule. The rule's filename must sort **after** SteamOS's
`wheel` rule, or the password rule wins. The owner installs it on the device;
see the [helper instructions](
../bin/steamos.md#optional-governor-helper-and-sudoers-rule).
Unpinned benchmarks need no sudo. Check the saved workload, thermals and
governor restoration before reporting a performance result.

`steamos wake --wait 60` sends a magic packet and waits for ssh. It needs a
configured MAC and a wired device with Wake-on-LAN enabled. A Steam Deck on
Wi-Fi does not wake this way.

Run `steamos doctor` for read-only local and project checks. Add
`--device NAME` or `--all` to check devices; without a selector it makes no
device connection. A benchmark-only project needs no `steamos.json`.
