# GPU profiling on macOS / Apple Silicon — Metal capture recipes

Command-first. **Not sure what to capture? Start at
[`gpu-diagnosis.md`](gpu-diagnosis.md)** (capability classes, metrics,
bottleneck table). CPU-side capture is in [`m3-macos.md`](m3-macos.md).

## Contents

- [Versions and probe](#versions-and-probe)
- [Recipe 1 — Metal System Trace](#recipe-1--metal-system-trace)
- [Recipe 2 — programmatic .gputrace](#recipe-2--programmatic-gputrace)
- [Recipe 3 — CLI inspection, macOS 27](#recipe-3--cli-inspection-macos-27)
- [Recipe 4 — Xcode GPU profiler (GUI)](#recipe-4--xcode-gpu-profiler-gui)
- [Runtime HUD](#runtime-hud)

## Versions and probe

- Written **2026-09-03**, host: **macOS 26.6.2, Xcode 26.6**. On this line
  the only autonomous deep path is `xctrace`; `.gputrace` inspection needs
  the Xcode GUI.
- **macOS 27 / Xcode 27** (beta 8 / beta 6 at writing) add `gpucapture`,
  `gpudebug`, and `metalperftrace`. Never assume them from the version;
  probe, then classify:

```sh
for t in xctrace gpucapture gpudebug metalperftrace; do
  printf '%s: ' "$t"
  command -v "$t" || xcrun --find "$t" 2>/dev/null || echo absent
done
xctrace list templates | grep -i metal    # expect "Metal System Trace"
```

`[ref]` below = documented by Apple, not re-run on this host.

## Recipe 1 — Metal System Trace

CPU↔GPU scheduling: encoding, submission, GPU execution, waits, bubbles,
presentation, graphics/compute overlap. AUTONOMOUS through export; the
Instruments GUI is optional. Verified 2026-09-03 (Instruments 16.0).

```sh
xctrace record --template "Metal System Trace" --output /tmp/mst.trace \
  --time-limit 10s --launch -- ./app --benchmark
xctrace export --input /tmp/mst.trace --toc          # list tables
xctrace export --input /tmp/mst.trace \
  --xpath '/trace-toc/run[@number="1"]/data/table[@schema="metal-gpu-intervals"]'
```

Read the table of contents first; schema names change between Xcode
releases (same caveat as the CPU Counters recipe in `m3-macos.md`). Tables
seen on this host that answer the routing questions:
`metal-gpu-intervals` (GPU execution per command buffer),
`metal-application-command-buffer-submissions` and
`metal-command-buffer-completed` (CPU submit → GPU done),
`metal-application-encoders-list`, `display-vsyncs-interval` and
`displayed-surfaces-interval` (present), `gpu-performance-state-intervals`
and `device-thermal-state-intervals` (the clock/throttle check),
`metal-object-label` (your debug-group names). Look for: GPU idle while the
CPU encodes (submission-bound), CPU blocked in a wait (GPU-bound),
fragmented command buffers, serialized compute and graphics, upload
stalls, present delays.

## Recipe 2 — programmatic .gputrace

Deterministic capture beats interactive capture. In a profiling build, wrap
the known workload with `MTLCaptureManager` and write a `.gputrace`; expose
explicit triggers (`--gpu-capture-frame N`, `--gpu-capture-pass NAME`). The
process must run with `MTL_CAPTURE_ENABLED=1` `[ref]`. Capture only enough
work to answer the question.

Without Recipe 3 the result is **CAPTURE-ONLY**: report the path, the exact
command, the frame or pass, and the questions for the GUI.

## Recipe 3 — CLI inspection, macOS 27

`gpucapture` captures a `.gputrace` from a running process; `gpudebug`
opens it and navigates command buffers, encoders, draws and dispatches,
pipeline state, resource contents, and shader source from the shell `[ref]`.
Drive it non-interactively (scripted commands, `-q`), never as a REPL;
`man gpudebug` is the authoritative reference. Resource `fetch` needs a
replayer on the same GPU architecture the trace came from; browsing does
not `[ref]`.

`metalperftrace` reads a system-wide background recording of Metal
metrics, so it can look back after a run `[ref]`:

```sh
metalperftrace collect /tmp/perf --last 30m        # writes a .atrc
metalperftrace overview /tmp/perf/<name>.atrc --json --aggregate \
  --domain com.example.game                         # regression input
```

Confirm the window covers only your benchmark before quoting it.

Do not restate syntax here. Apple's Apache-2.0 `game-porting-skills`
plugin in
[apple/game-porting-toolkit](https://github.com/apple/game-porting-toolkit)
carries `using-gpucapture` and `using-gpudebug`; install it when the
probe finds the tools. Follow the evidence hierarchy: workload → command
buffer → encoder → draw/dispatch → shader or resource cause. Stop
narrowing once localized.

## Recipe 4 — Xcode GPU profiler (GUI)

Pass duration, occupancy, bandwidth, execution-unit utilization,
bottleneck classification, per-line shader cost, divergence, overdraw
heatmaps. GUI (needs approval and computer use) or CAPTURE-ONLY.

**Replay caveat.** Counter and per-shader numbers come from *replaying* the
capture pass by pass; elapsed time, overlap, and scheduling come from the
*concurrent* run (Recipe 1). Never read scheduling from replay timing or
microarchitectural cause from the concurrent timeline.

Correctness: the Xcode GPU debugger on the same `.gputrace`. Memory: Xcode
memory report on the capture.

## Runtime HUD

`MTL_HUD_ENABLED=1 ./app` overlays frame time, pacing, memory, and encoder
timing. It is visual; use it only when a human or computer use can read it,
and prefer logged timestamps for anything quoted.

---
→ Interpreting any of these numbers: [`interpretation.md`](interpretation.md).
