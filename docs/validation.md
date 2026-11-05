# Validation

Status: initial Docker operational qualification passed on 2026-11-05. Exact
same-seed reproduction is not supported by the observed kernel behavior below.

## Local results

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
See [machine-readable results](../benchmarks/initial-1024.json) for artifact revisions,
kernel file hashes, prompts and measurements. Each invocation saves its own raw
report and images under `outputs/<timestamp>/`; generated files are excluded from
version control. Robot/orchid images were visually inspected and matched the prompt. These are
small operational smoke tests, not a long-running soak test or a quality benchmark.

## Runtime requirements

Use the committed dependency lock. Kernels 0.18.1 is required by this tested stack:
0.17.2 fails during kernel acquisition when telemetry is disabled because it
constructs an invalid HTTP User-Agent header. The image keeps telemetry disabled.

## Same-seed limitation

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
The benchmark reports `same_seed_bit_exact` separately from operational success.
Keep generated images rather than assuming the seed can recreate an exact picture.

A follow-up controlled probe confirmed identical starting noise, prompt embeddings,
packed 4-bit activations and activation scales. The low-rank FP32 activation result
varied by up to 4.77e-7. Holding that result fixed in a diagnostic-only interception
made the isolated projection bit-identical. Enabling PyTorch deterministic
algorithms alone did not eliminate the low-rank variation. This further narrows
the cause to that kernel calculation; freezing input-dependent activations is
not a production fix. A deterministic accumulation path needs investigation.

## Reproduce the checks

Baseline: 1024×1024, batch size one, four steps, guidance 1.0, seed 12345. The
benchmark uses one warmup, three measured calls with the same prompt and seed,
then a changed-prompt call. Every neural component must remain on the GPU.

Each run saves PNG images with generation metadata and a JSON report containing
library versions, CUDA runtime, GPU, exact model/kernel revisions, model placement,
quantization modules, loading time and per-call timings/memory. Whole-device peak
memory is sampled every 0.5 seconds and includes desktop/other process usage; it
is approximate. PyTorch allocated/reserved peaks are recorded separately and omit
some CUDA allocations outside PyTorch. Timings synchronize the GPU and cover the
pipeline call, excluding PNG encoding and disk writes.

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
