---
name: steamos
description: >-
  Work on a SteamOS device (Steam Deck, Steam Machine) shared with other
  agents and people: check its status, take and release the device lease,
  and hand off what needs a person at the device. Use before deploying,
  launching, benchmarking or changing anything on such a device, and when
  a task mentions a Steam Deck, Steam Machine or SteamOS target.
---

# SteamOS devices

Use the `steamos` command (see `steamos.md` beside it in agent-kit's
`bin/`). It reaches devices over ssh and changes nothing on them except
the lease, its log (`~/.agent-kit-steamos-lease.log` on the device) and,
when asked, Valve's helpers in `~/devkit-utils`.
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

Reading status needs no lease. When `status` says Valve's
`devkit-utils` are missing or not at the pinned commit and the work needs
them, run `steamos devkit install` while holding the lease; it replaces
the helpers another agent's title may be using.

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
