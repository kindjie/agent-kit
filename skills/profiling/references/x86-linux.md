# Deep profiling on x86 Linux — CLI recipes

Command-first guide for profiling native C/C++ on x86-64 Linux with `perf` and
the PMU. Generic; not project-specific. **Not sure what to capture? Start at
[`diagnosis.md`](diagnosis.md)** (symptom → cause → which signal). Read
[`interpretation.md`](interpretation.md) before
drawing conclusions from the numbers.

## Contents

- [Versions](#versions)
- [Verify counter access](#verify-counter-access-first)
- [Machine preparation](#machine-preparation)
- [Build](#build-for-profiling)
- [Sampling](#recipe-1--sampling)
- [Counters](#recipe-2--counters-and-ipc)
- [Top-down analysis](#recipe-3--top-down-analysis)
- [Code generation](#recipe-4--code-generation-and-diagnostics)
- [Remote hosts](#remote-or-cloud-hosts)

## Versions

Re-verify after toolchain drift: `perf` is tied to the kernel and PMU events are
microarchitecture-specific.
- Written **2026-05-30**. Reference host (bare-metal cloud instance):
  **Ubuntu 24.04, Clang 20.1.8, Intel Xeon Platinum 8481C (Sapphire Rapids,
  2×48c×2HT = 96 physical / 192 logical, 4 NUMA nodes)**, `perf_event_paranoid=1`.
- Fill your own: `uname -r` · `perf --version` · `cat /proc/sys/kernel/perf_event_paranoid`
  · `lscpu` (Model name / Core(s) per socket / Thread(s) per core / NUMA node(s)).
- `[verified 2026-05-30]` = the `perf_event_open` group below ran on the metal
  host. `perf`/`toplev` recipes are `[ref]` (standard, not re-run here).

## Verify counter access first

PMU availability varies by host, hypervisor, VM family, kernel policy, and
provider configuration. Some virtual machines expose hardware counters; others
return empty or `<not supported>` values while the benchmark still runs.
Confirm on the actual host before trusting a counter result:

```sh
perf stat -e cycles,instructions -- true
```

If counters are unavailable, choose a supported VM family or an authorized
physical host, or use signals that do not require the PMU. Check the provider's
current documentation rather than relying on a hard-coded instance list.

## Machine preparation

Pinning frequency, disabling boost or SMT, and changing
`perf_event_paranoid` alter host-wide state and may affect other workloads.
Run privileged commands only with explicit authorization. Use the controls
documented for the actual CPU driver and distribution, record the original
values and exact commands, prefer temporary controls, and restore the state
after capture.

```sh
cpupower frequency-info
cat /proc/sys/kernel/perf_event_paranoid
lscpu
```

These commands inspect rather than change the relevant state. If comparable
runs require controls, document the platform-specific setup and restoration.
Pin work to a core with `taskset -c 2 BIN`; pin memory near it on NUMA with
`numactl --cpunodebind=0 --membind=0 BIN`. Diagnosing a suspected NUMA
problem (topology, placement, coherence, policies): [`numa.md`](numa.md).

## Build for profiling
```sh
clang++ -O3 -g -fno-omit-frame-pointer -march=native ...
```

Record the exact compiler and flags used for every comparable run.

---

## Recipe 1 — Sampling

Documented reference; not re-run for this guide.
```sh
perf record -g --call-graph=dwarf -- ./BIN ARGS     # -g needs frame ptrs or dwarf
perf report --stdio | head -40                      # or interactive: perf report
# flamegraph: perf script | stackcollapse-perf.pl | flamegraph.pl > fg.svg
```

## Recipe 2 — Counters and IPC

Verified 2026-05-30 via `perf_event_open`.
Quick, whole-run:
```sh
perf stat -e cycles,instructions,L1-dcache-load-misses,LLC-load-misses,branch-misses -- ./BIN ARGS
# IPC = instructions / cycles
```
**Per-region, in-process** (the robust way — wrap only the kernel, not init):
open a `perf_event_open` group, leader = cycles, attach the rest, one atomic
read. Distilled pattern:
```c
struct perf_event_attr a={.type=PERF_TYPE_HARDWARE,.size=sizeof(a),
  .config=PERF_COUNT_HW_CPU_CYCLES,.disabled=1,
  .exclude_kernel=1,.exclude_hv=1,.read_format=PERF_FORMAT_GROUP};
int lead=syscall(__NR_perf_event_open,&a,0,-1,-1,0);   // pid=0 self, cpu=-1 any
// attach instructions / L1D-read-miss / LLC-read-miss / branch-misses with group_fd=lead
ioctl(lead,PERF_EVENT_IOC_RESET,PERF_IOC_FLAG_GROUP);
ioctl(lead,PERF_EVENT_IOC_ENABLE,PERF_IOC_FLAG_GROUP);
/* ...work... */
ioctl(lead,PERF_EVENT_IOC_DISABLE,PERF_IOC_FLAG_GROUP);
uint64_t buf[1+5]; read(lead,buf,sizeof buf);   // {nr, cycles, instructions, l1d, llc, branch}
```
- `exclude_kernel=1, exclude_hv=1` → user-only (matches what `paranoid≥1` allows).
- Group + `PERF_FORMAT_GROUP` → all counters scheduled together, read atomically;
  no PMU multiplexing across the window. Keep the group ≤ the # of GP counters
  (~4–6) or events get time-multiplexed (scaled estimates, noisier).
- Needs `perf_event_paranoid ≤ 2` or `CAP_PERFMON`.

## Recipe 3 — Top-down analysis

Documented reference; use it to classify the limiting pipeline stage.
```sh
perf stat --topdown -- ./BIN ARGS         # retiring / frontend / backend / bad-spec %
# deeper: toplev.py (pmu-tools) -l3 -- ./BIN ARGS    # drills into mem-bound vs core-bound
```
This is the x86 analogue of the M-series "CPU Counters" slot breakdown. Backend-
bound splits further into memory-bound vs core-bound with `toplev -l2+`.

## Recipe 4 — Code generation and diagnostics

Documented reference; not re-run for this guide.
```sh
llvm-objdump --disassemble --demangle --line-numbers ./BIN | less   # or objdump -dC
clang++ -O3 -fsave-optimization-record -Rpass=loop-vectorize -Rpass-missed=loop-vectorize -c f.cpp
clang++ -fsanitize=address -O1 -g ...    # =undefined, =thread; diagnosis only, perturbs perf
```
Look for AVX/AVX-512 vs scalar, `vfmadd*` (FMA), constant-folded mod/div, missed
inlines. Same approach as the M3 guide.

---

## Remote or cloud hosts

Use a current provider document to select a host that exposes the needed PMU
events. Before creating billable infrastructure, obtain authorization for the
provider, region, machine class, expected cost, and maximum run duration. Add
an automatic lifetime cap where supported and verify cleanup afterward.

Record the topology, kernel, `perf` version, compiler, and counter-access
policy. Code generation varies materially across compilers and targets, so pin
the toolchain when comparing runs.

---
→ Interpreting any of these numbers: [`interpretation.md`](interpretation.md).
