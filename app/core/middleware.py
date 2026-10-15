"""HTTP 中间件：请求 ID、访问日志、安全响应头。

相对旧版的修复：
1. **请求链路**：旧版日志没有任何请求标识，多客户端并发时无法串起一次请求的
   所有日志。现在每个请求分配 ``request_id`` 并回写到响应头与日志。
2. **访问日志**：旧版只有 uvicorn 的默认访问日志，且被压到 WARNING 后完全消失。
   现在应用自己记录方法、路径、状态码、耗时、客户端。
3. **安全响应头**：旧版完全没有。现在补上 ``X-Content-Type-Options``、
   ``X-Frame-Options``、``Referrer-Policy`` 等基础头。
4. **不记录敏感值**：查询串会被记录，但 ``token`` 参数会被脱敏。
"""

from __future__ import annotations

import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.config import settings
from app.logger import get_request_id, logger, new_request_id, set_request_id

_SENSITIVE_QUERY_KEYS = {"token", "access_token", "password", "secret", "code"}

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-XSS-Protection": "0",
}

#: 健康检查等高频探针不打访问日志，避免刷屏
_QUIET_PATHS = {"/api/v1/health"}


def _sanitize_query(raw_query: str) -> str:
    if not raw_query:
        return ""
    parts = []
    for key, value in parse_qsl(raw_query, keep_blank_values=True):
        parts.append((key, "***" if key.lower() in _SENSITIVE_QUERY_KEYS else value))
    return urlencode(parts)


def _safe_target(request: Request) -> str:
    url = urlsplit(str(request.url))
    return urlunsplit(("", "", url.path, _sanitize_query(url.query), ""))


def _request_origin(request: Request) -> str | None:
    """从请求头推导「浏览器实际使用的 Origin」。

    浏览器访问 ``http://192.168.2.102:8000/...`` 时，``Host`` 头就是
    ``192.168.2.102:8000``，据此可以判断用户到底用的是哪个地址。
    """
    host = request.headers.get("host")
    if not host:
        return None
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
    return f"{scheme}://{host}"


def _is_same_origin(request: Request, origin: str) -> bool:
    """``Origin`` 是否与浏览器实际访问的地址同源。

    同源请求浏览器**不会**做 CORS 检查，即使该地址不在 ``ALLOWED_ORIGINS`` 里也能正常工作。
    因此这种「用户用局域网地址/机器名访问页面」的情况不应报警。
    """
    actual = _request_origin(request)
    return bool(actual) and actual.lower() == origin.lower()


class RequestContextMiddleware(BaseHTTPMiddleware):
    """分配 request_id、记录访问日志、追加安全响应头。"""

    def __init__(self, app: ASGIApp, *, add_security_headers: bool = True) -> None:
        super().__init__(app)
        self._add_security_headers = add_security_headers

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("X-Request-ID")
        request_id = incoming if incoming and len(incoming) <= 64 else new_request_id()
        set_request_id(request_id)
        request.state.request_id = request_id

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.error(
                "%s %s 未捕获异常（%.1fms）",
                request.method,
                _safe_target(request),
                elapsed_ms,
                exc_info=True,
            )
            raise

        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-ms"] = f"{elapsed_ms:.1f}"

        if self._add_security_headers:
            for key, value in _SECURITY_HEADERS.items():
                response.headers.setdefault(key, value)

        # 跨源被拒的显式告警。
        #
        # 浏览器对这个响应会直接报 "Failed to fetch"，用户看不到 400 的原因；
        # 而服务端如果只按普通 4xx 记一行，也很难看出是「访问地址不在白名单」。
        # 这里把结论直接写进日志，并把允许的地址一并列出，便于一次性定位。
        origin = request.headers.get("origin")
        if (
            origin
            and origin != "null"
            and origin not in settings.allowed_origins_list
            and not _is_same_origin(request, origin)
        ):
            logger.warning(
                "跨源请求被拒绝：Origin=%s 不在 ALLOWED_ORIGINS 中。"
                "浏览器会显示 Failed to fetch。"
                "请用 %s 访问，或把该 Origin 加入 .env 的 ALLOWED_ORIGINS。"
                "当前白名单=%s",
                origin,
                settings.allowed_origins_list[0] if settings.allowed_origins_list else "(空)",
                settings.allowed_origins_list,
            )

        if request.url.path not in _QUIET_PATHS:
            log = logger.warning if response.status_code >= 400 else logger.info
            log(
                "%s %s -> %d（%.1fms）client=%s",
                request.method,
                _safe_target(request),
                response.status_code,
                elapsed_ms,
                request.client.host if request.client else "-",
            )

        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """按 ``Content-Length`` 拒绝过大的请求体。

    旧版只在解码图片前检查字节数，但请求体已经被读进内存了。
    这里在读取之前就用 413 拒绝。
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int | None = None) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes or settings.MAX_FILE_SIZE

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        raw_length = request.headers.get("content-length")
        if raw_length and raw_length.isdigit() and int(raw_length) > self._max_bytes:
            from starlette.responses import JSONResponse

            logger.warning(
                "请求体过大被拒：%s 字节（上限 %d）path=%s",
                raw_length,
                self._max_bytes,
                request.url.path,
            )
            return JSONResponse(
                status_code=413,
                content={
                    "detail": f"请求体过大，上限 {self._max_bytes} 字节",
                    "code": "payload_too_large",
                    "request_id": get_request_id(),
                },
            )
        return await call_next(request)


__all__ = ["BodySizeLimitMiddleware", "RequestContextMiddleware"]
