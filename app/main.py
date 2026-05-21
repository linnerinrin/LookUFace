"""
app/main.py
FastAPI 应用入口
lifespan() 生命周期管理 启动时创建数据库
create_app()创建fastapi实例 配置cors 写进路由
"""
import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles

from app.auth.routes import router as auth_router
from app.check.routes import router as check_router
from app.api.routes import router
from app.config import settings
from app.database import Base, engine
from app.logger import logger
from app.services.email_service import cleanup_expired_codes


@asynccontextmanager
async def lifespan(app: FastAPI):
    """生命周期管理：启动时初始化数据库，关闭时清理后台任务"""
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    Base.metadata.create_all(bind=engine)
    # [修改] 保存 cleanup 任务引用，关闭时 cancel() 避免 "Task was destroyed but it is pending" 警告
    cleanup_task = asyncio.create_task(cleanup_expired_codes())
    logger.info("Models loaded successfully")
    yield
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("Shutting down...")


def create_app() -> FastAPI:
    """创建FastAPI应用实例"""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="hello dream",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan
    )

    allowed_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        if isinstance(exc, HTTPException):
            raise exc
        logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"}
        )


    app.include_router(router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(check_router, prefix="/api/v1")

    # [优化] 使用 settings.BASE_DIR 替代 os.path 拼接
    static_dir = settings.BASE_DIR / "app" / "static"
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    import webbrowser
    import threading

    def open_browser():
        try:
            webbrowser.open(f"http://localhost:{settings.PORT}/idx.html")
        except:
            raise Exception("未检测到html文件或未打开浏览器，请于app/static/处检查文件完整性")

    threading.Timer(2, open_browser).start()

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )