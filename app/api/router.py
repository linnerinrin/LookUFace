"""API路由聚合

把单场地资源路由装配到统一API入口。

``/api/v1`` 下的完整资源清单：

| 前缀        | 说明                                    |
|-------------|-----------------------------------------|
| ``/faces``  | 人脸库CRUD与截图                      |
| ``/sessions`` | 进出场打卡 / 状态                      |
| ``/records`` | 进出场流水补录与查询                  |
| ``/stats``  | 数据看板聚合                          |
| ``/settings`` | 场地设置                               |
| （无前缀）  | ``/health``、``/version``               |
| ``/ws``     | 摄像头WebSocket                         |
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import faces, health, records, sessions, site_settings
from app.api.ws import router as ws_router

# 聚合现存路由，最终/api/v1前缀由应用工厂挂载
api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(faces.router)
api_router.include_router(sessions.router)
api_router.include_router(records.records_router)
api_router.include_router(records.stats_router)
api_router.include_router(site_settings.router)
api_router.include_router(ws_router)

__all__ = ["api_router"]
