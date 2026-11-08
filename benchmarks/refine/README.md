# Single-reference editing measurements

This profile measures one prepared reference and one instruction at 1024×1024,
using the pinned NVFP4 rank-32 transformer, NF4 encoder, ordered reduction and
expandable allocator. Model components remain on GPU; VAE tiling and slicing are
disabled. These results remain separate from the text-only baseline.

| Suite | Evidence | Root summary chart |
| --- | --- | --- |
| VRAM | [History](memory/gpu/BENCHMARKS.md), [latest method](memory/gpu/DETAILS.md) | [refine-memory.svg](../refine-memory.svg) |
| Warmed speed | [History](speed/BENCHMARKS.md), [latest method](speed/DETAILS.md) | [refine-speed.svg](../refine-speed.svg) |
| Fixed-seed correctness | [History](fixed-seed/BENCHMARKS.md), [latest method](fixed-seed/DETAILS.md) | [refine-fixed-seed.svg](../refine-fixed-seed.svg) |

The [index](BENCHMARKS.md) links immutable JSON/CSV runs. Raw logs, sampled GPU
timelines and generated images stay under ignored `outputs/`.

## Reproduce the workload

Prepare the default greenhouse image at 1024×1024, seed 12345 and four steps:

```shell
task generate WIDTH=1024 HEIGHT=1024 SEED=12345 STEPS=4
```

Copy its generated PNG from `outputs/<timestamp>/generate.png` to
`outputs/refine-reference.png`. The qualified source RGB pixel hash is
`bff5722b5a24c4832b821ba2e8f10f8f98ef48d0bb2e6760f3c7fc99ff28d440`.
The prepared reference's PNG SHA-256 is
`4a68126d54a0fc4d35cdb747ceb0313e59cfd0dd9385017507fd56df8ffb91a3`.
Input images remain local and untracked. Other references create a different
workload; their hashes and preprocessing appear in the archived profile.

The instruction is: “Keep the glass robot and orchids, but change the lighting
to warm golden sunset.” Then run:

```shell
task benchmark:refine:plan SUITE=memory
task benchmark:refine:memory
task benchmark:refine:speed
task benchmark:refine:seed
```

`task benchmark:refine:full` runs these three suites sequentially. Override
`REFERENCE` with a container path under `/app/persistent/outputs`, `REFINE_PROMPT` with an
instruction, and `REFINE_RESOLUTIONS` with output sizes to investigate. Initial
qualification covers only 1024×1024. `RUNS`, `WARMUPS`, `SEED`, `STEPS`, `TIMEOUT`
and `ALLOC_CONF` follow the existing benchmark conventions.

Plan performs CPU input normalization and fingerprinting without GPU access,
model loading or report writes. Memory uses dedicated stage counters and
whole-device sampling; speed has no memory sampler. Both use two discarded
warmups and five measured calls by default. Speed changes seeds while keeping
the edit instruction/reference fixed; normal production finite-value checks
remain enabled.

The correctness suite repeats the primary edit, repeats a second seed, changes
the instruction, repeats a mirrored reference, and checks a fresh process against
the original output pixels. The reference controls ensure an ignored input cannot
pass merely because the output repeats. Guarantees cover the tested GPU and stack.

Reference upload, file decoding and normalization occur before pipeline
measurement. Reference VAE encoding and conditioning are inside the measured
pipeline. Output validation, pixel hashing and PNG export are outside speed
timing. CPU output overlap and HTTP/MCP transfers are checked separately by
`task api:refine`; these CLI timings do not describe total API latency or queue wait.

Raw suite evidence appears under `outputs/refine/benchmarks/`. The initial memory
run used `outputs/benchmarks/refine/benchmarks/`; its completed measurements were
archived unchanged after correcting the report lookup path. No inference was
repeated merely to fix that path.

Reports and root SVGs can be regenerated without inference using
`task benchmark:refine:report SUITE=memory` (or `speed` / `fixed-seed`).
The reporter can use archived portable JSON when raw artifacts are unavailable.
For an older run, set `SUMMARY` to its explicit JSON path.
Existing run IDs cannot be replaced with changed evidence. Shared desktop/GPU
activity remains part of these measurements; unrelated services were left alone.
