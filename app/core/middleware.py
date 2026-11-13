# """架构作用：在ASGI请求边界串联请求标识、体积门禁、日志与安全头"""
"""HTTP中间件：请求ID、访问日志、安全响应头

查询串会被记录，但 ``token`` 参数会被脱敏
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


# """函数：解析查询参数并替换敏感键对应的值后重新编码"""
def _sanitize_query(raw_query: str) -> str:
    if not raw_query:
        return ""
    parts = []
    for key, value in parse_qsl(raw_query, keep_blank_values=True):
        parts.append((key, "***" if key.lower() in _SENSITIVE_QUERY_KEYS else value))
    return urlencode(parts)


# """函数：生成仅含路径和脱敏查询串的访问日志目标"""
def _safe_target(request: Request) -> str:
    url = urlsplit(str(request.url))
    return urlunsplit(("", "", url.path, _sanitize_query(url.query), ""))


# """函数：从Host与代理协议头推导浏览器访问源"""
def _request_origin(request: Request) -> str | None:
    """从请求头推导「浏览器实际使用的Origin」

    浏览器访问 ``http://192.168.2.102:8000/...`` 时，``Host`` 头就是
    ``192.168.2.102:8000``，据此可以判断用户到底用的是哪个地址
    """
    host = request.headers.get("host")
    if not host:
        return None
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
    return f"{scheme}://{host}"


# """函数：比较请求源与当前请求可推导出的同源地址"""
def _is_same_origin(request: Request, origin: str) -> bool:
    """``Origin`` 是否与浏览器实际访问的地址同源

    同源请求浏览器**不会**做CORS检查，即使该地址不在 ``ALLOWED_ORIGINS`` 里也能正常工作
    因此这种「用户用局域网地址/机器名访问页面」的情况不应报警
    """
    actual = _request_origin(request)
    return bool(actual) and actual.lower() == origin.lower()


class RequestContextMiddleware(BaseHTTPMiddleware):
    """分配request_id、记录访问日志、追加安全响应头"""

    def __init__(self, app: ASGIApp, *, add_security_headers: bool = True) -> None:
        super().__init__(app)
        self._add_security_headers = add_security_headers

    # """方法：设置请求ID，计时调用下游，并附加响应头和脱敏访问日志"""
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

    def _reject(self, size_desc: str, request: Request) -> Response:
        from starlette.responses import JSONResponse

        logger.warning(
            "请求体过大被拒：%s（上限 %d 字节）path=%s",
            size_desc,
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

    # """方法：声明长度超限直接拒绝；否则包装receive按实际接收字节兜底"""
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        raw_length = request.headers.get("content-length")
        if raw_length and raw_length.isdigit() and int(raw_length) > self._max_bytes:
            return self._reject(f"声明 {raw_length} 字节", request)

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
            return self._reject(f"实际接收超过 {self._max_bytes} 字节", request)
        finally:
            request._receive = original_receive


__all__ = ["BodySizeLimitMiddleware", "RequestContextMiddleware"]

