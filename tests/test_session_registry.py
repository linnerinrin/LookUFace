"""会话运行时注册表的测试。

重点覆盖一个**真实缺陷的回归**：
把进行中的暂离折算进 ``settled_away_seconds`` 并清空 ``away_started_at`` 后，
``is_away_expired`` 会永远返回 False（因为它的前提条件之一是
``away_started_at is not None``），导致「暂离超时自动签退」这个兜底机制
实际不生效。因此 ``build_state`` 必须保留 ``away_started_at``。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.config import settings
from app.domain.registry import SessionRegistry, session_registry
from app.domain.session import bill

BASE = datetime.now() - timedelta(hours=5)


@pytest.fixture
def registry() -> SessionRegistry:
    return SessionRegistry()


def test_begin_and_mark_away(registry: SessionRegistry) -> None:
    registry.begin(1, "张三", now=BASE)
    state = registry.build_state(
        user_id=1, name="张三", is_online=True, is_away=False,
        checkin_at=BASE, accumulated_minutes=0,
    )
    assert state.is_online is True
    assert state.away_started_at is None
    assert state.settled_away_seconds == 0.0

    registry.mark_away(1, "张三", now=BASE + timedelta(minutes=5))
    state = registry.build_state(
        user_id=1, name="张三", is_online=True, is_away=True,
        checkin_at=BASE, accumulated_minutes=0,
    )
    assert state.away_started_at == BASE + timedelta(minutes=5)


def test_clear_away_accumulates(registry: SessionRegistry) -> None:
    registry.begin(1, "张三", now=BASE)
    registry.mark_away(1, "张三", now=BASE + timedelta(minutes=5))

    seconds = registry.clear_away(1, "张三", now=BASE + timedelta(minutes=15))
    assert seconds == pytest.approx(600.0)

    state = registry.build_state(
        user_id=1, name="张三", is_online=True, is_away=False,
        checkin_at=BASE, accumulated_minutes=0,
    )
    assert state.settled_away_seconds == pytest.approx(600.0)
    assert state.away_started_at is None


def test_clear_away_without_mark_returns_zero(registry: SessionRegistry) -> None:
    registry.begin(1, "张三", now=BASE)
    assert registry.clear_away(1, "张三", now=BASE + timedelta(minutes=1)) == 0.0


def test_build_state_keeps_away_started_at() -> None:
    """核心回归：build_state 不得清空 away_started_at，否则超时永远判不出来。"""
    session_registry.begin(1, "张三", now=BASE)
    session_registry.mark_away(1, "张三", now=BASE + timedelta(minutes=5))

    state = session_registry.build_state(
        user_id=1, name="张三", is_online=True, is_away=True,
        checkin_at=BASE, accumulated_minutes=0,
    )
    assert state.away_started_at is not None, "清空后 is_away_expired 会永远为 False"

    # 未超时
    assert session_registry.is_expired(state, BASE + timedelta(minutes=10)) is False
    # 超时
    timeout = settings.AWAY_TIMEOUT_MINUTES
    assert session_registry.is_expired(
        state, BASE + timedelta(minutes=5 + timeout + 1)
    ) is True


def test_restart_fallback_uses_checkin_time(registry: SessionRegistry) -> None:
    """进程重启丢失运行态后，持久层显示暂离时把起点回退到计费起点。

    该回退是**保守**的（只可能少计费，不会多计费）。
    """
    state = registry.build_state(
        user_id=9, name="新进程", is_online=True, is_away=True,
        checkin_at=BASE, accumulated_minutes=0,
    )
    assert state.away_started_at == BASE

    result = bill(
        state,
        BASE + timedelta(minutes=60),
        rate_per_minute=0.5,
        max_session_minutes=1440,
    )
    assert result.session_minutes == 0  # 全程按暂离处理，不计费但不为负
    assert result.total_minutes >= 0


def test_away_time_consistently_excluded_when_billing() -> None:
    """同一个状态在「进行中暂离」下结算，结果必须与手动折算一致。"""
    session_registry.begin(2, "李四", now=BASE)
    session_registry.mark_away(2, "李四", now=BASE + timedelta(minutes=10))

    moment = BASE + timedelta(minutes=30)
    ongoing = session_registry.build_state(
        user_id=2, name="李四", is_online=True, is_away=True,
        checkin_at=BASE, accumulated_minutes=0,
    )
    ongoing_result = bill(ongoing, moment, rate_per_minute=0.5, max_session_minutes=1440)

    # 已流逝 30 分钟，暂离 20 分钟 -> 计费 10 分钟
    assert ongoing_result.session_minutes == 10

    # 收口后再结算，结果必须一致（不能重复扣除）
    ended = session_registry.clear_away(2, "李四", now=moment)
    settled = session_registry.build_state(
        user_id=2, name="李四", is_online=True, is_away=False,
        checkin_at=BASE, accumulated_minutes=0,
    )
    settled_result = bill(settled, moment, rate_per_minute=0.5, max_session_minutes=1440)

    assert settled_result.session_minutes == ongoing_result.session_minutes
    assert ended == pytest.approx(1200.0)


def test_drop_and_size(registry: SessionRegistry) -> None:
    registry.begin(1, "a", now=BASE)
    registry.begin(1, "b", now=BASE)
    assert registry.size == 2

    registry.drop(1, "a")
    assert registry.size == 1


def test_prune_unknown(registry: SessionRegistry) -> None:
    registry.begin(1, "keep", now=BASE)
    registry.begin(2, "stale", now=BASE)

    removed = registry.prune_unknown({(1, "keep")})
    assert removed == 1
    assert registry.size == 1
    assert registry.get(1, "keep") is not None
    assert registry.get(2, "stale") is None


def test_expired_away_keys(registry: SessionRegistry) -> None:
    registry.begin(1, "short", now=BASE)
    registry.mark_away(1, "short", now=BASE)
    registry.begin(2, "long", now=BASE)
    registry.mark_away(2, "long", now=BASE + timedelta(minutes=100))

    timeout = settings.AWAY_TIMEOUT_MINUTES
    expired = registry.expired_away_keys(BASE + timedelta(minutes=timeout + 1))
    assert (1, "short") in expired
    assert (2, "long") not in expired


def test_registry_is_thread_safe() -> None:
    """并发 begin/mark_away/clear_away 不应丢状态或抛错。"""
    import threading

    registry = SessionRegistry()
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            for step in range(200):
                registry.begin(index, f"u{index}", now=BASE)
                registry.mark_away(index, f"u{index}", now=BASE + timedelta(seconds=step))
                registry.clear_away(index, f"u{index}", now=BASE + timedelta(seconds=step + 1))
                registry.build_state(
                    user_id=index, name=f"u{index}", is_online=True, is_away=False,
                    checkin_at=BASE, accumulated_minutes=0,
                )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"并发访问出错：{errors}"
    assert registry.size == 8
