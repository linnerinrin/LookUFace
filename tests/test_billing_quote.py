"""账单与签退的语义测试。

覆盖两个真实缺陷：

**缺陷 1：「签退无论多久都显示 0 分钟 0 元」**

根因在前端 ``showCheckoutModal``：它把「在线且未暂离」默认勾选为「暂离」，
而「暂离」走的是**不结算**的分支，返回的 ``session_minutes`` / ``amount``
必然是 0；第二次打开时标题变成「签退结算」但复选框没有复位，仍走暂离分支。
表现为用户无论等多久，点「确认签退」都得到 0 分钟 0 元。

修复：接口显式返回 ``is_away_action`` / ``settled`` 让前端区分两种动作，
前端默认不勾选暂离。

**缺陷 2：「账单无论何时都能显示二维码和支付链接」**

``/billing/quote`` 原先只看「当前会话已累计分钟」，既无应付金额判断，
也不管是否已签退离线 —— 0 元也照样出二维码和支付链接。

修复：``payable`` 为真时才返回二维码与支付链接，否则两者留空。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.config import settings
from app.services.session import session_service

BASE = datetime.now() - timedelta(hours=6)
RATE = settings.BILLING_RATE_PER_MINUTE


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    session_service.reset()


# --------------------------------------------------------------------------- #
# 签退：暂离 vs 结算必须可区分
# --------------------------------------------------------------------------- #


def test_away_action_is_not_settled(db_session, user, face) -> None:
    """暂离不得结算，且必须通过 settled=False 明确表达。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=BASE)

    result = session_service.check_out(
        db_session, user_id=user.id, name=face.name, away=True,
        now=BASE + timedelta(minutes=30),
    )

    assert result.success is True
    assert result.settled is False, "暂离不结算，settled 必须为 False"
    assert result.billing is None, "暂离不应产生结算结果"
    assert result.checkin_at == BASE, "暂离不得改变计费起点"

    db_session.refresh(face)
    assert face.is_online is True, "暂离后仍在计时"
    assert face.is_away is True
    assert face.checkin_time == BASE, "计费起点必须保留，否则时长会被清零"


def test_real_checkout_is_settled(db_session, user, face) -> None:
    """真正的签退必须结算，并给出正确的分钟数与金额。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=BASE)

    result = session_service.check_out(
        db_session, user_id=user.id, name=face.name, away=False,
        now=BASE + timedelta(minutes=90),
    )

    assert result.settled is True
    assert result.billing is not None
    assert result.billing.session_minutes == 90
    assert result.billing.amount == pytest.approx(90 * RATE)
    assert result.checkin_at == BASE, "结算结果里应带上本次计费起点，便于前端展示"

    db_session.refresh(face)
    assert face.is_online is False


def test_checkout_response_exposes_breakdown(db_session, user, face) -> None:
    """结算结果必须带上算式明细，否则无法解释「为什么是 0 分钟」。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=BASE)
    # 中间暂离 40 分钟
    session_service.check_out(
        db_session, user_id=user.id, name=face.name, away=True,
        now=BASE + timedelta(minutes=10),
    )
    result = session_service.check_out(
        db_session, user_id=user.id, name=face.name, away=False,
        now=BASE + timedelta(minutes=60),
    )

    detail = result.breakdown
    assert detail is not None
    assert detail.elapsed_minutes == 60, "已流逝 60 分钟"
    assert detail.away_minutes == 50, "其中暂离 50 分钟"
    assert detail.billable_minutes == 10, "计费 10 分钟"
    assert detail.billable_seconds == 600


def test_dedup_checkout_does_not_settle(db_session, user, face) -> None:
    """对未在线的人签退必须失败，且绝对不能结算。"""
    result = session_service.check_out(
        db_session, user_id=user.id, name=face.name, away=False, now=BASE
    )
    assert result.success is False
    assert result.settled is False
    assert result.billing is None


def test_short_session_bills_zero_but_is_explainable(db_session, user, face) -> None:
    """不足一分钟确实应计 0 元 —— 但必须能说明原因（秒数）。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=BASE)
    result = session_service.check_out(
        db_session, user_id=user.id, name=face.name, away=False,
        now=BASE + timedelta(seconds=40),
    )

    assert result.settled is True, "确实发生了结算"
    assert result.billing is not None
    assert result.billing.session_minutes == 0
    assert result.billing.amount == 0.0
    assert result.breakdown is not None
    assert result.breakdown.billable_seconds == 40
    assert result.breakdown.billable_minutes == 0


# --------------------------------------------------------------------------- #
# 账单：只有存在应付金额时才开放付款入口
# --------------------------------------------------------------------------- #


def test_quote_not_payable_when_offline(db_session, user, face) -> None:
    """**核心回归**：未在计时时不得有应付金额，付款入口必须关闭。"""
    state, minutes, detail, amount, payable = session_service.quote(
        db_session, user_id=user.id, name=face.name
    )

    assert state.is_online is False
    assert minutes == 0
    assert amount == 0.0
    assert payable is False, "离线时 payable 必须为 False"
    assert detail.billable_seconds == 0
    assert detail.elapsed_minutes == 0


def test_quote_not_payable_after_settlement(db_session, user, face) -> None:
    """签退结算完成后，账单必须变成「无应付金额」。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=BASE)
    session_service.check_out(
        db_session, user_id=user.id, name=face.name, away=False,
        now=BASE + timedelta(minutes=30),
    )

    _state, minutes, _detail, amount, payable = session_service.quote(
        db_session, user_id=user.id, name=face.name
    )
    assert minutes == 0
    assert amount == 0.0
    assert payable is False


def test_quote_not_payable_when_less_than_one_minute(db_session, user, face) -> None:
    """不足一分钟：不在应付状态，不应给出付款入口。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=BASE)

    _state, minutes, detail, amount, payable = session_service.quote(
        db_session, user_id=user.id, name=face.name, now=BASE + timedelta(seconds=30)
    )
    assert minutes == 0
    assert amount == 0.0
    assert payable is False
    assert detail.billable_seconds == 30


def test_quote_payable_when_online_with_amount(db_session, user, face) -> None:
    """正在计时且已满一分钟：必须可付，且金额正确。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=BASE)

    state, minutes, detail, amount, payable = session_service.quote(
        db_session, user_id=user.id, name=face.name, now=BASE + timedelta(minutes=45)
    )

    assert state.is_online is True
    assert minutes == 45
    assert amount == pytest.approx(45 * RATE)
    assert payable is True
    assert detail.elapsed_minutes == 45
    assert detail.billable_minutes == 45


def test_quote_excludes_ongoing_away(db_session, user, face) -> None:
    """报价必须扣除进行中的暂离（暂离期间不计费）。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=BASE)
    session_service.check_out(
        db_session, user_id=user.id, name=face.name, away=True,
        now=BASE + timedelta(minutes=10),
    )

    _state, minutes, detail, amount, payable = session_service.quote(
        db_session, user_id=user.id, name=face.name, now=BASE + timedelta(minutes=40)
    )
    # 已流逝 40 分钟，暂离 30 分钟 -> 计费 10 分钟
    assert detail.elapsed_minutes == 40
    assert detail.away_minutes == 30
    assert minutes == 10
    assert amount == pytest.approx(10 * RATE)
    assert payable is True


def test_quote_does_not_mutate_state(db_session, user, face) -> None:
    """报价是只读操作，不得改变任何状态。"""
    session_service.check_in(db_session, user_id=user.id, name=face.name, now=BASE)
    db_session.refresh(face)
    before = (face.is_online, face.is_away, face.checkin_time, face.online_time)

    for _ in range(3):
        session_service.quote(
            db_session, user_id=user.id, name=face.name, now=BASE + timedelta(minutes=5)
        )

    db_session.refresh(face)
    after = (face.is_online, face.is_away, face.checkin_time, face.online_time)
    assert before == after, "报价不应修改数据库状态"


def test_quote_unknown_face_raises(db_session, user) -> None:
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        session_service.quote(db_session, user_id=user.id, name="不存在的人")
