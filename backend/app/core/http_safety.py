from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi.responses import JSONResponse


Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
AsgiApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class _PayloadTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    """Bound declared and streamed HTTP request bodies before route parsing."""

    def __init__(self, app: AsgiApp, *, max_body_bytes: int):
        if max_body_bytes < 1024:
            raise RuntimeError("RIFFLOOM_REQUEST_MAX_BODY_BYTES 不得小于 1024")
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        state = scope.get("state") or {}
        response = JSONResponse(
            status_code=413,
            content={
                "error": {
                    "code": "PAYLOAD_TOO_LARGE",
                    "message": "请求体超过服务允许的大小",
                    "retryable": False,
                    "request_id": state.get("request_id"),
                    "trace_id": state.get("trace_id"),
                    "details": {"max_body_bytes": self.max_body_bytes},
                }
            },
        )
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.lower(): value
            for key, value in scope.get("headers", [])
        }
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                declared_size = int(declared)
            except ValueError:
                declared_size = self.max_body_bytes + 1
            if declared_size < 0 or declared_size > self.max_body_bytes:
                await self._reject(scope, receive, send)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise _PayloadTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _PayloadTooLarge:
            await self._reject(scope, receive, send)
