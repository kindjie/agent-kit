# GPU backends behind abstractions — sokol, raylib, OpenGL

Load after [`gpu-diagnosis.md`](gpu-diagnosis.md) has named the workload,
when a graphics abstraction or OpenGL sits between the code and the GPU.
Browser paths are in [`gpu-web.md`](gpu-web.md).

Written **2026-09-03**; API names checked against upstream
[`sokol_gfx.h`](https://github.com/floooh/sokol/blob/master/sokol_gfx.h)
and `rlgl.h` headers on that date. `[ref]` = documented, not re-run here.

## Resolve the backend chain first

Never pick tools from the library's name. Record every layer that can be
identified (`application → abstraction → API → translation layer → native
API → GPU`), profile at the highest layer that yields reliable timings, and
escalate downward only for evidence that layer cannot give. A native
profiler does not necessarily see a translated workload just because it
ends in Metal or Vulkan; probe.

| Abstraction | Resolve with | Then use |
|---|---|---|
| sokol_gfx | `sg_query_backend()`: GLCORE, GLES3, D3D11, METAL_*, WGPU, VULKAN, DUMMY | [`gpu-macos.md`](gpu-macos.md), [`gpu-linux.md`](gpu-linux.md), [`gpu-web.md`](gpu-web.md), or the GL section below |
| raylib / rlgl | always OpenGL (`rlGetVersion()`) unless the project swapped the backend | GL section below, plus the batching rules; never the Metal or RGP path directly |
| direct OpenGL / GLES | `glGetString(GL_VERSION)`, driver, GPU, compositor | GL section below |

## sokol_gfx

- **Names.** Every `*_desc` has a `label`; set it on passes, pipelines,
  buffers, and images. `sg_push_debug_group` / `sg_pop_debug_group` map to
  Metal debug groups and to trace hooks; other backends get no native
  label from them.
- **API-level counts.** `sg_enable_stats()` then `sg_query_stats()` per
  frame: passes, pipeline and binding applies, draws, plus backend-specific
  counters. These describe what the app asked sokol to do, never GPU time.
- **Trace hooks.** Define `SOKOL_TRACE_HOOKS` in the profiling build and
  `sg_install_trace_hooks()` to wrap every call with Tracy zones or to log
  submission boundaries.
- **Compute.** Check `sg_query_features().compute` at runtime. The header
  lists compute passes for Metal, D3D11, desktop GL 4.3 (not macOS GL),
  GLES 3.1+, and WebGPU; the newer Vulkan backend was not on that list at
  writing. Record the sokol revision with any compute benchmark.

## raylib / rlgl

`Draw*()` calls append to an internal batch (default 8192 elements, 256
draw calls) that flushes on texture or mode change, overflow, or end of
frame, so one high-level call is not one GPU draw. Attribute a GPU interval
to a raylib function only when the batch boundaries make that valid.

`rlDrawRenderBatchActive()` forces a flush and creates a diagnostic
boundary at the cost of changing the workload: tag such results
instrumentation-perturbed, use them only to localize, remove the flush, and
re-measure before judging an optimization. Never keep extra flushes for
profiler convenience unless their production cost is separately measured.

Tracy's `TracyOpenGL.hpp` GPU zones need GL query functions loaded in the
app's translation unit, and raylib loads GL through its own bundled
loader, so test the integration on the exact raylib, Tracy, and platform
combination instead of assuming it. CPU-side Tracy zones always work.

## OpenGL / GLES

- **Timing.** GPU timer queries: `glQueryCounter(GL_TIMESTAMP)` pairs or
  `GL_TIME_ELAPSED` (`ARB_timer_query`, core since GL 3.3, present in
  macOS GL 4.1); on GLES, `EXT_disjoint_timer_query`, and discard samples
  when `GPU_DISJOINT_EXT` is set. Read results asynchronously. CPU time
  around `glDraw*` or `glDispatchCompute` is submission cost, not GPU
  time; never add `glFinish` or fences just to get a number unless
  synchronization cost is the question.
- **Record.** GL/GLES version, driver, GPU, window system and compositor,
  extensions used.
- **Escalation, Linux.** RenderDoc handles GL 3.2+ core profiles for
  correctness `[ref]`; `apitrace` captures and replays GL streams. For AMD
  counters, run the app through Zink (`MESA_LOADER_DRIVER_OVERRIDE=zink`)
  so `MESA_VK_TRACE=rgp` applies `[ref]`; this swaps the driver under
  test, so use it to localize, then confirm on the real driver.
- **Escalation, macOS.** GL is deprecated and Apple's GPU tools attribute
  Metal work, not GL calls. Treat native inspection as CAPTURE-ONLY at best
  and rely on timer queries plus Metal System Trace GPU intervals.

---
→ Native capture: [macOS](gpu-macos.md) · [Linux](gpu-linux.md) ·
Browser: [gpu-web](gpu-web.md)
