"""会话服务（签到 / 暂离 / 签退）集成测试，走真实数据库会话。

重点：
* 暂离不推进 ``checkin_time``（旧版 P0 缺陷）；
* ``online_time`` 结算后非负；
* 重复签到被去抖，不重复写库；
* 暂离超时兜底任务能自动结算。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.config import settings
from app.domain.registry import session_registry
from app.domain.session import preview_minutes
from app.services.session import session_service

# 使用「相对现在」的时间基准，而不是固定日期：
# 固定日期可能落在未来，会被「时钟回拨不得计费」的防护正确地挡成 0 分钟，
# 导致测试与实现谁对谁错难以分辨。
BASE = datetime.now() - timedelta(hours=5)


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    """每个用例前清空运行时注册表与去抖窗口。"""
    session_registry._states.clear()
    session_service.clear_debounce()


# --------------------------------------------------------------------------- #
# 签到
# --------------------------------------------------------------------------- #


def test_check_in_creates_session(db_session, user, face) -> None:
    result = session_service.check_in(
        db_session, user_id=user.id, name=face.name, now=BASE
    )
    assert result.success is True
    assert result.state.is_online is True
    assert result.state.is_away is False

    db_session.refresh(face)
    assert face.is_online is True
    assert face.is_away is False
    assert face.checkin_time == BASE


def test_repeated_check_in_is_deduplicated(db_session, user, face) -> None:
    first = session_service.check_in(db_session, user_id=user.id, name=face.name, now=BASE)
    assert first.deduplicated is False

    second = session_service.check_in(
        db_session, user_id=user.id, name=face.name, now=BASE + timedelta(seconds=1)
    )
    assert second.deduplicated is True

    db_session.refresh(face)
    # 签到时间不应被后续重复请求改写
    assert face.checkin_time == BASE


def test_check_in_unknown_face_raises(db_session, user) -> None:
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        session_service.check_in(db_session, user_id=user.id, name="不存在的人")


def test_other_users_face_is_not_visible(db_session, user, face) -> None:
    """越权：不应能操作别人的脸。"""
    from app.core.errors import NotFoundError

    other_user_id = user.id + 999
    with pytest.raises(NotFoundError):
        session_service.check_in(db_session, user_id=other_user_id, name=face.name)


# --------------------------------------------------------------------------- #
# 暂离 —— P0 缺陷回归
# --------------------------------------------------------------------------- #


def test_away_does_not_push_checkin_time_forward(db_session, user, face) -> None:
    """旧版会执行 checkin_time += away_time，把签到时间推到未来。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=BASE)

    away_at = BASE + timedelta(minutes=10)
    result = session_service.check_out(
        db_session, user_id=user.id, name=face.name, away=True, now=away_at
    )
    assert result.success is True

    db_session.refresh(face)
    assert face.is_away is True
    assert face.is_online is True
    # 核心断言：checkin_time 必须原地不动
    assert face.checkin_time == BASE, "暂离不得修改签到时间（旧版 P0 缺陷）"


def test_away_then_return_resumes_timing(db_session, user, face) -> None:
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=BASE)
    session_service.check_out(
        db_session,
        user_id=user.id,
        name=face.name,
        away=True,
        now=BASE + timedelta(minutes=10),
    )
    back = session_service.check_in(
        db_session,
        user_id=user.id,
        name=face.name,
        now=BASE + timedelta(minutes=25),
        skip_debounce=True,
    )
    assert back.success is True
    db_session.refresh(face)
    assert face.is_away is False
    assert face.checkin_time == BASE


# --------------------------------------------------------------------------- #
# 签退结算
# --------------------------------------------------------------------------- #


def test_checkout_bills_away_time_excluded(db_session, user, face) -> None:
    """签到 60 分钟，其中暂离 50 分钟 -> 只计费 10 分钟。

    时间线（BASE = 5 小时前）：
      10:00 签到
      10:10 暂离
      11:00 签退
    已流逝 60 分钟，暂离 50 分钟，计费 10 分钟。
    """
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=BASE)
    session_service.check_out(
        db_session,
        user_id=user.id,
        name=face.name,
        away=True,
        now=BASE + timedelta(minutes=10),
    )
    result = session_service.check_out(
        db_session,
        user_id=user.id,
        name=face.name,
        away=False,
        now=BASE + timedelta(minutes=60),
    )

    assert result.success is True
    assert result.billing is not None
    assert result.billing.session_minutes == 10
    assert result.billing.total_minutes == 10
    assert result.billing.amount == pytest.approx(5.0)

    db_session.refresh(face)
    assert face.is_online is False
    assert face.is_away is False
    assert face.checkin_time is None
    assert face.online_time == 10


def test_online_time_never_goes_negative(db_session, user, face) -> None:
    """旧版在「暂离时长 > 在线时长」时会把 online_time 算成负数。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=BASE)
    session_service.check_out(
        db_session,
        user_id=user.id,
        name=face.name,
        away=True,
        now=BASE + timedelta(minutes=1),
    )
    # 5 分钟后就签退：已流逝 5 分钟，但暂离已持续 4 分钟 -> 计费 1 分钟
    result = session_service.check_out(
        db_session,
        user_id=user.id,
        name=face.name,
        away=False,
        now=BASE + timedelta(minutes=5),
    )
    assert result.billing is not None
    assert result.billing.session_minutes >= 0
    assert result.billing.total_minutes >= 0

    db_session.refresh(face)
    assert face.online_time >= 0


def test_accumulated_time_grows_across_sessions(db_session, user, face) -> None:
    """两次独立会话，累计时长应叠加。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=BASE)
    first = session_service.check_out(
        db_session, user_id=user.id, name=face.name, away=False,
        now=BASE + timedelta(minutes=30),
    )
    assert first.billing is not None
    assert first.billing.total_minutes == 30

    second_start = BASE + timedelta(hours=5)
    session_service.check_in(
        db_session, user_id=user.id, name=face.name, now=second_start, skip_debounce=True
    )
    second = session_service.check_out(
        db_session, user_id=user.id, name=face.name, away=False,
        now=second_start + timedelta(minutes=45),
    )
    assert second.billing is not None
    assert second.billing.session_minutes == 45
    assert second.billing.total_minutes == 75

    db_session.refresh(face)
    assert face.online_time == 75


def test_checkout_without_session_fails_gracefully(db_session, user, face) -> None:
    result = session_service.check_out(
        db_session, user_id=user.id, name=face.name, away=False
    )
    assert result.success is False
    assert "未在计时" in result.message


def test_away_on_non_online_face_does_nothing(db_session, user, face) -> None:
    result = session_service.check_out(
        db_session, user_id=user.id, name=face.name, away=True
    )
    assert result.success is False


# --------------------------------------------------------------------------- #
# 状态查询
# --------------------------------------------------------------------------- #


def test_status_reports_preview_minutes(db_session, user, face) -> None:
    """预估分钟数必须基于传入的时间基准，且随时间增长。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=BASE)

    # 刚签到 -> 0 分钟
    assert session_service.current_minutes(
        db_session, user_id=user.id, name=face.name, now=BASE
    ) == 0

    # 42 分钟后 -> 42 分钟
    assert session_service.current_minutes(
        db_session, user_id=user.id, name=face.name, now=BASE + timedelta(minutes=42)
    ) == 42

    state = session_service.get_status(
        db_session, user_id=user.id, name=face.name, now=BASE + timedelta(minutes=42)
    )
    assert state.is_online is True
    assert (
        preview_minutes(state, BASE + timedelta(minutes=42), max_session_minutes=settings.MAX_SESSION_MINUTES)
        == 42
    )


def test_preview_excludes_ongoing_away(db_session, user, face) -> None:
    """预估分钟数也必须扣除进行中的暂离。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=BASE)
    session_service.check_out(
        db_session,
        user_id=user.id,
        name=face.name,
        away=True,
        now=BASE + timedelta(minutes=10),
    )
    # 已流逝 30 分钟，其中暂离 20 分钟 -> 10 分钟
    assert session_service.current_minutes(
        db_session, user_id=user.id, name=face.name, now=BASE + timedelta(minutes=30)
    ) == 10


# --------------------------------------------------------------------------- #
# 兜底自动签退
# --------------------------------------------------------------------------- #


def test_auto_settle_expired_away_session(db_session, user, face) -> None:
    """暂离超时后，兜底任务必须自动结算（旧版完全缺失该机制）。

    时间线：签到 BASE -> 暂离 BASE+5 -> 超时判定 BASE+5+TIMEOUT+1。
    计费 = 签到 到 暂离开始 = 5 分钟；超时后的时间不计费。
    """
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=BASE)
    session_service.check_out(
        db_session,
        user_id=user.id,
        name=face.name,
        away=True,
        now=BASE + timedelta(minutes=5),
    )

    timeout = settings.AWAY_TIMEOUT_MINUTES

    # 未超时：不应结算
    assert session_service.auto_settle_expired(
        db_session, now=BASE + timedelta(minutes=10)
    ) == 0
    # 刚好处在超时边界之前
    assert session_service.auto_settle_expired(
        db_session, now=BASE + timedelta(minutes=5 + timeout) - timedelta(seconds=1)
    ) == 0

    # 超过 AWAY_TIMEOUT_MINUTES：自动结算
    settled = session_service.auto_settle_expired(
        db_session, now=BASE + timedelta(minutes=5 + timeout + 1)
    )
    assert settled == 1

    db_session.refresh(face)
    assert face.is_online is False
    assert face.is_away is False
    assert face.checkin_time is None
    # 计费 5 分钟：超时后的时长不计费（用户已不在场）
    assert face.online_time == 5
    assert face.online_time >= 0


def test_stale_runtime_cleanup(db_session, user, face) -> None:
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=BASE)
    assert session_registry.size == 1

    # 模拟数据库中记录被删除
    db_session.delete(face)
    db_session.commit()

    removed = session_service.drop_stale_runtime(db_session)
    assert removed == 1
    assert session_registry.size == 0
