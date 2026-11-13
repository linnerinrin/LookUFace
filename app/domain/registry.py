# """架构作用：为数据库未持久化的暂离时间轴提供进程内补充状态，供服务层构造计费快照"""
# """模块：按人脸名记录暂离起点、已结束暂离秒数，并提供超时判定与无效条目清理"""
# 注册表锁保护单次字典操作；完整业务动作与数据库提交的一致性仍需要调用方协调
"""在线会话服务的运行时状态注册表

为什么需要它
------------
``user_faces`` 只有四个状态列可用：
``is_online`` / ``is_away`` / ``checkin_time`` / ``online_time``
但要精确计算「计费时长 = 已流逝 − 暂离时长」，还需要知道**当前这段暂离是从何时开始的**、
以及**本次会话已结束的暂离累计了多少**。四个列装不下这两项，而多列编码进
BOOLEAN/DATETIME会造成难以排查的隐式状态

因此采用两层设计：

* **持久层（DB，权威）**：``is_online`` / ``is_away`` 表示状态；
  ``checkin_time`` 存「本次会话的**计费起点**」（暂离期间不推进）；
  ``online_time`` 存「历史累计计费分钟」；
  ``away_since`` / ``away_seconds`` 持久化当前暂离起点与已结算暂离秒数
* **运行时层（本模块）**：同一份暂离时间轴的进程内缓存，避免每帧读库；
  内存丢失（重启/改名前）时从持久层列恢复，恢复不了才走保守回退

进程重启后的行为是**保守且正确**的：
先按持久化的 ``away_since`` / ``away_seconds`` 精确恢复；这些列也缺失时，
把该次暂离视为从 ``checkin_time`` 起一直在暂离 ——只会**少计费**，
绝不会产生旧的负数或虚高账单。后台任务会把超时的暂离会话自动结算

状态用内存字典 + 细粒度锁保存。单进程部署（当前形态）完全够用；
若将来要多进程，把本模块换成Redis实现即可，接口不变

**v1.2.0单租户化**：键从 ``(user_id, name)`` 收敛为 ``name``
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
    """某张人脸的运行时会话补充信息"""

    away_started_at: datetime | None = None
    settled_away_seconds: float = 0.0
    updated_at: datetime = field(default_factory=datetime.now)


class SessionRegistry:
    """线程安全的会话运行时注册表（键为人脸名）"""

    # 每个进程独立持有字典；RLock允许本类在持锁流程中复用其他受锁方法
    def __init__(self) -> None:
        self._states: dict[str, _RuntimeState] = {}
        self._lock = threading.RLock()

    # -------- 基础操作 --------
    # 新签到覆盖旧条目，保证上一次会话的暂离累计不带入新会话
    def begin(self, name: str, *, now: datetime | None = None) -> None:
        """开始一次全新会话"""
        with self._lock:
            self._states[name] = _RuntimeState(updated_at=now or datetime.now())

    # 服务层先保证当前不在暂离，再记录本段起点；此方法自身不会防止重复调用重置起点
    def mark_away(self, name: str, *, now: datetime | None = None) -> None:
        """进入暂离"""
        moment = now or datetime.now()
        with self._lock:
            state = self._states.setdefault(name, _RuntimeState())
            state.away_started_at = moment
            state.updated_at = moment

    # 收口当前段：清空起点、累加非负秒数并返回新增量，供结算层避免同一段重复扣除
    def clear_away(
        self,
        name: str,
        *,
        now: datetime | None = None,
        fallback_started_at: datetime | None = None,
        fallback_settled_seconds: float | None = None,
    ) -> float:
        """结束暂离，返回本次暂离持续秒数

        运行时起点丢失（重启/改名前）时，用持久层提供的
        ``fallback_started_at``（一般是 ``away_since``，兜底 ``checkin_at``）
        作为本段起点；``fallback_settled_seconds``（一般是 ``away_seconds``）
        用于恢复已结算累计，避免漏扣或多扣
        """
        moment = now or datetime.now()
        with self._lock:
            state = self._states.setdefault(name, _RuntimeState())
            if (
                state.away_started_at is None
                and fallback_settled_seconds is not None
                and state.settled_away_seconds <= 0.0
            ):
                state.settled_away_seconds = max(0.0, float(fallback_settled_seconds))
            started = state.away_started_at or fallback_started_at
            state.away_started_at = None
            state.updated_at = moment
            if started is None:
                return 0.0
            seconds = max(0.0, (moment - started).total_seconds())
            state.settled_away_seconds += seconds
            return seconds

    # 删除仅限运行态；调用方负责先完成数据库状态更新，再决定是否可以丢弃计费依据
    def drop(self, name: str) -> None:
        """会话结束后清理"""
        with self._lock:
            self._states.pop(name, None)

    # 改名迁移：把旧名运行态整体平移到新名，历史暂离不因改名丢失
    def rename(self, old_name: str, new_name: str) -> bool:
        """把 ``old_name`` 的运行时状态迁移到 ``new_name`` 键下。返回是否有状态被迁移"""
        if old_name == new_name:
            return False
        with self._lock:
            state = self._states.pop(old_name, None)
            if state is None:
                return False
            state.updated_at = datetime.now()
            self._states[new_name] = state
            return True

    # 返回锁内一次性复制的字段快照，避免调用方读到与并发写交错的混合状态
    def get(self, name: str) -> _RuntimeState | None:
        with self._lock:
            state = self._states.get(name)
            if state is None:
                return None
            return _RuntimeState(
                away_started_at=state.away_started_at,
                settled_away_seconds=state.settled_away_seconds,
                updated_at=state.updated_at,
            )

    # -------- 组合出领域状态 --------
    # 组合持久标志与内存时间轴为不可变领域对象，缺失活动暂离起点时保守回退到签到时刻
    def build_state(
        self,
        *,
        name: str,
        is_online: bool,
        is_away: bool,
        checkin_at: datetime | None,
        accumulated_minutes: int,
        away_since: datetime | None = None,
        away_seconds: float | None = None,
        now: datetime | None = None,
    ) -> SessionState:
        """把持久层字段与运行时补充信息合成领域状态

        运行时字段在锁内一次性快照，避免与并发写交错出混合状态；
        内存缺失时依次回退到持久层的 ``away_since`` / ``away_seconds``
        （重启恢复），再回退到 ``checkin_at`` 保守估计

        ``now`` 用于把「正在进行中的暂离」换算成已结算秒数。传入统一的时间基准
        可以保证计费与去抖、超时判断使用同一个时刻，避免同一请求内出现
        两个略微不同的 ``datetime.now()``
        """
        runtime = self.get(name)  # get已返回锁内复制的快照
        away_started_at = runtime.away_started_at if runtime else None
        settled = runtime.settled_away_seconds if runtime else 0.0

        # 运行时缺失 → 用持久层恢复（重启后精确恢复计费状态）
        if away_started_at is None and away_since is not None:
            away_started_at = away_since
        if settled <= 0.0 and away_seconds:
            settled = max(0.0, float(away_seconds))

        # 持久层也没有起点，但显示正在暂离：
        # 把暂离起点保守地回退到计费起点（只少计、不多计）
        if is_online and is_away and away_started_at is None and checkin_at is not None:
            away_started_at = checkin_at

        return SessionState(
            is_online=is_online,
            is_away=is_away,
            checkin_at=checkin_at,
            accumulated_minutes=int(accumulated_minutes or 0),
            away_started_at=away_started_at,
            settled_away_seconds=settled,
        )

    # -------- 超时清理 --------
    # 只扫描内存起点，不能找出重启后仅数据库保留的暂离；服务层另按数据库在线记录兜底
    def expired_away_keys(self, now: datetime | None = None) -> list[str]:
        """返回暂离已超时的人脸名（仅依据运行时信息，用于后台兜底）"""
        moment = now or datetime.now()
        with self._lock:
            expired: list[str] = []
            for name, state in self._states.items():
                if state.away_started_at is None:
                    continue
                elapsed_minutes = (moment - state.away_started_at).total_seconds() / 60.0
                if elapsed_minutes >= settings.AWAY_TIMEOUT_MINUTES:
                    expired.append(name)
            return expired

    # 将场地配置注入纯领域判定，避免注册表与领域模型各维护一套超时公式
    def is_expired(self, state: SessionState, now: datetime | None = None) -> bool:
        return is_away_expired(
            state,
            now or datetime.now(),
            away_timeout_minutes=settings.AWAY_TIMEOUT_MINUTES,
        )

    # 有效名集合由数据库提供，锁内先收集再删除，避免迭代字典时修改其大小
    def prune_unknown(self, valid_names: set[str]) -> int:
        """清理数据库中已不存在的人脸对应的运行态"""
        with self._lock:
            stale = [k for k in self._states if k not in valid_names]
            for key in stale:
                self._states.pop(key, None)
            if stale:
                logger.debug("清理 %d 条失效会话运行态", len(stale))
            return len(stale)

    # 诊断当前进程保留的会话补充条目数量，不等同于数据库在线人数
    @property
    def size(self) -> int:
        with self._lock:
            return len(self._states)


session_registry = SessionRegistry()

__all__ = ["SessionRegistry", "session_registry"]
