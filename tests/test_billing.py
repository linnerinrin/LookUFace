"""计费领域逻辑测试。

**核心目标：锁死 P0「计费负数」缺陷。**

旧版 ``app/core/checking.py`` 在暂离时把签到时间往未来推 ``away_time`` 分钟，
导致最终 ``now - checkin_time`` 为负、``online_time`` 被扣减。
下面的 ``test_legacy_away_bug_produces_negative`` 用旧算法复现该现象，
``test_away_never_produces_negative`` 断言新实现不可能出现负数。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.domain.session import (
    SessionState,
    bill,
    billable_seconds,
    compute_amount,
    is_away_expired,
    preview_minutes,
)

BASE = datetime(2026, 1, 1, 10, 0, 0)
RATE = 0.5
MAX_MINUTES = 1440


def _state(**kwargs) -> SessionState:
    defaults = dict(is_online=True, is_away=False, checkin_at=BASE, accumulated_minutes=0)
    defaults.update(kwargs)
    return SessionState(**defaults)


# --------------------------------------------------------------------------- #
# 旧缺陷复现
# --------------------------------------------------------------------------- #


def test_legacy_away_bug_produces_negative() -> None:
    """复现旧实现的负数计费，证明这个缺陷是真实存在的。"""
    checkin_time = BASE
    # 旧实现：face.checkin_time += timedelta(minutes=away_time)
    checkin_time += timedelta(minutes=30)
    now = BASE + timedelta(minutes=5)

    legacy_minutes = int((now - checkin_time).total_seconds() / 60)
    assert legacy_minutes == -25, "旧实现应当算出负数，这正是被修复的缺陷"


def test_away_never_produces_negative() -> None:
    """新实现：暂离 30 分钟后只过了 5 分钟，计费必须为 0 而不是负数。"""
    state = _state(
        is_away=True,
        away_started_at=BASE,
    )
    now = BASE + timedelta(minutes=5)

    seconds, capped = billable_seconds(state, now, max_session_minutes=MAX_MINUTES)
    assert seconds == 0
    assert capped is False

    result = bill(state, now, rate_per_minute=RATE, max_session_minutes=MAX_MINUTES)
    assert result.session_minutes == 0
    assert result.total_minutes == 0
    assert result.amount == 0.0


# --------------------------------------------------------------------------- #
# 正常计费
# --------------------------------------------------------------------------- #


def test_simple_billing_one_hour() -> None:
    state = _state()
    now = BASE + timedelta(hours=1)
    result = bill(state, now, rate_per_minute=RATE, max_session_minutes=MAX_MINUTES)
    assert result.session_minutes == 60
    assert result.total_minutes == 60
    assert result.amount == pytest.approx(30.0)


def test_settled_away_is_excluded() -> None:
    """已结束的暂离时长必须从计费中扣除。"""
    state = _state(settled_away_seconds=900.0)  # 暂离 15 分钟
    now = BASE + timedelta(hours=1)
    result = bill(state, now, rate_per_minute=RATE, max_session_minutes=MAX_MINUTES)
    assert result.session_minutes == 45
    assert result.amount == pytest.approx(22.5)


def test_ongoing_away_is_excluded() -> None:
    """正在进行的暂离也要实时扣除。"""
    state = _state(is_away=True, away_started_at=BASE + timedelta(minutes=20))
    now = BASE + timedelta(hours=1)
    result = bill(state, now, rate_per_minute=RATE, max_session_minutes=MAX_MINUTES)
    assert result.session_minutes == 20  # 60 - 40
    assert result.amount == pytest.approx(10.0)


def test_accumulated_minutes_add_up() -> None:
    state = _state(accumulated_minutes=100)
    now = BASE + timedelta(minutes=30)
    result = bill(state, now, rate_per_minute=RATE, max_session_minutes=MAX_MINUTES)
    assert result.session_minutes == 30
    assert result.total_minutes == 130


def test_partial_minute_not_billed() -> None:
    """不足一分钟不计费，避免「进门一秒算一分钟」。"""
    state = _state()
    now = BASE + timedelta(seconds=59)
    result = bill(state, now, rate_per_minute=RATE, max_session_minutes=MAX_MINUTES)
    assert result.session_minutes == 0
    assert result.amount == 0.0


# --------------------------------------------------------------------------- #
# 边界与异常数据
# --------------------------------------------------------------------------- #


def test_clock_rollback_yields_zero_not_negative() -> None:
    """系统时钟回拨（now < checkin_at）时计费为 0，绝不能是负数。"""
    state = _state()
    now = BASE - timedelta(hours=3)
    result = bill(state, now, rate_per_minute=RATE, max_session_minutes=MAX_MINUTES)
    assert result.session_minutes == 0
    assert result.total_minutes >= 0


def test_max_session_cap_applied() -> None:
    """超长会话被上限截断，防止天价账单。"""
    state = _state()
    now = BASE + timedelta(days=30)
    result = bill(state, now, rate_per_minute=RATE, max_session_minutes=60)
    assert result.session_minutes == 60
    assert result.capped is True


def test_no_checkin_time_is_free() -> None:
    state = SessionState(is_online=True, checkin_at=None)
    result = bill(state, BASE, rate_per_minute=RATE, max_session_minutes=MAX_MINUTES)
    assert result.session_minutes == 0
    assert result.amount == 0.0


def test_amount_rounding_to_cents() -> None:
    assert compute_amount(7, 0.5) == 3.5
    assert compute_amount(3, 0.333) == 1.0  # 1.0 精确到分：0.999 -> 1.00
    assert compute_amount(0, 0.5) == 0.0
    assert compute_amount(10, 0) == 0.0


# --------------------------------------------------------------------------- #
# 暂离超时
# --------------------------------------------------------------------------- #


def test_away_timeout_detection() -> None:
    state = _state(is_away=True, away_started_at=BASE)
    assert is_away_expired(state, BASE + timedelta(minutes=29), away_timeout_minutes=30) is False
    assert is_away_expired(state, BASE + timedelta(minutes=30), away_timeout_minutes=30) is True
    assert is_away_expired(state, BASE + timedelta(hours=2), away_timeout_minutes=30) is True


def test_away_timeout_ignored_when_not_away() -> None:
    state = _state(is_away=False, away_started_at=None)
    assert is_away_expired(state, BASE + timedelta(days=5), away_timeout_minutes=30) is False


def test_preview_minutes_matches_billable() -> None:
    state = _state()
    now = BASE + timedelta(minutes=95)
    assert preview_minutes(state, now, max_session_minutes=MAX_MINUTES) == 95
