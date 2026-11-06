# fixed-seed details

Latest run: `20261106T144735649392Z`.

GPU: NVIDIA GeForce RTX 5070 Ti; driver: 610.88; allocator: `expandable_segments:True`.

4 steps, 2 discarded warmups and 5 measured calls per resolution.

Workload SHA-256: `b7d228765b2627d33dcf7a674a0e6915166d39c95ccc512ba056de94db936db4`.

Context: shared live system; other workloads left running.

Full settings, dependency/source fingerprints and individual rows are in the immutable run JSON and CSV.

The primary prompt/seed must repeat exactly, including warmups and every measured image.
A second seed must repeat exactly and produce different pixels. A changed prompt must also produce different pixels.
A separate fresh process generates the primary seed once and must match its exact pixel hash.
The checks detect run-to-run regressions on the same GPU/stack; they do not assert GPU-to-GPU equality or aesthetic quality.
The seed suite never reports speed or VRAM as benchmark results.

Single-reference editing profile.
Prepared reference: 1024×1024; SHA-256 `4a68126d54a0fc4d35cdb747ceb0313e59cfd0dd9385017507fd56df8ffb91a3`.
Preprocessing: `exif-rgb-white-alpha-lanczos-1mp-align16-v1`. Reference encoding and conditioning are included in pipeline work.
Reference upload, file decoding and normalization precede measurement; the pipeline includes VAE reference encoding and conditioning.
Mirrored-reference controls in the fixed-seed suite must repeat exactly and differ from the primary reference.
