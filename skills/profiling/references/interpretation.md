# Profiling interpretation — measurement-integrity & reading traps

How to read perf numbers without fooling yourself: the traps that make a
*correctly captured* number mislead you. For **symptom → cause → remedy** see
[`diagnosis.md`](diagnosis.md); for capture commands see
[`m3-macos.md`](m3-macos.md) /
[`x86-linux.md`](x86-linux.md). Written **2026-05-30** —
principles, not version-bound; examples come from real measurements.

## Metrics don't mix
- **Cross-ISA IPC is NOT a headroom comparison.** ARM64 vs x86 retire different
  instruction counts for the same work (a NEON/AVX instruction folds N
  elements), so "M IPC 5 > x86 IPC 2.6" does **not** mean "more headroom" — IPC
  mixes core width with work-per-instruction. Compare IPC only *within* an ISA.
  Cross-arch, trust only ISA-internal facts: instruction-count *ratios* between
  variants, and the *direction* a metric moves.
- **Top-down slots ≠ CPI/IPC.** A top-down "60% Useful" and an IPC number are
  different metrics; do not convert one into the other. Validate inferred
  labels with controlled workloads and the profiler's current documentation.
- **Static analysis ≠ dynamic counts.** "Unique cache lines a kernel *touches*"
  (static) is not "L1 demand-*misses*" (dynamic). A layout can touch fewer lines
  yet miss more. Don't infer cache behavior from a static model.

## Reading a result correctly
- **Profile (where) and counters (why) answer different questions.** A sampler
  tells you *which* code is hot; counters tell you *why* it's slow (stalled vs
  throughput-bound). You usually need both — and they enter the diagnosis tree
  at different nodes.
- **Validate a derived label against a known case.** Before trusting an inferred
  counter label (e.g. an Instruments top-down component, or a "this is
  memory-bound" call), check it on a case whose nature you already know — a
  memory-bound buffer init vs a compute kernel should sit at opposite ends. If
  they don't, your labeling is wrong.

## Measurement hygiene
- **Aggregate counts must be thread-count-invariant.** If a parallel run sums
  per-thread work and you vary the work with thread count, the count column
  scales with T and reads as a bug. Hold the work fixed (fixed-N) across the T
  sweep so counts stay comparable.
- **Instruction-COUNT comparisons need a FIXED repeat/batch.** If your harness
  auto-tunes repeats to hit a time floor, faster variants get more repeats →
  their instruction/cycle *totals* are contaminated. IPC (a ratio) is immune;
  raw counts are not. Use a fixed batch for count comparisons.
- **Compiler & toolchain are part of the measurement.** Codegen cliffs exist
  (one compiler can be 7–38× off on a loop another handles fine). Pin one
  compiler, record the exact version; mismatched toolchains across machines make
  you measure the compiler, not the code.
- **Median + spread, not a single run.** Report median and an IQR/p95; a tight
  IQR means the number is real. On a *contended* machine, wall-clock timing is
  unreliable — prefer counts and ratios (cycles, IPC, instruction ratios), which
  are far more robust to interference than elapsed time.
- **Measurement perturbs.** Sanitizers, malloc logging, and heavy sampling all
  change timing. Use them to *find* problems, never to *quote* benchmark
  numbers.
- **PMU availability varies.** Some virtual machines expose supported hardware
  counters and others return empty or `<not supported>` values. Verify the PMU
  is live on the actual host before trusting counter data.
