"""应用工厂与生命周期管理。

相对旧版的修复：
1. **静态资源不再挂在根路径**。旧版
   ``app.mount("/", StaticFiles(directory=..., html=True))`` 会让整个应用所有
   未匹配路径都落到静态目录，``/docs``、``/redoc`` 能否访问取决于路由注册顺序，
   且任何拼错的 API 路径都会返回 HTML 而不是 JSON 错误。现在静态资源挂在
   ``/static``，根路径显式重定向到 ``/static/idx.html``。
2. **后台任务集中管理**：旧版只有验证码清理任务；现在补上「暂离超时自动签退」
   兜底任务（旧版完全缺失，用户断线后计费会无限累计）。
3. **启动期预检**：模型文件缺失不再等到第一个请求才 500，而是在启动日志里
   明确列出。
4. **事件循环登记**：把主循环交给日志广播器，解决工作线程日志丢失问题。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from starlette.staticfiles import StaticFiles

from app.api.router import api_router
from app.config import settings
from app.core.errors_handlers import register_exception_handlers
from app.core.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from app.db.models import SessionLocal, init_database
from app.logger import get_broadcaster, logger, setup_logging
from app.services.email_service import cleanup_expired_codes

#: 「暂离超时自动签退」的后台扫描间隔（秒）
SETTLE_INTERVAL_SECONDS = 60
DESCRIPTION = """
LookUFace —— 基于人脸识别的场地计时缴费服务。

* 摄像头帧经 WebSocket 推送，服务端完成检测、性别年龄、身份识别
* 识别到已知人脸后由**服务端**完成签到 / 暂离 / 签退与计费
* 所有金额均由服务端按数据库时间戳计算，客户端无法指定
"""


async def _settle_expired_sessions() -> None:
    """兜底任务：周期性把暂离超时的会话自动签退结算。"""
    from app.services.session import session_service

    while True:
        try:
            await asyncio.sleep(SETTLE_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            logger.debug("超时结算任务已停止")
            raise

        db = SessionLocal()
        try:
            session_service.auto_settle_expired(db)
            session_service.drop_stale_runtime(db)
        except Exception:
            logger.exception("超时结算任务执行失败")
        finally:
            db.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动与关闭钩子。"""
    logger.info("正在启动 %s v%s（debug=%s）", settings.APP_NAME, settings.APP_VERSION, settings.DEBUG)
    settings.ensure_runtime_dirs()
    init_database()

    # 模型预检：缺失只告警，不阻断启动（health 接口会暴露 degraded 状态）
    from app.vision.base import preflight

    preflight()

    loop = asyncio.get_running_loop()
    get_broadcaster().attach_loop(loop)

    tasks: list[asyncio.Task[None]] = [
        asyncio.create_task(cleanup_expired_codes(), name="cleanup-expired-codes"),
        asyncio.create_task(_settle_expired_sessions(), name="settle-expired-sessions"),
    ]
    logger.info("后台任务已启动：%s", ", ".join(task.get_name() for task in tasks))
    logger.info(
        "服务就绪 -> http://%s:%d/static/idx.html（API 文档 /docs）",
        "127.0.0.1" if settings.HOST in {"0.0.0.0", "::"} else settings.HOST,
        settings.PORT,
    )

    try:
        yield
    finally:
        logger.info("正在关闭…")
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        get_broadcaster().detach_loop()
        logger.info("已安全关闭")


def create_app() -> FastAPI:
    """构建 FastAPI 应用。"""
    setup_logging()

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
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-Process-Time-ms"],
        max_age=600,
    )

    register_exception_handlers(app)

    # API
    app.include_router(api_router, prefix="/api/v1")

    # 静态资源：挂在 /static，不再抢占根路径
    app.mount("/static", StaticFiles(directory=str(settings.STATIC_DIR), html=True), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> RedirectResponse:
        return RedirectResponse(url="/static/idx.html", status_code=307)

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> RedirectResponse:
        return RedirectResponse(url="/static/logo.png", status_code=307)

    return app


app = create_app()

__all__ = ["app", "create_app"]
