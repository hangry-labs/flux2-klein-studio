# memory details

Latest run: `20261106T144440372889Z`.

GPU: NVIDIA GeForce RTX 5070 Ti; driver: 610.88; allocator: `expandable_segments:True`.

4 steps, 2 discarded warmups and 5 measured calls per resolution.

Workload SHA-256: `54c1a46047049c84a01e8008190feee9702301efd8bbe83869528ed4d2412212`.

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

Single-reference editing profile.
Prepared reference: 1024×1024; SHA-256 `4a68126d54a0fc4d35cdb747ceb0313e59cfd0dd9385017507fd56df8ffb91a3`.
Preprocessing: `exif-rgb-white-alpha-lanczos-1mp-align16-v1`. Reference encoding and conditioning are included in pipeline work.
Reference upload, file decoding and normalization precede measurement; the pipeline includes VAE reference encoding and conditioning.
Mirrored-reference controls in the fixed-seed suite must repeat exactly and differ from the primary reference.
