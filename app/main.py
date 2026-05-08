"""
FastAPI 应用入口
- 配置 CORS
- 注册路由（API 路由 + 认证路由）
- 生命周期管理
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.auth.routes import router as auth_router
from app.api.routes import router
from app.config import settings
from app.storage.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """生命周期管理：启动时初始化数据库"""
    print(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    init_db()
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

    app.include_router(router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )