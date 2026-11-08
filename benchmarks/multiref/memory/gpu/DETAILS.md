# Multi-reference memory

Fresh worker per profile; populated offline cache; ordered NVFP4 r32/NF4, all model components on GPU; no offload/tiling changes; shared live system. Memory: one warmup, two measured calls, whole-device sampling target100ms plus allocator counters; not a speed benchmark. Inputs: generated fictional-person views, hashes retained.

Run: `memory-20261108T154028Z`. All outputs1024×1024,4steps,seed12345. Reference edge is per input, not output resolution.

| References | Reference edge | Status | PyTorch allocated GiB | Reserved GiB | Device peak GiB | Min free GiB |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 512 | passed | 7.43 | 7.89 | 10.28 | 5.35 |
| 2 | 512 | passed | 7.43 | 7.90 | 10.07 | 5.55 |
| 4 | 512 | passed | 7.43 | 7.90 | 9.97 | 5.65 |
| 1 | 1024 | passed | 7.43 | 7.90 | 9.98 | 5.65 |
| 2 | 1024 | passed | 7.44 | 7.84 | 9.90 | 5.72 |
| 4 | 1024 | passed | 7.77 | 8.62 | 10.66 | 4.96 |

Device measurements include the desktop and other processes; sampling can miss peaks. Allocated counters describe PyTorch tensors/workspace. Spare VRAM is workload-specific, not a universal capacity guarantee. Loading/warmup/per-call records are retained in the JSON.
