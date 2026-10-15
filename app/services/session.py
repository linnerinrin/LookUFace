"""在线会话服务：签到 / 暂离 / 签退 与计费结算。

这是对旧版 ``app/core/checking.py`` 的行为级重写。关键修复：

1. **暂离不再推进签到时间**（旧版 P0 缺陷：暂离会把 ``checkin_time`` 推到未来，
   导致最终算出**负数**计费分钟）。现在暂离起止由 ``SessionRegistry`` 记录，
   结算时从已流逝时间中减去。
2. **计费由服务端唯一裁决**：金额、分钟数全部由后端根据数据库时间戳计算，
   前端传上来的任何时长都只作为展示参考，不参与结算。
3. **幂等与去抖**：同一张脸在 ``CHECKIN_DEBOUNCE_SECONDS`` 内的重复签到请求
   直接返回上一次结果，不再重复写库（旧版每帧写库造成请求风暴）。
4. **并发安全**：同一 ``(user_id, name)`` 的操作被细粒度锁串行化，
   避免「同一帧多次触发」造成的状态错乱。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.db.models import UserFace
from app.db.repository import get_face, online_faces, set_face_state
from app.domain.registry import session_registry
from app.domain.session import BillingResult, SessionState, bill, preview_minutes
from app.logger import logger


@dataclass(slots=True)
class ActionResult:
    """一次业务动作的结果。"""

    success: bool
    message: str
    name: str
    state: SessionState
    deduplicated: bool = False
    billing: BillingResult | None = None


class _Debounce:
    """按 key 的短时去抖，抑制同帧/连帧重复请求。

    key 中必须包含**人脸记录 id**：仅用 ``(user_id, name)`` 时，若某张脸被删除后
    由另一个人以同名重新注册，新记录会继承旧记录的去抖窗口，导致首次签到被
    错误地抑制掉（实测可复现）。同时定期清理过期条目，避免字典无限增长。
    """

    _PRUNE_THRESHOLD = 512

    def __init__(self) -> None:
        self._last: dict[tuple[str, int, int], float] = {}
        self._lock = threading.Lock()

    def should_skip(self, key: tuple[str, int, int], window_seconds: float) -> bool:
        if window_seconds <= 0:
            return False
        now = time.monotonic()
        with self._lock:
            if len(self._last) > self._PRUNE_THRESHOLD:
                self._prune_locked(now)
            previous = self._last.get(key)
            if previous is not None and (now - previous) < window_seconds:
                return True
            self._last[key] = now
            return False

    def _prune_locked(self, now: float) -> None:
        # 保留窗口取最大可能值，保守清理
        horizon = max(settings.CHECKIN_DEBOUNCE_SECONDS, settings.CHECKOUT_DEBOUNCE_SECONDS) * 4 + 60
        stale = [k for k, ts in self._last.items() if (now - ts) > horizon]
        for key in stale:
            self._last.pop(key, None)

    def forget(self, key: tuple[str, int, int]) -> None:
        """删除某张脸的窗口（人脸被删除/重命名时调用）。"""
        with self._lock:
            self._last.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._last.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._last)


class SessionService:
    """场地计时会话服务。"""

    def __init__(self) -> None:
        self._locks: dict[tuple[int, str], threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._debounce = _Debounce()

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    def _lock_for(self, user_id: int, name: str) -> threading.Lock:
        key = (int(user_id), name)
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    @staticmethod
    def _load_face(db: Session, user_id: int, name: str) -> UserFace:
        face = get_face(db, user_id, name)
        if face is None:
            raise NotFoundError(f"人脸不存在：{name}", code="face_not_found")
        return face

    @staticmethod
    def state_of(
        user_id: int,
        name: str,
        face: UserFace,
        *,
        now: datetime | None = None,
    ) -> SessionState:
        return session_registry.build_state(
            user_id=user_id,
            name=name,
            is_online=bool(face.is_online),
            is_away=bool(face.is_away),
            checkin_at=face.checkin_time,
            accumulated_minutes=int(face.online_time or 0),
            now=now,
        )

    # ------------------------------------------------------------------ #
    # 签到
    # ------------------------------------------------------------------ #
    def check_in(
        self,
        db: Session,
        *,
        user_id: int,
        name: str,
        now: datetime | None = None,
        skip_debounce: bool = False,
    ) -> ActionResult:
        """签到。已是暂离状态则视为归位。"""
        moment = now or datetime.now()
        name = (name or "").strip()
        if not name:
            raise ValidationError("人名为空")

        with self._lock_for(user_id, name):
            face = self._load_face(db, user_id, name)

            if not skip_debounce and not face.is_online:
                if self._debounce.should_skip(
                    ("in", user_id, face.id), settings.CHECKIN_DEBOUNCE_SECONDS
                ):
                    logger.debug("签到请求被去抖抑制 name=%s", name)
                    return ActionResult(
                        success=True,
                        message=f"{name} 签到请求已受理（去抖窗口内重复请求）",
                        name=name,
                        state=self.state_of(user_id, name, face, now=moment),
                        deduplicated=True,
                    )

            # 情况一：全新会话
            if not face.is_online:
                session_registry.begin(user_id, name, now=moment)
                set_face_state(db, face_id=face.id, is_online=True, is_away=False, checkin_at=moment)
                face.is_online = True
                face.is_away = False
                face.checkin_time = moment
                logger.info("签到成功（新会话）name=%s user_id=%s", name, user_id)
                return ActionResult(
                    success=True,
                    message=f"{name} 已开始计时，离场或暂离请记得签退付费",
                    name=name,
                    state=self.state_of(user_id, name, face, now=moment),
                )

            # 情况二：暂离归位
            if face.is_away:
                away_seconds = session_registry.clear_away(user_id, name, now=moment)
                set_face_state(db, face_id=face.id, is_away=False)
                face.is_away = False
                logger.info(
                    "签到成功（暂离归位）name=%s 暂离 %d 秒", name, int(away_seconds)
                )
                return ActionResult(
                    success=True,
                    message=f"{name} 已归位，继续计时",
                    name=name,
                    state=self.state_of(user_id, name, face, now=moment),
                )

            # 情况三：本来就在线
            logger.debug("重复签到被忽略 name=%s", name)
            return ActionResult(
                success=True,
                message=f"{name} 已在计时中",
                name=name,
                state=self.state_of(user_id, name, face, now=moment),
                deduplicated=True,
            )

    # ------------------------------------------------------------------ #
    # 签退 / 暂离
    # ------------------------------------------------------------------ #
    def check_out(
        self,
        db: Session,
        *,
        user_id: int,
        name: str,
        away: bool,
        now: datetime | None = None,
        skip_debounce: bool = False,
    ) -> ActionResult:
        """签退（``away=False``）或标记暂离（``away=True``）。"""
        moment = now or datetime.now()
        name = (name or "").strip()
        if not name:
            raise ValidationError("人名为空")

        with self._lock_for(user_id, name):
            face = self._load_face(db, user_id, name)

            if not face.is_online:
                return ActionResult(
                    success=False,
                    message=f"{name} 当前未在计时，无需签退",
                    name=name,
                    state=self.state_of(user_id, name, face, now=moment),
                )

            if away:
                return self._mark_away(db, face, user_id, name, moment, skip_debounce)
            return self._finalize(db, face, user_id, name, moment)

    def _mark_away(
        self,
        db: Session,
        face: UserFace,
        user_id: int,
        name: str,
        moment: datetime,
        skip_debounce: bool,
    ) -> ActionResult:
        if face.is_away:
            return ActionResult(
                success=True,
                message=f"{name} 已处于暂离状态",
                name=name,
                state=self.state_of(user_id, name, face, now=moment),
                deduplicated=True,
            )

        if not skip_debounce and self._debounce.should_skip(
            ("away", user_id, face.id), settings.CHECKOUT_DEBOUNCE_SECONDS
        ):
            return ActionResult(
                success=True,
                message=f"{name} 暂离请求已受理（去抖窗口内重复请求）",
                name=name,
                state=self.state_of(user_id, name, face, now=moment),
                deduplicated=True,
            )

        session_registry.mark_away(user_id, name, now=moment)
        set_face_state(db, face_id=face.id, is_away=True)
        face.is_away = True
        logger.info("暂离开始 name=%s user_id=%s", name, user_id)
        return ActionResult(
            success=True,
            message=f"{name} 已暂离，超过 {settings.AWAY_TIMEOUT_MINUTES} 分钟未归将自动签退结算",
            name=name,
            state=self.state_of(user_id, name, face, now=moment),
        )

    def _finalize(
        self,
        db: Session,
        face: UserFace,
        user_id: int,
        name: str,
        moment: datetime,
        *,
        reason: str = "normal",
    ) -> ActionResult:
        """结算并关闭会话。计费完全由服务端计算。"""
        state = self.state_of(user_id, name, face, now=moment)

        # 先把仍在进行中的暂离收口：
        # ``clear_away`` 会把这段时长累加进 settled_away_seconds，同时清空
        # away_started_at（注册表里）。但上面取到的 state 是收口**之前**的快照，
        # 仍然带着 away_started_at；若直接交给 bill()，这段暂离会被
        # 「settled_away_seconds」和「进行中的暂离」各扣一次（实测重复扣除 2 倍）。
        # 因此这里显式折算出收口后的状态。
        if state.is_away:
            ended = session_registry.clear_away(user_id, name, now=moment)
            settled = state.settled_away_seconds + max(0.0, ended)
            state = SessionState(
                is_online=True,
                is_away=False,
                checkin_at=state.checkin_at,
                accumulated_minutes=state.accumulated_minutes,
                away_started_at=None,
                settled_away_seconds=settled,
            )
            # 同步持久层：会话即将结束，暂离标记一并清除
            set_face_state(db, face_id=face.id, is_away=False)

        result = bill(
            state,
            moment,
            rate_per_minute=settings.BILLING_RATE_PER_MINUTE,
            max_session_minutes=settings.MAX_SESSION_MINUTES,
        )

        # 关键：online_time 只在结算时增加，且服务端保证其为非负数
        total = max(0, int(result.total_minutes))
        set_face_state(
            db,
            face_id=face.id,
            is_online=False,
            is_away=False,
            checkin_at=None,
            accumulated_minutes=total,
        )
        db.expire(face)

        session_registry.drop(user_id, name)

        face.is_online = False
        face.is_away = False
        face.checkin_time = None
        face.online_time = total

        logger.info(
            "签退结算 name=%s 本次 %d 分钟 累计 %d 分钟 金额 %.2f 元 原因=%s%s",
            name,
            result.session_minutes,
            total,
            result.amount,
            reason,
            "（触发计费上限）" if result.capped else "",
        )
        return ActionResult(
            success=True,
            message=(
                f"{name} 签退成功，本次 {result.session_minutes} 分钟，"
                f"累计 {total} 分钟，应付 {result.amount:.2f} 元"
            ),
            name=name,
            state=SessionState(is_online=False, accumulated_minutes=total),
            billing=result,
        )

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def get_status(
        self,
        db: Session,
        *,
        user_id: int,
        name: str,
        now: datetime | None = None,
    ) -> SessionState:
        face = self._load_face(db, user_id, name)
        return self.state_of(user_id, name, face, now=now)

    def current_minutes(
        self,
        db: Session,
        *,
        user_id: int,
        name: str,
        now: datetime | None = None,
    ) -> int:
        """当前会话的预估计费分钟数（不落库）。"""
        moment = now or datetime.now()
        face = self._load_face(db, user_id, name)
        state = self.state_of(user_id, name, face, now=moment)
        if not state.is_online:
            return 0
        return preview_minutes(state, moment, max_session_minutes=settings.MAX_SESSION_MINUTES)

    def auto_settle_expired(self, db: Session, *, now: datetime | None = None) -> int:
        """兜底任务：把暂离超时的会话自动签退结算。

        旧版完全没有这个机制 —— 用户拔网线/关页面后 ``is_online`` 永远为 True，
        计费无限累计。这里按 ``AWAY_TIMEOUT_MINUTES`` 收口。
        """
        moment = now or datetime.now()
        settled = 0

        for face in list(online_faces(db)):
            state = self.state_of(face.user_id, face.name, face, now=moment)
            if not session_registry.is_expired(state, moment):
                continue
            try:
                self._finalize(
                    db,
                    face,
                    face.user_id,
                    face.name,
                    moment,
                    reason="暂离超时自动签退",
                )
                settled += 1
            except Exception:
                logger.exception("自动签退失败 name=%s", face.name)

        if settled:
            logger.info("兜底任务已自动结算 %d 个超时暂离会话", settled)
        return settled

    def drop_stale_runtime(self, db: Session) -> int:
        """清理数据库中已删除人脸的运行态。"""
        from app.db.repository import all_face_keys

        return session_registry.prune_unknown(all_face_keys(db))

    def clear_debounce(self) -> None:
        self._debounce.clear()

    def forget_face(self, user_id: int, name: str) -> None:
        """人脸被删除/重命名时，清理它的运行态、去抖窗口与锁。"""
        key = (int(user_id), name)
        session_registry.drop(*key)
        self._debounce.forget(("in", key[0], 0))
        with self._locks_guard:
            self._locks.pop(key, None)

    def reset(self) -> None:
        """清空全部运行时状态（仅测试与运维重启场景使用）。"""
        session_registry._states.clear()
        self._debounce.clear()
        with self._locks_guard:
            self._locks.clear()


session_service = SessionService()

__all__ = ["ActionResult", "SessionService", "session_service"]
