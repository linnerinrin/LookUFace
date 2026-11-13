# """模块：场地设置路由"""
# """架构作用：将局部配置更新交给设置服务，并返回生效及持久化状态"""
"""场地设置路由

改完**立刻生效**（业务代码每次现取 ``settings``），并写回 ``.env`` 以便重启后保留
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.schemas import SiteSettingsResponse, UpdateSiteSettingsRequest
from app.config import settings
from app.logger import logger
from app.services.settings_service import site_settings_service

router = APIRouter(prefix="/settings", tags=["场地设置"])


# 把运行时配置、文件状态与部署信息组合为响应模型
def _to_response(values: dict, persisted: bool) -> SiteSettingsResponse:
    return SiteSettingsResponse(
        **values,
        persisted=persisted,
        env_file=str(site_settings_service.env_path),
        host=settings.HOST,
    )


# 读取端点：返回当前运行时设置、持久化探测结果与部署元信息
@router.get("", response_model=SiteSettingsResponse)
async def get_site_settings() -> SiteSettingsResponse:
    """读取当前生效的场地设置"""
    return _to_response(site_settings_service.snapshot(), site_settings_service.persisted_state())


# 更新端点：先应用运行时再写.env，两者结果分别回传
@router.put("", response_model=SiteSettingsResponse)
async def update_site_settings(request: UpdateSiteSettingsRequest) -> SiteSettingsResponse:
    """局部更新场地设置：只传要改的字段

    ``persisted=false`` 表示值已在本次运行中生效，但写 ``.env`` 失败
    （例如目录只读），重启后会回到旧值
    """
    values, persisted = site_settings_service.update(request.model_dump(exclude_none=True))
    logger.info("场地设置更新请求已处理 persisted=%s", persisted)
    return _to_response(values, persisted)


__all__ = ["router"]
