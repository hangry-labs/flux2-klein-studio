# speed details

Latest run: `20261106T144655881551Z`.

GPU: NVIDIA GeForce RTX 5070 Ti; driver: 610.88; allocator: `expandable_segments:True`.

4 steps, 2 discarded warmups and 5 measured calls per resolution.

Workload SHA-256: `54c1a46047049c84a01e8008190feee9702301efd8bbe83869528ed4d2412212`.

Context: shared live system; other workloads left running.

Full settings, dependency/source fingerprints and individual rows are in the immutable run JSON and CSV.

Pipeline wall time includes tokenization, encoding, denoising, decoding and CUDA synchronization.
It excludes model loading, output checks, pixel hashing, PNG writes and container startup.
No periodic GPU polling, peak-memory resets or equality assertions run in this suite.
The current timed profile retains normal CLI per-step finite-value checks. The initial separated speed run omitted them; compare those profiles with care.
The default text workload rotates three prompts; an explicit prompt uses one instruction with deterministic per-call seeds. Warmups use the primary prompt.
First-call time can include compilation; persistent caches may already contain some shapes.
p95 uses linear interpolation and is descriptive with only five samples.

Single-reference editing profile.
Prepared reference: 1024×1024; SHA-256 `4a68126d54a0fc4d35cdb747ceb0313e59cfd0dd9385017507fd56df8ffb91a3`.
Preprocessing: `exif-rgb-white-alpha-lanczos-1mp-align16-v1`. Reference encoding and conditioning are included in pipeline work.
Reference upload, file decoding and normalization precede measurement; the pipeline includes VAE reference encoding and conditioning.
Mirrored-reference controls in the fixed-seed suite must repeat exactly and differ from the primary reference.
