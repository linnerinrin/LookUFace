# """架构作用：位于WebSocket传输与分析/会话服务之间，把解码帧转换为识别结果及业务动作"""
# """模块：在线程池执行帧分析，以角色选择入口签到或出口暂离/签退，并统计处理与丢弃数量"""
# 背压额度按入口/出口分析器分别持有，数据库会话则按一次业务联动创建和关闭
"""视频帧分析服务（含背压与自动签到联动）

1. **服务端负责业务联动**：识别出已知人脸后，由本模块直接调用
   ``session_service``，且其内部有按名字的去抖与细粒度锁，重复帧不会重复写库
2. **背压**：用信号量限制在途帧数，超出则直接丢弃旧帧（视频流场景下
   丢帧比排队更有价值，排队只会让延迟无上限增长）
3. **两种摄像头语义**：入口摄像头自动签到，出口摄像头自动暂离/签退
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

#: WS帧解码限额：压缩字节上限与解码后像素上限（防御巨帧阻塞事件循环/耗尽内存）
MAX_FRAME_BYTES = 8 * 1024 * 1024          # 8 MB压缩载荷
MAX_FRAME_DIMENSION = 4096                  # 单边最长像素
MAX_FRAME_PIXELS = 16 * 1024 * 1024         # 总像素上限（约4K）


@dataclass(slots=True)
class FrameOutcome:
    """一帧的处理结果（含业务联动摘要）"""

    analysis: FrameAnalysis
    actions: list[str] = field(default_factory=list)
    dropped: bool = False


class FrameAnalyzer:
    """按摄像头角色分析视频帧并触发会话业务"""

    # 将并发额度下限固定为1；锁单独保护统计，避免分析阶段长时间占用统计锁
    def __init__(self, *, max_pending: int | None = None) -> None:
        limit = max(1, settings.MAX_PENDING_FRAMES if max_pending is None else max_pending)
        self._semaphore = threading.BoundedSemaphore(limit)
        self._stats_lock = threading.Lock()
        self._processed = 0
        self._dropped = 0

    # ------------------------------------------------------------------ #
    # 统计
    # ------------------------------------------------------------------ #
    # 一次性复制两个计数，调用者不能通过返回字典修改内部统计
    @property
    def stats(self) -> dict[str, int]:
        with self._stats_lock:
            return {"processed": self._processed, "dropped": self._dropped}

    # 这里只统计成功处理和背压丢弃；异常返回的空结果不会增加这两个计数
    def _mark(self, *, dropped: bool) -> None:
        with self._stats_lock:
            if dropped:
                self._dropped += 1
            else:
                self._processed += 1

    # ------------------------------------------------------------------ #
    # 帧处理
    # ------------------------------------------------------------------ #
    # 压缩字节先做限额，再解码；解码后再校验像素尺寸，超限直接丢弃返回None
    @staticmethod
    def decode_frame(payload: bytes) -> np.ndarray | None:
        """把JPEG字节解码为BGR图像。超限（压缩体积/尺寸/像素）返回None"""
        if not payload:
            return None
        if len(payload) > MAX_FRAME_BYTES:
            logger.warning("丢弃超限帧：压缩体积 %d 字节 > %d", len(payload), MAX_FRAME_BYTES)
            return None
        buffer = np.frombuffer(payload, np.uint8)
        frame = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        if frame is None or frame.size == 0:
            return None
        height, width = frame.shape[:2]
        if (
            max(height, width) > MAX_FRAME_DIMENSION
            or height * width > MAX_FRAME_PIXELS
        ):
            logger.warning(
                "丢弃超限帧：尺寸 %dx%d 超过限制（单边<=%d，像素<=%d）",
                width, height, MAX_FRAME_DIMENSION, MAX_FRAME_PIXELS,
            )
            return None
        return frame

    # 非阻塞抢占分析额度；成功后无论分析、业务联动是否异常，都在finally归还额度
    def process_sync(
        self,
        frame: np.ndarray,
        *,
        role: str = ROLE_ENTRY,
        detect_gender_age: bool = True,
    ) -> FrameOutcome:
        """同步处理一帧。在工作线程中调用，避免阻塞事件循环"""
        if not self._semaphore.acquire(blocking=False):
            # 背压：上一帧还没处理完，直接丢弃本帧
            self._mark(dropped=True)
            return FrameOutcome(analysis=FrameAnalysis(), dropped=True)
        try:
            return self._process_body(
                frame, role=role, detect_gender_age=detect_gender_age
            )
        finally:
            self._semaphore.release()

    # 分析主体：假定额度已由调用方持有，只负责分析与业务联动
    def _process_body(
        self,
        frame: np.ndarray,
        *,
        role: str,
        detect_gender_age: bool,
    ) -> FrameOutcome:
        try:
            analysis = face_analysis_service.analyze_image(
                frame,
                detect_gender_age=detect_gender_age,
                detect_identity=True,
            )
            actions = self._apply_business(role=role, faces=analysis.faces)
            self._mark(dropped=False)
            return FrameOutcome(analysis=analysis, actions=actions)
        except AppError as exc:
            logger.warning("帧分析失败：%s", exc.message)
            return FrameOutcome(analysis=FrameAnalysis())
        except Exception:
            logger.exception("帧分析发生未预期异常")
            return FrameOutcome(analysis=FrameAnalysis())

    # 提交边界背压：先抢占额度再向executor提交，未抢到额度直接丢弃，
    # 待执行帧不会在默认线程池里无限排队；额度在工作线程内finally归还，
    # 即使等待中的协程被取消，已提交的任务也会自行释放额度
    async def process(
        self,
        frame: np.ndarray,
        *,
        role: str = ROLE_ENTRY,
        detect_gender_age: bool = True,
    ) -> FrameOutcome:
        """异步处理一帧（内部走线程池）"""
        if not self._semaphore.acquire(blocking=False):
            self._mark(dropped=True)
            return FrameOutcome(analysis=FrameAnalysis(), dropped=True)

        loop = asyncio.get_running_loop()

        def _run() -> FrameOutcome:
            try:
                return self._process_body(
                    frame, role=role, detect_gender_age=detect_gender_age
                )
            finally:
                self._semaphore.release()

        return await loop.run_in_executor(None, _run)

    # ------------------------------------------------------------------ #
    # 业务联动
    # ------------------------------------------------------------------ #
    #: 出口签退迟滞：识别到「暂离中」的人后，需其暂离持续满该秒数才最终签退，
    #: 避免「上一帧刚标暂离、下一帧立即签退」打断计时连续性
    EXIT_SETTLE_SECONDS = 30.0

    # 过滤Unknown后共用短生命周期数据库会话；逐脸查询当前状态，再委托服务层执行转换
    @staticmethod
    def _apply_business(
        *,
        role: str,
        faces: list[AnalyzedFace],
    ) -> list[str]:
        """对识别出的人脸执行签到/暂离/签退

        服务端自己判断当前状态，不再让前端发一堆探测请求
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
                    state = session_service.get_status(db, name=name)
                except AppError:
                    continue

                if role == ROLE_ENTRY:
                    # 只有在「未在线」或「暂离中」才需要签到，否则内部自动忽略
                    if not state.is_online or state.is_away:
                        result = session_service.check_in(db, name=name)
                        if not result.deduplicated:
                            actions.append(result.message)
                else:  # ROLE_EXIT
                    if state.is_online and not state.is_away:
                        result = session_service.check_out(db, name=name, away=True)
                        if not result.deduplicated:
                            actions.append(result.message)
                    elif state.is_online and state.is_away:
                        # 迟滞签退：暂离持续满阈值才视为「新离场事件」并签退；
                        # 刚标暂离后的连续出口帧只是「持续识别」，不触发结算
                        away_since = state.away_started_at
                        if away_since is None:
                            continue
                        dwell = (datetime.now() - away_since).total_seconds()
                        if dwell < FrameAnalyzer.EXIT_SETTLE_SECONDS:
                            continue
                        result = session_service.check_out(db, name=name, away=False)
                        if not result.deduplicated:
                            actions.append(result.message)
        except Exception:
            logger.exception("会话业务联动失败 role=%s", role)
        finally:
            db.close()

        if actions:
            logger.info("[%s] 业务动作：%s", role, " | ".join(actions))
        return actions


#: 入口 / 出口两个分析器（各自独立的背压额度）
entry_analyzer = FrameAnalyzer()
exit_analyzer = FrameAnalyzer()


# 仅明确exit使用出口实例，其余角色默认入口；调用者应提前校验角色值
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
