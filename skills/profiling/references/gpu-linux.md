# GPU profiling on Linux / SteamOS — RADV, NVIDIA, RenderDoc recipes

Command-first. **Not sure what to capture? Start at
[`gpu-diagnosis.md`](gpu-diagnosis.md)** (capability classes, metrics,
bottleneck table). CPU-side capture is in [`x86-linux.md`](x86-linux.md).

## Contents

- [Probe](#probe)
- [Recipe 1 — application counters](#recipe-1--application-counters)
- [Recipe 2 — RADV captures](#recipe-2--radv-captures)
- [Recipe 3 — runtime telemetry](#recipe-3--runtime-telemetry)
- [Recipe 4 — SteamOS, Gamescope, Proton](#recipe-4--steamos-gamescope-proton)
- [Recipe 5 — NVIDIA](#recipe-5--nvidia)
- [Recipe 6 — RenderDoc](#recipe-6--renderdoc)

Written **2026-09-03**. Variables checked against the
[Mesa environment variables reference](https://docs.mesa3d.org/envvars.html),
the MangoHud README, the Proton README, and Valve's Steam Linux Runtime
bug-reporting guide; `[ref]` = documented, not re-run here.

## Probe

```sh
vulkaninfo --summary 2>/dev/null | grep -E 'driverName|deviceName'
for t in tracy-capture tracy-csvexport mangohud renderdoccmd ncu nsys; do
  printf '%s: ' "$t"; command -v "$t" || echo absent
done
```

RADV, NVIDIA, and Intel expose different escalation tools; the driver name
decides which recipes apply.

## Recipe 1 — application counters

The closed loop that stays AUTONOMOUS on every driver: Vulkan timestamp
queries per pass, pipeline statistics queries where supported, and
explicit counters (draws, dispatches, barriers, uploaded bytes, queue
waits), written as JSON per run and compared across revisions. Name passes
with `VK_EXT_debug_utils` labels so RGP and RenderDoc show the same names.
Prefer this over any overlay for quoted numbers.

## Recipe 2 — RADV captures

Capture is AUTONOMOUS when the workload runs; reading `.rgp`, `.rmv`, and
`.rra` needs the AMD GUI tools, so inspection is **CAPTURE-ONLY** unless
computer use exists. Files land in `/tmp` `[ref]`.

```sh
MESA_VK_TRACE=rgp MESA_VK_TRACE_FRAME=300 ./app          # one frame
MESA_VK_TRACE=rgp MESA_VK_TRACE_PER_SUBMIT=1 ./benchmark # compute-only
MESA_VK_TRACE=rgp MESA_VK_TRACE_TRIGGER=/tmp/trigger ./app
touch /tmp/trigger                                       # capture now
MESA_VK_TRACE=rmv ./app                                  # memory
MESA_VK_TRACE=rra ./app                                  # ray tracing
```

`MESA_VK_TRACE_FRAME` is ignored under `MESA_VK_TRACE_PER_SUBMIT`. Raise
`RADV_THREAD_TRACE_BUFFER_SIZE` if a capture truncates; per-submit buffers
do not auto-resize `[ref]`. Use `rmv` for VRAM growth, fragmentation,
residency, and resource lifetime; `rra` only when acceleration structures
or traversal are materially involved.

Handoff questions for RGP: dominant event; VALU vs memory bound; occupancy
and what limits it (VGPRs, LDS); latency hidden or not; barrier bubbles;
async compute overlap; wave imbalance. Answer none of them from the
uninspected file.

## Recipe 3 — runtime telemetry

MangoHud logs frametime, clocks, power, VRAM, load, and temperature to
CSV without a visible overlay `[ref]`:

```sh
MANGOHUD_CONFIG=output_folder=/tmp/mh,autostart_log=2,log_duration=30,log_interval=0 \
  mangohud ./app
```

Read the CSV; never quote the overlay. Clock and temperature columns are
the throttling check the workload record needs.

## Recipe 4 — SteamOS, Gamescope, Proton

The stack is `game → (Proton: Wine → DXVK or vkd3d-proton) → Steam Linux
Runtime container → Vulkan → RADV → Gamescope`. A Steam launch runs inside
that container, so a host-shell run is a different configuration: capture
RGP, MangoHud, and Tracy from the launch that reproduces the problem, and
compare the two only on purpose. Add to the workload record: Proton
version or native, Gamescope state, frame limiter, VRR, HDR, scaling, TDP
limit, manual clocks, performance profile, battery or mains.

- **Environment divergence.** Start Steam as
  `STEAM_LINUX_RUNTIME_LOG=1 steam`, reproduce, then read
  `var/slr-app*-*.log` and `VERSIONS.txt` in the runtime's install
  directory, `steamapps/common/SteamLinuxRuntime_*` under the Steam
  library; `PRESSURE_VESSEL_VERBOSE=1` adds detail `[ref]`. The system
  report `~/.steam/root/logs/steam-runtime-system-info-*.txt` is written by
  the Steam client's Runtime Diagnostics menu action, a user step `[ref]`.
  Read these before changing code for a "works outside Steam" report.
- **Proton.** `PROTON_LOG=1` (`PROTON_LOG_DIR` for the path) combines
  with the runtime log above; `PROTON_WAIT_ATTACH=1` pauses for a debugger
  and `PROTON_CRASH_REPORT_DIR` collects minidumps, per the Proton README
  `[ref]`. Log to localize, then disable logging for timing. A
  Proton-only regression is attributed at the closest layer where the
  native and Proton runs diverge, not to "Proton".
- **Frame limiters.** Gamescope, Steam settings, and VRR cap the frame
  rate outside the app; low GPU utilization under a cap is not a
  bottleneck. Control the cap for throughput work, restore it for
  shipping validation.
- **Presentation.** Gamescope composites, scales, limits, and may run its
  own async compute, so app GPU time is not displayed frame time. Measure
  the app (Recipe 1), then pacing with and without Gamescope, one variable
  at a time, before optimizing either side. Scheduling questions escalate
  to `ftrace`/`perf sched` and GPUVis with amdgpu tracepoints, read as in
  [`diagnosis.md`](diagnosis.md) off-CPU.

## Recipe 5 — NVIDIA

Compute kernels: `ncu` (Nsight Compute CLI) for per-kernel counters and
bottleneck sections, `nsys profile` + `nsys stats` for the timeline; both
export CSV, so they are AUTONOMOUS. Graphics: Nsight Graphics is GUI-first;
capture what its CLI allows and classify the rest CAPTURE-ONLY.

## Recipe 6 — RenderDoc

Correctness, not cost: pipeline state, descriptors, resource contents, draw
arguments, pixel history. `renderdoccmd capture` scripts the capture; the
Python API can read a `.rdc` headlessly for state and resource checks, but
most visual views are CAPTURE-ONLY. RenderDoc answers *what happened*; RGP
and Xcode answer *why it was expensive*.

---
→ Interpreting any of these numbers: [`interpretation.md`](interpretation.md).
