# """模块：在线计时会话路由"""
# """架构作用：把签到、暂离、签退及报价请求映射到会话服务"""
"""在线会话（签到 / 暂离 / 签退 / 状态）路由，人脸名即业务键"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from app.api.deps import DbSession
from app.api.schemas import (
    CheckInRequest,
    CheckInResponse,
    CheckOutRequest,
    CheckOutResponse,
    SessionStatusRequest,
    SessionStatusResponse,
)
from app.config import settings
from app.logger import logger
from app.services.session import session_service

router = APIRouter(prefix="/sessions", tags=["计时会话"])


# 签到端点：姓名交给状态机，返回是否被去抖视为重复
@router.post("/checkin", response_model=CheckInResponse)
async def check_in(
    request: CheckInRequest,
    db: DbSession,
) -> CheckInResponse:
    """签到。已是暂离状态时视为归位"""
    result = session_service.check_in(db, name=request.name)
    return CheckInResponse(
        success=result.success,
        name=result.name,
        message=result.message,
        is_online=result.state.is_online,
        is_away=result.state.is_away,
        deduplicated=result.deduplicated,
    )


# 签退端点：away=true走暂离，否则按补录参数结算并返回账单
@router.post("/checkout", response_model=CheckOutResponse)
async def check_out(
    request: CheckOutRequest,
    db: DbSession,
) -> CheckOutResponse:
    """签退（``away=false``）或暂离（``away=true``）

    补录在场时间用 ``duration_minutes``（分钟数）：
    服务端把签到时间顺推为 ``结算时刻 - duration_minutes``
    也可用 ``checkout_time`` 指定结算时刻，或 ``checkin_time`` 直接改起点
    """
    result = session_service.check_out(
        db,
        name=request.name,
        away=request.away,
        checkout_time=request.checkout_time,
        checkin_time=request.checkin_time,
        duration_minutes=request.duration_minutes,
    )
    billing = result.billing
    detail = result.breakdown
    return CheckOutResponse(
        success=result.success,
        name=result.name,
        message=result.message,
        session_minutes=billing.session_minutes if billing else 0,
        total_minutes=billing.total_minutes if billing else result.state.accumulated_minutes,
        amount=billing.amount if billing else 0.0,
        is_online=result.state.is_online,
        is_away=result.state.is_away,
        # 关键：明确告诉前端这次是「暂离」还是「签退结算」
        # 暂离不结算，session_minutes/amount必然为0；前端若把两者混为一谈，
        # 就会出现「无论多久都显示0分钟0元」的误解
        is_away_action=request.away,
        settled=result.settled,
        checkin_time=result.checkin_at,
        # 真实结算时刻（可能来自用户自定义，或被服务端收敛）
        checkout_time=result.settled_at,
        elapsed_minutes=detail.elapsed_minutes if detail else 0,
        elapsed_seconds=detail.elapsed_seconds if detail else 0,
        away_minutes=detail.away_minutes if detail else 0,
        rate_per_minute=settings.BILLING_RATE_PER_MINUTE,
    )


# 状态端点：返回持久状态与服务端实时预估
@router.post("/status", response_model=SessionStatusResponse)
async def session_status(
    request: SessionStatusRequest,
    db: DbSession,
) -> SessionStatusResponse:
    """查询实时会话状态与预估账单（含算式明细）"""
    state, minutes, detail, amount, _payable = session_service.quote(
        db,
        name=request.name,
    )

    return SessionStatusResponse(
        name=request.name,
        is_online=state.is_online,
        is_away=state.is_away,
        checkin_time=state.checkin_at,
        preview_minutes=minutes,
        online_time=int(state.accumulated_minutes or 0),
        away_timeout_minutes=settings.AWAY_TIMEOUT_MINUTES,
        elapsed_minutes=detail.elapsed_minutes,
        elapsed_seconds=detail.elapsed_seconds,
        away_minutes=detail.away_minutes,
        rate_per_minute=settings.BILLING_RATE_PER_MINUTE,
        amount=amount,
        # 服务端当前时刻：前端据此设置签退时间的上限，
        # 避免依赖浏览器本地时钟（两者可能有偏差）
        server_now=datetime.now(),
    )


#: 手动结算限频：两次全场地扫描的最小间隔（秒）
_SETTLE_EXPIRED_MIN_INTERVAL = 60.0
_last_settle_expired_at = 0.0


# 运维端点：手动触发全场地超时暂离结算（限频，避免反复全量扫描）
@router.post("/settle-expired")
async def settle_expired(db: DbSession) -> dict[str, object]:
    """手动触发一次「暂离超时自动签退」兜底扫描

    正常由后台任务周期性执行；此接口便于运维在需要时立刻收口
    限频：间隔不足 ``_SETTLE_EXPIRED_MIN_INTERVAL`` 秒时拒绝执行并提示
    """
    import time

    global _last_settle_expired_at
    now = time.monotonic()
    elapsed = now - _last_settle_expired_at
    if elapsed < _SETTLE_EXPIRED_MIN_INTERVAL:
        retry_after = int(_SETTLE_EXPIRED_MIN_INTERVAL - elapsed) + 1
        logger.warning("手动结算被限频：距上次扫描 %.0f 秒，%d 秒后可重试", elapsed, retry_after)
        return {
            "success": False,
            "settled": 0,
            "throttled": True,
            "retry_after_seconds": retry_after,
            "away_timeout_minutes": settings.AWAY_TIMEOUT_MINUTES,
        }
    _last_settle_expired_at = now

    settled = session_service.auto_settle_expired(db)
    logger.info("手动触发超时结算，收口 %d 个会话", settled)
    return {"success": True, "settled": settled, "throttled": False, "away_timeout_minutes": settings.AWAY_TIMEOUT_MINUTES}


__all__ = ["router"]
