# """模块：API路由聚合"""
# """架构作用：把单场地资源路由装配到统一API入口"""
"""API路由聚合

``/api/v1`` 下的完整资源清单：

| 前缀        | 说明                                    |
|-------------|-----------------------------------------|
| ``/faces``  | 人脸库CRUD与截图                      |
| ``/sessions`` | 签到 / 暂离 / 签退 / 状态 / 超时收口 |
| ``/billing`` | 计费报价与支付二维码                   |
| ``/settings`` | 场地设置（计费单价、暂离超时等）     |
| ``/logs``   | 前端日志上报与历史查询                  |
| （无前缀）  | ``/health``、``/version``               |
| ``/ws``     | 摄像头与日志WebSocket                  |

``/auth`` 已在v1.2.0随账号体系一并移除
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import billing, faces, health, logs, sessions, site_settings
from app.api.ws import router as ws_router

# 聚合现存路由，最终/api/v1前缀由应用工厂挂载
api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(faces.router)
api_router.include_router(sessions.router)
api_router.include_router(billing.router)
api_router.include_router(site_settings.router)
api_router.include_router(logs.router)
api_router.include_router(ws_router)

__all__ = ["api_router"]
