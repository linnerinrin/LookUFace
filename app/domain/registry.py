"""在线会话服务的运行时状态注册表。

为什么需要它
------------
「表结构不变」的约束下，``user_faces`` 只有四个状态列可用：
``is_online`` / ``is_away`` / ``checkin_time`` / ``online_time``。
但要精确计算「计费时长 = 已流逝 − 暂离时长」，还需要知道**当前这段暂离是从何时开始的**、
以及**本次会话已结束的暂离累计了多少**。四个列装不下这两项，而多列编码进
BOOLEAN/DATETIME 会造成难以排查的隐式状态。

因此采用两层设计：

* **持久层（DB，权威）**：``is_online`` / ``is_away`` 表示状态；
  ``checkin_time`` 存「本次会话的**计费起点**」（暂离期间不推进）；
  ``online_time`` 存「历史累计计费分钟」。
* **运行时层（本模块）**：记录当前暂离的起始时刻与本次会话已结束的暂离累计秒数。

进程重启后的行为是**保守且正确**的：
``away_started_at`` 若丢失，则把该次暂离视为从 ``checkin_time`` 起一直在暂离 ——
只会**少计费**，绝不会产生旧的负数或虚高账单。后台任务会把超时的暂离会话
自动结算，避免长时间未收口。

状态用内存字典 + 细粒度锁保存。单进程部署（当前形态）完全够用；
若将来要多进程，把本模块换成 Redis 实现即可，接口不变。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime

from app.config import settings
from app.domain.session import SessionState, is_away_expired
from app.logger import logger


@dataclass
class _RuntimeState:
    """某个「用户-人脸」的运行时会话补充信息。"""

    away_started_at: datetime | None = None
    settled_away_seconds: float = 0.0
    updated_at: datetime = field(default_factory=datetime.now)


class SessionRegistry:
    """线程安全的会话运行时注册表。"""

    def __init__(self) -> None:
        self._states: dict[tuple[int, str], _RuntimeState] = {}
        self._lock = threading.RLock()

    # -------- 基础操作 --------
    def _key(self, user_id: int, name: str) -> tuple[int, str]:
        return (int(user_id), name)

    def begin(self, user_id: int, name: str, *, now: datetime | None = None) -> None:
        """开始一次全新会话。"""
        with self._lock:
            self._states[self._key(user_id, name)] = _RuntimeState(updated_at=now or datetime.now())

    def mark_away(self, user_id: int, name: str, *, now: datetime | None = None) -> None:
        """进入暂离。"""
        moment = now or datetime.now()
        with self._lock:
            state = self._states.setdefault(self._key(user_id, name), _RuntimeState())
            state.away_started_at = moment
            state.updated_at = moment

    def clear_away(self, user_id: int, name: str, *, now: datetime | None = None) -> float:
        """结束暂离，返回本次暂离持续秒数。"""
        moment = now or datetime.now()
        with self._lock:
            state = self._states.setdefault(self._key(user_id, name), _RuntimeState())
            started = state.away_started_at
            state.away_started_at = None
            state.updated_at = moment
            if started is None:
                return 0.0
            seconds = max(0.0, (moment - started).total_seconds())
            state.settled_away_seconds += seconds
            return seconds

    def drop(self, user_id: int, name: str) -> None:
        """会话结束后清理。"""
        with self._lock:
            self._states.pop(self._key(user_id, name), None)

    def get(self, user_id: int, name: str) -> _RuntimeState | None:
        with self._lock:
            return self._states.get(self._key(user_id, name))

    # -------- 组合出领域状态 --------
    def build_state(
        self,
        *,
        user_id: int,
        name: str,
        is_online: bool,
        is_away: bool,
        checkin_at: datetime | None,
        accumulated_minutes: int,
        now: datetime | None = None,
    ) -> SessionState:
        """把持久层字段与运行时补充信息合成领域状态。

        ``now`` 用于把「正在进行中的暂离」换算成已结算秒数。传入统一的时间基准
        可以保证计费与去抖、超时判断使用同一个时刻，避免同一请求内出现
        两个略微不同的 ``datetime.now()``。
        """
        runtime = self.get(user_id, name)
        away_started_at = runtime.away_started_at if runtime else None
        settled = runtime.settled_away_seconds if runtime else 0.0

        # 进程重启导致运行时信息丢失，但持久层显示正在暂离：
        # 把暂离起点保守地回退到计费起点（只少计、不多计）。
        if is_online and is_away and away_started_at is None and checkin_at is not None:
            away_started_at = checkin_at

        # 注意：这里**保留** away_started_at 原样返回。
        # 领域层 billable_seconds 会在计算时把「进行中的暂离」算进去，
        # is_away_expired 也依赖它判断超时。若在此处把 ongoing 折算进
        # settled_away_seconds 并清空 away_started_at，会导致暂离超时永远无法判定。
        return SessionState(
            is_online=is_online,
            is_away=is_away,
            checkin_at=checkin_at,
            accumulated_minutes=int(accumulated_minutes or 0),
            away_started_at=away_started_at,
            settled_away_seconds=settled,
        )

    # -------- 超时清理 --------
    def expired_away_keys(self, now: datetime | None = None) -> list[tuple[int, str]]:
        """返回暂离已超时的键（仅依据运行时信息，用于后台兜底）。"""
        moment = now or datetime.now()
        with self._lock:
            expired: list[tuple[int, str]] = []
            for key, state in self._states.items():
                if state.away_started_at is None:
                    continue
                elapsed_minutes = (moment - state.away_started_at).total_seconds() / 60.0
                if elapsed_minutes >= settings.AWAY_TIMEOUT_MINUTES:
                    expired.append(key)
            return expired

    def is_expired(self, state: SessionState, now: datetime | None = None) -> bool:
        return is_away_expired(
            state,
            now or datetime.now(),
            away_timeout_minutes=settings.AWAY_TIMEOUT_MINUTES,
        )

    def prune_unknown(self, valid_keys: set[tuple[int, str]]) -> int:
        """清理数据库中已不存在的人脸对应的运行态。"""
        with self._lock:
            stale = [k for k in self._states if k not in valid_keys]
            for key in stale:
                self._states.pop(key, None)
            if stale:
                logger.debug("清理 %d 条失效会话运行态", len(stale))
            return len(stale)

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._states)


session_registry = SessionRegistry()

__all__ = ["SessionRegistry", "session_registry"]
