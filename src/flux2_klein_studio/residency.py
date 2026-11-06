"""Single resident worker with a configurable idle deadline and owned teardown."""

import multiprocessing
import threading
import time

from .studio_worker import serve


class ModelWorker:
    def __init__(self, idle_seconds=300):
        self.lock = threading.RLock()
        self.process = None
        self.connection = None
        self.state = "unloaded"
        self.last_used = 0
        self.idle_seconds = idle_seconds
        self.stop = threading.Event()
        self.monitor = threading.Thread(target=self.expire, daemon=True)
        self.monitor.start()

    def expire(self):
        while not self.stop.wait(0.5):
            with self.lock:
                if (
                    self.state == "ready"
                    and time.monotonic() - self.last_used >= self.idle_seconds
                ):
                    self.unload()

    def unload(self):
        with self.lock:
            if self.process and self.process.pid is not None:
                self.process.terminate()
                self.process.join(timeout=5)
                if self.process.is_alive():
                    self.process.kill()
                    self.process.join(timeout=5)
            if self.connection:
                self.connection.close()
            self.process = self.connection = None
            self.state = "unloaded"

    def snapshot(self):
        with self.lock:
            return {
                "state": self.state,
                "idle_seconds": self.idle_seconds,
                "unload_in_seconds": max(
                    0, round(self.idle_seconds - (time.monotonic() - self.last_used))
                )
                if self.state == "ready"
                else None,
            }

    def generate(self, settings, phase, cancelled):
        with self.lock:
            if cancelled.is_set():
                raise RuntimeError("Generation cancelled")
            if self.process is None:
                context = multiprocessing.get_context("spawn")
                parent, child = context.Pipe()
                self.process = context.Process(target=serve, args=(child,), daemon=True)
                self.connection = parent
                try:
                    self.process.start()
                except (OSError, RuntimeError):
                    self.unload()
                    raise
                finally:
                    child.close()
            connection = self.connection
            self.state = "generating"
            try:
                connection.send(settings)
            except (EOFError, OSError):
                self.unload()
                raise
        deadline = time.monotonic() + 300
        try:
            while time.monotonic() < deadline:
                if not connection.poll(0.2):
                    continue
                event = connection.recv()
                if event["phase"] in ("loading", "generating"):
                    with self.lock:
                        self.state = event["phase"]
                    phase(event["phase"])
                elif event["phase"] == "completed":
                    with self.lock:
                        self.state = "ready"
                        self.last_used = time.monotonic()
                    return event
                else:
                    raise RuntimeError(event["error"])
            raise RuntimeError("Generation exceeded the five-minute limit")
        except (EOFError, OSError, RuntimeError):
            self.unload()
            raise

    def close(self):
        self.stop.set()
        self.unload()
        self.monitor.join(timeout=6)
