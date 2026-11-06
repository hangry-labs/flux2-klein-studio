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
tools without loading weights. `task api:smoke` generates matching 1024×1024
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
Disabling every preset hides generate_image from MCP discovery and makes agent
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
OpenAI account. The initial subset supports one image per request (`n=1`),
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
    timeout=330.0,
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
four-step profile. Other quality modes, batches, streaming, edits and additional
OpenAI image controls are rejected rather than silently approximated.
Validation errors use HTTP 400 with an OpenAI-style `error` object. Runtime
failures use 502, request timeouts 504. Browser API validation keeps its existing
HTTP 422 behavior. The OpenAPI schema is available at `/openapi.json`.

## MCP tools

Configure an MCP client for Streamable HTTP at `/mcp/` (keep the trailing slash).
The transport uses the official [Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk)
with stateless HTTP and JSON responses.

| Tool | Purpose |
| --- | --- |
| `get_status` | Check whether this instance can accept work; inspect residency |
| `list_resolutions` | Discover exact named sizes and aspect ratios |
| `generate_image` | Generate one image and return image content plus a recipe |

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
to random, and gallery storage to false. Tool schemas expose the resolution enum
and no width/height fields. The result includes an MCP image block, human-readable
recipe JSON and structured metadata with the actual seed/settings.

## Balancing and deployment behavior

Each instance admits **one generation at a time**, shared with the browser.
There is no hidden request queue. Busy HTTP requests return 429 with
Retry-After: 1; MCP returns a tool error describing the busy instance.
Liveness remains 200; readiness is 503 while busy and 200 when another generation
can be admitted. An unloaded model is ready to load on demand; model_state lets
the balancer distinguish that from a warm worker. Readiness is a snapshot, so
clients must still handle a busy response after a successful readiness check.

Both protocols return image bytes in the completed response. There are no
MCP session IDs or follow-up download URLs requiring sticky routing. Configure
proxy/client timeouts to exceed cold loading plus generation (the server's
request deadline is 310 seconds). Client retries are disabled in the example
because retrying a lost response can generate another image; there is no
cross-instance idempotency or job store. Cancellation of the owned request/task
stops its active generation when the HTTP connection closes. Current MCP clients
use HTTP cancellation; older clients that only send a separate cancellation
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
