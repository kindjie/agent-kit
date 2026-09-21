# Deep profiling on Apple Silicon (M-series / macOS) — CLI recipes

Command-first guide for profiling native C/C++ on Apple Silicon **without the
Instruments GUI** where possible. Generic; not project-specific. **Not sure what
to capture? Start at [`diagnosis.md`](diagnosis.md)** (symptom → cause → which
signal). Read [`interpretation.md`](interpretation.md)
before drawing conclusions from the numbers.

## Contents

- [Versions](#versions)
- [Platform facts](#key-facts-about-this-platform)
- [Build](#build-for-profiling)
- [Sampling](#recipe-1--sampling)
- [Cycles and instructions](#recipe-2--cycles-and-instructions)
- [CPU counters](#recipe-3--cpu-counters)
- [Code generation](#recipe-4--code-generation)
- [Compiler remarks](#recipe-5--compiler-remarks)
- [Correctness tools](#recipe-6--correctness-and-memory-tools)
- [Instruments](#when-the-cli-isnt-enough--manual-instruments-gui-checklist)
- [Optimization loop](#optimization-loop)

## Versions

Re-verify after toolchain drift; xctrace schemas and its CLI can change between
Xcode releases.
- Written **2026-05-30**, host: **macOS 26.5 (25F71), Apple M3 Max (arm64)**.
- AppleClang **21.0.0** (clang-2100.1.1.101); Xcode **26.5 (17F42)**;
  `xctrace` **16.0 (17F42)**; `samply` **0.13.1**; `otool` from Xcode CLT.
- `[verified 2026-05-30]` = run on this host. `[ref]` = documented, not re-run.

## Key facts about this platform
- **No `perf` / `perf_event_open`.** Linux's interface does not exist on macOS.
- **The PMU is still reachable, no root**, two ways: `proc_pid_rusage` (raw
  cycles+instructions → IPC) and `xctrace` "CPU Counters" (top-down slot
  breakdown). `kperf` (raw event selection) needs root; dtrace `cpc` is blocked
  by SIP. Don't go there unless you have sudo and a reason.
- **Single memory domain.** Apple Silicon Macs present one uniform memory
  system and macOS exposes no NUMA placement interface — make no NUMA claims
  here; [`numa.md`](numa.md) applies to Linux/Windows hosts.
- The arm64 convention uses `x29` as the frame pointer, which helps unwinding,
  but optimized code can omit a complete frame chain. Keep
  `-fno-omit-frame-pointer` for profiling builds and verify symbol quality.

## Build for profiling
```sh
cmake -S . -B build-profile -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DCMAKE_CXX_FLAGS="-O3 -g -fno-omit-frame-pointer -mcpu=native"
cmake --build build-profile -j
```
`-g` for symbols; `-mcpu=native` to tune. Keep it a separate dir from your
release build. Make the target run **several seconds dominated by the kernel**
you care about (crank iterations) so sampling/counters have signal.

---

## Recipe 1 — Sampling

Verified 2026-05-30.
```sh
samply record --save-only -o /tmp/p.json -- ./build-profile/BIN ARGS   # rate default 1000 Hz
samply load /tmp/p.json            # opens Firefox Profiler (interactive)
```
- `--save-only` = headless capture; `--rate 2000` for tighter loops.
- Opening the interactive viewer requires user approval when the agent is
  operating a GUI or browser.
- Stock fallback: `/usr/bin/sample PID 10 1 -file /tmp/s.txt` (attach, 10 s @ 1 ms).
- **Gotchas:** unsymbolicated → you'll see bare `0x…` offsets (need the `-g`
  build; tight inner loops can defeat the unwinder). Concentration *is* signal:
  "60% of samples in one ~256 B code range" = that's your hot loop.

## Recipe 2 — Cycles and instructions

Verified 2026-05-30. This method does not require root.
`proc_pid_rusage(RUSAGE_INFO_V4)` exposes `ri_cycles` + `ri_instructions` from
the PMU per process. Wrap the timed region:
```c
#include <libproc.h>
#include <unistd.h>
static void rd(uint64_t* c, uint64_t* i){
  struct rusage_info_v4 ri;
  proc_pid_rusage(getpid(), RUSAGE_INFO_V4, (rusage_info_t*)&ri);
  *c = ri.ri_cycles; *i = ri.ri_instructions;
}
/* uint64_t c0,i0,c1,i1; rd(&c0,&i0); ...work...; rd(&c1,&i1);
   cycles=c1-c0; instructions=i1-i0; IPC=(double)instructions/cycles; */
```
- **No root, no entitlement, no private framework.** This is one supported way
  to get IPC.
- **Whole-process** (all threads, user+kernel). The start→stop delta over a
  *single-threaded* region = that region's work; for parallel regions it's the
  summed work across threads. Idle thread-pool workers (blocked on a condvar)
  add ~0.
- Cache/branch misses are **not** available this way — use Recipe 3 for the
  bottleneck shape, or accept cycles+instructions only.

## Recipe 3 — CPU counters

Verified 2026-05-30 with the xctrace CPU Counters template.
```sh
xctrace record --template "CPU Counters" --output /tmp/cc.trace \
  --launch -- ./build-profile/BIN ARGS
xctrace export --input /tmp/cc.trace \
  --xpath '/trace-toc/run[@number="1"]/data/table[@schema="CounterMetricAggregatedForProcess"]' \
  > /tmp/cc.xml
```
Each row is a per-1ms `<uint64-array>` of four cycle components that sum to the
window. The exported schema does not name their semantics. Aggregate the
steady-state, skip the initialization ramp, and normalize only after validating
component identities with controlled phases.
- **No instructions-retired in this template** → get IPC from Recipe 2.
- **Component labels are inferred, not exported.** Disambiguate with a phase
  contrast: the **init phase** (first-touch of a large buffer = memory-bound) is
  back-end-heavy; the **kernel phase** (compute) is Useful-heavy. Whichever
  component dominates init = back-end stall.
- **Cross-validate inferred labels with controlled phases.** The exported
  schema does not identify the component semantics, and IPC is not a reliable
  conversion to a top-down slot percentage.

## Recipe 4 — Code generation

Verified 2026-05-30 for disassembly and auto-vectorization inspection.
```sh
otool -tvV ./build-profile/BIN | c++filt | sed -n '/yourSymbol/,/^[_a-zA-Z]/p'  # stock CLT
# otool shows MANGLED C++ names (hence c++filt). Preferred if installed:
#   brew install llvm; llvm-objdump --disassemble --demangle --line-numbers ./build-profile/BIN
```
Look for: **scalar where you expected vectors** — but note the compiler often
*already emits NEON* (`-mcpu=native` + `-O3`), so an explicit-SIMD path can be
redundant and slower (see interpretation doc); constant folding (`and w,#0x1f`
for `x%32`, `lsr` for `/32` — no `udiv`); unexpected `bl` calls (missed inlining)
in hot loops.

## Recipe 5 — Compiler remarks

Documented reference; not re-run for this guide.
```sh
cmake -S . -B build-remarks -DCMAKE_CXX_FLAGS=\
"-O3 -fsave-optimization-record -Rpass=loop-vectorize -Rpass-missed=loop-vectorize -Rpass-analysis=loop-vectorize"
cmake --build build-remarks -j 2>&1 | tee /tmp/remarks.txt
```
`.opt.yaml` lands next to each object. Actionable passes: `loop-vectorize`,
`slp-vectorize`, `loop-unroll`, `inline`. Ignore `gvn`/`licm` noise. Common
miss reasons: aliasing, unknown trip count, non-contiguous access, calls in loop.

## Recipe 6 — Correctness and memory tools

Documented reference; use for diagnosis because these tools perturb timing.
```sh
cmake -S . -B build-asan  -DCMAKE_CXX_FLAGS="-fsanitize=address -O1 -g"      # also =undefined, =thread
ASAN_OPTIONS=halt_on_error=1:abort_on_error=1 ctest --test-dir build-asan
# malloc: MallocStackLogging=1 MallocNanoZone=0 ./BIN & ; leaks <pid> ; malloc_history <pid> -allBySize
```
Sanitizer and LTO combinations depend on the toolchain and sanitizer; for
example, some control-flow instrumentation requires LTO. Use a project-supported
diagnostic build. `leaks`/`heap`/`malloc_history` may need a non-hardened local
binary (SIP). **These change timing — never quote their numbers as benchmarks.**

---

## When the CLI isn't enough — manual Instruments (GUI) checklist
xctrace records; you interpret in Instruments.app. Capture with
`xctrace record --template "<T>" --launch -- BIN ARGS`, then open the `.trace`.
Opening Instruments requires user approval when the agent is operating a GUI.
- **Time Profiler** → call tree, separate-by-thread, invert, self vs total. Look
  for unexpected time in alloc / string / logging / refcount / locks / syscalls.
- **Allocations** → sort by count and by bytes; allocations in hot loops;
  `std::string`/`vector`/`shared_ptr`/lambda churn.
- **VM Tracker** → resident/dirty growth after warmup; large virtual allocs.
- **System Trace / Thread States** → blocked threads, oversubscription, wakeups,
  lock contention, work bouncing across cores.

## Optimization loop
1. profile build → 2. baseline benchmark (median + p95) → 3. `samply` hot path →
4. Recipe 2/3 to classify the bottleneck (compute / back-end / front-end) →
5. disassembly + remarks on the hot code → 6. **one** change → 7. re-run same
benchmark, keep only if median AND p95 improve.

---
→ Interpreting any of these numbers: [`interpretation.md`](interpretation.md).
