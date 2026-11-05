# speed details

Latest run: `20261105T221736819129Z`.

GPU: NVIDIA GeForce RTX 5070 Ti; driver: 610.88; allocator: `expandable_segments:True`.

4 steps, 2 discarded warmups and 5 measured calls per resolution.

Workload SHA-256: `b25614daacc72cc57fbece1c245abd680a1db08b07ededbaad15b82ae54f6bde`.

Context: shared live system; other workloads left running.

Full settings, dependency/source fingerprints and individual rows are in the immutable run JSON and CSV.

Pipeline wall time includes tokenization, encoding, denoising, decoding and CUDA synchronization.
It excludes model loading, output checks, pixel hashing, PNG writes and container startup.
No periodic GPU polling, peak-memory resets or equality assertions run in this suite.
The current timed profile retains normal CLI per-step finite-value checks. The initial separated speed run omitted them; compare those profiles with care.
Three default prompts rotate with deterministic per-call seeds; warmups use the first prompt.
First-call time can include compilation; persistent caches may already contain some shapes.
p95 uses linear interpolation and is descriptive with only five samples.
