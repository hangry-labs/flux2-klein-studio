# memory details

Latest run: `20261105T220648011510Z`.

GPU: NVIDIA GeForce RTX 5070 Ti; driver: 610.88; allocator: `expandable_segments:True`.

4 steps, 2 discarded warmups and 5 measured calls per resolution.

Workload SHA-256: `b25614daacc72cc57fbece1c245abd680a1db08b07ededbaad15b82ae54f6bde`.

Context: shared live system; other workloads left running.

Full settings, dependency/source fingerprints and individual rows are in the immutable run JSON and CSV.

Loading and generation use separate peak counters and sampled telemetry.
Generation allocated/reserved peaks cover measured calls; warmups are excluded from those aggregates.
Minimum sampled free memory includes loading, warmups and measured calls for capacity checking.
Device delta is whole-device peak minus the pre-load baseline; it includes context/allocator/driver effects and other applications.
It is an estimate on this shared GPU, not exact per-process attribution. Values may be negative if other workloads release memory.
Sampling targets 100 ms plus query overhead and can miss brief peaks; Torch counters capture allocator peaks.
The 512 MiB headroom flag is advisory. CUDA placement does not rule out Windows driver paging.
Cold-load baseline already includes the CUDA context initialized by doctor; it is taken before model loading.
No application offload, VAE tiling or slicing. No speed claim is derived from this instrumented suite.
