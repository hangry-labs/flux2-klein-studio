# Multi-reference quality

Fresh worker per profile; populated offline cache; ordered NVFP4 r32/NF4, all model components on GPU; no offload/tiling changes; shared live system. Memory: one warmup, two measured calls, whole-device sampling target100ms plus allocator counters; not a speed benchmark. Inputs: generated fictional-person views, hashes retained.

Run: `quality-20261108T153654Z`. All outputs1024×1024,4steps,seed12345. Reference edge is per input, not output resolution.

| References | Edge | Status | Repeat checked |
| --- | --- | --- | --- |
| 1 | 1024 | passed | Not run |
| 2 | 1024 | passed | Not run |
| 4 | 1024 | passed | True |

Exact hashes establish reproducibility, not likeness. Synthetic views may already contain identity drift. Visual review and additional real-photo evaluation remain separate.
