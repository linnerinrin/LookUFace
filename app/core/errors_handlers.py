"""全局异常处理。

相对旧版的修复
--------------
旧版在 ``app/main.py`` 里写了一个 ``@app.exception_handler(Exception)``，
内部又是 ``if isinstance(exc, HTTPException): raise exc`` —— 在 async 处理器里
裸 ``raise`` 会污染异常栈，而且请求体校验失败（422）走的是另一条路径，
最终前端拿到的错误结构完全不统一。

现在按异常类型分别注册处理器，统一输出::

    {"detail": "...", "code": "...", "request_id": "...", "details": {...}}
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.core.errors import AppError
from app.logger import logger, new_request_id


def _payload(detail: str, code: str, *, details: dict | None = None, request_id: str = "-") -> dict:
    body: dict[str, object] = {"detail": detail, "code": code, "request_id": request_id}
    if details:
        body["details"] = details
    return body


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or new_request_id()


def register_exception_handlers(app: FastAPI) -> None:
    """注册全部异常处理器。"""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        rid = _request_id(request)
        level = logger.warning if exc.status_code < 500 else logger.error
        level(
            "业务异常 %s %s -> %d %s",
            request.method,
            request.url.path,
            exc.status_code,
            exc.message,
        )
        headers = {}
        if exc.status_code == 401:
            headers["WWW-Authenticate"] = "Bearer"
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_payload(rid),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        rid = _request_id(request)
        # 把 pydantic 的错误整理成前端友好的「字段 -> 说明」
        fields: dict[str, str] = {}
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
            fields[location or "body"] = error.get("msg", "参数不合法")
        logger.warning(
            "参数校验失败 %s %s -> %s", request.method, request.url.path, fields
        )
        return JSONResponse(
            status_code=422,
            content=_payload("请求参数校验失败", "validation_error", details=fields, request_id=rid),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        rid = _request_id(request)
        detail = exc.detail if isinstance(exc.detail, str) else "请求失败"
        if exc.status_code >= 500:
            logger.error("HTTP 异常 %s -> %s", request.url.path, detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(detail, f"http_{exc.status_code}", request_id=rid),
            headers=getattr(exc, "headers", None) or {},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        rid = _request_id(request)
        logger.error(
            "未捕获异常 %s %s", request.method, request.url.path, exc_info=True
        )
        return JSONResponse(
            status_code=500,
            content=_payload("服务器内部错误", "internal_error", request_id=rid),
        )


__all__ = ["register_exception_handlers"]
