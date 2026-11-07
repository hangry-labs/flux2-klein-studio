# FLUX.2 Klein Studio containers

**Publication status:** the rolling `latest` and `latest_tiny` development images
are published on Docker Hub and GHCR. Matching registry indexes were verified on
2026-11-07 for application version `0.1.0-snapshot`. Stable version tags are
created separately by the release workflow.

Application code is licensed under [Apache-2.0](../LICENSE). Models, dependencies
and adapted code retain their own terms and notices; see [NOTICE](../NOTICE) and
[THIRD_PARTY.md](../THIRD_PARTY.md).

Local text-to-image generation with a browser UI, OpenAI-compatible Images API
and MCP. The initial stack is NVFP4 rank-32 FLUX.2 Klein 4B plus NF4 Qwen3,
entirely on GPU. Linux amd64 and an NVIDIA GPU are required. RTX 5070 Ti 16 GiB
is the qualified development hardware; see [validation](validation.md).

| Variant | Build target | Published rolling tag | Stable tag convention | Model loading |
| --- | --- | --- | --- | --- |
| Full | `baked` | `latest` | `vX.Y` or `vX.Y.Z` | Pinned model and CUDA kernel included; offline by default |
| Tiny | `tiny` | `latest_tiny` | Version plus `_tiny` | Same dependencies and features; downloads pinned assets on first use |

Images are available from `hangrylabs/flux2-klein-studio` on Docker Hub and
`ghcr.io/hangry-labs/flux2-klein-studio` on GHCR. Full images save the initial network download, while
tiny images reduce transfer size. Tiny still includes the CUDA/PyTorch runtime.
Both variants include dependency notices and verified corresponding-source
archives under `/app/runtime-sources`; no separate source image is published.
The full model cache adds about 5 GiB, and its initial copy into a data volume
uses additional disk space. It does not change GPU memory requirements.

Start the full image, then open **http://localhost:7860**:

```shell
docker run --rm --gpus all --init -p 127.0.0.1:7860:7860 --mount type=volume,source=flux2-klein-studio-data,target=/data hangrylabs/flux2-klein-studio:latest
```

For the download-on-first-use variant, keep the same volume:

```shell
docker run --rm --gpus all --init -p 127.0.0.1:7860:7860 --mount type=volume,source=flux2-klein-studio-data,target=/data hangrylabs/flux2-klein-studio:latest_tiny
```

The GHCR repository is interchangeable. Rolling tags intentionally remain unpinned
in these commands. Stable deployment commands should use the readable release
tag and its verified top-level OCI index digest once a stable release is published.

## Verified development image identities

The [successful publication](https://github.com/hangry-labs/flux2-klein-studio/actions/runs/42258301756)
built commit `6933729651392235d304b80cf220adcb3b5d8021`. Docker Hub and GHCR
reported identical top-level OCI indexes for each variant on 2026-11-07:

| Variant | OCI index digest |
| --- | --- |
| Full | `sha256:8e9c5cba83aa9703582bbba066955e566de43837e0d9df0ed73aada1f16ad8c2` |
| Tiny | `sha256:b0803f6c265d81c745c933f707cd608b420ec8fe67400615d851a5b0219a5e82` |

These identify that development build. Rolling tags can advance on later pushes;
use `docker buildx imagetools inspect IMAGE:TAG` to inspect their current identity.

## Build from source

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
