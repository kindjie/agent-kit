# GPU diagnosis — rendering, compute, and CPU↔GPU scheduling

**Start here for GPU work.** Platform-independent: what the agent may claim,
how to localize the slow pass, and how to classify the bottleneck. It links
to capture guides and repeats nothing from them:
- **Capture the signal:** [`gpu-macos.md`](gpu-macos.md) ·
  [`gpu-linux.md`](gpu-linux.md)
- **Measurement hygiene** (median + spread, fixed workload, proxies are not
  results) is in [`interpretation.md`](interpretation.md) and applies as-is.

Written **2026-09-03**. Principle, not version-bound.

## Contents

- [Capability boundary](#capability-boundary)
- [Workload and metrics](#workload-and-metrics)
- [Localize before escalating](#localize-before-escalating)
- [Bottleneck classification](#bottleneck-classification)
- [Escalation path](#escalation-path)
- [Report and handoff](#report-and-handoff)

## Capability boundary

Classify each step before running it, from evidence on the host: target GPU
present, workload buildable and runnable, and the tool found by a probe
(`command -v`, `xcrun --find`) rather than assumed from the OS version.

| Class | Meaning | Typical steps |
|---|---|---|
| AUTONOMOUS | agent runs it and reads the result | instrumentation, benchmarks, timestamp queries, Tracy CSV, `xctrace export`, `gpudebug`, `ncu`/`nsys` |
| CAPTURE-ONLY | agent produces the artifact but cannot read it | `.rgp`/`.rmv`/`.rra`, `.gputrace` without `gpudebug`, most RenderDoc views |
| GUI | needs an interactive profiler and the environment provides computer use | Xcode GPU profiler, RGP, Instruments |
| UNAVAILABLE | no target GPU | prepare harnesses, commands, and hypotheses only |

**Claim rule (once, applies everywhere).** Occupancy, bandwidth, cache
behaviour, stalls, utilization, frame time, and CPU↔GPU overlap are
*measured* values. Report them only from output the agent read. Source
inspection yields a hypothesis, never a measurement. When a step is
CAPTURE-ONLY, stop at the artifact and hand off (see
[Report and handoff](#report-and-handoff)).

**Confidentiality.** `.gputrace`, `.rgp`, and `.rdc` captures embed shader
source and resource contents. The upload rule in `SKILL.md` applies.

## Workload and metrics

Record with every result: GPU, OS, driver/runtime, build configuration,
resolution and quality settings, scene or workload, warmup, sample count.
GPU clocks and thermal state drift far more than CPU clocks between runs, so
also record power/performance mode and check for throttling before trusting
a delta; warm shader caches and pipelines unless startup is the target.

Primary metrics, per domain:

| Domain | Primary (must improve) | Explanatory only |
|---|---|---|
| Rendering | GPU frame time p50/p95/p99, CPU submission time, target pass time | occupancy, bandwidth, draw/dispatch/barrier counts, utilization |
| Compute | useful items/s (tiles, cells, particles, bytes), dispatch time p50/p95 | instruction count, register count, cache hit rate |

FPS hides timing differences; report time. Normalize where workload size can
move (ns/tile, bytes/item, dispatches/frame) to separate algorithmic wins
from smaller inputs. A change that improves an explanatory metric but not a
primary one is not an optimization.

## Localize before escalating

0. **Resolve the backend chain.** Abstraction, API, translation layer,
   native API. Through sokol, raylib, or OpenGL, load
   [`gpu-backends.md`](gpu-backends.md); in a browser or WebGPU runtime,
   [`gpu-web.md`](gpu-web.md). They decide which capture guide applies.
1. **Name the work.** Stable semantic zone names (`render/depth`,
   `sim/fluid/advect`) on GPU passes and on the CPU frame (simulation, prep,
   command generation, submission, present). Emit them through Tracy and the
   API label extension (`VK_EXT_debug_utils`, Metal debug groups / signposts)
   so native traces carry the same names.
2. **Timeline first.** Tracy answers the routing questions: CPU-side or
   GPU-side; GPU starved by command generation or CPU blocked on the GPU;
   graphics/compute overlapping or serialized; which pass dominates; which
   frames spike; did a change merely move work. Capture headless with
   `tracy-capture -o run.tracy` and read with `tracy-csvexport`; if these
   are absent, add API timestamp queries (Vulkan timestamp / pipeline
   statistics queries, Metal GPU start/end times) and emit JSON per run.
3. **CPU-side result** → [`diagnosis.md`](diagnosis.md), the GPU is not the
   problem. **GPU-side** → classify below, then escalate to the platform
   profiler only for the localized pass.

Do not touch a shader before its pass is shown to dominate elapsed time.

## Bottleneck classification

Choose from measurements, never from code reading.

| Bottleneck | Signal | Confirm ↔ rule out | Remedy class |
|---|---|---|---|
| CPU submission | GPU idle gaps; passes fast but submitted late | render-thread time ≥ GPU time; command-gen dominates CPU zones | batching, fewer state changes / descriptor updates, pipeline caching, GPU-driven submission |
| Compute-bound | high ALU utilization, bandwidth well below peak | counter view: ALU busy, memory unit not saturated | algorithmic cuts, precision, transcendental count, divergence, specialization |
| Bandwidth-bound | memory unit saturated, ALU low | bytes/frame near peak; cache-miss counters high | layout (SoA, packing), narrower formats, compression, fewer intermediates, tile/reuse |
| Latency / occupancy | long-latency ops with few active waves | occupancy limited by registers or shared memory; stalls unhidden | register pressure, workgroup size, shared memory, shorter dep chains |
| Synchronization | bubbles at barriers, fence and queue waits | timeline gaps coincide with barriers/fences | remove, narrow, reorder, or split dependencies; async compute |
| Overdraw / fragment | fragment time scales with resolution, not geometry | heatmap or resolution sweep | depth prepass, ordering, occlusion, cheaper fullscreen passes |
| Tiny work | many small draws/dispatches; front-end overhead | per-draw cost flat, count high | batching, indirect, fusion; re-measure, merging can hurt locality |

Occupancy is never the goal; keep an occupancy change only when elapsed
time improves.

## Escalation path

```text
reproducible benchmark → timestamps + Tracy
  ├─ CPU-side → diagnosis.md (CPU profiler)
  └─ GPU pass localized
       ├─ macOS  → gpu-macos.md  (xctrace / gpudebug / Xcode GUI)
       ├─ RADV   → gpu-linux.md  (counters → .rgp → RGP GUI)
       └─ NVIDIA → gpu-linux.md  (ncu / nsys / Nsight GUI)
correctness: Metal → Xcode GPU debugger · Vulkan → RenderDoc
memory:      Metal → Xcode memory tools · RADV → .rmv
```

For a regression: same hardware and workload on both revisions, enough
samples, localize the changed pass on the timeline, read the diff for that
pass, then confirm by revert, bisect, or targeted change. Do not profile
subsystems the timeline has already cleared.

## Report and handoff

Every investigation ends with: **finding** (what dominates, or "insufficient
evidence"); **evidence**, each item tagged MEASURED, INFERRED, or REQUIRES
GUI; **change**; **result** as before → after on primary metrics with
spread; **correctness** (output hash or image diff, workload equivalence);
**remaining bottleneck**; **artifacts** (`.tracy`, `.gputrace`, `.rgp`,
`.rmv`, `.rdc`, JSON) with paths; **reproduction** commands.

Stop when the target hardware is absent, the next evidence is GUI-only and
no GUI access exists, noise exceeds the suspected gain, correctness cannot
be verified, or the target is met. A GUI handoff names the capture, the
exact event to open, the metrics to read, and the decision they gate:

```text
captures/frame-300.rgp → visibility dispatch (event #41)
read: wave occupancy, VALU vs memory-unit busy, bubble before barrier #17
gates: data-layout experiment vs workgroup-size experiment
```

---
→ Capture: [macOS](gpu-macos.md) · [Linux](gpu-linux.md) ·
Read it right: [interpretation](interpretation.md)
