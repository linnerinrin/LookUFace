"""场地计时缴费的领域逻辑（纯函数，不依赖数据库）。

这是对 P0「计费负数」缺陷的修复核心
------------------------------------
旧版 ``app/core/checking.py`` 在暂离时执行::

    face.checkin_time += datetime.timedelta(minutes=away_time)

即把**签到时间往未来推** ``away_time`` 分钟。已实测：10:00 签到、暂离 30 分钟后
checkin_time 变成 10:30，若此时签退，``now - checkin_time`` 为负，
``online_time`` 会被**扣减**（可为负）。

正确模型
--------
计费时长 = 本次会话已流逝时间 − 暂离时长，且必须 **clamp 到 [0, MAX]**::

    elapsed   = now - checkin_at
    away      = away_accumulated + (now - away_started_at if is_away else 0)
    billable  = clamp(elapsed - away, 0, MAX_SESSION_MINUTES * 60)

由于既有表结构不变（没有新增累计字段），暂离时长由 ``checkin_time`` 与
「当前时刻 − 暂离开始时刻」共同推导 —— 见 ``app/services/session.py`` 中
对状态的封装。本模块只负责**给定输入算输出**，因此可以完全单测覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True, slots=True)
class SessionState:
    """一次在线会话的完整快照。"""

    is_online: bool = False
    is_away: bool = False
    #: 会话计费起点（暂离期间不推进）
    checkin_at: datetime | None = None
    #: 已累计的历史计费分钟数（会话开始前）
    accumulated_minutes: int = 0
    #: 本次会话中「仍在进行」的那段暂离的开始时刻
    away_started_at: datetime | None = None
    #: 本次会话中已结束的暂离总秒数
    settled_away_seconds: float = 0.0

    @property
    def has_active_session(self) -> bool:
        return bool(self.is_online and self.checkin_at is not None)


@dataclass(frozen=True, slots=True)
class BillingResult:
    """一次结算的结果。"""

    session_minutes: int
    total_minutes: int
    amount: float
    capped: bool
    online_seconds: int


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def billable_seconds(
    state: SessionState,
    now: datetime,
    *,
    max_session_minutes: int,
) -> tuple[int, bool]:
    """计算本次会话的计费秒数。

    返回 ``(计费秒数, 是否被上限截断)``。
    结果保证 ``>= 0``，从根本上排除旧版的负数计费。
    """
    if state.checkin_at is None:
        return 0, False

    elapsed = (now - state.checkin_at).total_seconds()
    # 时钟回拨或数据异常时，流逝时间为负 -> 视为 0，而不是产生负账单
    if elapsed <= 0:
        return 0, False

    away = state.settled_away_seconds
    if state.is_away and state.away_started_at is not None:
        ongoing = (now - state.away_started_at).total_seconds()
        if ongoing > 0:
            away += ongoing

    net = elapsed - away
    if net <= 0:
        return 0, False

    ceiling = max_session_minutes * 60
    capped = net > ceiling
    return int(_clamp(net, 0, ceiling)), capped


def compute_amount(minutes: int, rate_per_minute: float) -> float:
    """按分钟单价计算金额，四舍五入到分。"""
    if minutes <= 0 or rate_per_minute <= 0:
        return 0.0
    amount = Decimal(str(rate_per_minute)) * Decimal(int(minutes))
    return float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def bill(
    state: SessionState,
    now: datetime,
    *,
    rate_per_minute: float,
    max_session_minutes: int,
) -> BillingResult:
    """结算本次会话。"""
    seconds, capped = billable_seconds(state, now, max_session_minutes=max_session_minutes)
    # 不足一分钟不计费，向下取整，避免「进门一秒就算一分钟」
    session_minutes = seconds // 60
    total_minutes = int(state.accumulated_minutes or 0) + int(session_minutes)
    return BillingResult(
        session_minutes=int(session_minutes),
        total_minutes=total_minutes,
        amount=compute_amount(int(session_minutes), rate_per_minute),
        capped=capped,
        online_seconds=int(seconds),
    )


def is_away_expired(
    state: SessionState,
    now: datetime,
    *,
    away_timeout_minutes: int,
) -> bool:
    """判断当前暂离是否已超时（用于自动签退）。"""
    if not (state.is_online and state.is_away and state.away_started_at is not None):
        return False
    return (now - state.away_started_at) >= timedelta(minutes=away_timeout_minutes)


def preview_minutes(state: SessionState, now: datetime, *, max_session_minutes: int) -> int:
    """预估当前账单分钟数（前端展示用），不落库。"""
    seconds, _ = billable_seconds(state, now, max_session_minutes=max_session_minutes)
    return int(seconds // 60)


@dataclass(frozen=True, slots=True)
class Breakdown:
    """计费算式明细。

    把「为什么是这个金额」所需的每一项都摊开，便于排查
    「为什么签退显示 0 分钟」这类问题 —— 只给出一个最终数字时，
    无法区分「确实只待了几十秒」与「时间基准算错了」。
    """

    #: 从签到到现在经过的分钟数（未扣暂离）
    elapsed_minutes: int
    #: 从签到到现在**精确经过的秒数**（不扣暂离）。
    #: 用于把「签到 7 秒后就签退」与「时间基准算错了」区分开 ——
    #: 只显示"不足 1 分钟"时用户无法判断到底是哪一种。
    elapsed_seconds: int
    #: 其中被暂离扣掉的分钟数
    away_minutes: int
    #: 实际计费的分钟数（向下取整）
    billable_minutes: int
    #: 计费是否触发了单次上限
    capped: bool
    #: 精确的计费秒数（用于解释「不足一分钟为何是 0」）
    billable_seconds: int

    @property
    def amount_note(self) -> str:
        if self.billable_minutes <= 0:
            return f"不足 1 分钟（实际 {self.billable_seconds} 秒），按规则不计费"
        return f"{self.billable_minutes} 分钟"


def breakdown(
    state: SessionState,
    now: datetime,
    *,
    max_session_minutes: int,
) -> Breakdown:
    """计算计费算式明细。"""
    elapsed_seconds = 0.0
    if state.checkin_at is not None:
        elapsed_seconds = max(0.0, (now - state.checkin_at).total_seconds())

    away_seconds = state.settled_away_seconds
    if state.is_away and state.away_started_at is not None:
        ongoing = (now - state.away_started_at).total_seconds()
        if ongoing > 0:
            away_seconds += ongoing

    billable, capped = billable_seconds(state, now, max_session_minutes=max_session_minutes)

    return Breakdown(
        elapsed_minutes=int(elapsed_seconds // 60),
        elapsed_seconds=int(elapsed_seconds),
        away_minutes=int(max(0.0, away_seconds) // 60),
        billable_minutes=int(billable // 60),
        capped=capped,
        billable_seconds=int(billable),
    )


__all__ = [
    "BillingResult",
    "Breakdown",
    "SessionState",
    "bill",
    "billable_seconds",
    "breakdown",
    "compute_amount",
    "is_away_expired",
    "preview_minutes",
]
