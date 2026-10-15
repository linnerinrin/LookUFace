"""WebSocket 路由：摄像头视频流与控制台日志流。

相对旧版的修复：
1. **认证**：旧版 ``/ws/camera`` 完全不校验身份，任何能连上端口的人都可以
   推流并对**任意 user_id** 触发签到（实际是前端从 localStorage 拿 token 却没有
   传进来）。现在握手时校验 JWT，未通过直接以 1008 关闭。
2. **心跳**：旧版没有 ping/pong，网络抖动后连接会静默僵死。现在客户端可发
   文本心跳，服务端回应 ``pong``。
3. **背压**：帧处理走 ``FrameAnalyzer``，在途帧超限即丢帧，不会无限堆积。
4. **业务联动**：识别到已知人脸后由服务端直接执行签到/签退，
   不再依赖前端每帧发 HTTP 请求。
5. **错误隔离**：单帧异常只回一条错误消息，不断开连接。
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError, AuthError
from app.db.models import SessionLocal, User
from app.logger import get_broadcaster, logger
from app.services.frame_pipeline import ROLE_ENTRY, ROLE_EXIT, analyzer_for
from app.services.security import decode_token

router = APIRouter(tags=["WebSocket"])

_WS_AUTH_FAILED = 1008  # RFC 6455: policy violation


def _authenticate(token: str | None) -> User | None:
    """校验查询参数里的 JWT，返回用户。"""
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    user_id = payload.get("user_id")
    if not isinstance(user_id, int):
        return None
    db: Session = SessionLocal()
    try:
        return db.get(User, user_id)
    finally:
        db.close()


async def _handle_camera(websocket: WebSocket, role: str) -> None:
    """共享的摄像头帧处理循环。"""
    token = websocket.query_params.get("token")
    user = _authenticate(token)
    if user is None:
        await websocket.close(code=_WS_AUTH_FAILED, reason="未认证：缺少或无效的 token")
        logger.warning("[%s] 摄像头连接被拒绝（认证失败）", role)
        return

    await websocket.accept()
    label = "入口" if role == ROLE_ENTRY else "出口"
    analyzer = analyzer_for(role)
    logger.info("[%s] 摄像头已连接 user=%s", label, user.email)

    try:
        while True:
            try:
                message = await websocket.receive()
            except RuntimeError:
                break

            if message.get("type") == "websocket.disconnect":
                break

            payload: bytes | None = message.get("bytes")
            text: str | None = message.get("text")

            # 文本消息用于心跳 / 控制
            if payload is None and text is not None:
                if text.strip().lower() in {"ping", '"ping"'}:
                    await websocket.send_json({"success": True, "type": "pong"})
                elif text.strip().lower() == "stats":
                    await websocket.send_json({"success": True, "type": "stats", **analyzer.stats})
                continue

            if not payload:
                continue

            frame = analyzer.decode_frame(payload)
            if frame is None:
                continue

            try:
                outcome = await analyzer.process(frame, user_id=user.id, role=role)
            except AppError as exc:
                await websocket.send_json(
                    {"success": False, "detail": exc.message, "code": exc.code}
                )
                continue
            except Exception:
                logger.exception("[%s] 帧处理失败", label)
                await websocket.send_json(
                    {"success": False, "detail": "帧处理失败", "code": "frame_failed"}
                )
                continue

            if outcome.dropped:
                # 背压丢帧：不占用带宽回包，仅记录
                continue

            analysis = outcome.analysis
            await websocket.send_json(
                {
                    "success": True,
                    "role": role,
                    "face_count": analysis.face_count,
                    "faces": [
                        {
                            "bbox": face.bbox,
                            "gender": face.gender,
                            "age": face.age,
                            "confidence": face.confidence,
                            "identity": face.identity,
                            "identity_confidence": face.identity_confidence,
                            "detection_confidence": face.detection_confidence,
                        }
                        for face in analysis.faces
                    ],
                    "processing_ms": analysis.processing_ms,
                    "actions": outcome.actions,
                    "away_timeout_minutes": settings.AWAY_TIMEOUT_MINUTES,
                }
            )
    except WebSocketDisconnect:
        logger.info("[%s] 摄像头已断开 user=%s", label, user.email)
    except Exception:
        logger.exception("[%s] 摄像头连接异常终止", label)
    finally:
        logger.info("[%s] 摄像头连接结束 user=%s", label, user.email)


@router.websocket("/ws/camera")
async def websocket_camera(websocket: WebSocket) -> None:
    """入口摄像头：识别到已知人脸自动签到。"""
    await _handle_camera(websocket, ROLE_ENTRY)


@router.websocket("/ws/camera-exit")
async def websocket_camera_exit(websocket: WebSocket) -> None:
    """出口摄像头：在线者自动暂离，暂离者自动签退结算。"""
    await _handle_camera(websocket, ROLE_EXIT)


@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket) -> None:
    """控制台日志实时推送。"""
    await websocket.accept()
    broadcaster = get_broadcaster()
    broadcaster.add_ws(websocket)
    logger.info("控制台客户端已连接（当前 %d 个）", broadcaster.connection_count)

    try:
        while True:
            try:
                text = await websocket.receive_text()
            except RuntimeError:
                break
            if text.strip().lower() == "ping":
                await websocket.send_text("[INFO] pong")
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("控制台 WebSocket 异常")
    finally:
        broadcaster.remove_ws(websocket)
        logger.info("控制台客户端已断开（剩余 %d 个）", broadcaster.connection_count)


__all__ = ["router"]
