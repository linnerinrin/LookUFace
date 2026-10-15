"""API 路由聚合。

``/api/v1`` 下的完整资源清单：

| 前缀        | 说明                                    |
|-------------|-----------------------------------------|
| ``/auth``   | 注册、登录、验证码、资料、改密          |
| ``/faces``  | 人脸库 CRUD 与截图                      |
| ``/sessions`` | 签到 / 暂离 / 签退 / 状态 / 超时收口 |
| ``/billing`` | 计费报价与支付二维码                   |
| ``/logs``   | 前端日志上报与历史查询                  |
| （无前缀）  | ``/health``、``/version``               |
| ``/ws``     | 摄像头与日志 WebSocket                  |
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import auth, billing, faces, health, logs, sessions
from app.api.ws import router as ws_router

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(faces.router)
api_router.include_router(sessions.router)
api_router.include_router(billing.router)
api_router.include_router(logs.router)
api_router.include_router(ws_router)

__all__ = ["api_router"]
