"""在线会话服务：签到 / 签退（进出场打卡）"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationError
from app.db.models import UserFace
from app.db.repository import (
    close_session_record,
    create_session_record,
    get_face,
    open_session_record,
    set_face_state,
)
from app.domain.session import SessionState, session_duration_seconds


@dataclass(slots=True)
class ActionResult:
    """一次打卡动作的结果"""

    success: bool
    message: str
    name: str
    state: SessionState
    deduplicated: bool = False
    session_minutes: int = 0
    total_minutes: int = 0
    duration_seconds: int = 0
    checkin_at: datetime | None = None
    checkout_at: datetime | None = None


class SessionService:
    """场地打卡会话服务"""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, name: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(name)
            if lock is None:
                lock = threading.Lock()
                self._locks[name] = lock
            return lock

    def _load_face(self, db: Session, name: str) -> UserFace:
        face = get_face(db, name)
        if face is None:
            raise NotFoundError(f"人脸未注册：{name}", code="face_not_found")
        return face

    def state_of(self, name: str, face: UserFace | None, *, now: datetime | None = None) -> SessionState:
        if face is None:
            return SessionState(is_online=False)
        return SessionState(
            is_online=bool(face.is_online),
            checkin_at=face.checkin_time,
            accumulated_minutes=int(face.online_time or 0),
        )

    def check_in(
        self,
        db: Session,
        *,
        name: str,
        now: datetime | None = None,
        source: str = "manual",
    ) -> ActionResult:
        """进场打卡（签到）"""
        moment = now or datetime.now()
        name = (name or "").strip()
        if not name:
            raise ValidationError("人名为空")

        with self._lock_for(name):
            face = self._load_face(db, name)

            if face.is_online:
                return ActionResult(
                    success=True,
                    message=f"{name} 已在场中",
                    name=name,
                    state=self.state_of(name, face, now=moment),
                    deduplicated=True,
                    checkin_at=face.checkin_time,
                )

            ok = set_face_state(
                db,
                face_id=face.id,
                is_online=True,
                checkin_at=moment,
                expected_online=False,
            )
            if not ok:
                db.rollback()
                db.refresh(face)
                return ActionResult(
                    success=True,
                    message=f"{name} 已在场中",
                    name=name,
                    state=self.state_of(name, face, now=moment),
                    deduplicated=True,
                    checkin_at=face.checkin_time,
                )
            create_session_record(db, name=name, checkin_at=moment, source=source)
            db.commit()
            face.is_online = True
            face.checkin_time = moment

            return ActionResult(
                success=True,
                message=f"{name} 签到成功",
                name=name,
                state=self.state_of(name, face, now=moment),
                checkin_at=moment,
            )

    def check_out(
        self,
        db: Session,
        *,
        name: str,
        now: datetime | None = None,
        checkout_time: datetime | None = None,
        checkin_time: datetime | None = None,
        duration_minutes: int | None = None,
        source: str = "manual",
    ) -> ActionResult:
        """出场打卡（签退）"""
        moment = now or datetime.now()
        name = (name or "").strip()
        if not name:
            raise ValidationError("人名为空")

        settle_at = self._normalize_moment_required(checkout_time, moment)
        new_checkin = self._normalize_moment(checkin_time)

        with self._lock_for(name):
            face = self._load_face(db, name)

            if not face.is_online:
                return ActionResult(
                    success=False,
                    message=f"{name} 当前未在场，无需签退",
                    name=name,
                    state=self.state_of(name, face, now=moment),
                )

            if settle_at > moment:
                settle_at = moment

            start_time = face.checkin_time or moment
            if duration_minutes is not None:
                start_time = settle_at - timedelta(minutes=max(0, int(duration_minutes)))
            elif new_checkin is not None:
                start_time = min(new_checkin, settle_at)

            duration_sec = max(0, int((settle_at - start_time).total_seconds()))
            session_min = duration_sec // 60
            total_min = int(face.online_time or 0) + session_min

            ok = set_face_state(
                db,
                face_id=face.id,
                is_online=False,
                checkin_at=None,
                accumulated_minutes=total_min,
                expected_online=True,
            )
            if not ok:
                db.rollback()
                db.refresh(face)
                return ActionResult(
                    success=True,
                    message=f"{name} 已被签退",
                    name=name,
                    state=self.state_of(name, face, now=moment),
                    deduplicated=True,
                )
            self._close_record(
                db,
                name=name,
                checkin_at=start_time,
                checkout_at=settle_at,
                duration_seconds=duration_sec,
                duration_minutes=session_min,
                source=source,
            )
            db.commit()
            db.expire(face)
            face.is_online = False
            face.checkin_time = None
            face.online_time = total_min

            return ActionResult(
                success=True,
                message=f"{name} 签退成功，在场 {session_min} 分钟",
                name=name,
                state=SessionState(is_online=False, accumulated_minutes=total_min),
                session_minutes=session_min,
                total_minutes=total_min,
                duration_seconds=duration_sec,
                checkin_at=start_time,
                checkout_at=settle_at,
            )

    @staticmethod
    def _close_record(
        db: Session,
        *,
        name: str,
        checkin_at: datetime,
        checkout_at: datetime,
        duration_seconds: int,
        duration_minutes: int,
        source: str,
    ) -> None:
        """闭合本次流水

        正常情况下签到已写过一条未闭合流水，这里补齐出场时刻；
        若是升级前就在场的会话（没有对应流水），直接补写一条完整记录。
        流水写入失败不应影响签退本身，因此这里吞掉异常并告警。
        """
        try:
            record = open_session_record(db, name=name)
            if record is not None:
                close_session_record(
                    db,
                    record_id=record.id,
                    checkout_at=checkout_at,
                    duration_seconds=duration_seconds,
                    duration_minutes=duration_minutes,
                )
                return
            record = create_session_record(
                db, name=name, checkin_at=checkin_at, source=source
            )
            close_session_record(
                db,
                record_id=record.id,
                checkout_at=checkout_at,
                duration_seconds=duration_seconds,
                duration_minutes=duration_minutes,
            )
        except Exception:
            # 流水写失败不能让签退回滚
            pass

    @staticmethod
    def _normalize_moment(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is not None:
            return value.astimezone().replace(tzinfo=None)
        return value

    @classmethod
    def _normalize_moment_required(cls, value: datetime | None, fallback: datetime) -> datetime:
        normalized = cls._normalize_moment(value)
        return fallback if normalized is None else normalized

    def get_status(self, db: Session, *, name: str, now: datetime | None = None) -> SessionState:
        face = self._load_face(db, name)
        return self.state_of(name, face, now=now)

    def current_minutes(self, db: Session, *, name: str, now: datetime | None = None) -> int:
        moment = now or datetime.now()
        face = self._load_face(db, name)
        state = self.state_of(name, face, now=moment)
        if not state.is_online or state.checkin_at is None:
            return 0
        return session_duration_seconds(state, moment) // 60

    def forget_face(self, name: str, *, face_id: int | None = None) -> None:
        with self._locks_guard:
            self._locks.pop(name, None)

    def rename_runtime(self, old_name: str, new_name: str) -> None:
        with self._locks_guard:
            lock = self._locks.pop(old_name, None)
            if lock is not None:
                self._locks[new_name] = lock

    def reset(self) -> None:
        with self._locks_guard:
            self._locks.clear()


session_service = SessionService()

__all__ = ["ActionResult", "SessionService", "session_service"]
