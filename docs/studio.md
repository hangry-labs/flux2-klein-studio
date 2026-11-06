# Browser studio

```shell
task build
task ui
```

Open **http://localhost:7860**. `task ui:logs` follows server logs and
`task docker:down` stops the service without removing the model cache or images.
Plain `docker compose up -d` also starts the studio. Explicit CLI commands remain
available through `docker compose run --rm studio generate ...`.

After model and kernel caches have been populated, set `STUDIO_OFFLINE=1` in an
ignored local `.env` file and recreate the service to use cached assets without
Hub checks. Initial downloads require online mode. This setting is passed to the
same CLI `--offline` path already qualified by the generation tests.

## Generate

Describe your picture, select a canvas size and generate. Examples fill the prompt
without altering other settings. Four steps and **Random seed on** are the initial
browser defaults. Each Generate request chooses a fresh seed; the seed field and
completed result show the actual value used. Turn Random seed off to enter or keep
a fixed seed. The CLI and HTTP API retain their explicit seed default of 12345.
The seed controls starting randomness: keep prompt, size, seed and steps to repeat
an image on the qualified GPU and stack. More steps are not automatically better.
Custom dimensions must be 64–1920 pixels and divisible by 16. This first UI bounds
input to that range; actual fit depends on the GPU and other applications.

One image uses the GPU at a time. Concurrent requests wait in a shared FIFO queue
instead of loading another model. The studio shows queue position, loading,
generation and CPU output preparation. Cancelling a waiting image removes only
that job; cancelling the active GPU image stops its worker. A five-minute worker timeout bounds stalled
jobs. Raw diagnostics remain in the service logs (`task ui:logs`).

A resident inference worker uses the same pipeline loader, ordered reduction,
default expandable allocator and numerical checks as the CLI. It loads the model
on demand and reuses those weights for later requests. **System → Model residency**
sets how long to keep weights loaded after a generation: five minutes by default,
with a configurable 0–86400 seconds. Each completed generation restarts the idle
timer. Zero unloads after generation; **Unload now** releases an idle model
immediately. The timeout is saved across studio/container restarts in the local
studio settings file. The idle timer never unloads an actively generating model.

Cancellation, worker failures, timeouts and service shutdown discard the worker
and release its weights. The next request then loads a fresh worker. Loading and
the first kernel compilation are additional to the displayed generation time.
CLI commands and benchmark workers retain their existing lifecycle.

## Gallery and files

**Save result to gallery** is unchecked by default. An unchecked generation remains
a downloadable temporary preview, outside the persistent gallery directory.
Temporary previews are removed on service shutdown/restart or when evicted from
the bounded 100-preview working set. Download a preview before leaving the studio
if you want to keep it. A reload does not restore unsaved previews to the gallery.
Checked generations persist under `outputs/studio/<job-id>/` with their settings.

Saved images appear in Gallery. Selecting an image shows it and restores its
prompt, seed, dimensions and steps without starting another generation. Reusing
a gallery recipe turns Random seed off so its seed can be repeated.
The download format selector offers **PNG** (original), **WebP** (lossless) and
**JPEG** (quality 95, lossy). PNG and WebP preserve the generated RGB pixels;
JPEG changes pixels and is unsuitable for pixel-equality comparisons. PNG keeps
its embedded generation metadata; WebP and JPEG carry the recipe in EXIF
ImageDescription. Conversion uses the existing Pillow dependency, requires no
FFmpeg, and runs only on download without saving extra files. Recipe JSON
downloads portable settings and the pinned model revision. Browser prompts are not stored separately
in browser storage. Reusing a gallery recipe does not silently opt the next image
into saving; the checkbox remains under your control.

The latest 100 saved job records are kept in memory and rediscovered after a
restart, independently of temporary previews. Older saved files remain on disk;
saved results are never automatically deleted. Interrupted saved jobs are shown
as failed. Existing CLI and benchmark output folders are not imported.

Use a card's **Delete** button, or select multiple cards and **Delete selected**.
The confirmation describes permanent removal; **Keep files** or Escape cancels
before deletion. Confirming removes the entire saved job folder: image, prompt,
metadata, reports and any old worker logs. Downloaded copies elsewhere on your
machine remain untouched. Active/cancelling jobs cannot be deleted until stopped.
Failed and cancelled saved jobs can be removed too. Partial batch failures are
reported with the completed count; files in other job folders are unaffected.

## System

**Image sizes and VRAM** lists the named agent presets with their reference
loading and generation peaks. Enable individual rows or whole shape families,
then save. Choices persist across restarts and control both the MCP schema and
agent API admission. Cached requests for disabled sizes are rejected. The table
also shows the resulting default; disabling every row turns off agent generation.
Refresh/reconnect an agent to update its cached tool list. Existing admitted
jobs can finish, and browser canvas controls remain available for local use.

The displayed loading/generation numbers are PyTorch reserved peaks from the
labeled reference GPU and pinned stack. Minimum free memory is sampled across
the whole GPU, including other applications. The benchmark uses a fresh worker
per size; a resident model switching sizes may retain more allocator space.
See the [benchmark guide](../benchmarks/README.md#named-agent-presets) for
measurement and regeneration commands.

System shows the model profile, lifecycle and GPU utilization, VRAM, temperature,
power and fan sensors when available. Browser polling runs only while System is
visible and the document is active. The server sampler stops after 60 seconds
without demand and retains at most 600 samples. History and the one-/ten-minute
window are session-owned. Missing sensors are omitted, and temporary failures
preserve the last browser history. Whole-device values include other applications.

The dark shell, orange accents, collapsible header, workflow navigation and System
layout follow the Hangry Labs studio family, adapted for images. All browser
assets are packaged locally. The supplied product logo, mascot, badge and favicon
live in [assets/](../assets/README.md) as WebP images. The horizontal logo is used
in the header, the mascot on the empty canvas, and the badge as a subtle backdrop.

## Local HTTP API

The service is intended for a trusted local machine, with Compose binding only
127.0.0.1. It has no user authentication. Host and Origin checks reject unrelated
browser origins. Public/LAN exposure needs a deliberate deployment configuration.
The JSON OpenAPI description is available at `/openapi.json`; no CDN-based API
documentation is required by the application.
The service also provides [OpenAI-compatible image generation and MCP](agent-api.md)
with named resolution presets. These share the browser worker and gallery opt-in.

| Route | Behavior |
| --- | --- |
| `GET /api/status` | Active model, job state, presets, limits and defaults; no model load |
| `POST /api/jobs` | Validated prompt, seed, width, height and steps; starts a job |
| `GET /api/jobs` | Most recent saved gallery job records |
| `DELETE /api/jobs/{id}` | Permanently remove an inactive job and all its files |
| `GET /api/settings`, `PUT /api/settings` | Read/save the model idle timeout |
| `GET /api/resolutions`, `PUT /api/resolutions` | Reference VRAM table and persistent enabled agent presets |
| `POST /api/model/unload` | Release idle model weights; rejected during generation |
| `GET /api/jobs/{id}` | Progress or terminal state |
| `POST /api/jobs/{id}/cancel` | Cancel an active worker |
| `GET /api/jobs/{id}/image` | Completed PNG only |
| `GET /api/jobs/{id}/download?format=png\|webp\|jpeg` | Attachment in the chosen format; defaults to original PNG |
| `GET /system/gpu` | Demand-driven bounded GPU history |

The web dependencies are pinned separately in `requirements-ui.txt` and
`requirements-ui.lock`, constrained to the existing inference lock. Adding the
browser service did not update the qualified inference package versions.

```shell
uv pip compile requirements-ui.txt --constraint requirements.lock --python-version 3.13 --python-platform x86_64-unknown-linux-gnu --index-strategy unsafe-best-match --emit-index-url --no-annotate --no-header --output-file requirements-ui.lock
```
