---
name: flux2-klein-image-generation
description: Generate or refine images with FLUX.2 Klein Studio, including Pixel Art, Koni Anime, NASA concept art, green-area outpainting and iterative zoom.
metadata:
  version: "6"
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
- For extending an image or filling painted green areas, use `refine_image`
  with `adapter: "outpaint"` and a prepared RGB reference. See Green Fill below.
- For a generated close-up, use `refine_image` with `adapter: "zoom"` and
  `zoom_region`. Use the latest result as the source for another zoom. See Red Zoom below.
- For pixel-art character sprites, use `generate_image` with
  `adapter: "pixel-art"` when that style matches the request. The skill result
  includes current adapter availability; do not select an unavailable adapter.
- For anime illustrations, use `generate_image` with `adapter: "koni-anime"`
  when that style matches the request. It supports character and scene
  descriptions without Pixel Art's fixed green-screen background.
- For retro-futuristic space illustrations, use `generate_image` with
  `adapter: "nasa-concept"`. Describe the station, spacecraft, colony or other
  subject; the server adds `NASA_CONCEPT ` automatically, with no fixed suffix.
  These are new illustrations inspired by historical space concept art, not
  archival NASA images or reliable engineering designs.
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
and choose Koni for anime illustration or the base model as appropriate.

Example `generate_image` arguments:

```json
{
  "prompt": "a cute orange robot companion with glowing blue eyes, full body",
  "adapter": "pixel-art",
  "resolution": "square_big",
  "output_format": "png"
}
```

For Koni Anime, supply your description without the trigger. The server builds:

```text
anime_style, [your description]
```

Koni has no fixed suffix or green background requirement. Describe the setting,
background and lighting you want. For example:

```json
{
  "prompt": "an adult traveler with a red backpack in a snowy mountain village at night, warm window lights",
  "adapter": "koni-anime",
  "resolution": "square_big"
}
```

For NASA space concept art, provide only the scene description. The server builds:

```text
NASA_CONCEPT [your description]
```

For example:

```json
{
  "prompt": "retro-futuristic space concept art of a rotating wheel habitat above the Moon, painted technical illustration",
  "adapter": "nasa-concept",
  "resolution": "square_big"
}
```

Describe the setting and background normally; NASA has no fixed suffix. The
style suggests vintage space illustrations, not archival images or accurate
engineering plans. Do not add `NASA_CONCEPT` yourself.

Pixel Art and Koni use fixed strength 1 in MCP/HTTP; NASA uses 0.5 to reduce grid
artifacts observed at full strength on the quantized model. Do not send a strength
argument. Inspect the result; this is not a guarantee of artifact-free output.
Only one adapter is active for an
image. Switching adapter, returning to the base model or refining an image is
handled by the service; do not ask the user to unload weights manually.

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

Refine uses the base model unless `adapter: "outpaint"` or `"zoom"` is selected.
Pixel Art, Koni and NASA adapters are generation-only, although their results can be references.
Separate mask files, inpainting strength and multiple references are not exposed. For another
edit, use the latest returned image as the next reference. IDs are instance-local;
do not assume a different load-balanced instance can resolve them.

## Green Fill / Outpaint (experimental)

This adapter is editing-only. Supply one image with solid **RGB(0,255,0),
`#00FF00`** areas where new content should be generated. To extend the picture,
prepare a larger RGB canvas, fill extra space with that green and place the
original image on it. Interior green painting is experimental but locally tested.
The browser Refine editor provides these preparation tools; MCP callers supply
the already prepared image. Do not pass a separate mask or request the browser
to paint on behalf of a tool call.
Small interior marks can remain green. A broader marked region or an explicit
instruction to remove the marks helped locally; treat these as experiments,
not guaranteed repairs.

Use `refine_image` with `adapter: "outpaint"`. The server always includes this
fixed instruction; do not put it in the optional `prompt` yourself:

```text
Fill the green spaces according to the image
```

Pass `adapter: "outpaint"`, `image_base64` containing the green-marked image and
an enabled `resolution`. Omit `prompt` or use `""` for natural contextual fill.
To guide the result, supply only optional fill details, such as `"a brick wall"`.
The server appends them after the fixed instruction. HTTP `/v1/images/edits`
uses the same adapter and optional details, with `size` for output dimensions.
Strength is fixed at 1.1. Base-model Refine still requires an edit instruction.

Reference preparation preserves exact green markers, including on resize. Larger
references are still reduced to the existing one-megapixel conditioning limit;
output size is independent. Output is RGB, not alpha. This is reference-guided
generation: unpainted pixels may also change, and a drawn region is not a promise
of pixel-exact preservation outside it. Normal green parts of a supplied picture
can also be interpreted as fill regions. Inspect the returned image before
claiming a successful edit.

## Red Zoom / Detail focus (experimental)

Use `refine_image` with `adapter: "zoom"` and an unmarked reference. Supply
`zoom_region` with `x`, `y`, `width`, `height` as fractions from 0 to 1 of the
oriented source image, independent of output size. Coordinates begin at the top
left; the rectangle must fit inside the image. The server draws a hollow red
outline after reference normalization. Do not fill the region solid red.

For example, select the central half of an existing result:

```json
{
  "adapter": "zoom",
  "source_job_id": "<completed job_id from this instance>",
  "zoom_region": {"x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5},
  "resolution": "square_big"
}
```

The server always adds `Zoom into the red highlighted area`, at fixed strength
1.1. Omit `prompt` for the default instruction, or provide only optional focus
details. Alternatively, submit an already red-outlined reference and omit
`zoom_region` to avoid drawing a second rectangle.

To zoom further, use the **newly returned `job_id`** as `source_job_id` and
choose a fresh rectangle on that result. Do not keep reusing the original source
unless the user wants alternate first-pass selections. Across replicas, send the
latest image bytes instead of an instance-local ID.

This generates a close-up and can invent or alter details. It is not faithful
super-resolution or evidence of details present in the source. Target adherence
varies; a local head-to-face iteration worked, while another region was ignored.
Small targets and naturally red content can confuse the model. Inspect results
before claiming that the requested area was enlarged.

HTTP `/v1/images/edits` accepts the same `adapter` and `zoom_region`, with `size`
instead of `resolution`. Multipart encodes `zoom_region` as a JSON object string.
The browser provides a rectangle selector and **Zoom again from this result**.

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
`resolution`. HTTP generation accepts the same `adapter` choice and bare
`prompt`; the server adds the template. Use the deployment's configured base URL
and bearer authentication if enabled. Read the live schemas and resolution
catalog instead of copying instance-local settings into requests.
