# FLUX.2 Klein Studio

Experimental local text-to-image generation using FLUX.2 Klein 4B with a Nunchaku
Lite NVFP4 rank-32 transformer and an NF4 text encoder. The Docker CLI generates
1024×1024 images with all model components on the GPU, without offloading.
Browser UI and an HTTP API are planned.

**Known issue:** the current CUDA kernel can produce visibly different images
with the same prompt and seed. Exact image reproduction is unresolved. See
[validation and investigation](docs/validation.md#same-seed-limitation).

## Run locally

Requires Docker with Compose GPU support and NVIDIA GPU access. The current
image targets Linux x86_64 containers on a Blackwell GPU. Tested on an RTX 5070 Ti
with 16 GiB VRAM; other hardware has not been qualified. Windows requires a Docker
installation configured for Linux containers and NVIDIA GPU passthrough.
See [validation](docs/validation.md) for measured settings and memory use.

Clone the repository, then run these commands in PowerShell or Bash from its root.

Build the development image:

```shell
docker compose build
```

Generate an image. The first run downloads the pinned model and compatible CUDA kernel:

```shell
docker compose run --rm studio generate --prompt "A glass robot tending orchids in a sunlit greenhouse, cinematic lighting, highly detailed"
```

Images and JSON reports appear under `outputs/<timestamp>/`. Model and kernel
caches live in the `flux2-klein-studio-data` Docker volume and survive container
removal. Keep that volume to avoid downloading the assets again. Removing it,
including with `docker compose down --volumes`, deletes the cached assets.
The image is built locally as `flux2-klein-studio:dev`; no registry image or stable
release is published. The CLI exposes no HTTP port.

## Runtime commands

Check CUDA and library imports without downloading model weights:

```shell
docker compose run --rm studio doctor
```

Optionally download the model before generation:

```shell
docker compose run --rm studio download
```

Run the 1024×1024 operational benchmark and same-seed comparison:

```shell
docker compose run --rm studio benchmark
```

The benchmark measures a warmup, repeated calls and a changed prompt. Operational
success and exact same-seed pixel equality are reported separately.

## What the settings mean

- **Prompt:** the written description of the picture.
- **Seed:** the starting randomness. Default: `12345`. This kernel profile was
  observed to produce different pixels even with the same prompt and seed in the
  same process. Save the output image when you want to keep an exact result.
- **Steps:** rounds of image refinement. This packaged model's initial profile
  uses four steps; more steps are not automatically better for a distilled model.
- **NVFP4 / NF4:** different four-bit weight formats used by the transformer and
  text encoder. Other parts and computations still use higher precision.
- **Rank 32:** part of the transformer checkpoint's quantization design, not an
  image-quality slider or a LoRA strength setting.
- **Offloading:** moving model components between GPU and host memory to save
  VRAM. This profile keeps them on the GPU and fails rather than enabling offload.

The initial runner fixes width/height to 1024×1024, batch size to one and guidance
to `1.0`. `--seed`, `--steps`, `--prompt` and `--output-dir` can be changed. The
benchmark performs a warmup, three measured calls, then a changed-prompt call.
Use `--runs` to change the number of measured calls.

After one successful inference has populated both caches, use `--offline` with
`generate` or `benchmark` for cached loading. Verify complete offline operation
with Docker's network disabled, as described in the validation notes.

Model weights are downloaded into the volume, not bundled into the image. The
runtime explicitly permits code from the pinned Nunchaku kernel repository through
`DIFFUSERS_TRUST_REMOTE_KERNELS=true`. It checks the expected kernel revision and
fails if the upstream version reference changes; dependency updates need validation.

## Development and attribution

```shell
docker build --target test -t flux2-klein-studio:test .
```

The test stage runs CPU tests for the residency and quantization guards; actual
GPU generation requires the runtime checks above. Dependency pins are in
`requirements.txt`, with the full Linux dependency resolution in `requirements.lock`;
validation records the selected model/kernel revisions and loaded kernel file hashes.
To update the lock, use `uv pip compile requirements.txt --python-version 3.13 --python-platform x86_64-unknown-linux-gnu --index-strategy unsafe-best-match --emit-index-url --output-file requirements.lock`,
then rebuild and repeat the GPU checks. The lock targets the Linux container,
not the host operating system.

Model: [lite-infer/flux.2-klein-4b-nunchaku-lite-nvfp4-r32](https://huggingface.co/lite-infer/flux.2-klein-4b-nunchaku-lite-nvfp4-r32),
based on Black Forest Labs FLUX.2 Klein 4B. Runtime: Hugging Face Diffusers,
Transformers, bitsandbytes and Nunchaku Lite CUDA kernels. See
[third-party notices](THIRD_PARTY.md).

Inference runs locally. Initial installation and model/kernel acquisition require
network access; prompts are not sent to a hosted image-generation service. Avoid
misleading impersonation and obtain permission for private or identifying material.
Generated images can contain errors and do not establish that a depicted event occurred.
