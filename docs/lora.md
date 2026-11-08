# Curated LoRA adapters

Generate supports three curated style adapters; Refine supports experimental
Green Fill and Red Zoom editing adapters. These use the existing NVFP4 rank-32
transformer and NF4 encoder through the browser, HTTP API and MCP.
One adapter is active per image. Other LoRAs are
not qualified by these checks.

| Adapter ID | UI choice | Automatic prompt | Background |
| --- | --- | --- | --- |
| `pixel-art` | Pixel Art · character sprites | Sprite prefix and game-asset/green-screen suffix | Fixed green-screen request |
| `koni-anime` | Koni · Anime illustration | `anime_style, [your description]` | Describe the scene and background normally |
| `nasa-concept` | NASA · Retro space concept art | `NASA_CONCEPT [your description]` | Describe the scene and background normally |

Sources: [Pixel Art](https://huggingface.co/Limbicnation/pixel-art-lora) and
[Koni Anime](https://huggingface.co/Sawata97/flux2_4b_koni_animestyle) and
[NASA concept art](https://huggingface.co/davanstrien/nasa-concept-art-flux2-klein-lora).

## NASA retro space concept art

Select **NASA · Retro space concept art** in Generate. Describe a station,
spacecraft, colony or other scene; the server prepends `NASA_CONCEPT ` with no
fixed suffix. The adapter targets the retro-futuristic space illustration style
of the 1960s–70s. Its small training set teaches a general look, rather than
specific spacecraft. Results are new illustrations, not historical NASA images
or validated engineering designs.

Browser, HTTP generation and MCP `generate_image` share fixed strength **0.5**.
Use `adapter: "nasa-concept"` and supply only your description:

```json
{"prompt":"retro-futuristic space concept art of a rotating wheel space station above the Moon, painted technical illustration","adapter":"nasa-concept","resolution":"square_big"}
```

HTTP uses `size` instead of MCP's `resolution`. NASA is generation-only; its
results can still be sent to Refine or Red Zoom. The trigger and pinned profile
are preserved in recipes. Offline installations need the adapter's first
download with `STUDIO_OFFLINE=0`; weights are cached and are not baked into images.

```shell
task lora:nasa-concept
task lora:nasa-check -- --offline
```

The low-level CLI accepts complete prompts, including the trigger. The comparison
task generates three matched base/adapter pairs at 1024×1024 and checks identical
seed repeatability, zero-strength identity and removal/restoration of the base
model. Local images, recipes, memory diagnostics and an HTML review page go to
`outputs/lora-nasa-concept/<timestamp>/`. These are functional visual checks,
separate from the benchmark suites.

The pinned Diffusers file is `pytorch_lora_weights.safetensors`, revision
`157bcf7e568e6fa601becf1d7c96b5ad20c3be85`, SHA-256
`15c17d7b8cf900461e71a7c516c11b0dd13f536fca02d9eeb05ec92a4f5b6680`.
Metadata declares ordinary LoRA, rank 4, alpha 4 and BF16 tensors: a unit scale.
It installs 60 modules and adds **7.97 MiB** of adapter GPU weights, also observed
as the initial allocation increase. This is adapter storage, not whole-device
peak VRAM. Base transformer/encoder quantization and GPU residency stay intact.
Full strength 1 produced grid artifacts in our 1024-pixel trial. Strength 0.5
gave cleaner results, while FP32 adapter arithmetic and eight steps did not
remove the pattern at strength 1. The cause remains unconfirmed; this curated
profile blends more of the base model rather than claiming full-strength parity
with the author's examples. Inspect generated images for artifacts.
The model card declares Apache-2.0; its pinned README is cached alongside the
hash-verified weights. Training checkpoints and optimizer state are not used.

## Red Zoom / Detail focus (experimental)

Source: [fal/flux-2-klein-4B-zoom-lora](https://huggingface.co/fal/flux-2-klein-4B-zoom-lora).
In **Refine**, choose **Red Zoom · Detail focus (experimental)**, then upload a
picture or send a generated result. Drag a red rectangle around the target, or
set its position and size with the percentage inputs. The hollow outline leaves
the target visible. Its aspect ratio follows **Output canvas**, including when
the source has a different shape. Drag inside to move the rectangle without
resizing it. Click outside to clear it, or drag outside to replace it with a new
selection. Width/height percentage controls stay linked to the output ratio.
**Centre selection** restores a centred rectangle with that ratio. After clearing,
select an area before generating. Output dimensions set the generated image size.

The server always includes `Zoom into the red highlighted area` at strength 1.1.
The text box accepts optional extra focus details. After generation, click
**Zoom again from this result**: the result becomes the new source, with a fresh
rectangle. Select another area and generate again to continue closer.

This is generative zoom: it redraws a close-up and may invent or change details.
It is not a faithful recovery of unseen source detail. A local 1024-pixel trial
zoomed a robot's head and then its face; another target was ignored. Small
rectangles and naturally red objects can confuse the adapter. Inspect each result.
Same-seed repeat and adapter removal/base-identity controls passed on the pinned
runtime, with quantization and GPU placement guards intact.

HTTP `/v1/images/edits` and MCP `refine_image` accept `adapter: "zoom"`, one image
source and optional `zoom_region`:

```json
{"x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5}
```

These are source-image fractions from the top left, not output pixels. The
rectangle must fit inside the source. The server draws the red outline after
normalizing the reference to at most one megapixel. Multipart sends the rectangle
as a JSON string. If the supplied image already has a red outline, omit
`zoom_region`. Omit `prompt` for the fixed instruction alone. Use the returned
`job_id` as the next `source_job_id` with a fresh region for another zoom.

Opt-in Gallery saving retains the flattened red-marked reference and recipe.
Reopening it replays that exact reference without adding another box. Its saved
rectangle is fixed; use the result or upload an unmarked source to select a new
area. The current-session unmarked source is temporary.

The pinned file is `flux-red-zoom-lora.safetensors`, revision
`319512a7563ffc17845414bd8ca93a0a462aa21b`, SHA-256
`a435808332f495450f203786a45c337163e2d6e8253043035b1202cc7a4f915b`.
It uses the original AI Toolkit export, rank 16 and unit scale. Conversion installs
88 FP32 adapter modules, totaling **76.25 MiB** of adapter GPU tensors; this is not
whole-application peak VRAM. The model card declares Apache-2.0; its pinned
`README.md` is retained in the cache. Weights are downloaded on first use and
are not baked into images or Git.

Run `task lora:zoom-check REFERENCE=/data/outputs/your-reference.png` for two
regions, an iterative zoom, repeat and removal controls. Review the local HTML
and recipes in `outputs/lora-zoom/<timestamp>/`. This is a functional visual
pilot with allocator diagnostics, separate from the benchmark suites.

## Green Fill / Outpaint (experimental)

Source: [fal/flux-2-klein-4B-outpaint-lora](https://huggingface.co/fal/flux-2-klein-4B-outpaint-lora).
In **Refine**, select **Green Fill · Outpaint (experimental)** and choose a
reference. Paint with the fixed **#00FF00** brush where content should be filled.
**Restore picture**, **Undo paint** and **Clear paint** undo current-session marks.
Increase **Output canvas** width/height to add green borders; the position selector
anchors the original picture. Smaller canvases fit it proportionally. Painting
follows the picture when canvas size or position changes.

The fixed instruction is always included:

```text
Fill the green spaces according to the image
```

The text box is **Optional: what should fill the green space?** Leave it empty
for natural contextual fill, or describe additional content, such as `a brick
wall`. The server appends this guidance on a new line after the fixed instruction.
The fixed text is displayed read-only and is not part of the editable field.
Border expansion and interior green marks passed an initial 1024-pixel visual
trial; results remain subject to the image model's limitations.
Thin freehand marks remained green in one test. A broader mark and an explicit
instruction to remove the green stripe both helped in that example; neither
is a guarantee for other images.

Green Fill is editing-only, with fixed browser/API strength **1.1**. Generation
style adapters remain unavailable for Refine. The green-marked RGB image is the
reference, not a separate mask; unpainted pixels may also change. No alpha channel
or pixel-preserving compositing is added. Naturally pure-green content can also
be interpreted as a fill region.

HTTP `POST /v1/images/edits` and MCP `refine_image` accept `adapter: "outpaint"`.
Supply one already green-marked image, a prepared reference ID, or a source job
whose picture already contains green areas. Omit `prompt` or use `""` for natural
fill; otherwise supply only optional fill details such as `"a brick wall"`.
Use named `size` for HTTP or `resolution` for MCP. Changing output size alone
does not create green borders for API callers: prepare the expanded reference
first. Recipes retain optional guidance in `prompt` and the composed instruction
in `effective_prompt`. Legacy full prompts have their leading fixed instruction
removed before composition, so it is not duplicated on reuse.

The editor prepares at most one megapixel of reference pixels, independently
of output size. Exact painted pixels remain `(0,255,0)`. For raw API images,
reference normalization preserves the pure-green mask with nearest-neighbour
resampling while resizing picture content normally. Browser reference uploads
can opt into the same preparation with multipart `preserve_green=true`.

Opt-in Gallery storage retains the flattened green-marked reference and recipe.
Replay restores the editing adapter and prompt; saved paint is part of that
reference, not an editable stroke history or recoverable unpainted source. A
current-session source and its undo history stay temporary in the browser.

The pinned file is `flux-outpaint-lora.safetensors`, revision
`b11770ac6a3cf9325dcf81742c12b1c4e257880f`, SHA-256
`9623927f2471613e0c9d7810da3c39f0b277a448193b223ea3cee67d5750974a`.
Its normalized rank-16 FP32 tensors occupy **76.25 MiB** across 88 projections;
that is adapter tensor storage, not whole-device peak VRAM. The source repository
declares Apache-2.0 in README, which is cached with its weights. First-use network
and offline-cache rules are the same as for the style adapters.

For a local 1024 comparison of green borders, interior marks, custom instructions,
repeatability and adapter removal, provide a local reference file:

```shell
task lora:outpaint-check REFERENCE=/data/outputs/my-reference.png
```

Results and HTML comparisons appear under `outputs/lora-outpaint/`. This is a
visual diagnostic, not an additional Refine resolution benchmark suite.

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

Generation adapters use fixed strengths in the browser and generation APIs:
Pixel Art/Koni use 1; NASA uses 0.5. Consecutive
requests reuse the selected weights; switching styles replaces the adapter,
and a base-model request removes it
from the resident worker. Refine uses the base model unless its Green Fill or Red Zoom
editing adapter is selected. Preparing a
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
