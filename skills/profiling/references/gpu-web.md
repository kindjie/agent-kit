# GPU profiling in the browser — WebGPU and WebGL

Load after [`gpu-diagnosis.md`](gpu-diagnosis.md) when the workload runs
in a browser or a WebGPU runtime. The CPU side of a browser workload
(Wasm tiers, workers, the JS↔Wasm boundary) is in [`wasm.md`](wasm.md).
Written **2026-09-03**; `[ref]` = documented, not re-run here.

## Identify the implementation

Record browser and version, WebGPU or WebGL implementation (Dawn in
Chromium, wgpu in Firefox, WebKit's in Safari; Dawn or wgpu natively),
the native backend underneath, GPU, OS, and the adapter's features and
limits. Identical WGSL or GLSL does not perform identically across
implementations; compare within one.

## Separate browser cost from GPU cost

```text
JS / Wasm → command construction → queue submit → GPU execution → compositor
```

Browser tracing covers the first two and the last (JS, garbage collection,
workers, command generation, GPU-process scheduling); only GPU timestamp
queries cover execution. Do not call a frame-time regression GPU-bound
until the layers are separated.

**Autonomous harness.** Drive the page with Playwright: launch Chromium,
run the benchmark, read its JSON through `page.evaluate`, and wrap the run
in `browser.startTracing(page, {path})` / `browser.stopTracing()` for a
Chrome trace of the CPU side `[ref]`. A Playwright MCP server, when one is
present, can do the same interactively. A scripted Chromium gets the
quantized timestamps described below; no verified launch switch lifts
that, only a human-set `chrome://flags` entry.

## WebGPU timing

Request the `timestamp-query` feature in `requestDevice` after checking
`adapter.features`; create a `GPUQuerySet` of type `timestamp`, attach
`timestampWrites` to each meaningful render or compute pass
(`render/opaque`, `compute/visibility`), `resolveQuerySet` into a buffer,
and read it with `mapAsync` on a later frame. Never block the measured
frame on the result.

Precision: Chrome quantizes timestamps to 100 µs for timing-attack
mitigation; `chrome://flags/#enable-webgpu-developer-features` disables the
quantization for development `[ref]`. So: repeated samples, pass-scale
intervals rather than tiny ops, no decisions from differences near the
resolution, and record whether the feature was available plus enough raw
samples to see the quantization. If the feature is absent, downgrade the
GPU evidence explicitly; CPU submission time is not GPU time.

## WebGL timing

WebGL 2: `EXT_disjoint_timer_query_webgl2`; WebGL 1:
`EXT_disjoint_timer_query`, widely removed. Availability varies by browser
(Safari gates it behind a setting) `[ref]`, so capability-test with
`getExtension`, collect results asynchronously, and discard any sample
whose interval had `GPU_DISJOINT_EXT` set. `performance.now()` around draw
calls is submission time.

Engines on top of WebGL batch like rlgl does: profile batch and pass
boundaries, and add no `flush`, fence, or readback for profiler
convenience unless the perturbation is accounted for. Spector.js explains
frame structure, state, shaders, and resources; it is not a timing source.

## Native escalation

A browser's GPU process is sandboxed; capturing its Metal or Vulkan work
is an implementation-specific experiment, not a WebGPU property. Probe
before relying on it, and when a path works, record implementation, tool,
ship vehicle, minimum version, release state, and fallback. A beta or
flag-gated facility never becomes a baseline requirement.

---
→ Native capture: [macOS](gpu-macos.md) · [Linux](gpu-linux.md) ·
Read it right: [interpretation](interpretation.md)
