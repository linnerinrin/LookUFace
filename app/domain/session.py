"""打卡会话领域逻辑"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class SessionState:
    """一次在线会话的完整快照"""

    is_online: bool = False
    checkin_at: datetime | None = None
    accumulated_minutes: int = 0

    @property
    def has_active_session(self) -> bool:
        return bool(self.is_online and self.checkin_at is not None)


def session_duration_seconds(state: SessionState, now: datetime) -> int:
    """计算当前会话在场秒数"""
    if state.checkin_at is None:
        return 0
    elapsed = (now - state.checkin_at).total_seconds()
    return max(0, int(elapsed))


__all__ = [
    "SessionState",
    "session_duration_seconds",
]
