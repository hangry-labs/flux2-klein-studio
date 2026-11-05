# fixed-seed details

Latest run: `20261105T221441523245Z`.

GPU: NVIDIA GeForce RTX 5070 Ti; driver: 610.88; allocator: `expandable_segments:True`.

4 steps, 2 discarded warmups and 5 measured calls per resolution.

Workload SHA-256: `c75c654feec82c9748c70992e973e7ca737f8ebe7f4480cfd82ab311d8a977d6`.

Context: shared live system; other workloads left running.

Full settings, dependency/source fingerprints and individual rows are in the immutable run JSON and CSV.

The primary prompt/seed must repeat exactly, including warmups and every measured image.
A second seed must repeat exactly and produce different pixels. A changed prompt must also produce different pixels.
A separate fresh process generates the primary seed once and must match its exact pixel hash.
The checks detect run-to-run regressions on the same GPU/stack; they do not assert GPU-to-GPU equality or aesthetic quality.
The seed suite never reports speed or VRAM as benchmark results.
