"""Observe peer disconnects after request bodies, including mounted MCP apps."""

import asyncio
from contextlib import suppress


class DisconnectMonitor:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        state = scope.setdefault("state", {})
        state["studio_disconnected"] = False
        watcher = None

        async def watch():
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    state["studio_disconnected"] = True
                    return

        async def watched_receive():
            nonlocal watcher
            if watcher is not None:
                await asyncio.shield(watcher)
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.disconnect":
                state["studio_disconnected"] = True
            elif message["type"] == "http.request" and not message.get(
                "more_body", False
            ):
                # The body is exhausted. One reader now owns the disconnect signal;
                # status polls inspect state without competing to consume ASGI events.
                watcher = asyncio.create_task(watch())
            return message

        try:
            await self.app(scope, watched_receive, send)
        finally:
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher


async def client_disconnected(request):
    return request.scope.get("state", {}).get("studio_disconnected", False)
