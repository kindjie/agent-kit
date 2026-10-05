---
name: profiling
description: >-
  Collects or interprets a performance profile: maps a symptom to a signal,
  runs the profiler on Apple Silicon macOS or x86 Linux, and reads results
  without overclaiming cause. Use for NUMA, topology-sensitive core/cache/
  socket scaling, parallel cliffs, page placement, coherence; GPU frame time,
  passes, shaders, GPU memory, CPU↔GPU sync on Metal, Vulkan/RADV, SteamOS,
  NVIDIA, OpenGL, WebGPU, WebGL, directly or through sokol or raylib; or
  browser WebAssembly tier-up, workers, JS↔Wasm boundaries, and sampling
  profiles.
---

# Profiling Reference

Load a reference only when you reach that step.

Profiling can attach to processes, expose runtime data, open GUI applications,
or change host-wide performance controls. Stay within the user's authority:
prefer read-only capture, request approval before privileged or host-wide
changes, record and restore changed state, and obtain approval before opening
an interactive viewer when the environment requires it. Do not upload profiles
containing private symbols or data without explicit authorization.

Start with [`diagnosis.md`](references/diagnosis.md) to map symptoms to
bottlenecks and signals. Read
[`interpretation.md`](references/interpretation.md) before drawing conclusions
from measurements. Use the capture guide for the host under test:

- [`m3-macos.md`](references/m3-macos.md) for Apple Silicon / macOS.
- [`x86-linux.md`](references/x86-linux.md) for x86 Linux.

For GPU symptoms (frame time, a render or compute pass, CPU waiting on the
GPU, GPU memory), start at [`gpu-diagnosis.md`](references/gpu-diagnosis.md)
instead: it fixes what may be claimed from each tool class, then routes to
[`gpu-macos.md`](references/gpu-macos.md),
[`gpu-linux.md`](references/gpu-linux.md), and the abstraction and browser
references. For CPU work running as WebAssembly in a browser, read
[`wasm.md`](references/wasm.md) before capturing.

When the host exposes multiple locality domains and a memory-bound or
parallel-scaling symptom points at topology, load
[`numa.md`](references/numa.md) — prove NUMA is the mechanism before writing
NUMA-specific code.
