"""
app/main.py
FastAPI 应用入口
lifespan() 生命周期管理 启动时创建数据库
create_app()创建fastapi实例 配置cors 写进路由
"""
import asyncio
import os

from fastapi import FastAPI
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
    """生命周期管理：启动时初始化数据库"""
    print(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    Base.metadata.create_all(bind=engine)
    asyncio.create_task(cleanup_expired_codes())
    print("Models loaded successfully")
    yield
    print("Shutting down...")


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例"""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="基于 FastAPI 的人脸分析服务，支持人脸检测、年龄性别识别、人脸识别",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan
    )

    # CORS 配置
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error(f"{str(exc)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"detail":str(exc)}
        )


    app.include_router(router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(check_router, prefix="/api/v1")

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    import webbrowser
    import threading

    def open_browser():
        try:
            webbrowser.open("http://localhost:8000/idx.html")
        except:
            raise Exception("未检测到html文件或未打开浏览器，请于app/static/处检查文件完整性")

    threading.Timer(2, open_browser).start()

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )