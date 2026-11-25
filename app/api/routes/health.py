"""健康检查路由"""

from __future__ import annotations

from fastapi import APIRouter

from sqlalchemy import text

from app.api.deps import DbSession
from app.api.schemas import HealthResponse
from app.config import settings

router = APIRouter(tags=["系统"])


# 用SELECT 1探测连接，异常转换为degraded所需的布尔值
def _database_ok(db: DbSession) -> bool:
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


# 健康检查端点：SELECT 1探测数据库；模型分开报告「文件预检」与「加载就绪」
@router.get("/health", response_model=HealthResponse)
async def health_check(db: DbSession) -> HealthResponse:
    """健康检查

    * ``models``：文件级预检（存在 + 可读 + 非空）；
    * ``models_loaded``：模型实例的真实加载就绪状态（各模型类已成功构建实例），
      冷启动尚未触发推理时为False，避免「文件在但加载必炸」被误报健康
    """
    from app.vision.base import load_readiness, preflight

    models = preflight()
    loaded = load_readiness()
    expected = {"FaceDetector"}
    models_loaded = expected.issubset(load_state for load_state in loaded)
    database_ok = _database_ok(db)
    files_ok = all(models.values())

    return HealthResponse(
        status="healthy" if (database_ok and files_ok) else "degraded",
        version=settings.APP_VERSION,
        database=database_ok,
        models=models,
        models_loaded=models_loaded,
    )


# 版本端点：返回应用名与版本号，供部署探活与前端展示
@router.get("/version")
async def version() -> dict[str, object]:
    """版本与关键配置摘要（不暴露任何密钥）"""
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "debug": settings.DEBUG,
        "recognize_threshold": settings.RECOGNIZE_THRESHOLD,
        "face_conf_threshold": settings.FACE_CONF_THRESHOLD,
    }


__all__ = ["router"]
