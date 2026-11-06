# Validation

Status: the default ordered mode passed same-seed repeatability checks on an
RTX 5070 Ti with the pinned software stack. The original native mode still varies.
The subsequent [resolution baseline](benchmarks.md) measures speed and memory
across sizes, including a separate expandable-segment allocator profile.

## Ordered reduction

The default `--reduction ordered` replaces only the SVDQ low-rank side calculation.
One Triton program owns each row/rank tile and combines 128-channel tensor-core
partial products in a fixed sequence, without inter-program atomic additions.
The native quantizer still produces the NVFP4 activations/scales and native GEMM
performs the projection. Its nondeterministic low-rank side result is discarded
and replaced by the ordered result computed from the current input and checkpoint
weights. No activations are frozen and no dense model weights are substituted.

This applies the fixed-order principle from [NVIDIA's CCCL article](https://developer.nvidia.com/blog/?p=113316)
using the Triton runtime already included with PyTorch. It does not invoke CUB's
API or provide GPU-to-GPU reproducibility.

Paired 1024×1024 tests in the same Docker image, with four steps and no offloading:

| Check | Native | Ordered |
| --- | --- | --- |
| Five measured calls, seed 12345 | Different pixel hashes | One identical pixel hash, including warmup |
| Mean warmed latency | 1.433 seconds | 1.487 seconds |
| Exact same-seed pixel comparison | Failed | Passed |
| Extra resident allocation | Baseline | 33.75 MiB |

The observed latency increase was 3.7% in this small test. Ordered mode also passed
three measured calls with seed 54321 using a cold compiler cache; that seed
produced a different image. A fresh container reproduced seed 12345's exact image.
These tests used Docker networking disabled. Model, NF4 encoder and VAE remained
on CUDA. Nine GPU-enabled tests passed, including six numerical shapes against a
float64 reference, padded row counts and unsupported-dtype rejection.
CPU-only test builds skip the two GPU-specific tests explicitly.

See [ordered results](../benchmarks/history/initial/ordered-1024.json). This establishes measured
run-to-run behavior on this GPU and stack, not a guarantee across GPUs, compiler
versions, precision profiles or future code changes. The fixed-seed suite fails
if its same-seed image hashes differ. The first inference may include compilation
time; compiled kernels persist in the data volume.

## Initial native-mode results

Tested development image, RTX 5070 Ti (16303 MiB), driver 610.88, Docker
Desktop Linux containers, with networking disabled and no source-code mount:

| Check | Result |
| --- | --- |
| Profile | 1024×1024, batch 1, 4 steps, guidance 1.0, seed 12345 |
| Calls | 1 warmup, 3 measured, 1 changed prompt |
| Mean measured pipeline latency | 1.448 seconds, population standard deviation 0.051 seconds |
| First pipeline call | 2.442 seconds, excluding model loading |
| Cached pipeline loading | 4.344 seconds in this run |
| Peak PyTorch allocated memory | 7.43 GiB |
| Peak PyTorch reserved memory | 9.79 GiB, unchanged across all five calls |
| Sampled whole-GPU usage | 12830 MiB / 12.53 GiB, including Windows/other processes |
| Model placement | Transformer, NF4 Qwen3 encoder and VAE all on cuda:0; no offload |
| Numerical/output checks | Finite denoising tensors, nonblank 1024×1024 images |
| Same-seed pixel equality | Failed; recorded as a separate repeatability limitation |
| CPU guard tests | 7 passed |
| Fresh-container restart | Additional network-disabled generation passed, 2.253-second first call |

Pinned stack: torch 2.14.0+cu130, Diffusers 0.42.1, Transformers 5.22.1,
bitsandbytes 0.50.2, kernels 0.18.1, Accelerate 1.15.0, Hub 2.4.0.
See [machine-readable results](../benchmarks/history/initial/initial-1024.json) for artifact revisions,
kernel file hashes, prompts and measurements. Each invocation saves its own raw
report and images under `outputs/<timestamp>/`; generated files are excluded from
version control. Robot/orchid images were visually inspected and matched the prompt. These are
small operational smoke tests, not a long-running soak test or a quality benchmark.

## Runtime requirements

Use the committed dependency lock. Kernels 0.18.1 is required by this tested stack:
0.17.2 fails during kernel acquisition when telemetry is disabled because it
constructs an invalid HTTP User-Agent header. The image keeps telemetry disabled.

## Original native-mode limitation

Repeated calls with identical prompts and seeds produced different pixels in the
initial successful run. A diagnostic found identical text-encoder embeddings but
different results from an isolated SVDQ CUDA projection fed identical inputs
(observed maximum BF16 difference 0.0078125). The full denoising process then
diverged even when supplied the same precomputed prompt embeddings.

This isolates variation to the loaded quantized execution path; it does not prove
every contributing CUDA operation or identify the exact source behind the binary.
The upstream kernel build also identifies its source tree as dirty and is unsigned.
We pin the immutable Hub snapshot and record hashes of the actual loaded files;
we do not claim a reproducible source build or exact same-seed image reproduction.
The historical combined benchmark reported `same_seed_bit_exact` separately from operational success.
Keep generated images rather than assuming the seed can recreate an exact picture.

A follow-up controlled probe confirmed identical starting noise, prompt embeddings,
packed 4-bit activations and activation scales. The low-rank FP32 activation result
varied by up to 4.77e-7. Holding that result fixed in a diagnostic-only interception
made the isolated projection bit-identical. Enabling PyTorch deterministic
algorithms alone did not eliminate the low-rank variation. This further narrows
the cause to that kernel calculation; freezing input-dependent activations is
not a production fix. The ordered implementation above recomputes that result
from each actual input using fixed-order accumulation.

## Reproduce the checks

The historical initial check used 1024×1024, batch size one, four steps, guidance
1.0 and seed 12345, with one warmup, three repeats and a changed prompt.
Current benchmarks separate correctness from speed and memory:

```shell
task bench:seed SEED_RESOLUTIONS=1024x1024
task bench:memory RESOLUTIONS=1024x1024
task bench:speed RESOLUTIONS=1024x1024
```

The fixed-seed suite saves comparison PNGs and additionally checks a changed
seed and fresh-process reproduction. The memory suite records loading and
generation separately with a 100 ms target sampling interval; the speed suite
has no periodic VRAM polling or equality assertions. Defaults are two warmups
and five measured calls per resolution. Every neural component remains on CUDA.
See the [benchmark guide](../benchmarks/README.md) for report definitions,
limitations, output locations and the preserved historical measurements.

After a successful online run, check restart and cached offline behavior:

```shell
docker compose run --rm studio generate --offline
```

For a strict network-disabled check, bind an existing output directory and use
the same named data volume. This example writes into the volume itself:

```shell
docker run --rm --gpus all --network none --mount type=volume,source=flux2-klein-studio-data,target=/data flux2-klein-studio:dev generate --offline
```

Artifacts written without the Compose output bind remain inside the data volume.
Use `docker cp` from a temporary container if needed. No model, kernel or compiler
cache may be missing for the strict offline run.

Failures produce a `report.json` with status `failed`; CPU/disk spill and active
model-offload hooks are errors. No automatic offload, dtype, image-size or model
fallback is used to make a failing profile appear to pass.

## Named-preset VRAM and agent controls (2026-11-06)

The [memory run 20261106T112139378542Z](../benchmarks/memory/gpu/runs/20261106T112139378542Z.json)
passed all 17 named resolutions with fresh workers, two warmups and five measured
calls per size, four steps and the existing ordered/expandable profile. The local
studio was stopped to release its resident model; other applications remained
running. Loading reserved peak was about 5.02 GiB; measured generation reserved
peaks ranged from 5.79 to 9.22 GiB, with 7.89 GiB at 1024×1024. Device sampling
and pre-load baselines remain separate in the archive. These measurements update
the root memory.svg and packaged settings reference without a speed claim.

The expanded regression suite passed 55 tests with CUDA enabled; the CPU build
passes with two numerical CUDA skips. Additional tests cover persisted policy,
filtered MCP/OpenAPI catalogs, disabled names/numeric aliases, all-disabled mode,
default selection, invalid/I/O-failed settings and accepted work finishing after
a policy change. Plan/export tests check catalog coverage and measured profile.

Real browser checks covered individual/family toggles, mixed checkbox states,
save/reload, a container restart with a reduced catalog, all-disabled operation,
and restoring the initial all-enabled policy. MCP discovery omitted disabled
sizes, stale tool calls failed, and numeric HTTP aliases returned 400. Desktop,
tablet and mobile checks (1440, 900 and 390 pixels wide, expanded/compact header)
had no horizontal page overflow; the table scrolls within its mobile container.

With only square_big and portrait_medium enabled, paired real HTTP/MCP
1024×1024 generations reproduced the existing greenhouse golden hash. Own
temporary jobs were removed and existing gallery files preserved. The final
studio was rebuilt with the reference included and left running.

## Agent API workflow checks (2026-11-06)

The OpenAI-compatible Images subset and stateless MCP endpoint use the same
qualified pipeline. The official OpenAI Python client 3.46.0 generated a
1024×1024 image successfully; HTTP and MCP generation both retained the existing
seed-12345 greenhouse pixel hash
`bff5722b5a24c4832b821ba2e8f10f8f98ef48d0bb2e6760f3c7fc99ff28d440`.

All 17 [named presets](agent-api.md#named-resolutions) generated and decoded at
their advertised dimensions on the tested GPU, without changing the inference
lock or enabling offload. These are workflow checks for one prompt, not new
speed/memory benchmarks or a repeatability guarantee for every preset.

The container regression suite has 47 passing tests with CUDA enabled; the CPU
build passes with two numerical CUDA tests skipped. API tests cover discovery,
validation, formats, shared admission, opt-in persistence, authentication,
cancellation, timeout and worker failure. ASGI disconnect observation preserves
request body chunks and cleans its watcher after normal responses. Actual MCP
HTTP cancellation with a current client and an HTTP request disconnect both
cancelled the owned jobs and released admission. Older MCP clients that only
send a separate cancellation notification have the limitation described in the
[API guide](agent-api.md#balancing-and-deployment-behavior).

Run `task api:check` for discovery, `task api:smoke` for paired 1024 generation,
and `task api:resolutions` for all named sizes. Test artifacts are temporary and
the probe removes only the jobs it creates; existing gallery files are preserved.


## Container publication preparation, 2026-11-06

The local full (`baked`) and tiny image targets were built from the pinned runtime
stack. Full downloaded the pinned model and the single Linux CUDA kernel variant
without GPU access during its build. With networking disabled and an initially
empty application cache, it seeded the bundled assets and generated a 1024×1024
image. A fresh-container offline restart and a tiny-image offline run against the
existing cache produced the same RGB pixel hash as the prior studio golden:

```text
bff5722b5a24c4832b821ba2e8f10f8f98ef48d0bb2e6760f3c7fc99ff28d440
```

The prompt was the default greenhouse robot, seed 12345, four steps, ordered
reduction and expandable allocation. Placement checks kept transformer, encoder
and VAE on GPU, without application offload, VAE tiling or slicing. This is a
packaging smoke check on the existing RTX 5070 Ti, rather than a new speed or
memory benchmark. The initial full-image cache used a temporary host-backed data
directory because the shared Docker disk was nearly full; it contained no prior
model cache. No host source mount supplied application code during inference.

All 70 tests passed with GPU access; the CPU image build passed with the two
numerical GPU tests explicitly skipped. Lightweight publication/release checks,
release transformation dry runs, Compose validation, Ruff and actionlint v1.7.12
passed. Runtime notice collection found zero missing-notice items after the
reviewed wheel supplements. The source image collected and verified 220 files
(565,799,184 bytes) for 69 Debian source packages and four reviewed wheel packages.
The exact older tzdata descriptor is recorded in `tools/debian-source-index.txt`;
archive hash locators retain SHA256 and size verification for downloaded files.

The rebuilt studio passed readiness, OpenAI model/resolution discovery and MCP
tool discovery, and retained the two existing gallery entries. Container
qualification logs, generated images and exported build evidence stay in ignored
`.ci-build/`. No registry publication, Git push or release tag was performed.
Cross-registry digest verification is implemented and tested with fixtures; actual
registry identity can only be qualified after an authorized publication succeeds.


## Concurrent generation queue, 2026-11-06

All 83 GPU-enabled container tests passed. The CPU build passed with the two GPU
numerical tests explicitly skipped. Queue tests exercise five simultaneous calls,
FIFO order and one GPU slot, HTTP/MCP multi-image results, atomic admission,
queue-full responses, independent waiting/generation deadlines, owned cancellation,
worker-start/failure recovery, CPU output overlap, four-buffer backpressure and
old-preview cleanup failures. Existing gateway lifecycle tests for preload, trim
and persistent keep-loaded behavior pass alongside the new scheduler.

An isolated container with networking disabled ran five simultaneous 1024×1024
calls (three HTTP, two MCP), followed by two-image HTTP and MCP batches: nine
images in total. Sampling observed five outstanding jobs and four queued. All
seed-12345 results matched the existing greenhouse golden pixel hash; seed 12346
also matched between the two protocols. The raw RGB handoff and concurrent CPU
PNG output preserved image pixels. One resident worker served the complete run,
with the existing model-placement guards and no application offloading.

This qualifies API scheduling and correctness on the pinned RTX 5070 Ti stack;
it is a smoke test, not a speed or VRAM benchmark. `task api:queue` repeats it and
removes only its own temporary jobs. Local logs remain under ignored `.ci-build/`.
No benchmark history or summary chart was replaced by these smoke-test timings.
