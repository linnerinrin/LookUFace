# """架构作用：以不可变领域快照实现计费规则，隔离数据库、摄像头与接口传输细节"""
# """模块：计算扣除暂离后的秒数、整分钟、金额、累计分钟及暂离超时与账单解释明细"""
# 输入时刻应由服务层归一为一致时区；本模块只消费给定快照，不自行补齐或持久化暂离历史
"""场地计时缴费的领域逻辑（纯函数，不依赖数据库）

计费时长 = 本次会话已流逝时间 − 暂离时长，且 **clamp到 [0, MAX]**::

    elapsed   = now - checkin_at
    away      = away_accumulated + (now - away_started_at if is_away else 0)
    billable  = clamp(elapsed - away, 0, MAX_SESSION_MINUTES * 60)

本模块只负责**给定输入算输出**，因此可以完全单测覆盖
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True, slots=True)
class SessionState:
    """一次在线会话的完整快照"""

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

    # 在线标记和起点同时有效才算完整活动会话；单个标记不能替代时间基准
    @property
    def has_active_session(self) -> bool:
        return bool(self.is_online and self.checkin_at is not None)


@dataclass(frozen=True, slots=True)
class BillingResult:
    """一次结算的结果"""

    session_minutes: int
    total_minutes: int
    amount: float
    capped: bool
    online_seconds: int


# 将净计费秒数截断到配置区间；配置层负责提供非负上限
def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# 统一秒数入口：已流逝减已结束/进行中暂离，最后下限归零、上限截断并丢弃小数秒
def billable_seconds(
    state: SessionState,
    now: datetime,
    *,
    max_session_minutes: int,
) -> tuple[int, bool]:
    """计算本次会话的计费秒数

    返回 ``(计费秒数, 是否被上限截断)``
    结果保证 ``>= 0``，不产生负数计费
    """
    if state.checkin_at is None:
        return 0, False

    elapsed = (now - state.checkin_at).total_seconds()
    # 时钟回拨或数据异常时，流逝时间为负 -> 视为0，而不是产生负账单
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


# 单价先转字符串再构造Decimal，避免二进制浮点乘法误差；金额仅按本次整分钟计算
def compute_amount(minutes: int, rate_per_minute: float) -> float:
    """按分钟单价计算金额，四舍五入到分"""
    if minutes <= 0 or rate_per_minute <= 0:
        return 0.0
    amount = Decimal(str(rate_per_minute)) * Decimal(int(minutes))
    return float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# 输出本次与历史合计分钟；纯计算不改变输入快照，是否持久化由服务层决定
def bill(
    state: SessionState,
    now: datetime,
    *,
    rate_per_minute: float,
    max_session_minutes: int,
) -> BillingResult:
    """结算本次会话"""
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


# 只有在线、暂离、存在起点三项齐全才判断超时，恰好达到阈值也视为过期
def is_away_expired(
    state: SessionState,
    now: datetime,
    *,
    away_timeout_minutes: int,
) -> bool:
    """判断当前暂离是否已超时（用于自动签退）"""
    if not (state.is_online and state.is_away and state.away_started_at is not None):
        return False
    return (now - state.away_started_at) >= timedelta(minutes=away_timeout_minutes)


# 展示预估复用结算同一秒数公式，避免报价和最终结算采用不同的舍入规则
def preview_minutes(state: SessionState, now: datetime, *, max_session_minutes: int) -> int:
    """预估当前账单分钟数（前端展示用），不落库"""
    seconds, _ = billable_seconds(state, now, max_session_minutes=max_session_minutes)
    return int(seconds // 60)


@dataclass(frozen=True, slots=True)
class Breakdown:
    """计费算式明细

    把「为什么是这个金额」所需的每一项都摊开，便于排查
    「为什么签退显示0分钟」这类问题 —— 只给出一个最终数字时，
    无法区分「确实只待了几十秒」与「时间基准算错了」
    """

    #: 从签到到现在经过的分钟数（未扣暂离）
    elapsed_minutes: int
    #: 从签到到现在**精确经过的秒数**（不扣暂离）
    #: 用于把「签到7秒后就签退」与「时间基准算错了」区分开 ——
    #: 只显示"不足1分钟"时用户无法判断到底是哪一种
    elapsed_seconds: int
    #: 其中被暂离扣掉的分钟数
    away_minutes: int
    #: 实际计费的分钟数（向下取整）
    billable_minutes: int
    #: 计费是否触发了单次上限
    capped: bool
    #: 精确的计费秒数（用于解释「不足一分钟为何是0」）
    billable_seconds: int

    # 展示实际截断后的计费秒数；零分钟时保留秒数便于解释向下取整
    @property
    def amount_note(self) -> str:
        if self.billable_minutes <= 0:
            return f"不足 1 分钟（实际 {self.billable_seconds} 秒），按规则不计费"
        return f"{self.billable_minutes} 分钟"


# 明细展示流逝与暂离的原始量，计费项调用统一公式；各分钟字段单独向下取整，不宜直接相减校验
def breakdown(
    state: SessionState,
    now: datetime,
    *,
    max_session_minutes: int,
) -> Breakdown:
    """计算计费算式明细"""
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
