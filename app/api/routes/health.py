"""健康检查路由。"""

from __future__ import annotations

from fastapi import APIRouter

from sqlalchemy import text

from app.api.deps import DbSession
from app.api.schemas import HealthResponse
from app.config import settings
from app.logger import logger

router = APIRouter(tags=["系统"])


def _database_ok(db: DbSession) -> bool:
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("数据库健康检查失败")
        return False


@router.get("/health", response_model=HealthResponse)
async def health_check(db: DbSession) -> HealthResponse:
    """健康检查。

    相对旧版的修复：旧版硬编码 ``models_loaded=True``，无论模型是否真的加载成功，
    监控看到的永远是健康。现在真实探测数据库连通性与模型文件是否存在。
    """
    from app.vision.base import preflight

    models = preflight()
    database_ok = _database_ok(db)
    all_models = all(models.values())

    return HealthResponse(
        status="healthy" if (database_ok and all_models) else "degraded",
        version=settings.APP_VERSION,
        database=database_ok,
        models=models,
        models_loaded=all_models,
    )


@router.get("/version")
async def version() -> dict[str, object]:
    """版本与关键配置摘要（不暴露任何密钥）。"""
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "debug": settings.DEBUG,
        "recognize_threshold": settings.RECOGNIZE_THRESHOLD,
        "face_conf_threshold": settings.FACE_CONF_THRESHOLD,
        "away_timeout_minutes": settings.AWAY_TIMEOUT_MINUTES,
        "rate_per_minute": settings.BILLING_RATE_PER_MINUTE,
        "smtp_enabled": settings.SMTP_ENABLED,
    }


__all__ = ["router"]
