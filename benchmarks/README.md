# FLUX.2 Klein Studio benchmarks

Manual, Docker-first suites measure **GPU memory**, **generation speed** and
**fixed-seed correctness** separately. Each resolution uses a fresh process,
with one checkpoint loaded, batch size one and no application offload, VAE tiling
or slicing. Expandable memory segments and ordered reduction are the defaults.
These are shared live-system measurements; other workloads remain running.
Small timing differences and changes in desktop VRAM are expected.

| Suite | Task | Results | Root summary chart |
| --- | --- | --- | --- |
| VRAM per size | `task bench:memory` | [Memory history](memory/gpu/BENCHMARKS.md) | [memory.svg](memory.svg) |
| Warmed generation speed | `task bench:speed` | [Speed history](speed/BENCHMARKS.md) | [speed.svg](speed.svg) |
| Exact fixed-seed regression | `task bench:seed` | [Seed history](fixed-seed/BENCHMARKS.md) | [fixed-seed.svg](fixed-seed.svg) |

Each area has a `BENCHMARKS.md` history, latest-run `DETAILS.md` and immutable
`runs/<run-id>.json` / CSV snapshots. Resolutions, runs and allocator profiles are
never pooled. Only the latest run for each suite feeds its root summary SVG.
The earlier combined results are retained in [history](history/allocator-comparison/README.md).
Their original summary SVGs also remain in this root directory.

```text
benchmarks/
  README.md, BENCHMARKS.md
  memory.svg, speed.svg, fixed-seed.svg
  memory/gpu/   BENCHMARKS.md, DETAILS.md, runs/*.json, runs/*.csv
  speed/        BENCHMARKS.md, DETAILS.md, runs/*.json, runs/*.csv
  fixed-seed/   BENCHMARKS.md, DETAILS.md, runs/*.json, runs/*.csv
  history/      initial qualification and allocator comparisons
```

## Run

Build the current runtime and generate once to populate model, kernel and compiler
caches. Then run the suites offline against those caches:

```shell
task build
task generate
task benchmark:plan SUITE=memory
task bench:memory
task bench:speed
task bench:seed
```

`task bench:full` runs the three suites **sequentially**, updating their separate
reports and charts after each suite. No other services are stopped. The plan task
has no GPU access and performs no model loading, inference or report writes.
It requires the already-built local image. Select `SUITE=speed` or
`SUITE=fixed-seed` for another plan. `RESOLUTIONS` selects performance sizes and
`SEED_RESOLUTIONS` selects fixed-seed sizes, matching the actual suite tasks.
`task bench:smoke SUITE=speed` runs a two-call, single-size check and retains only
ignored local evidence. It does not change public history or charts. Use
`SUITE=memory` or `SUITE=fixed-seed` to smoke-test those suites.

```shell
task bench:full RESOLUTIONS=512x512,1024x1024,1536x1536 RUNS=3 WARMUPS=1
task bench:seed SEED_RESOLUTIONS=1024x1024
task bench:memory ALLOC_CONF=backend:native
task benchmark WIDTH=1280 HEIGHT=1280
task bench:capacity
```

Default performance sizes cover 512/768/1024/1280/1536 square, 1536×1024 and its
portrait, and 1920×1088 and its portrait. Fixed-seed defaults are 512, 1024 and
1536 square. `RESOLUTIONS` selects memory/speed sizes; `SEED_RESOLUTIONS` selects
seed-test sizes. Both dimensions must be at least 64 and divisible by 16.
`RUNS=5`, `WARMUPS=2`, `SEED=12345`, `STEPS=4` and `TIMEOUT=180` are defaults.
`TIMEOUT` bounds each child process, including loading and any PNG writing.

## Memory

### Named agent presets

`task benchmark:presets:plan` shows the actual memory workload for every named
agent resolution. `task benchmark:presets` stops the local studio, measures all
17 presets in fresh processes, archives the run, updates memory.svg, exports the
studio's packaged reference and rebuilds/restarts the studio. Finish any active
generation before invoking it. Other applications remain running.

The baseline uses the ordinary memory suite: four steps, ordered reduction,
expandable segments, two warmups and five measured calls per size with the same
varied prompt/seed schedule. There is no offloading, tiling or slicing, and this
instrumented run makes no speed claim. Sampled device baseline/free memory and
allocated/reserved peaks stay distinct in the immutable JSON/CSV evidence.

The 2026-11-06 run measured every named preset successfully. At 1024×1024 the
generation reserved peak was about 7.89 GiB; the full catalog ranged from
5.79 to 9.22 GiB. Loading reserved peak was about 5.02 GiB. These are PyTorch
allocator figures, not total memory required by the entire shared desktop.
See [latest details](memory/gpu/DETAILS.md) and the root [memory chart](memory.svg).

`task benchmark:presets:export` regenerates
`src/flux2_klein_studio/data/preset-memory.json` from the latest archived memory
run without inference. The exporter requires coverage of every named resolution
and the studio's model/revision/four-step/ordered/allocator profile. It carries
the archive identity and canonical JSON content hash into the packaged reference;
values are copied from measured records, never hand-entered. Use `--input` with
the exporter directly to select an older full-catalog archive. Rebuild the
runtime after exporting a changed reference. Failed rows retain their status;
they are not successful capacity qualifications.

The settings table labels loading and generation **reserved** peaks, and minimum
sampled free whole-device VRAM. A resident worker switching shapes can reserve
more memory than this fresh-worker-per-size baseline. GPU/driver/software changes
and other running applications also change practical headroom. Enabling a preset
is an operator choice, not an automatic guarantee that it fits every GPU.

The memory suite samples whole-device used/free VRAM around **loading** and
around each generation, targeting 100 ms plus query overhead. It establishes
a pre-model baseline after CUDA context initialization. Device peak minus that
baseline is reported separately from PyTorch's own allocated/reserved counters.
Other applications can change that delta; it is not exact per-process usage.

Loading peaks and measured generation peaks are separate columns. Warmups are
excluded from the generation aggregates. Capacity checking uses minimum sampled
free VRAM across loading, warmups and measured calls. Samples can miss short
peaks, so the allocator counters remain important. Reserved memory includes
blocks held for reuse; allocated memory belongs to active tensors. CUDA placement
does not rule out driver-managed paging under Windows memory pressure.

`task bench:capacity` uses this suite with ascending squares and stops at OOM,
timeout or a passed size below 512 MiB sampled free VRAM. This is a practical
workload-specific boundary, rather than a theoretical maximum. Unexpected errors
stop with a nonzero exit code. Failed telemetry also fails the memory suite.
No speed result comes from this instrumented suite.

## Speed

The speed suite runs two discarded warmups and five measured calls per resolution.
Three default prompts (greenhouse, lake and portrait) rotate with deterministic
per-call seeds. Supplying `--prompt` uses that single prompt instead. Memory and
speed use the same workload schedule, recorded and fingerprinted in each run.

Timing includes tokenization, text encoding, denoising, VAE decoding and CUDA
synchronization. It excludes loading, pixel hashing, output checks, PNG writing
and container startup. There is no periodic VRAM polling, peak-memory reset
or seed-equality assertion in the timed speed suite. Normal CLI per-step
finite-value checks remain in the timed path. The initial separated speed run
omitted those checks; its historical row is retained, and the latest run uses
the finalized profile recorded as `generation_profile.per_step_finite_check`.
It reports median, mean, population standard deviation, min/max, interpolated
p95 and aggregate images/second. Five samples provide a useful initial baseline,
rather than a reliable tail-latency estimate. `RUNS` can increase the sample count.

First pipeline call and cached-model loading times remain separate. Persistent
compiler caches may already contain some shapes, so these are not clean-cache
compilation measurements. Performance suites save JSON/logs rather than PNGs by
default; pass `-- --save-images` when pictures are useful for inspection.

## Fixed seed

This correctness suite saves images and checks:

1. Primary prompt and seed produce exact identical pixels across warmups and repeats.
2. A second seed repeats exactly and produces different pixels from the primary seed.
3. A changed prompt produces different pixels with the primary seed.
4. A fresh process reproduces the primary image's pixel hash exactly.

Any failure returns a nonzero exit code and retains local evidence. This is an
operational regression test on the same GPU and software stack, rather than an
image-quality assessment or GPU-to-GPU guarantee. It does not compare pixels
between resolutions. Speed and memory are not reported as seed-suite results.
Direct `seed-test` checks the in-process controls; the sweep adds the restart check.

## Evidence and organization

Raw reports, memory samples, stdout/stderr and optional PNGs stay under ignored
`outputs/benchmarks/<suite>-<run-id>/`. Summaries are written after every size, so
OOM, timeout or another failure does not erase earlier results. An expected OOM
or timeout can finish a sweep with a recorded non-passing row; `completed` means
the experiment ended, rather than every resolution passed.

Tasks archive portable measurements and rebuild Markdown/CSV/SVG reports after
a successful sweep. To render an interrupted or failed sweep explicitly:

```shell
task benchmark:report SUITE=memory SUMMARY=outputs/benchmarks/memory-RUN_ID/summary.json
```

Use `SUMMARY=latest` (default) to select the latest local summary for that suite.
An active run is rejected. Snapshots cannot be replaced with changed measurements.
Public snapshots retain settings, workload hashes, versions, GPU/driver identity,
model/kernel revisions and source fingerprints. Local paths, raw exceptions,
cache locations, configuration files, memory timelines and images remain local.
Reports describe the shared-system context explicitly.

To redraw a specific snapshot without inference:

```shell
task bench:plot REPORT=benchmarks/speed/runs/RUN_ID.json CHART=benchmarks/speed.svg
```

Plotting and reporting use the optional tools image, keeping Matplotlib out of
the inference dependencies. Historical charts can also be regenerated from
their original JSON snapshots. CPU harness tests and GPU numerical kernel tests
run with `task test:gpu`; `task lint` checks the Python sources and formatting.
