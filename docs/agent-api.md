# Image generation for applications and agents

The same Docker service provides the browser, an OpenAI-compatible Images API
subset and an MCP endpoint. All use one resident model worker, ordered reduction,
the expandable allocator and opt-in gallery storage. No second model is loaded.

| Connection | Address |
| --- | --- |
| OpenAI client base URL | `http://localhost:7860/v1` |
| Model ID | `flux2-klein-4b` |
| MCP Streamable HTTP URL | `http://localhost:7860/mcp/` |
| Named size catalog | `GET /v1/images/resolutions` |
| Process liveness | `GET /health/live` |
| Admission readiness | `GET /health/ready` |

Start with `task build` and `task ui`. `task api:check` inspects discovery and MCP
tools without loading weights. `task api:queue` checks five concurrent calls and
two-image batches through HTTP and MCP (nine 1024×1024 images).
`task api:smoke` generates matching 1024×1024
images through HTTP and MCP and verifies their pixels. `task api:resolutions`
also generates every named size. These are workflow checks, not performance
benchmarks; they remove only their own temporary test jobs.
The 1024 smoke check requires square_big to be enabled in System.

## Named resolutions

Names always mean **width × height**. Square is 1:1; portrait is taller than wide.
`square_big` is the initial default. Small/medium/big refer to dimensions, not a separate
model quality mode. Agents choose a name instead of calculating dimensions.

| Family | Small | Medium | Big | Aspect ratio |
| --- | --- | --- | --- | --- |
| `square_*` | 512×512 | 768×768 | 1024×1024 | 1:1 |
| `portrait_*` | 576×768 | 768×1024 | 960×1280 | 3:4 |
| `landscape_*` | 768×576 | 1024×768 | 1280×960 | 4:3 |
| `widescreen_*` | 1024×576 | 1280×720 | 1536×864 | 16:9 |
| `panorama_*` | 1024×512 | 1280×640 | 1536×768 | 2:1 |

For example: `portrait_medium`, `landscape_big`, `panorama_small`.
Two additional presets preserve common OpenAI client dimensions:
`photo_portrait` = 1024×1536 (2:3), `photo_landscape` = 1536×1024 (3:2).
The HTTP `size` field accepts names, their exact dimension strings, or `auto`
(the current enabled default). MCP accepts only the named enum. Other dimensions are rejected;
the browser/CLI retain their existing custom canvas controls.

### Control which sizes agents can use

**System → Image sizes and VRAM** shows all presets, with loading/generation
VRAM reference measurements and minimum observed free GPU memory. Toggle an
individual row or a whole shape family, then **Save enabled sizes**. These
operator choices are stored locally and survive container restarts. New
installations start with every preset enabled.

Both MCP tools/list (including the resolution enum) and list_resolutions expose
only enabled choices. The OpenAI size catalog and OpenAPI schema follow the
same policy. Disabled names and their numeric size strings are rejected before
worker admission, including calls using an old cached MCP schema. Refresh the
agent's tools or reconnect to update its cached choices. Already admitted
generations can finish with their original settings.

The default remains square_big while enabled; otherwise it becomes the first
enabled preset in catalog order. The UI displays that default. Omitting a size,
using HTTP auto/null, or omitting MCP resolution uses the current default.
Disabling every preset hides generate_image and refine_image from MCP discovery and makes agent
generation/readiness return unavailable (HTTP 503). The read-only MCP tools
remain accessible. Local browser canvas controls and CLI generation remain
operator-controlled.

The settings endpoint is `GET /api/resolutions` and
`PUT /api/resolutions` with `{"enabled_resolutions":["square_small","portrait_medium"]}`.
It belongs to the trusted-local studio administration API. The reference data
is generated from an [archived memory run](../benchmarks/memory/gpu/DETAILS.md),
packaged into the image and labeled with GPU, workload and run identity. It is
not a live prediction for every GPU: PyTorch reserved peaks exclude memory used
by other applications and driver overhead, and a resident worker changing
shapes may reserve more than a fresh worker per size.

## OpenAI-compatible Images API

The request/response envelope follows [OpenAI's image generation API](https://developers.openai.com/api/reference/resources/images/methods/generate).
This service runs the local Klein model; it does not call OpenAI or require an
OpenAI account. Requests support 1–16 images (`n`, default 1),
non-streaming base64 output, PNG/lossless WebP/JPEG, and model discovery.
`GET /v1/models` lists the model, and `GET /v1/models/flux2-klein-4b` retrieves it.
The pinned Hugging Face repository ID is also accepted as a model identifier.

```python
import base64
import os
from pathlib import Path
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:7860/v1",
    api_key=os.getenv("STUDIO_API_KEY") or "local",  # SDK requires a value.
    timeout=1240.0,
    max_retries=0,
)
result = client.images.generate(
    model="flux2-klein-4b",
    prompt="An orange sailboat on a calm mountain lake at sunrise",
    size="1024x1024",
    output_format="webp",
    extra_body={"seed": 12345},
)
Path("sailboat.webp").write_bytes(base64.b64decode(result.data[0].b64_json))
```

To use a named size with a client whose type annotations only accept standard
dimensions, supply it through `extra_body={"size": "portrait_medium"}` instead
of the `size` argument. `seed` and `save_gallery` are local extensions, also sent
through extra_body. Omitting/null seed chooses a random seed; providing a seed
from 0 to 9007199254740991 requests repeatability on the qualified stack.
`save_gallery` defaults to false. Four inference steps are used by both agent APIs.

The response contains `created`, `data[0].b64_json`, `size`, `output_format`, and
a local `studio` object with actual seed, preset, dimensions, model revision,
job ID, generation time and original RGB pixel hash. Metadata is also embedded
in the image; JPEG changes pixels, while PNG and WebP preserve them.
The hash describes the original generated RGB pixels, not encoded bytes or JPEG.
See [download behavior](studio.md#gallery-and-files).

`response_format="b64_json"` is accepted; URL output is not offered by this
initial API. Quality may be omitted, `auto` or `standard`; these use the same
four-step profile. Other quality modes, streaming and additional
OpenAI image controls are rejected rather than silently approximated.
Validation errors use HTTP 400 with an OpenAI-style `error` object. Runtime
failures use 502, request timeouts 504. Browser API validation keeps its existing
HTTP 422 behavior. The OpenAPI schema is available at `/openapi.json`.

### Multiple images and concurrent requests

Set `n` to request several images with the same prompt and size:

```json
{"prompt":"An orange sailboat at sunrise","size":"square_big","n":3,"seed":12345}
```

`data` contains three base64 images in request order. With `n=1`, the response
and `studio` metadata keep their existing shape. With `n>1`, `studio` contains
`count` and an `images` list with a recipe, job ID, actual seed, queue wait and
original pixel hash for each corresponding result. A fixed seed increments per
image (12345, 12346, 12347), wrapping at 2^53; omitted seeds are independently
random. Repeating a batch repeats those seeds on the qualified stack.

Images enter the same queue as individual requests and the browser. Batch
admission is atomic: if there is insufficient capacity, no part of that batch
starts. GPU generation stays sequential, so `n` does not multiply model VRAM.
The response waits until every image completes. If an image fails or the caller
cancels, remaining unfinished images belonging to that request are cancelled;
other callers' jobs continue. Completed opt-in gallery images remain available.
This is synchronous multi-image generation, without an OpenAI Batch API/job-file
endpoint or a prompt-list field; send concurrent requests for different prompts.

## Style adapters

Generation also accepts the local extension `adapter: "pixel-art"` or
`adapter: "koni-anime"` through
`POST /v1/images/generations` and MCP `generate_image`. Put only the user's
description in `prompt`; the server adds the fixed trigger text. For example:

```json
{"prompt":"a brave knight holding a sword","adapter":"pixel-art","size":"square_big","seed":12345}
```

Recipes preserve `prompt`, `effective_prompt` and `adapter_profile` separately.
For MCP, use the same `adapter` and `prompt` fields, with `resolution` instead of
HTTP `size`:

```json
{"prompt":"a brave knight holding a sword","adapter":"pixel-art","resolution":"square_big","seed":12345}
```

For Pixel Art, the server adds the sprite trigger words and green-screen background wording.
Do not include the adapter template in `prompt`. The result is RGB with a green
background; automatic background removal and alpha output are not implemented.
For Koni Anime, set `adapter: "koni-anime"` instead. The server prepends
`anime_style, ` with no fixed suffix; describe the scene and background normally.
Both styles use strength 1. Switch by choosing the adapter for each request;
the worker replaces the previous adapter automatically.
Omitting `adapter` uses the base model without prompt wrapping. Adapter selection
uses separate generation and editing choices: generation accepts Pixel Art/Koni,
while edits accept only `adapter: "outpaint"` or the base model. See the
[adapter guide](lora.md) for caching, fixed strength and export details.

## Reference-image editing

`POST /v1/images/edits` accepts one reference plus an edit instruction. It uses the
same named sizes, seed rules, `n=1..16`, queue, output formats and opt-in gallery
storage as generation. The response envelope is identical; each recipe adds
`reference` hashes, prepared dimensions and preprocessing details.

For experimental green-area filling, set `adapter: "outpaint"` and submit one
RGB reference whose fill regions are pure `#00FF00`. The server always includes
`Fill the green spaces according to the image`. Omit `prompt` or use an empty
string for contextual fill, or provide only optional content such as
`"a brick wall"`, which is appended on a new line. Ordinary base-model editing
still requires a nonempty instruction. HTTP raw image preparation preserves exact green
markers when resizing. For `/api/references` uploads, multipart
`preserve_green=true` opts into this preparation explicitly. Strength is 1.1.

For outpainting, callers must prepare green padding around the picture before
uploading it; output `size` alone does not create a padded reference. The browser
provides painting and canvas-extension tools. MCP `refine_image` accepts the same
editing adapter and an already green-marked image. Unpainted pixels may change;
this is image conditioning, not a separate pixel-preserving mask/composite pass.

Existing OpenAI Python clients can send a multipart image:

```python
with open("reference.png", "rb") as image:
    result = client.images.edit(
        model="flux2-klein-4b",
        image=image,
        prompt="Keep the subject, but change the lighting to warm sunset.",
        size="1024x1024",
        extra_body={"seed": 12345},
    )
```

The route accepts multipart `image` or a single `image[]` field, and JSON with
`images: [{"image_url": "data:image/png;base64,..."}]`. JSON `image` also accepts
base64 or a base64 data URL as a local extension. Remote image URLs and the OpenAI
Files API are not implemented. This runs the local model without calling OpenAI.
The request format follows the
[Images edit reference](https://developers.openai.com/api/reference/resources/images/methods/edit).

For a completed result from this instance, avoid uploading it again:

```json
{"prompt":"Make the orchids blue.","source_job_id":"<returned job ID>","size":"square_big","seed":12345}
```

Alternatively, `reference_id` reuses a temporary upload from `POST /api/references`.
Supply exactly one source. IDs are **instance-local**: a gateway distributing edits
between replicas should forward image bytes/data URLs or route a job/reference
ID back to its originating instance.

Uploads are limited to 16 MiB (24 MiB for the whole HTTP/MCP request), one still
image, edges of 32–16384 pixels and at most 32 Mi pixels. Supported decoding
includes PNG, JPEG and WebP. Orientation is applied, transparency is composited
onto white, and metadata is stripped. The prepared RGB reference is limited to
1024×1024 total pixels and dimensions aligned to 16. Its aspect ratio is retained
apart from alignment rounding. Output size remains independent.

An admitted edit receives its own prepared reference copy, so deleting the
source job or temporary upload does not break waiting work. Upload handles are
temporary (up to one hour idle, with a 64-reference retention limit). By default,
reference copies and edited results remain temporary too. With
`save_gallery=true`, the prepared reference PNG and its recipe stay with the
saved result and are deleted with that gallery job. Retrieve the saved input at
`GET /api/jobs/{job_id}/reference` for reproducibility. The original uploaded file
and its private metadata are not retained as gallery assets.

Separate mask-file and strength parameters are unsupported and rejected. Green
Fill uses an RGB reference marker rather than a native pipeline mask input.
Start at 1024×1024: reference
conditioning adds GPU work and memory, and the System size table describes the
text-only baseline. See [editing benchmarks](../benchmarks/refine/README.md).
`task api:refine` checks paired HTTP/MCP edits, multi-image outputs and iterative
reuse at 1024, and removes only its own test jobs.

## MCP tools

Configure an MCP client for Streamable HTTP at `/mcp/` (keep the trailing slash).
The connection instructions and generation/edit tool descriptions encourage
agents to call `skill` once per conversation, before choosing an image workflow
or preparing its prompt. Its result contains the bundled guide, guide version,
current enabled resolutions and adapter availability. After a successful read,
continue with the request; do not retrieve it for every image. This is advisory:
existing clients still work, and no guide token is required.

The same guide is readable as MCP resource
`flux2-klein-studio://skills/image-generation` or at
[`/skills/image-generation/SKILL.md`](http://localhost:7860/skills/image-generation/SKILL.md)
on a local server. It covers Generate versus Refine, Pixel Art selection and
automatic green-screen wording, RGB limitations, seeds, queueing and recipes.
The skill is bundled with the service; clients do not need a separate install.
The transport uses the official [Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk)
with stateless HTTP and JSON responses.

| Tool | Purpose |
| --- | --- |
| `skill` | Read the image guide first, with current sizes and adapter availability; no model load |
| `get_status` | Check whether this instance can accept work; inspect residency |
| `list_resolutions` | Discover exact named sizes and aspect ratios |
| `generate_image` | Generate 1–16 images and return image content plus recipes |
| `refine_image` | Edit one reference with an instruction; return 1–16 images |

Example generate_image arguments:

```json
{
  "prompt": "A tiny lighthouse on a floating island above the clouds",
  "resolution": "landscape_medium",
  "output_format": "webp",
  "seed": 12345,
  "save_gallery": false
}
```

Only prompt is required. Resolution defaults to square_big, format to PNG, seed
to random, gallery storage to false and `n` to 1. Set `n` up to 16 for multiple
image blocks and `structuredContent.images` in the same order. Fixed seeds follow
the same increment rule as HTTP. Tool schemas expose the resolution enum
and no width/height fields. The result includes an MCP image block, human-readable
recipe JSON and structured metadata with the actual seed/settings.

For `refine_image`, provide the edit `prompt` and exactly one of `image_base64`,
`source_job_id` or `reference_id`. `image_base64` accepts raw base64 or a data URL;
it is the portable choice across replicas. Resolution, seed, format, `n` and
save-gallery options follow `generate_image`. Its result carries native MCP image
blocks plus reference provenance in the structured recipe.

## Balancing and deployment behavior

Each instance accepts concurrent requests into a **shared FIFO queue** and runs
**one image on the GPU at a time**. The browser, HTTP API and MCP share the queue.
Request validation, random-seed selection and job preparation can run while the
GPU is occupied. PNG encoding, pixel hashing, disk output and download conversion
use bounded CPU workers and can overlap the next generation. Model execution,
text encoding and VAE work remain sequential; no second model is loaded.

`STUDIO_QUEUE_LIMIT` controls capacity in outstanding images, including active
and CPU-processing work (default 64, range 1–1024). Queued jobs hold settings and
storage metadata; at most four generated raw pixel buffers are handed to CPU
output workers. An ordinary busy GPU does not reject new requests. Only a full
queue returns HTTP 429 with `Retry-After: 1` and `error.code=queue_full`; MCP
returns a corresponding tool error. Batches consume one slot per image.

Readiness stays HTTP 200 while enabled image sizes and queue capacity are
available, including while the GPU is generating. It becomes 503 when full,
shutting down or disabled. Liveness remains 200. HTTP readiness, MCP `get_status`
and `/api/status` expose queue capacity, outstanding/queued/processing counts,
available slots and the active GPU job. Individual browser job responses include
`queue_position`. An unloaded model is ready to load on demand. Readiness is a
snapshot; callers must still handle an admission race with other requests.

Queue waiting has its own deadline: `STUDIO_QUEUE_TIMEOUT_SECONDS` defaults to
900 seconds. Each image then has a 310-second generation/response deadline,
backed by the existing 300-second worker limit. Use proxy/client timeouts that
cover queue waiting, cold loading, generation and transfer; a 310-second overall
proxy timeout may be too short for queued requests. Compose forwards both queue
settings from the environment. `queue_seconds` in each recipe reports actual
waiting separately from generation `seconds`. Queued jobs are local to this
process; restart marks saved unfinished jobs failed rather than replaying them.

Both protocols return image bytes in the completed response. There are no
MCP session IDs or follow-up download URLs requiring sticky routing. Configure
proxy/client timeouts to cover the queue deadlines above. Client retries are
disabled in the example because retrying a lost response can generate another image; there is no
cross-instance idempotency or job store. Cancellation of the owned request/task
removes its waiting jobs and stops its active generation when the HTTP connection
closes. Cancelling a waiting job does not unload another caller’s active model.
Current MCP clients use HTTP cancellation; older clients that only send a separate cancellation
notification may leave generation running until completion or timeout, because
stateless instances do not share request IDs. The original CLI worker still has its 300-second
execution timeout. No automatic retry, CPU offload or larger-memory fallback occurs.

Compose stays bound to loopback. For an infrastructure deployment, route `/v1/`,
`/mcp/` and the health paths to the worker through your proxy. Set
`STUDIO_ALLOWED_HOSTS` to the comma-separated hostnames it will receive, without
schemes or ports. Configure `STUDIO_API_KEY` in your deployment's secret store;
both `/v1/` and `/mcp/` then require Authorization: Bearer with that key. Local
default is no key. These variables are forwarded by Compose and read at startup.
Health checks require no key. Host checks still apply, and cross-origin browser
mutations remain blocked. The key covers agent endpoints; the existing studio
UI and browser routes remain intended for trusted local access.

Temporary outputs follow the studio's bounded-preview lifetime; gallery writes
require explicit save_gallery=true. Existing saved images are unaffected.
