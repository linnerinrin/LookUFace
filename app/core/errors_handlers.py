# """架构作用：将领域、校验、HTTP与未知异常统一映射为JSON响应"""
"""全局异常处理

按异常类型分别注册处理器，统一输出::

    {"detail": "...", "code": "...", "request_id": "...", "details": {...}}
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.core.errors import AppError
from app.logger import logger, new_request_id


# """辅助函数：组装统一错误响应体，可选附加字段详情"""
def _payload(detail: str, code: str, *, details: dict | None = None, request_id: str = "-") -> dict:
    body: dict[str, object] = {"detail": detail, "code": code, "request_id": request_id}
    if details:
        body["details"] = details
    return body


# """辅助函数：复用请求中间件写入的ID，缺失时生成备用ID"""
def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or new_request_id()


# """函数：按异常类别注册处理器，记录必要日志并返回规范JSON"""
def register_exception_handlers(app: FastAPI) -> None:
    """注册全部异常处理器"""

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
        # 把pydantic的错误整理成前端友好的「字段 -> 说明」
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

