# """模块：摄像头与日志WebSocket"""
# """架构作用：接收视频帧并返回分析结果，或订阅实时日志"""
"""WebSocket路由：摄像头视频流与控制台日志流

1. **心跳**：客户端可发文本心跳，服务端回应 ``pong``
2. **背压**：帧处理走 ``FrameAnalyzer``，在途帧超限即丢帧，不会无限堆积
3. **业务联动**：识别到已知人脸后由服务端直接执行签到/签退
4. **错误隔离**：单帧异常只回一条错误消息，不断开连接
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import settings
from app.core.errors import AppError
from app.logger import get_broadcaster, logger
from app.services.frame_pipeline import ROLE_ENTRY, ROLE_EXIT, analyzer_for

router = APIRouter(tags=["WebSocket"])


# 摄像头连接主循环：校验角色后循环接收文本/二进制帧并回写结果
async def _handle_camera(websocket: WebSocket, role: str) -> None:
    """共享的摄像头帧处理循环"""
    await websocket.accept()
    label = "入口" if role == ROLE_ENTRY else "出口"
    analyzer = analyzer_for(role)
    logger.info("[%s] 摄像头已连接", label)

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

            # 文本消息用于心跳/控制
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
                outcome = await analyzer.process(frame, role=role)
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
        logger.info("[%s] 摄像头已断开", label)
    except Exception:
        logger.exception("[%s] 摄像头连接异常终止", label)
    finally:
        logger.info("[%s] 摄像头连接结束", label)


# 入口WS端点：固定role=entry，交给通用处理器
@router.websocket("/ws/camera")
async def websocket_camera(websocket: WebSocket) -> None:
    """入口摄像头：识别到已知人脸自动签到"""
    await _handle_camera(websocket, ROLE_ENTRY)


# 出口WS端点：固定role=exit，交给通用处理器
@router.websocket("/ws/camera-exit")
async def websocket_camera_exit(websocket: WebSocket) -> None:
    """出口摄像头：在线者自动暂离，暂离者自动签退结算"""
    await _handle_camera(websocket, ROLE_EXIT)


# 日志WS端点：登记广播器后保持连接，断开时移除
@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket) -> None:
    """控制台日志实时推送"""
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
