"""A stub Webhook receiver: a real HTTP server on a free localhost port."""

import asyncio
import json
import socket
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn


class WebhookReceiver:
    """Records every POST and answers with a scriptable status, delay and body drip."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.arrivals: dict[str, list[float]] = {}
        """payment_id -> time.monotonic() of each call's arrival."""
        self.status = 200
        self.delay = 0.0
        self.script: list[tuple[int, float]] = []
        """(status, delay) answers for the next calls, used before `status`/`delay`."""
        self.drip_seconds = 0.0
        self.abandoned: list[str] = []
        """payment_ids whose request the client hung up on mid-response."""
        self.url = ""

    def for_payment(self, payment_id: str) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["payment_id"] == payment_id]

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":  # lifespan: uvicorn is configured with lifespan off
            return
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        payload = json.loads(body)
        self.requests.append(payload)
        self.arrivals.setdefault(payload["payment_id"], []).append(time.monotonic())
        status, delay = self.script.pop(0) if self.script else (self.status, self.delay)
        if delay:
            await asyncio.sleep(delay)
        await send({"type": "http.response.start", "status": status, "headers": []})
        # Optionally drip the body so no single read times out but the total is long.
        disconnect = asyncio.create_task(receive())
        for _ in range(int(self.drip_seconds / 0.1)):
            if disconnect.done():
                self.abandoned.append(payload["payment_id"])
                return
            await send({"type": "http.response.body", "body": b".", "more_body": True})
            await asyncio.sleep(0.1)
        disconnect.cancel()
        await send({"type": "http.response.body", "body": b""})


@asynccontextmanager
async def serve_webhook_receiver() -> AsyncIterator[WebhookReceiver]:
    receiver = WebhookReceiver()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    receiver.url = f"http://127.0.0.1:{sock.getsockname()[1]}/webhook"
    server = uvicorn.Server(
        uvicorn.Config(receiver, lifespan="off", log_level="warning")
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        if task.done():
            task.result()
        await asyncio.sleep(0.01)
    try:
        yield receiver
    finally:
        server.should_exit = True
        await task
