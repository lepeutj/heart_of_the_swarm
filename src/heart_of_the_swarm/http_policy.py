from __future__ import annotations

import json
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class HTTPPolicy(BaseModel):
    """Bound untrusted HTTP request bodies before application parsing starts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_request_bytes: int = Field(default=2_000_000, ge=1)


class RequestBodyLimitMiddleware:
    """Reject oversized bodies before routing, dependencies, or endpoint side effects."""

    def __init__(self, app: ASGIApp, policy: HTTPPolicy) -> None:
        self.app = app
        self.policy = policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = _content_length(scope.get("headers", ()))
        if content_length is not None and content_length > self.policy.max_request_bytes:
            await self._reject(send)
            return

        messages: list[Message] = []
        received_bytes = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                messages.append(message)
                break
            received_bytes += len(message.get("body", b""))
            if received_bytes > self.policy.max_request_bytes:
                await self._reject(send)
                return
            messages.append(message)
            if not message.get("more_body", False):
                break

        async def replay() -> Message:
            if messages:
                return messages.pop(0)
            return await receive()

        await self.app(scope, replay, send)

    async def _reject(self, send: Send) -> None:
        body = json.dumps(
            {
                "detail": {
                    "code": "http.request_too_large",
                    "message": "request body exceeds configured limit",
                    "max_request_bytes": self.policy.max_request_bytes,
                }
            },
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _content_length(headers: Iterable[tuple[bytes, bytes]]) -> int | None:
    for name, value in headers:
        if name.lower() != b"content-length":
            continue
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None
