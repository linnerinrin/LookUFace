"""视频帧分析服务（含背压与自动签到联动）。

相对旧版的修复（**P0 请求风暴的根因治理**）
------------------------------------------
旧版 ``app/api/routes.py`` 的 ``_handle_camera_stream`` 直接
``await run_sync_analysis(frame, ...)``，然后由**前端**在每一帧的返回结果里
对每张脸各发一次 ``/check/checkin`` 或 ``/check/status``+``/check/checkout``。
以 10fps 计算，单人使用就产生约 20~30 次 HTTP 请求与数据库写入/秒。

现在：
1. **服务端负责业务联动**：识别出已知人脸后，由本模块直接调用
   ``session_service``，且其内部有按名字的去抖与细粒度锁，重复帧不会重复写库。
2. **背压**：用信号量限制在途帧数，超出则直接丢弃旧帧（视频流场景下
   丢帧比排队更有价值，排队只会让延迟无上限增长）。
3. **两种摄像头语义**：入口摄像头自动签到，出口摄像头自动暂离/签退。
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from datetime import datetime

import cv2
import numpy as np

from app.config import settings
from app.core.errors import AppError
from app.db.models import SessionLocal
from app.logger import logger
from app.services.analysis import AnalyzedFace, FrameAnalysis, face_analysis_service
from app.services.session import session_service

#: 摄像头语义
ROLE_ENTRY = "entry"
ROLE_EXIT = "exit"


@dataclass(slots=True)
class FrameOutcome:
    """一帧的处理结果（含业务联动摘要）。"""

    analysis: FrameAnalysis
    actions: list[str] = field(default_factory=list)
    dropped: bool = False


class FrameAnalyzer:
    """按摄像头角色分析视频帧并触发会话业务。"""

    def __init__(self, *, max_pending: int | None = None) -> None:
        limit = max(1, settings.MAX_PENDING_FRAMES if max_pending is None else max_pending)
        self._semaphore = threading.BoundedSemaphore(limit)
        self._stats_lock = threading.Lock()
        self._processed = 0
        self._dropped = 0

    # ------------------------------------------------------------------ #
    # 统计
    # ------------------------------------------------------------------ #
    @property
    def stats(self) -> dict[str, int]:
        with self._stats_lock:
            return {"processed": self._processed, "dropped": self._dropped}

    def _mark(self, *, dropped: bool) -> None:
        with self._stats_lock:
            if dropped:
                self._dropped += 1
            else:
                self._processed += 1

    # ------------------------------------------------------------------ #
    # 帧处理
    # ------------------------------------------------------------------ #
    @staticmethod
    def decode_frame(payload: bytes) -> np.ndarray | None:
        """把 JPEG 字节解码为 BGR 图像。"""
        if not payload:
            return None
        buffer = np.frombuffer(payload, np.uint8)
        frame = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        if frame is None or frame.size == 0:
            return None
        return frame

    def process_sync(
        self,
        frame: np.ndarray,
        *,
        user_id: int,
        role: str = ROLE_ENTRY,
        detect_gender_age: bool = True,
    ) -> FrameOutcome:
        """同步处理一帧。在工作线程中调用，避免阻塞事件循环。"""
        if not self._semaphore.acquire(blocking=False):
            # 背压：上一帧还没处理完，直接丢弃本帧
            self._mark(dropped=True)
            return FrameOutcome(analysis=FrameAnalysis(), dropped=True)

        try:
            analysis = face_analysis_service.analyze_image(
                frame,
                detect_gender_age=detect_gender_age,
                detect_identity=True,
                user_id=user_id,
            )
            actions = self._apply_business(user_id=user_id, role=role, faces=analysis.faces)
            self._mark(dropped=False)
            return FrameOutcome(analysis=analysis, actions=actions)
        except AppError as exc:
            logger.warning("帧分析失败：%s", exc.message)
            return FrameOutcome(analysis=FrameAnalysis())
        except Exception:
            logger.exception("帧分析发生未预期异常")
            return FrameOutcome(analysis=FrameAnalysis())
        finally:
            self._semaphore.release()

    async def process(
        self,
        frame: np.ndarray,
        *,
        user_id: int,
        role: str = ROLE_ENTRY,
        detect_gender_age: bool = True,
    ) -> FrameOutcome:
        """异步处理一帧（内部走线程池）。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.process_sync(
                frame,
                user_id=user_id,
                role=role,
                detect_gender_age=detect_gender_age,
            ),
        )

    # ------------------------------------------------------------------ #
    # 业务联动
    # ------------------------------------------------------------------ #
    @staticmethod
    def _apply_business(
        *,
        user_id: int,
        role: str,
        faces: list[AnalyzedFace],
    ) -> list[str]:
        """对识别出的人脸执行签到/暂离/签退。

        服务端自己判断当前状态，不再让前端发一堆探测请求。
        """
        known = [face for face in faces if face.is_known]
        if not known:
            return []

        actions: list[str] = []
        db = SessionLocal()
        try:
            for face in known:
                name = face.identity
                try:
                    state = session_service.get_status(db, user_id=user_id, name=name)
                except AppError:
                    continue

                if role == ROLE_ENTRY:
                    # 只有在「未在线」或「暂离中」才需要签到，否则内部自动忽略
                    if not state.is_online or state.is_away:
                        result = session_service.check_in(db, user_id=user_id, name=name)
                        if not result.deduplicated:
                            actions.append(result.message)
                else:  # ROLE_EXIT
                    if state.is_online and not state.is_away:
                        result = session_service.check_out(
                            db, user_id=user_id, name=name, away=True
                        )
                        if not result.deduplicated:
                            actions.append(result.message)
                    elif state.is_online and state.is_away:
                        result = session_service.check_out(
                            db, user_id=user_id, name=name, away=False
                        )
                        actions.append(result.message)
        except Exception:
            logger.exception("会话业务联动失败 user_id=%s role=%s", user_id, role)
        finally:
            db.close()

        if actions:
            logger.info("[%s] 业务动作：%s", role, " | ".join(actions))
        return actions


#: 入口 / 出口两个分析器（各自独立的背压额度）
entry_analyzer = FrameAnalyzer()
exit_analyzer = FrameAnalyzer()


def analyzer_for(role: str) -> FrameAnalyzer:
    return exit_analyzer if role == ROLE_EXIT else entry_analyzer


__all__ = [
    "FrameAnalyzer",
    "FrameOutcome",
    "ROLE_ENTRY",
    "ROLE_EXIT",
    "analyzer_for",
    "entry_analyzer",
    "exit_analyzer",
]
