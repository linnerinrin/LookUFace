"""自定义签退时间（事后补签退）的测试。

需求：管理员可以填「真实离开时间」而不是「点按钮的时间」，
服务端据此**精确计算**时长与金额。

必须守住的边界：

* 签退时间早于签到时间 -> 拒绝（否则会出现负时长或凭空少算）；
* 签退时间晚于当前时间 -> 拒绝（不允许预支未来的时长），但容忍 60 秒时钟误差；
* 自定义时间下的计费必须与「等到那个时刻再签退」的结果**完全一致**；
* 暂离不受该字段影响（暂离不结算）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.core.errors import ValidationError
from app.services.session import session_service

RATE = settings.BILLING_RATE_PER_MINUTE
# 用相对现在的时间，避免固定日期落在未来被"时钟回拨/未来时间"防护挡住
CHECKIN = datetime.now() - timedelta(hours=8)
NOW = datetime.now()


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    session_service.reset()


# --------------------------------------------------------------------------- #
# 基本用法
# --------------------------------------------------------------------------- #


def test_custom_checkout_time_is_used_for_billing(db_session, user, face) -> None:
    """自定义签退时间必须真的参与计算。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=CHECKIN)

    # 签到后 100 分钟就离开，但 8 小时后才来点签退
    leave_at = CHECKIN + timedelta(minutes=100)
    result = session_service.check_out(
        db_session,
        user_id=user.id,
        name=face.name,
        away=False,
        now=NOW,
        checkout_time=leave_at,
    )

    assert result.settled is True
    assert result.billing is not None
    assert result.billing.session_minutes == 100, "应按自定义的离开时间计算，而不是按点按钮的时间"
    assert result.billing.amount == pytest.approx(100 * RATE)
    assert result.breakdown is not None
    assert result.breakdown.elapsed_minutes == 100


def test_custom_time_matches_waiting_until_that_moment(db_session, user, face) -> None:
    """用自定义时间结算，结果必须与「真的等到那一刻再签退」完全一致。"""
    leave_at = CHECKIN + timedelta(minutes=157)

    # 方式一：模拟"当时就签退"
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=CHECKIN)
    direct = session_service.check_out(
        db_session, user_id=user.id, name=face.name, away=False, now=leave_at
    )

    # 方式二：事后补签退，传入同一个时刻
    session_service.reset()
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=CHECKIN)
    retro = session_service.check_out(
        db_session,
        user_id=user.id,
        name=face.name,
        away=False,
        now=NOW,
        checkout_time=leave_at,
    )

    assert direct.billing is not None and retro.billing is not None
    assert retro.billing.session_minutes == direct.billing.session_minutes == 157
    assert retro.billing.amount == pytest.approx(direct.billing.amount)


def test_custom_time_omitted_means_now(db_session, user, face) -> None:
    """不传自定义时间时按当前时间结算。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=CHECKIN)
    result = session_service.check_out(
        db_session, user_id=user.id, name=face.name, away=False, now=NOW
    )
    assert result.breakdown is not None
    # 480 分钟（8 小时）
    assert result.breakdown.elapsed_minutes == 480


def test_custom_time_second_precision(db_session, user, face) -> None:
    """秒级精度：90 秒 -> 1 分钟；59 秒 -> 0 分钟。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=CHECKIN)

    result = session_service.check_out(
        db_session,
        user_id=user.id,
        name=face.name,
        away=False,
        now=NOW,
        checkout_time=CHECKIN + timedelta(seconds=90),
    )
    assert result.breakdown is not None
    assert result.breakdown.billable_seconds == 90
    assert result.billing is not None and result.billing.session_minutes == 1

    session_service.reset()
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=CHECKIN)
    result2 = session_service.check_out(
        db_session,
        user_id=user.id,
        name=face.name,
        away=False,
        now=NOW,
        checkout_time=CHECKIN + timedelta(seconds=59),
    )
    assert result2.breakdown is not None
    assert result2.breakdown.billable_seconds == 59
    assert result2.billing is not None and result2.billing.session_minutes == 0


# --------------------------------------------------------------------------- #
# 边界与校验
# --------------------------------------------------------------------------- #


def test_future_time_never_overcharges_either_way(db_session, user, face) -> None:
    """无论拒绝还是收敛，都不可能因为未来时间而多收钱。

    这条是"安全性"的兜底证明：拿一个夸张的未来时间，
    结果要么被拒绝（少算），要么与不填时完全一致 —— 绝不会得出更大的金额。
    """
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=CHECKIN)
    normal = session_service.check_out(
        db_session, user_id=user.id, name=face.name, away=False, now=NOW,
    )
    assert normal.billing is not None

    session_service.reset()
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=CHECKIN)
    try:
        future = session_service.check_out(
            db_session, user_id=user.id, name=face.name, away=False, now=NOW,
            checkout_time=NOW + timedelta(days=3),
        )
    except ValidationError:
        # 被拒绝 —— 少算，安全
        return

    assert future.billing is not None
    assert future.billing.session_minutes <= normal.billing.session_minutes
    assert future.billing.amount <= normal.billing.amount


def test_small_clock_skew_is_tolerated(db_session, user, face) -> None:
    """管理员机器比服务器快几秒时也必须能正常结算（会被收敛，不报错）。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=CHECKIN)

    result = session_service.check_out(
        db_session,
        user_id=user.id,
        name=face.name,
        away=False,
        now=NOW,
        checkout_time=NOW + timedelta(seconds=30),
    )
    assert result.settled is True


def test_checkout_time_equal_to_checkin_is_allowed(db_session, user, face) -> None:
    """签退时间等于签到时间是合法边界（0 分钟）。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=CHECKIN)

    result = session_service.check_out(
        db_session,
        user_id=user.id,
        name=face.name,
        away=False,
        now=NOW,
        checkout_time=CHECKIN,
    )
    assert result.settled is True
    assert result.billing is not None
    assert result.billing.session_minutes == 0
    assert result.billing.amount == 0.0


# --------------------------------------------------------------------------- #
# 时区与暂离
# --------------------------------------------------------------------------- #


def test_timezone_aware_checkout_time_is_converted(db_session, user, face) -> None:
    """带时区的时间（如 ...Z）应换算成本地时间后再计算。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=CHECKIN)

    # 与 NOW 同一时刻，但用 UTC 表示
    aware_now = NOW.astimezone(timezone.utc)
    result = session_service.check_out(
        db_session,
        user_id=user.id,
        name=face.name,
        away=False,
        now=NOW,
        checkout_time=aware_now,
    )
    assert result.settled is True
    assert result.breakdown is not None
    # 换算正确的话，流逝时间应接近 8 小时（480 分钟），不应因时区差 8 小时而错
    assert 479 <= result.breakdown.elapsed_minutes <= 480


def test_away_ignores_custom_checkout_time(db_session, user, face) -> None:
    """暂离不结算，因此自定义签退时间对暂离无意义 —— 不得影响行为。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=CHECKIN)

    result = session_service.check_out(
        db_session,
        user_id=user.id,
        name=face.name,
        away=True,
        now=NOW,
        checkout_time=CHECKIN + timedelta(minutes=5),
    )

    assert result.settled is False
    assert result.billing is None
    db_session.refresh(face)
    assert face.is_online is True
    assert face.is_away is True
    assert face.checkin_time == CHECKIN, "暂离不得改动计费起点"


def test_custom_time_with_away_deduction(db_session, user, face) -> None:
    """自定义签退时间 + 中途暂离：两者必须正确叠加。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=CHECKIN)
    # 20 分钟时暂离，50 分钟时归位
    session_service.check_out(
        db_session, user_id=user.id, name=face.name, away=True,
        now=CHECKIN + timedelta(minutes=20),
    )
    session_service.check_in(
        db_session, user_id=user.id, name=face.name,
        now=CHECKIN + timedelta(minutes=50), skip_debounce=True,
    )

    # 160 分钟时离开（事后补签退）
    result = session_service.check_out(
        db_session,
        user_id=user.id,
        name=face.name,
        away=False,
        now=NOW,
        checkout_time=CHECKIN + timedelta(minutes=160),
    )

    assert result.breakdown is not None
    assert result.breakdown.elapsed_minutes == 160
    assert result.breakdown.away_minutes == 30, "暂离 20->50 分钟，共 30 分钟"
    assert result.breakdown.billable_minutes == 130
    assert result.billing is not None
    assert result.billing.amount == pytest.approx(130 * RATE)
