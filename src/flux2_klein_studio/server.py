"""Local studio: bounded jobs around the qualified CLI, without importing torch."""

import json
import re
import shutil
import subprocess
import threading
import time
import uuid
import tempfile
from collections import deque
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .cli import MODEL_ID, MODEL_REVISION
from .residency import ModelWorker

STATIC = Path(__file__).with_name("static")
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


class Generation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    prompt: str = Field(min_length=1, max_length=4000)
    seed: int = Field(default=12345, ge=0, le=2**63 - 1)
    width: int = Field(default=1024, ge=64, le=1920, multiple_of=16)
    height: int = Field(default=1024, ge=64, le=1920, multiple_of=16)
    steps: int = Field(default=4, ge=1, le=50)
    save_gallery: bool = False

    @field_validator("prompt")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Describe the image you want to create")
        return value.strip()


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    idle_seconds: int = Field(default=300, ge=0, le=86400)


class Jobs:
    def __init__(self, root):
        self.root = root
        self.lock = threading.RLock()
        self.items = {}
        self.active = None
        self.temporary = tempfile.TemporaryDirectory(prefix="klein-studio-")
        self.model = ModelWorker()
        self.worker = None
        self.closing = False
        root.mkdir(parents=True, exist_ok=True)
        settings_path = root / "settings.json"
        if settings_path.exists():
            with suppress(ValueError, OSError, TypeError):
                self.model.idle_seconds = Settings(
                    **json.loads(settings_path.read_text())
                ).idle_seconds
        for path in sorted(
            root.glob("*/job.json"), key=lambda path: path.stat().st_mtime, reverse=True
        )[:100]:
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                if item["id"] != path.parent.name or not re.fullmatch(
                    r"[a-f0-9]{32}", item["id"]
                ):
                    continue
                if item["status"] not in TERMINAL:
                    item.update(
                        status="failed",
                        error="Generation was interrupted by a studio restart.",
                    )
                self.items[item["id"]] = item
            except (OSError, ValueError, KeyError):
                continue

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

    def submit(self, options):
        with self.lock:
            if self.closing or self.active:
                raise HTTPException(
                    409, "A generation is already active. Wait or cancel it first."
                )
            identifier = uuid.uuid4().hex
            item = {
                "id": identifier,
                "status": "loading",
                "created": time.time(),
                "settings": options.model_dump(),
                "error": None,
            }
            self.folder(item).mkdir()
            self.items[identifier] = item
            # Temporary previews must not evict saved gallery records.
            cohort = [
                key
                for key in self.items
                if self.items[key]["settings"].get("save_gallery", True)
                == options.save_gallery
            ]
            if len(cohort) > 100:
                oldest = min(cohort, key=lambda key: self.items[key]["created"])
                if not self.items[oldest]["settings"].get("save_gallery", True):
                    self.delete(oldest)
                else:
                    del self.items[oldest]
            self.active = identifier
            self.cancel_event = threading.Event()
            self.save(item)
            self.worker = threading.Thread(
                target=self.execute, args=(identifier,), daemon=True
            )
            self.worker.start()
            return dict(item)

    def execute(self, identifier):
        def phase(value):
            with self.lock:
                if self.items[identifier]["status"] != "cancelling":
                    self.items[identifier]["status"] = value

        try:
            with self.lock:
                item = self.items[identifier]
                if item["status"] == "cancelling":
                    item["status"] = "cancelled"
                    return
            result = self.model.generate(item["settings"], phase, self.cancel_event)
            with self.lock:
                if item["status"] == "cancelling":
                    item["status"] = "cancelled"
                else:
                    folder = self.folder(item)
                    (folder / "generate.png").write_bytes(result["png"])
                    (folder / "report.json").write_text(
                        json.dumps(result.get("report", {}), indent=2), encoding="utf-8"
                    )
                    item.update(
                        status="completed",
                        seconds=result["seconds"],
                        pixel_sha256=result["pixel_sha256"],
                        image=f"/api/jobs/{identifier}/image",
                    )
        except (EOFError, OSError, ValueError, KeyError, RuntimeError):
            with self.lock:
                item = self.items[identifier]
                if item["status"] == "cancelling":
                    item["status"] = "cancelled"
                else:
                    item.update(
                        status="failed",
                        error="Generation failed. Check service logs; try a smaller canvas if VRAM is full.",
                    )
        finally:
            with self.lock:
                try:
                    self.save(self.items[identifier])
                except OSError:
                    self.items[identifier].update(
                        status="failed",
                        error="Could not save job metadata. Check free disk space and file permissions.",
                    )
                finally:
                    self.active = None

    def get(self, identifier):
        with self.lock:
            if identifier not in self.items:
                raise HTTPException(404, "Image job not found")
            item = dict(self.items[identifier])
            return item

    def cancel(self, identifier):
        with self.lock:
            item = self.get(identifier)
            if item["status"] not in TERMINAL:
                self.items[identifier]["status"] = "cancelling"
                self.cancel_event.set()
                self.model.unload()
            return self.get(identifier)

    def close(self):
        with self.lock:
            self.closing = True
            if self.active:
                self.cancel(self.active)
        self.model.close()
        if self.worker:
            self.worker.join(timeout=8)
        self.temporary.cleanup()

    def delete(self, identifier):
        """Remove only a known, inactive job directory and all its local artifacts."""
        with self.lock:
            if not re.fullmatch(r"[a-f0-9]{32}", identifier):
                raise HTTPException(404, "Image job not found")
            item = self.get(identifier)
            if identifier == self.active or item["status"] not in TERMINAL:
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


def create_app(root=Path("/data/outputs/studio")):
    jobs = Jobs(root)
    gpu = GPU()

    @asynccontextmanager
    async def lifespan(_app):
        yield
        jobs.close()
        gpu.stop.set()
        if gpu.thread:
            gpu.thread.join(timeout=5)

    app = FastAPI(
        title="FLUX.2 Klein Studio",
        version="0.1.0-dev",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.jobs = jobs
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"],
    )

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        origin = request.headers.get("origin")
        if (
            request.method not in ("GET", "HEAD", "OPTIONS")
            and origin
            and origin != str(request.base_url).rstrip("/")
        ):
            raise_error = HTTPException(
                403, "Cross-origin generation requests are disabled"
            )
            from fastapi.responses import JSONResponse

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

    @app.get("/api/status")
    def status():
        return {
            "state": "busy" if jobs.active else "ready",
            "active_job": jobs.active,
            "model": MODEL_ID,
            "revision": MODEL_REVISION,
            "version": "0.1.0-dev",
            "model_lifecycle": "Resident worker with configurable idle unload",
            "model_residency": jobs.model.snapshot(),
            "presets": PRESETS,
            "defaults": Generation(prompt="example").model_dump(exclude={"prompt"}),
            "limits": {
                "min_dimension": 64,
                "max_dimension": 1920,
                "dimension_multiple": 16,
            },
            "reduction": "ordered",
            "offloading": False,
        }

    @app.get("/api/settings")
    def settings():
        return {"idle_seconds": jobs.model.idle_seconds}

    @app.put("/api/settings")
    def configure(options: Settings):
        with jobs.lock:
            path = root / "settings.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(options.model_dump_json(), encoding="utf-8")
            temporary.replace(path)
            with jobs.model.lock:
                jobs.model.idle_seconds = options.idle_seconds
        return options.model_dump()

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
        item = jobs.get(identifier)
        folder = jobs.folder(item)
        paths = list(folder.glob("*/generate.png"))
        if (folder / "generate.png").is_file():
            paths.append(folder / "generate.png")
        if item["status"] != "completed" or len(paths) != 1:
            raise HTTPException(404, "Image is not available")
        return FileResponse(
            paths[0],
            media_type="image/png",
            filename=f"flux2-{item['settings']['seed']}.png",
            content_disposition_type="inline",
        )

    @app.get("/system/gpu")
    def telemetry():
        return gpu.snapshot()

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
