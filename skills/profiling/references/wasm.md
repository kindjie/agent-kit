# Browser-hosted WebAssembly — CPU profiling recipes

Load when native code is compiled to Wasm and runs in a browser. The
symptom map in [`diagnosis.md`](diagnosis.md) and the hygiene rules in
[`interpretation.md`](interpretation.md) still apply; this file covers what
the browser changes. GPU work in the same page: [`gpu-web.md`](gpu-web.md).

Written **2026-09-03**; flags checked against the
[Emscripten `emcc` reference](https://emscripten.org/docs/tools_reference/emcc.html),
the Chrome DevTools Protocol reference, V8's flag definitions, and the
Playwright API. `[ref]` = documented, not re-run here.

## What is different

`source → LLVM → Wasm → engine baseline tier → optimizing tier → CPU`.
Native-build profiles do not transfer: the engine, its tiers, and the
browser's scheduler are part of the measurement. Record browser and
version, engine, OS, CPU, toolchain and version (`emcc --version`),
optimization and LTO flags, threading and worker count,
`crossOriginIsolated`, SIMD enablement, and what symbol information the
build carries.

Keep three regimes apart and never merge them into one statistic:

| Regime | Measure | Trap |
|---|---|---|
| Startup | download, compile, instantiate, worker creation, static init | only matters if asked; measure explicitly |
| Tier-up | successive windows of the same workload | a fixed warmup count proves nothing; watch for the timing to stabilize and keep the window series (`1–10: 3.8 ms, 11–50: 2.6 ms, 51–500: 1.7 ms` is tiering evidence, not noise) |
| Steady state | batches after stabilization, median + spread | background tabs are throttled; headless numbers are relative, validate headed |

## Wall-clock timing

`performance.now()` around a batch of repetitions, never around one tiny
call. Resolution is coarsened: 100 µs by default, 5 µs when
`crossOriginIsolated` is true `[ref]`, so record isolation state and size
batches so quantization is negligible. Consume every result so the
compiler cannot drop the work.

## Build configurations

| Config | Flags | Use |
|---|---|---|
| release | production `-O` level | the benchmark of record |
| profile | release flags + `--profiling-funcs` (names only) or `--profiling` (names, readable JS) | sampling profiles with real function names |
| debug | `-g` / `-gseparate-dwarf=FILE`, `-gsource-map`, `--emit-symbol-map` | source-level inspection; not a timing build |

`--profiling-funcs` keeps the Wasm name section and otherwise minifies as
normal `[ref]`. A DWARF build may change the pipeline; never quote its
timings as shipping timings. Symbol maps translate function indices in an
anonymous profile.

## Chromium — autonomous path

Through Playwright on Chromium, `browserContext.newCDPSession(page)` gives
the DevTools Protocol `[ref]`:

```js
const cdp = await context.newCDPSession(page);
await cdp.send('Profiler.enable');
await cdp.send('Profiler.setSamplingInterval', { interval: 100 }); // µs
await cdp.send('Profiler.start');
await page.evaluate(() => runBenchmark({ iterations: 500 }));       // JSON out
const { profile } = await cdp.send('Profiler.stop');        // .cpuprofile
```

Save the raw profile and read self and inclusive samples, hot paths,
Wasm↔JS transitions, and revision-to-revision deltas. Threaded builds put
the work on worker targets: attach to them (CDP `Target.setAutoAttach`)
and profile each; a sparse main-thread profile is not low utilization.
For *when and why* code runs (rAF, layout, tasks, workers, present) take
a Chrome trace with `browser.startTracing` as in `gpu-web.md`; sampling
says *where*, tracing says *when*. Automate the whole loop: launch, load
the benchmark URL, warm, start capture, fixed workload, stop, save
artifacts, parse.

**Perturbation.** V8 tiers Wasm down to a debuggable baseline while
DevTools is attached and re-tiers when a profile starts `[ref]`; never
quote timings taken while paused or stepping. Whether a scripted CDP
session counts as attached is unverified, so validate against a known
case: run the page's own `performance.now()` batch with and without the
session open, and if the attached run is slower, the profile is not
representative. `Profiler.startPreciseCoverage`
"prevents running optimized code" `[ref]`, so coverage and performance
never share a run.

## Firefox and Safari

Firefox Profiler samples all threads with JS and Wasm frames, exports
profiles, and doubles as an analysis UI: importing `perf script` output
is documented, and a Chrome-trace importer exists in its source `[ref]`.
Safari Web Inspector Timelines is GUI-first; treat it under the
GUI capability rules and keep any export it offers. Hot spots differ
between V8, SpiderMonkey, and JavaScriptCore, and so do their tiering
schedules, so a V8 warmup recipe is not a JSC warmup recipe.

## Threads

Emscripten pthreads are Web Workers over `SharedArrayBuffer`, which needs
COOP/COEP headers; check `crossOriginIsolated` at runtime instead of
trusting deployment `[ref]`. A worker created without a pool starts only
after returning to the event loop; `-sPTHREAD_POOL_SIZE=N` pre-creates
workers before `main` `[ref]`. `pthread_join` or condition waits on the
main thread busy-wait and can deadlock with proxied calls `[ref]`: when a
profile shows idle workers and a blocked main thread, fix the
synchronization design before touching worker code.

Sweep thread count 1, 2, 4, … `navigator.hardwareConcurrency` and report
T(N), speedup, efficiency, and main-thread frame impact; rendering, audio,
the GPU process, JIT threads, and the OS share the cores, so the best N
is often below the logical count. Separate worker startup latency from
steady-state throughput and prewarm a job system before measuring it.

## Boundary, memory, SIMD

- **JS↔Wasm boundary.** Count boundary calls, bytes copied, callbacks,
  and worker messages per frame. Many short Wasm frames between JS frames
  is choreography cost, not slow Wasm; batch before optimizing a function.
- **Placement.** Keep simulation and other bulk compute off the main
  thread; report compute throughput and main-thread responsiveness
  together, and do not trade frames for a small throughput gain.
- **Memory.** Browser heap tools see one linear `WebAssembly.Memory`.
  When samples land in `malloc`, `memcpy`, growth, or serialization, use
  `--memoryprofiler`, `mallinfo()`, or app counters for allocation count
  and bytes, peak heap, `memory.grow` events, and bytes copied `[ref]`.
- **SIMD.** Compare scalar and SIMD builds under one browser and warmup;
  confirm vector instructions survived with `wasm-objdump -d`; final
  instruction selection is still the engine's.

## Linux escalation

When browser sampling cannot separate engine from application, launch
Chromium with `--js-flags='--perf-prof --interpreted-frames-native-stack'`
and use Linux `perf` 5 or newer with `perf inject --jit` `[ref]`.
`--perf-prof-annotate-wasm` exists but V8 labels it experimental; probe
the installed build and never make it a baseline. Read the result with
[`x86-linux.md`](x86-linux.md).

## Instrumentation

Do not force a native profiling library into the browser target; use
browser sampling plus application markers unless the library documents
browser support. Emscripten's `--tracing` API (frames, tasks, marks,
allocations) and the `--cpuprofiler`, `--memoryprofiler`, and
`--threadprofiler` page overlays exist `[ref]`; the overlays are GUI
evidence unless their data is exported.

## Routing and validation

```text
structured Wasm benchmark
  ├─ startup regression → compile / instantiate / worker-start timing
  └─ runtime regression → browser CPU sampler
       ├─ Wasm hot     → names, source map or DWARF, emitted Wasm, threads
       ├─ JS boundary  → boundary counters, batching
       └─ browser work → trace timeline
```

The question is which layer owns the cost: algorithm, compiler lowering,
engine codegen, memory, scheduling, synchronization, boundary crossings,
browser APIs, or browser runtime. Validate a win in every engine that
matters and label it (universal, V8-only, JSC regression, threaded-only,
SIMD-only, startup-versus-steady-state); never hide an engine regression
in an aggregate.

---
→ Symptom map: [diagnosis](diagnosis.md) ·
Read it right: [interpretation](interpretation.md)
