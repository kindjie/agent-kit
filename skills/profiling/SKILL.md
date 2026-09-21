---
name: profiling
description: >-
  Collect or interpret a performance profile — map a symptom to the signal
  worth capturing, run the platform profiler on Apple Silicon macOS or x86
  Linux, and read the numbers without overclaiming cause. Covers NUMA and
  topology-sensitive scaling: parallel cliffs at core, cache, or socket
  boundaries, page placement, and coherence traffic. GPU work — frame
  time, pass cost, shader bottlenecks, GPU memory, CPU↔GPU sync — on
  Metal, Vulkan/RADV, SteamOS, or NVIDIA, directly or through sokol,
  raylib, OpenGL, WebGPU, or WebGL. WebAssembly CPU profiling in the
  browser: tier-up, workers, the JS↔Wasm boundary, sampling profiles.
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
