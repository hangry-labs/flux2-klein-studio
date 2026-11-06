"""FIFO GPU admission with bounded, overlapping CPU output work."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
import threading
import time
import uuid

from fastapi import HTTPException
from PIL import Image
from PIL.PngImagePlugin import PngInfo

TERMINAL = {"completed", "failed", "cancelled"}


class GenerationQueue:
    """Scheduling mixin; Jobs owns storage, policy and the resident model."""

    def init_queue(self):
        self.queue_limit = int(os.getenv("STUDIO_QUEUE_LIMIT", "64"))
        if not 1 <= self.queue_limit <= 1024:
            raise ValueError("STUDIO_QUEUE_LIMIT must be between 1 and 1024")
        self.pending = deque()
        self.events = {}
        self.protected = set()
        self.finishing = set()
        self.cancel_event = threading.Event()
        self.output_pool = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="klein-output"
        )
        # Bound raw pixel buffers even when disk/CPU output cannot keep up.
        self.output_slots = threading.BoundedSemaphore(4)

    def queue_status(self):
        with self.lock:
            outstanding = sum(
                item["status"] not in TERMINAL for item in self.items.values()
            )
            return {
                "capacity": self.queue_limit,
                "outstanding": outstanding,
                "queued": len(self.pending),
                "processing": sum(
                    item["status"] == "processing" for item in self.items.values()
                ),
                "active_job": self.active,
                "available_slots": max(0, self.queue_limit - outstanding),
                "accepting_requests": not self.closing
                and outstanding < self.queue_limit,
            }

    def submit(self, options):
        return self.submit_many([options])[0]

    def submit_many(self, options, *, owned=False):
        with self.lock:
            if self.closing:
                raise HTTPException(503, "This instance is shutting down")
            if len(options) > self.queue_status()["available_slots"]:
                raise HTTPException(
                    429,
                    "The image queue is full. Retry or choose another instance.",
                    headers={"Retry-After": "1"},
                )
            items = []
            try:
                for settings in options:
                    identifier = uuid.uuid4().hex
                    item = dict(
                        id=identifier,
                        status="queued",
                        created=time.time(),
                        settings=settings.model_dump(),
                        error=None,
                    )
                    self.folder(item).mkdir()
                    items.append(item)
                    self.save(item)
            except OSError:
                # Atomic batch admission: nothing starts if preparation fails.
                import shutil

                for item in items:
                    shutil.rmtree(self.folder(item), ignore_errors=True)
                raise HTTPException(
                    503, "Could not prepare image jobs; check available storage."
                ) from None
            for item in items:
                identifier = item["id"]
                self.items[identifier] = item
                self.events[identifier] = threading.Event()
                if owned:
                    self.protected.add(identifier)
                self.pending.append(identifier)
            if self.active is None:
                identifier = self._next()
                self.worker = threading.Thread(
                    target=self.execute,
                    args=(identifier,),
                    daemon=True,
                    name="klein-gpu",
                )
                try:
                    self.worker.start()
                except (RuntimeError, OSError) as exc:
                    self.worker = None
                    self.active = None
                    for item in items:
                        key = item["id"]
                        if key in self.pending:
                            self.pending.remove(key)
                        self.events[key].set()
                        self.protected.discard(key)
                        item.update(
                            status="failed", error="Could not start the image worker"
                        )
                        self._save_terminal(key)
                    raise HTTPException(
                        503, "Could not start the image worker"
                    ) from exc
            self._prune()
            return [self.get(item["id"]) for item in items]

    def _next(self):
        self.active = None
        while self.pending and not self.closing:
            identifier = self.pending.popleft()
            if self.items[identifier]["status"] in TERMINAL:
                continue
            self.active = identifier
            self.cancel_event = self.events[identifier]
            self.items[identifier].update(status="loading", started=time.time())
            self.items[identifier]["queue_seconds"] = max(
                0, time.time() - self.items[identifier]["created"]
            )
            break
        return self.active

    def _save_terminal(self, identifier):
        try:
            self.save(self.items[identifier])
        except OSError:
            self.items[identifier].update(
                status="failed",
                error="Could not save job metadata. Check free disk space and file permissions.",
            )

    def execute(self, identifier):
        while identifier is not None:
            event = self.events[identifier]

            def phase(value, key=identifier):
                with self.lock:
                    if self.items[key]["status"] not in TERMINAL | {"cancelling"}:
                        self.items[key]["status"] = value

            self.output_slots.acquire()
            handed_off = False
            try:
                if event.is_set():
                    raise RuntimeError("Generation cancelled")
                result = self.model.generate(
                    self.items[identifier]["settings"], phase, event
                )
                with self.lock:
                    self.items[identifier]["status"] = "processing"
                    self.finishing.add(identifier)
                self.output_pool.submit(self.finish, identifier, result)
                handed_off = True
            except Exception:
                with self.lock:
                    self.items[identifier].update(
                        status="cancelled" if event.is_set() else "failed",
                        error=None
                        if event.is_set()
                        else "Generation failed. Check service logs; try a smaller canvas if VRAM is full.",
                    )
                    self._save_terminal(identifier)
            finally:
                if not handed_off:
                    self.finishing.discard(identifier)
                    self.output_slots.release()
                with self.lock:
                    identifier = self._next()

    def finish(self, identifier, result):
        """Hash, encode and persist CPU pixels while the GPU starts the next job."""
        try:
            if self.events[identifier].is_set():
                return
            if "pixels" in result:
                image = Image.frombytes("RGB", tuple(result["size"]), result["pixels"])
                metadata = PngInfo()
                metadata.add_text("generation", json.dumps(result["recipe"]))
                output = io.BytesIO()
                image.save(output, format="PNG", pnginfo=metadata)
                result["png"] = output.getvalue()
                result["pixel_sha256"] = hashlib.sha256(result["pixels"]).hexdigest()
            with self.lock:
                if self.events[identifier].is_set():
                    return
                item = self.items[identifier]
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
        except Exception:
            with self.lock:
                self.items[identifier].update(
                    status="failed",
                    error="Could not prepare the generated image. Check service logs and available storage.",
                )
        finally:
            with self.lock:
                if self.events[identifier].is_set():
                    self.items[identifier]["status"] = "cancelled"
                self._save_terminal(identifier)
                self.finishing.discard(identifier)
            self.output_slots.release()

    def cancel(self, identifier):
        with self.lock:
            item = self.get(identifier)
            if item["status"] not in TERMINAL:
                self.events[identifier].set()
                if identifier == self.active:
                    # The GPU owner observes its own event and tears down its worker.
                    self.items[identifier]["status"] = "cancelling"
                else:
                    self.items[identifier]["status"] = "cancelled"
                    if identifier in self.pending:
                        self.pending.remove(identifier)
                    self._save_terminal(identifier)
            return self.get(identifier)

    def release(self, identifier):
        with self.lock:
            self.protected.discard(identifier)
            self._prune()

    def _prune(self):
        for saved in (False, True):
            cohort = [
                key
                for key, item in self.items.items()
                if item["settings"].get("save_gallery", True) == saved
            ]
            candidates = sorted(
                (
                    key
                    for key in cohort
                    if key not in self.protected
                    and key not in self.finishing
                    and self.items[key]["status"] in TERMINAL
                    and key != self.active
                ),
                key=lambda key: self.items[key]["created"],
            )
            for key in candidates[: max(0, len(cohort) - 100)]:
                if saved:
                    del self.items[key]
                else:
                    try:
                        self.delete(key)
                    except (HTTPException, OSError):
                        # An open old preview must not break a newly admitted job.
                        continue
                self.events.pop(key, None)

    def close(self):
        with self.lock:
            self.closing = True
            for identifier in list(self.items):
                if self.items[identifier]["status"] not in TERMINAL:
                    self.cancel(identifier)
        self.model.close()
        if self.worker and self.worker.ident is not None:
            self.worker.join(timeout=8)
        self.output_pool.shutdown(wait=True, cancel_futures=False)
        self.temporary.cleanup()
