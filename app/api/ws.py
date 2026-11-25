"""WebSocket路由：摄像头视频流

1. **心跳**：客户端可发文本心跳，服务端回应 ``pong``
2. **背压**：帧处理走 ``FrameAnalyzer``，在途帧超限即丢帧，不会无限堆积
3. **业务联动**：识别到已知人脸后由服务端直接执行签到/签退
4. **错误隔离**：单帧异常只回一条错误消息，不断开连接
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.errors import AppError
from app.services.frame_pipeline import ROLE_ENTRY, ROLE_EXIT, analyzer_for

router = APIRouter(tags=["WebSocket"])


# 摄像头连接主循环：校验角色后循环接收文本/二进制帧并回写结果
async def _handle_camera(websocket: WebSocket, role: str) -> None:
    """共享的摄像头帧处理循环"""
    await websocket.accept()
    analyzer = analyzer_for(role)

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
                            "identity": face.identity,
                            "identity_confidence": face.identity_confidence,
                            "detection_confidence": face.detection_confidence,
                        }
                        for face in analysis.faces
                    ],
                    "processing_ms": analysis.processing_ms,
                    "actions": outcome.actions,
                }
            )
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


# 入口WS端点：固定role=entry，交给通用处理器
@router.websocket("/ws/camera")
async def websocket_camera(websocket: WebSocket) -> None:
    """入口摄像头：识别到已知人脸自动签到"""
    await _handle_camera(websocket, ROLE_ENTRY)


# 出口WS端点：固定role=exit，交给通用处理器
@router.websocket("/ws/camera-exit")
async def websocket_camera_exit(websocket: WebSocket) -> None:
    """出口摄像头：识别到在场者直接签退，计入本次在场时长"""
    await _handle_camera(websocket, ROLE_EXIT)


__all__ = ["router"]
