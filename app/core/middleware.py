"""HTTP中间件：请求ID与安全响应头"""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.config import settings

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-XSS-Protection": "0",
}

class RequestContextMiddleware(BaseHTTPMiddleware):
    """分配request_id、追加响应头"""

    def __init__(self, app: ASGIApp, *, add_security_headers: bool = True) -> None:
        super().__init__(app)
        self._add_security_headers = add_security_headers

    # 方法：设置请求ID，计时调用下游，并附加响应头
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("X-Request-ID")
        request_id = incoming if incoming and len(incoming) <= 64 else uuid.uuid4().hex[:8]
        request.state.request_id = request_id

        started = time.perf_counter()
        response = await call_next(request)

        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-ms"] = f"{elapsed_ms:.1f}"

        if self._add_security_headers:
            for key, value in _SECURITY_HEADERS.items():
                response.headers.setdefault(key, value)

        return response


class _PayloadTooLarge(Exception):
    """实际接收字节超过限额（Content-Length缺失/谎报时的流式兜底）"""


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """限制请求体大小：优先按 ``Content-Length`` 拒绝，缺失/谎报时按实际接收字节流式限额

    旧版只在解码图片前检查字节数，但请求体已经被读进内存了
    这里在读取之前就用413拒绝；声明长度缺失或不可信时，
    包装receive通道累计实际字节数，超限即中断
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int | None = None) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes or settings.MAX_FILE_SIZE

    def _reject(self, request: Request) -> Response:
        from starlette.responses import JSONResponse

        return JSONResponse(
            status_code=413,
            content={
                "detail": f"请求体过大，上限 {self._max_bytes} 字节",
                "code": "payload_too_large",
                "request_id": getattr(request.state, "request_id", "-"),
            },
        )

    # 方法：声明长度超限直接拒绝；否则包装receive按实际接收字节兜底
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        raw_length = request.headers.get("content-length")
        if raw_length and raw_length.isdigit() and int(raw_length) > self._max_bytes:
            return self._reject(request)

        # 流式限额：无论声明是否缺失/可信，都累计实际接收字节
        received = 0
        original_receive = request._receive

        async def limited_receive():
            nonlocal received
            message = await original_receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_bytes:
                    raise _PayloadTooLarge
            return message

        request._receive = limited_receive
        try:
            return await call_next(request)
        except _PayloadTooLarge:
            return self._reject(request)
        finally:
            request._receive = original_receive


__all__ = ["BodySizeLimitMiddleware", "RequestContextMiddleware"]

