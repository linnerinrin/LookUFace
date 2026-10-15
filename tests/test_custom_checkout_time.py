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


def test_checkout_before_checkin_is_rejected(db_session, user, face) -> None:
    """签退时间早于签到时间必须拒绝，而不是算出负时长。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=CHECKIN)

    with pytest.raises(ValidationError) as excinfo:
        session_service.check_out(
            db_session,
            user_id=user.id,
            name=face.name,
            away=False,
            now=NOW,
            checkout_time=CHECKIN - timedelta(minutes=1),
        )

    error = excinfo.value
    assert error.code == "checkout_before_checkin"
    assert "不能早于签到时间" in error.message
    assert error.details and "checkin_time" in error.details

    # 拒绝时不得改变任何状态
    db_session.refresh(face)
    assert face.is_online is True, "校验失败不得关闭会话"
    assert face.checkin_time == CHECKIN


def test_checkout_in_future_is_clamped_not_rejected(db_session, user, face) -> None:
    """签退时间晚于当前时刻时，应**收敛为当前时刻**而不是报错。

    为什么改成收敛：实测最常见的触发场景是「用户打开签退弹窗时控件被填入
    '当时'的时间，过了几分钟才点确认」—— 那个时间点已经变成未来，
    但用户的意图明确就是"现在签退"。报错只会让人困惑
    （实测错误信息："当前约 03:36:59，你填的是 03:45:37"）。

    收敛是**安全**的：未来时间只会让时长偏大，收敛只会少算，绝不会多收钱。
    真正的数据错误（早于签到时间）仍然报错，见上一个用例。
    """
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=CHECKIN)

    result = session_service.check_out(
        db_session,
        user_id=user.id,
        name=face.name,
        away=False,
        now=NOW,
        checkout_time=NOW + timedelta(minutes=30),
    )

    assert result.settled is True, "应照常结算，而不是拒绝"
    assert result.breakdown is not None
    # 收敛到 NOW：签到是 8 小时前，所以计费就是 480 分钟（而不是 510）
    assert result.breakdown.elapsed_minutes == 480, (
        f"应按当前时刻（480 分钟）而不是未来时刻（510 分钟）计算，"
        f"实际 {result.breakdown.elapsed_minutes}"
    )
    assert result.billing is not None
    assert result.billing.session_minutes == 480

    db_session.refresh(face)
    assert face.is_online is False, "应当已正常签退"


def test_clamping_never_overcharges(db_session, user, face) -> None:
    """收敛必须只会"少算"，绝不会因为未来时间而多收钱。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=CHECKIN)

    clamped = session_service.check_out(
        db_session, user_id=user.id, name=face.name, away=False, now=NOW,
        checkout_time=NOW + timedelta(days=3),
    )

    session_service.reset()
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=CHECKIN)
    normal = session_service.check_out(
        db_session, user_id=user.id, name=face.name, away=False, now=NOW,
    )

    assert clamped.billing is not None and normal.billing is not None
    assert clamped.billing.session_minutes == normal.billing.session_minutes, (
        "填一个夸张的未来时间，结果应与不填完全一致（都被收敛到当前时刻）"
    )
    assert clamped.billing.amount == pytest.approx(normal.billing.amount)


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
