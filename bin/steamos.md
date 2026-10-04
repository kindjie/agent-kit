# steamos

Share and inspect SteamOS devices, such as a Steam Deck or a Steam
Machine, from agents and people working on the same machine. It talks to
each device over ssh; nothing is installed on the device.

```sh
steamos status                      # device facts and who holds it
steamos --device NAME status --json
steamos lease take 'deploy build 12' [--hours N]
steamos lease renew [--hours N]
steamos lease show | check | release
steamos lease break --reason 'why'
```

Exit codes: 0 success; 1 refused (the lease is held, or not yours); 2
usage or configuration; 3 the device could not be reached.

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

## Status

`status` is read-only. It reports the SteamOS version, build and update
channel, the model, free space in the device home, power, whether
Valve's `devkit-utils` are present, and the lease. Devices may run
different SteamOS channels and versions; `status` reports each device's
rather than assuming one. Valve's `steamos-get-status` is not used,
because on a Steam Deck it also turns off wireless power management.

## Notes

- On macOS, a Python interpreter may be blocked from reaching the local
  network by the Local Network privacy setting while system tools are
  not. This command reaches devices only through `ssh`.
- A Steam Deck on Wi-Fi does not reliably wake from sleep for a
  Wake-on-LAN packet; a wired device usually can, once Wake-on-LAN is
  enabled on it.
