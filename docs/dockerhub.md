# FLUX.2 Klein Studio containers

**Publication status:** build and publication automation is being prepared. No
registry image or stable release has been published or qualified for deployment.
The application license must be selected before the publishing gate can pass.

Local text-to-image generation with a browser UI, OpenAI-compatible Images API
and MCP. The initial stack is NVFP4 rank-32 FLUX.2 Klein 4B plus NF4 Qwen3,
entirely on GPU. Linux amd64 and an NVIDIA GPU are required. RTX 5070 Ti 16 GiB
is the qualified development hardware; see [validation](validation.md).

| Variant | Build target | Registry suffix | Model loading |
| --- | --- | --- | --- |
| Full | `baked` | `latest`, `vX.Y.Z` | Pinned model and CUDA kernel included; offline by default |
| Tiny | `tiny` | `latest_tiny`, `vX.Y.Z_tiny` | Same dependencies and features; downloads pinned assets on first use |
| Sources | `sources` | `latest_sources`, `vX.Y.Z_sources` | Corresponding source archives and checksums; no executable application |

The workflow targets `hangrylabs/flux2-klein-studio` on Docker Hub and
`ghcr.io/hangry-labs/flux2-klein-studio` on GHCR. These are planned references
until publication succeeds. Full images save the initial network download, while
tiny images reduce transfer size. Tiny still includes the CUDA/PyTorch runtime.
The full model cache adds about 5 GiB, and its initial copy into a data volume
uses additional disk space. It does not change GPU memory requirements.

After a successful publication, the moving full-image command will be:

```shell
docker run --rm --gpus all --init -p 127.0.0.1:7860:7860 --mount type=volume,source=flux2-klein-studio-data,target=/data hangrylabs/flux2-klein-studio:latest
```

Use `latest_tiny` for the download-on-first-use variant, and keep the same volume.
The GHCR repository is interchangeable. Stable deployment commands must include
the readable release tag and its verified top-level OCI index digest; those
commands will be added after the first successful publication.

For a full local build that works today:

```shell
task docker:build:full
docker run --rm --gpus all --init -p 127.0.0.1:7860:7860 --mount type=volume,source=flux2-klein-studio-data,target=/data flux2-klein-studio:full
```

The full image seeds missing pinned cache files into `/data/huggingface` on first
use and preserves other cached snapshots. Keep `/data` for models, compiler caches,
settings and saved outputs. Compose additionally binds `./outputs` for easy host
access; a plain `docker run` stores outputs in the named volume.
`STUDIO_OFFLINE=1` explicitly requires cached assets; `STUDIO_OFFLINE=0` enables
network access for loading. Full defaults to offline, tiny to online. Image
generation remains local; model downloads contact Hugging Face when enabled.

UI: `http://localhost:7860`; Images API: `/v1`; MCP: `/mcp/`. Configure
`STUDIO_API_KEY` and `STUDIO_ALLOWED_HOSTS` when exposing beyond loopback. See
[agent API](agent-api.md), [studio controls](studio.md), [benchmarks](../benchmarks/README.md)
and [release procedure](RELEASING.md). Use responsibly and respect applicable
model, dependency and artwork terms in [THIRD_PARTY.md](../THIRD_PARTY.md).
