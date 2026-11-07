---
name: flux2-klein-image-generation
description: Generate images or refine an existing picture with FLUX.2 Klein Studio's MCP tools, including its curated Pixel Art adapter.
metadata:
  version: "1"
---

# FLUX.2 Klein Studio image skill

Read `skill` once per conversation before choosing an image tool or preparing
its prompt. A successful result means the guide is loaded: continue with the
request rather than calling `skill` repeatedly. Read it again after switching
servers or when the server advertises a new guide version. This is guidance;
there is no guide token to pass to generation.

## Choose the workflow

- For a new image from a description, use `generate_image`.
- For a change to an existing image, use `refine_image` with an edit instruction
  and exactly one reference source. Preserve the user's requested subject and
  changes; do not turn an editing request into unrelated text-to-image work.
- For pixel-art character sprites, use `generate_image` with
  `adapter: "pixel-art"` when that style matches the request. The skill result
  includes current adapter availability; do not select an unavailable adapter.
- Omit `adapter` for ordinary base-model generation. Only adapters advertised
  by this server are supported; arbitrary LoRA names, uploads, strengths and
  adapter stacking are not exposed by these tools.

## Describe the image

Turn the user's brief into a useful image description: subject, action, framing,
appearance, setting, lighting and style as relevant. Preserve explicit choices.
These are generative instructions, not guarantees of anatomy, exact text, colour
values or identical character appearance across different prompts.

For Pixel Art, supply **only the subject description** in `prompt`. The server
adds this fixed template automatically:

```text
pixel art sprite, [your description], game asset, solid green screen background, flat uniform green background, isolated character
```

Do not add the prefix or suffix yourself, or request a transparent/checkerboard
background as part of this adapter's normal workflow. Its output is RGB with a
green background, not an alpha channel. Exact green RGB/hex values are not
guaranteed. Background removal is not implemented. If the user specifically
wants a scene background, explain this adapter's fixed green-screen behaviour
and use base-model generation when appropriate.

Example `generate_image` arguments:

```json
{
  "prompt": "a cute orange robot companion with glowing blue eyes, full body",
  "adapter": "pixel-art",
  "resolution": "square_big",
  "output_format": "png"
}
```

## Choose a size and seed

Use an enabled named `resolution` from `list_resolutions`; do not invent preset
names or send width/height. The skill result includes the current choices and
default. Names use `square_*`, `portrait_*`, `landscape_*`, widescreen and other
aspect-ratio presets as advertised. For a 1024x1024 square, choose `square_big`
if enabled. Omit resolution to accept the current server default.

Omit `seed` for a random result. Reuse the returned seed and all generation
settings for repeatability on the same qualified runtime. A fixed seed does not
lock character identity when prompt, adapter or other settings change.

## Refine an image

Call `refine_image` with the change in `prompt` and exactly one of:

- `source_job_id`: a completed job on this same instance;
- `reference_id`: an already prepared reference on this same instance;
- `image_base64`: one image as base64 or a base64 data URL, including when
  transferring between replicas.

Refine uses the base model and does not accept an adapter. It can take a Pixel
Art result as a reference, but it does not keep that LoRA loaded for the edit.
Masks, inpainting strength and multiple references are not exposed. For another
edit, use the latest returned image as the next reference. IDs are instance-local;
do not assume a different load-balanced instance can resolve them.

## Receive results and handle the queue

Generation and Refine return synchronously after completion, with image content
and structured recipes. Formats are `png`, lossless `webp` and lossy `jpeg`.
Keep `save_gallery: false` unless the user requests persistent gallery storage.
Do not claim to have visually inspected an image from recipe metadata alone.

Use `n` from 1 to 16 for several images sharing one description; a supplied seed
increments for each image. Send concurrent requests for different descriptions.
Requests share a FIFO queue, with one image using the GPU at a time. Queued work
is normal; do not submit a duplicate just because a call is still waiting.
`get_status` reports acceptance and queue state without loading the model.
Handle queue-full and unavailable responses explicitly; another instance is an
option when capacity is exhausted. Do not promise unsupported background removal,
training, arbitrary adapters or exact pixel-level edits.

## HTTP callers

The same workflows are available at `POST /v1/images/generations` and
`POST /v1/images/edits`. Use HTTP `size` for the named preset; MCP calls use
`resolution`. HTTP generation accepts the same `adapter: "pixel-art"` and bare
`prompt`; the server adds the template. Use the deployment's configured base URL
and bearer authentication if enabled. Read the live schemas and resolution
catalog instead of copying instance-local settings into requests.
