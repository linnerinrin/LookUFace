"""应用工厂与生命周期管理"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from starlette.staticfiles import StaticFiles

from app.api.router import api_router
from app.config import settings
from app.core.errors_handlers import register_exception_handlers
from app.core.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from app.db.models import init_database

DESCRIPTION = """
LookUFace —— 基于人脸识别的打卡服务。
"""


# 启动初始化资源，退出时安全关闭
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动与关闭钩子"""
    settings.ensure_runtime_dirs()
    init_database()

    # 模型预检：缺失只告警，不阻断启动（health接口会暴露degraded状态）
    from app.vision.base import preflight

    preflight()

    yield


# 注册中间件、路由和静态资源，返回供ASGI加载的应用
def create_app() -> FastAPI:
    """构建FastAPI应用"""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=DESCRIPTION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # 中间件（注册顺序 = 由外到内）
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        # X-Requested-With是前端连通性探测（api.js的diagnoseConnectivity）使用的头
        # 遗漏它会让该探测的预检失败，从而把「CORS正常」误判为「CORS被拒」
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Requested-With"],
        expose_headers=["X-Request-ID", "X-Process-Time-ms"],
        max_age=600,
    )

    register_exception_handlers(app)

    # API
    app.include_router(api_router, prefix="/api/v1")

    # 静态资源：挂在/static，不再抢占根路径
    app.mount("/static", StaticFiles(directory=str(settings.STATIC_DIR), html=True), name="static")

    # 根路径重定向到静态首页，不进入业务API
    @app.get("/", include_in_schema=False)
    async def index() -> RedirectResponse:
        return RedirectResponse(url="/static/idx.html", status_code=307)

    # 浏览器图标由各页面 head 里的内联 SVG(data URI) 提供，不再需要服务端路由

    return app


app = create_app()

__all__ = ["app", "create_app"]
