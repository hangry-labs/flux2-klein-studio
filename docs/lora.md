# Pixel-art LoRA pilot

Generate can use one curated adapter from
[Limbicnation/pixel-art-lora](https://huggingface.co/Limbicnation/pixel-art-lora).
This is an experimental inference feature for the existing NVFP4 rank-32
transformer and NF4 encoder. It is available in the browser, HTTP generation API,
MCP `generate_image` and CLI. Other LoRAs are not qualified by this pilot.

## Choose a style in the browser

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

The adapter has fixed strength 1 in the browser and generation APIs. Consecutive
requests reuse its weights; a base-model or Refine request removes the adapter
from the resident worker. Refine currently uses the base model. Preparing a
reference from an adapted result remains available through **Send to Refine**.

The first use downloads the pinned adapter and license. An offline installation
without that cache disables the option and rejects adapter requests before model
loading. Enable network loading with `STUDIO_OFFLINE=0` for the first adapter
generation; cached use can then run offline. Full images default to offline.

For HTTP `POST /v1/images/generations` or MCP `generate_image`, set
`adapter: "pixel-art"` and supply only the description in `prompt`. The same
server-owned template applies. Responses retain the original `prompt` and include
`effective_prompt` and the pinned `adapter_profile`.

## Generate an example

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

## Export and scaling

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
