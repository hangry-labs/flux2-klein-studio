"""Local studio: bounded jobs around the qualified CLI, without importing torch."""

import json
import logging
from .adapters import catalog as adapter_catalog
import hmac
import os
import re
import shutil
import subprocess
import threading
import time
import tempfile
from typing import Literal
from collections import deque
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .cli import MODEL_ID, MODEL_REVISION
from .residency import ModelWorker
from .schemas import Generation
from .images import encode_image
from .presets import catalog
from .transport import DisconnectMonitor
from .resolution_policy import ResolutionPolicy, ResolutionSettings
from . import __version__
from .build_info import build_identity, offline_default
from .job_queue import GenerationQueue
from .references import ReferenceStore, MAX_REQUEST_BYTES

STATIC = Path(__file__).with_name("static")
ASSETS = Path(__file__).resolve().parents[2] / "assets"
TERMINAL = {"completed", "failed", "cancelled"}
PRESETS = [
    (1024, 1024),
    (512, 512),
    (768, 768),
    (1280, 1280),
    (1536, 1024),
    (1024, 1536),
    (1920, 1088),
    (1088, 1920),
    (1536, 1536),
]
logger = logging.getLogger(__name__)


class StoredGeneration(Generation):
    """Validate core settings while retaining reference and adapter snapshots."""

    model_config = ConfigDict(extra="allow", strict=True)
    # A job.json under the saved root predates opt-in storage in early builds.
    # Absence of the flag means saved, matching folder() and gallery listing.
    save_gallery: bool = True


class StoredJob(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True, allow_inf_nan=False)
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    status: Literal[
        "queued",
        "loading",
        "generating",
        "processing",
        "cancelling",
        "completed",
        "failed",
        "cancelled",
    ]
    created: float = Field(ge=0)
    settings: StoredGeneration
    seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def saved_result(self):
        if not self.settings.save_gallery:
            raise ValueError("Only saved jobs can be restored")
        if self.status == "completed" and self.seconds is None:
            raise ValueError("Completed jobs need generation timing")
        return self


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    idle_seconds: int = Field(default=300, ge=0, le=86400)
    keep_loaded: bool = False


class Jobs(GenerationQueue):
    def __init__(self, root):
        self.root = root
        self.lock = threading.RLock()
        root.mkdir(parents=True, exist_ok=True)
        self.resolutions = ResolutionPolicy(root / "agent-resolutions.json", self.lock)
        self.items = {}
        self.init_queue()
        self.active = None
        self.temporary = tempfile.TemporaryDirectory(prefix="klein-studio-")
        self.references = ReferenceStore(Path(self.temporary.name) / "references")
        self.model = ModelWorker()
        self.worker = None
        self.closing = False
        root.mkdir(parents=True, exist_ok=True)
        settings_path = root / "settings.json"
        if settings_path.exists():
            with suppress(ValueError, OSError, TypeError):
                options = Settings(**json.loads(settings_path.read_text()))
                self.model.idle_seconds = options.idle_seconds
                self.model.keep_loaded = options.keep_loaded
        for path in root.glob("*/job.json"):
            try:
                stored = StoredJob.model_validate_json(path.read_text(encoding="utf-8"))
                if stored.id != path.parent.name:
                    raise ValueError("Job ID does not match its folder")
                item = stored.model_dump(exclude_unset=True)
                if stored.status == "completed":
                    item["image"] = f"/api/jobs/{stored.id}/image"
                if item["status"] not in TERMINAL:
                    item.update(
                        status="failed",
                        error="Generation was interrupted by a studio restart.",
                    )
                self.items[item["id"]] = item
            except (OSError, ValueError):
                logger.warning(
                    "Skipping invalid saved job record %s", path, exc_info=True
                )

    def folder(self, item):
        return (
            self.root
            if item["settings"].get("save_gallery", True)
            else Path(self.temporary.name)
        ) / item["id"]

    def save(self, item):
        if not item["settings"].get("save_gallery", True):
            return
        path = self.folder(item) / "job.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(item, indent=2), encoding="utf-8")
        temporary.replace(path)

    def get(self, identifier):
        with self.lock:
            if identifier not in self.items:
                raise HTTPException(404, "Image job not found")
            item = dict(self.items[identifier])
            if identifier in self.pending:
                item["queue_position"] = self.pending.index(identifier) + 1
            else:
                item["queue_position"] = 0
            return item

    def image_path(self, identifier):
        item = self.get(identifier)
        folder = self.folder(item)
        paths = list(folder.glob("*/generate.png"))
        if (folder / "generate.png").is_file():
            paths.append(folder / "generate.png")
        if item["status"] != "completed" or len(paths) != 1:
            raise HTTPException(404, "Image is not available")
        return item, paths[0]

    def delete(self, identifier):
        """Remove only a known, inactive job directory and all its local artifacts."""
        with self.lock:
            if not re.fullmatch(r"[a-f0-9]{32}", identifier):
                raise HTTPException(404, "Image job not found")
            item = self.get(identifier)
            if (
                identifier == self.active
                or item["status"] not in TERMINAL
                or identifier in self.protected
                or identifier in self.finishing
            ):
                raise HTTPException(
                    409, "Cancel the job and wait for it to stop before deleting it."
                )
            folder = self.folder(item)
            owner = (
                self.root
                if item["settings"].get("save_gallery", True)
                else Path(self.temporary.name)
            )
            if (
                folder.is_symlink()
                or folder.is_junction()
                or folder.resolve().parent != owner.resolve()
            ):
                raise HTTPException(
                    409, "Refusing to delete a linked or unexpected job directory."
                )
            try:
                if folder.exists():
                    shutil.rmtree(folder)
            except OSError as exc:
                raise HTTPException(
                    409,
                    "Could not remove all job files. Close any open downloads and retry.",
                ) from exc
            del self.items[identifier]
            self.events.pop(identifier, None)
            return {"id": identifier, "deleted": True}


class GPU:
    """No sampling until System requests it; bounded history and idle lifetime."""

    def __init__(self):
        self.lock = threading.Lock()
        self.history = deque(maxlen=600)
        self.requested = 0
        self.thread = None
        self.stop = threading.Event()

    def sample(self):
        try:
            raw = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw,power.limit,fan.speed",
                    "--format=csv,noheader,nounits",
                ],
                text=True,
                timeout=4,
            )
            devices = []
            for line in raw.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) != 9:
                    continue
                values = {}
                for key, part in zip(
                    (
                        "used",
                        "total",
                        "utilization",
                        "temperature",
                        "power",
                        "power_limit",
                        "fan",
                    ),
                    parts[2:],
                    strict=True,
                ):
                    try:
                        values[key] = float(part)
                    except ValueError:
                        values[key] = None
                devices.append({"index": parts[0], "name": parts[1], **values})
            return devices
        except (OSError, subprocess.SubprocessError):
            return []

    def loop(self):
        while not self.stop.is_set():
            with self.lock:
                if time.monotonic() - self.requested > 60:
                    self.thread = None
                    return
            sample = {"timestamp": time.time(), "gpus": self.sample()}
            with self.lock:
                self.history.append(sample)
            self.stop.wait(1)

    def snapshot(self):
        with self.lock:
            self.requested = time.monotonic()
            if self.thread is None:
                self.thread = threading.Thread(target=self.loop, daemon=True)
                self.thread.start()
            return {
                "gpus": self.history[-1]["gpus"] if self.history else [],
                "history": list(self.history),
                "sample_interval_seconds": 1,
                "idle_timeout_seconds": 60,
            }


def create_app(root=Path("/app/persistent/outputs/studio")):
    from .image_api import attach_image_api
    from .mcp_server import create_mcp_server, SKILL_FILE, SKILL_PATH

    jobs = Jobs(root)
    gpu = GPU()
    mcp = create_mcp_server(jobs)
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        max_request_body_size=MAX_REQUEST_BYTES,
        # The enclosing app enforces the same Host/Origin policy for every route.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )

    @asynccontextmanager
    async def lifespan(_app):
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            jobs.close()
            gpu.stop.set()
            if gpu.thread:
                gpu.thread.join(timeout=5)

    app = FastAPI(
        title="FLUX.2 Klein Studio",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.jobs = jobs
    app.state.mcp = mcp
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[
            value.strip()
            for value in os.getenv(
                "STUDIO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],testserver"
            ).split(",")
            if value.strip()
        ],
    )
    api_key = os.getenv("STUDIO_API_KEY", "").strip()

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        if api_key and (
            request.url.path.startswith("/v1/")
            or request.url.path.rstrip("/") == "/mcp"
        ):
            scheme, _, key = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(
                key.encode(), api_key.encode()
            ):
                return JSONResponse(
                    {
                        "error": {
                            "message": "A valid API bearer key is required.",
                            "type": "authentication_error",
                            "param": None,
                            "code": "invalid_api_key",
                        }
                    },
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
                )
        origin = request.headers.get("origin")
        if (
            request.method not in ("GET", "HEAD", "OPTIONS")
            and origin
            and origin != str(request.base_url).rstrip("/")
        ):
            raise_error = HTTPException(
                403, "Cross-origin generation requests are disabled"
            )
            return JSONResponse({"detail": raise_error.detail}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'"
        )
        return response

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get(SKILL_PATH, include_in_schema=False)
    def agent_skill():
        return FileResponse(SKILL_FILE, media_type="text/markdown")

    @app.get("/api/status")
    def status():
        return {
            "state": "busy" if jobs.active else "ready",
            "active_job": jobs.active,
            "queue": jobs.queue_status(),
            "model": MODEL_ID,
            "revision": MODEL_REVISION,
            "version": __version__,
            "build": build_identity(),
            "model_lifecycle": "Resident worker with configurable idle unload",
            "model_residency": jobs.model.snapshot(),
            "presets": PRESETS,
            "resolution_presets": catalog(),
            "adapters": adapter_catalog(offline_default(), mode="generate"),
            "edit_adapters": adapter_catalog(offline_default(), mode="edit"),
            "agent_api": {
                "openai_base_url": "/v1",
                "mcp": "/mcp/",
                "api_key_required": bool(api_key),
            },
            "defaults": Generation(prompt="example").model_dump(exclude={"prompt"}),
            "limits": {
                "min_dimension": 64,
                "max_dimension": 1920,
                "dimension_multiple": 16,
            },
            "reduction": "ordered",
            "offloading": False,
            "download_formats": [
                {"value": "png", "label": "PNG · Original", "extension": "png"},
                {"value": "webp", "label": "WebP · Lossless", "extension": "webp"},
                {"value": "jpeg", "label": "JPEG · Smaller, lossy", "extension": "jpg"},
            ],
        }

    @app.get("/api/settings")
    def settings():
        return {
            "idle_seconds": jobs.model.idle_seconds,
            "keep_loaded": jobs.model.keep_loaded,
        }

    @app.put("/api/settings")
    def configure(options: Settings):
        with jobs.lock:
            path = root / "settings.json"
            temporary = path.with_suffix(".tmp")
            try:
                temporary.write_text(options.model_dump_json(), encoding="utf-8")
                temporary.replace(path)
            except OSError as exc:
                raise HTTPException(
                    409,
                    "Could not save model settings. Check free disk space and permissions.",
                ) from exc
            with jobs.model.lock:
                jobs.model.idle_seconds = options.idle_seconds
                jobs.model.keep_loaded = options.keep_loaded
        return options.model_dump()

    @app.get("/api/resolutions")
    def resolution_settings():
        return jobs.resolutions.snapshot()

    @app.put("/api/resolutions")
    def configure_resolutions(options: ResolutionSettings):
        try:
            result = jobs.resolutions.save(options)
        except OSError as exc:
            raise HTTPException(
                409,
                "Could not save resolution settings. Check free disk space and permissions.",
            ) from exc
        app.openapi_schema = None
        return result

    @app.post("/api/model/load")
    def load_model():
        with jobs.lock:
            if jobs.active:
                raise HTTPException(
                    409, "Wait for generation before loading the model."
                )
            try:
                return jobs.model.preload()
            except (OSError, RuntimeError) as exc:
                raise HTTPException(
                    503, "Model preload failed; inspect service health."
                ) from exc

    @app.post("/api/model/trim")
    def trim_model():
        with jobs.lock:
            if jobs.active:
                raise HTTPException(
                    409, "Wait for generation before trimming the allocator."
                )
            try:
                return jobs.model.trim()
            except (OSError, RuntimeError) as exc:
                raise HTTPException(
                    503, "Allocator trim failed; inspect service health."
                ) from exc

    @app.post("/api/model/unload")
    def unload_model():
        with jobs.lock:
            if jobs.active:
                raise HTTPException(
                    409, "Wait for generation or cancel it before unloading the model."
                )
            jobs.model.unload()
        return jobs.model.snapshot()

    @app.get("/api/jobs")
    def listing():
        with jobs.lock:
            return [
                jobs.get(key)
                for key in sorted(
                    (
                        key
                        for key in jobs.items
                        if jobs.items[key]["settings"].get("save_gallery", True)
                    ),
                    key=lambda key: jobs.items[key]["created"],
                    reverse=True,
                )
            ]

    @app.post("/api/jobs", status_code=202)
    def generate(options: Generation):
        return jobs.submit(options)

    @app.get("/api/jobs/{identifier}")
    def job(identifier: str):
        return jobs.get(identifier)

    @app.delete("/api/jobs/{identifier}")
    def delete(identifier: str):
        return jobs.delete(identifier)

    @app.post("/api/jobs/{identifier}/cancel")
    def cancel(identifier: str):
        return jobs.cancel(identifier)

    @app.get("/api/jobs/{identifier}/image")
    def image(identifier: str):
        item, path = jobs.image_path(identifier)
        return FileResponse(
            path,
            media_type="image/png",
            filename=f"flux2-{item['settings']['seed']}.png",
            content_disposition_type="inline",
        )

    @app.get("/api/jobs/{identifier}/download")
    def download(identifier: str, format: Literal["png", "webp", "jpeg"] = "png"):
        item, path = jobs.image_path(identifier)
        filename = f"flux2-{item['settings']['seed']}"
        if format == "png":
            return FileResponse(
                path, media_type="image/png", filename=filename + ".png"
            )
        # Convert on demand, without creating files or changing the canonical PNG.
        try:
            output = encode_image(path, format, item["settings"])
        except FileNotFoundError as exc:
            raise HTTPException(404, "Image is not available") from exc
        extension = "jpg" if format == "jpeg" else format
        return Response(
            output,
            media_type=f"image/{format}",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}.{extension}"'
            },
        )

    @app.get("/system/gpu")
    def telemetry():
        return gpu.snapshot()

    from .reference_api import attach_reference_api

    attach_reference_api(app, jobs)
    attach_image_api(app, jobs)
    app.add_middleware(DisconnectMonitor)
    app.mount("/mcp", mcp_app, name="mcp")
    app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
