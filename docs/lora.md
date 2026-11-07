# Curated LoRA adapters

Generate supports two curated adapters. This is an experimental inference feature for the existing NVFP4 rank-32
transformer and NF4 encoder. It is available in the browser, HTTP generation API,
MCP `generate_image` and CLI. One adapter is active per image. Other LoRAs are
not qualified by these checks.

| Adapter ID | UI choice | Automatic prompt | Background |
| --- | --- | --- | --- |
| `pixel-art` | Pixel Art · character sprites | Sprite prefix and game-asset/green-screen suffix | Fixed green-screen request |
| `koni-anime` | Koni · Anime illustration | `anime_style, [your description]` | Describe the scene and background normally |

Sources: [Pixel Art](https://huggingface.co/Limbicnation/pixel-art-lora) and
[Koni Anime](https://huggingface.co/Sawata97/flux2_4b_koni_animestyle).

## Koni Anime

Select **Koni · Anime illustration** in Generate and write your image description.
The server adds the author's `anime_style` trigger before it, with no fixed suffix
or green-screen requirement. Backgrounds and lighting belong in your description.
Do not add the trigger yourself in the browser, HTTP API or MCP.

For HTTP generation use `adapter: "koni-anime"` with `size`; MCP `generate_image`
uses the same adapter ID with `resolution`. Both accept only the bare description:

```json
{"prompt":"an adult traveler with a red backpack in a snowy mountain village at night, warm window lights","adapter":"koni-anime","resolution":"square_big"}
```

The low-level CLI accepts the complete prompt verbatim:

```shell
task lora:koni-anime
task lora:koni-check -- --offline
```

The comparison task generates three matched base/Koni pairs at 1024×1024 and
checks same-seed repetition, zero-strength identity and base restoration. Its
images, recipes and local HTML gallery appear in `outputs/lora-koni-anime/`.
Inspect the pictures as well as numerical checks; an adapter is not a guarantee
of improved anatomy or universal image quality.

The pinned source is revision `b392813770c6155d3ae22d3fb8062f247f5108a2`, file
`Flux_klein_4b_anime_Koni.safetensors`, SHA-256
`c5f8fef8610f8f7725ab4da0410ff6ffb09542e9a65676ebcf01527eb3af429f`.
Diffusers converts its AI Toolkit projection layout before additive installation.
The qualified profile uses strength 1, rank 64 and unit scale. Its normalized
BF16 adapter tensors total **183.75 MiB** on GPU; the source tensors total
176.25 MiB before conversion. These are adapter weight allocations, not the
application's whole-device peak VRAM. Base NVFP4/NF4 weights remain quantized.

The repository declares Apache-2.0 in its model card; it has no separate LICENSE
file. The pinned `README.md` declaration is retained alongside cached weights.
Weights are downloaded on first use and are not baked into images or Git.

## Pixel Art in the browser

In **Generate**, select **Pixel Art · character sprites** under **Style adapter**.
Write only your subject or scene in **Image description**. The server builds:

```text
pixel art sprite, [your description], game asset, solid green screen background, flat uniform green background, isolated character
```

The fixed prefix and suffix appear in the read-only **Fixed adapter prompt**
panel. They are not inserted into your editable description. **None · Base model**
uses your description directly. Each queued job snapshots the adapter identity,
template and complete prompt; saved results preserve these in their recipes.
Opening one from Gallery restores its description and adapter without repeating
the trigger text.

The green-screen wording replaces the author's transparency wording, which
produced a painted checkerboard in our comparison. It requests a mostly uniform
green background, not an exact RGB colour or a transparency channel. Background
removal remains a separate step.

Both adapters have fixed strength 1 in the browser and generation APIs. Consecutive
requests reuse the selected weights; switching styles replaces the adapter,
and a base-model or Refine request removes it
from the resident worker. Refine currently uses the base model. Preparing a
reference from an adapted result remains available through **Send to Refine**.

The first use downloads the pinned adapter and license declaration. An offline installation
without that cache disables the option and rejects adapter requests before model
loading. Enable network loading with `STUDIO_OFFLINE=0` for the first adapter
generation; cached use can then run offline. Full images default to offline.

For HTTP `POST /v1/images/generations` or MCP `generate_image`, set
`adapter: "pixel-art"` and supply only the description in `prompt`. The same
server-owned template applies. Responses retain the original `prompt` and include
`effective_prompt` and the pinned `adapter_profile`.

## Generate a Pixel Art example

The low-level CLI below accepts a complete prompt verbatim; include its trigger
words yourself. The Task example already supplies them.

Build the current source image, then generate a 1024×1024 knight:

```shell
task build
task lora:pixel-art
```

For a different prompt, use the CLI directly:

```shell
docker compose run --rm studio generate --lora pixel-art --lora-strength 1 --width 1024 --height 1024 --seed 12345 --prompt "pixel art sprite, a cute orange robot companion with glowing blue eyes, game asset, solid green screen background, flat uniform green background, isolated character"
```

The first adapter use downloads its weights and license into the persistent
Hugging Face cache. Add `--offline` for subsequent cached runs. Strength defaults
to 1 and accepts 0–2; 0 returns base-model pixels. Removing `--lora` runs ordinary
generation. PNG recipes and JSON reports record the adapter revision, hash,
strength, precision and scaling details. Outputs retain the pipeline's RGB format;
the adapter does not add a transparency channel.

## Compare before and after

```shell
task lora:check -- --offline
```

This generates matched base/adapter pairs for a knight, robot and dragon at
1024×1024, then checks an identical-seed adapter repeat, zero-strength identity
and removal/restoration of the base model. It preserves the placement and
quantization guards. Results, recipes, PyTorch memory counters and an `index.html`
comparison page appear under `outputs/lora-pixel-art/<timestamp>/`.

Numerical checks are separate from visual quality: inspect the pictures even
when the checks pass. The prompts already request pixel art, so the base model
can also produce that style. A changed picture alone does not establish better
quality or improved anatomy. This is a visual pilot with memory diagnostics,
not a warmed speed benchmark or a new resolution benchmark suite.

## Pixel Art export and scaling

The pilot uses `pytorch_lora_weights.comfyui.safetensors` from revision
`0ac8e5c3400af68228811edc324721e25fc26777`, SHA-256
`24e938f510f5dd0c890ac8b1078f4abb87a50c9c053b2e85c44821c0f30011ad`.
It uses that export's unit-scale convention and preserves its FP32 adapter
weights. The base model stays quantized; no dense replacement model is created.

The repository's Diffusers export includes rsLoRA metadata with rank 64 and alpha
128, implying a 16× multiplier. That scaling produced dark/noisy results in our
pilot, including a diagnostic at the model card's 512×512 size. The supplied
ComfyUI export omits this metadata and uses unit scaling. Treat the exports'
strength values as different conventions; this pilot qualifies the pinned
ComfyUI export only. It does not establish why the other export fails.

Its FP32 adapter tensors occupy **310.19 MiB**. The initial 1024 pilot observed
the same additional CUDA allocation when loading them, with PyTorch allocated
generation peaks increasing from approximately **7.43 GiB to 7.73 GiB**. These
are allocator diagnostics for the tested workload, not whole-device VRAM or a
guarantee for other inputs. The images remain on GPU without application offload.

The adapter is Apache-2.0 licensed; its license is retained in the downloaded
snapshot. No adapter weights are baked into the image or committed to Git.
