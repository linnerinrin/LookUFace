"""在线会话（签到 / 暂离 / 签退 / 状态）路由。

与旧版的契约变化（前端需要同步改动）
------------------------------------
* 路径：``/api/v1/check/checkin`` → ``/api/v1/sessions/checkin``
* 签到请求体去掉了 ``request_times``：旧版靠前端帧计数做去抖，不可靠；
  现在由服务端按 ``CHECKIN_DEBOUNCE_SECONDS`` 去抖。
* 签退请求体去掉了 ``away_time``：旧版把该值加到签到时间上，是 P0 计费缺陷的源头。
* 新增 ``/sessions/status`` 返回实时预估分钟数，供前端展示。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
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


@router.post("/checkin", response_model=CheckInResponse)
async def check_in(
    request: CheckInRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> CheckInResponse:
    """签到。已是暂离状态时视为归位。"""
    result = session_service.check_in(
        db,
        user_id=current_user.id,
        name=request.name,
    )
    return CheckInResponse(
        success=result.success,
        name=result.name,
        message=result.message,
        is_online=result.state.is_online,
        is_away=result.state.is_away,
        deduplicated=result.deduplicated,
    )


@router.post("/checkout", response_model=CheckOutResponse)
async def check_out(
    request: CheckOutRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> CheckOutResponse:
    """签退（``away=false``）或暂离（``away=true``）。"""
    result = session_service.check_out(
        db,
        user_id=current_user.id,
        name=request.name,
        away=request.away,
    )
    billing = result.billing
    return CheckOutResponse(
        success=result.success,
        name=result.name,
        message=result.message,
        session_minutes=billing.session_minutes if billing else 0,
        total_minutes=billing.total_minutes if billing else result.state.accumulated_minutes,
        amount=billing.amount if billing else 0.0,
        is_online=result.state.is_online,
        is_away=result.state.is_away,
    )


@router.post("/status", response_model=SessionStatusResponse)
async def session_status(
    request: SessionStatusRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> SessionStatusResponse:
    """查询实时会话状态与预估账单。"""
    state = session_service.get_status(db, user_id=current_user.id, name=request.name)
    from app.domain.session import preview_minutes

    preview = 0
    if state.is_online and state.checkin_at is not None:
        preview = preview_minutes(
            state,
            datetime.now(),
            max_session_minutes=settings.MAX_SESSION_MINUTES,
        )

    return SessionStatusResponse(
        name=request.name,
        is_online=state.is_online,
        is_away=state.is_away,
        checkin_time=state.checkin_at,
        preview_minutes=preview,
        online_time=int(state.accumulated_minutes or 0),
        away_timeout_minutes=settings.AWAY_TIMEOUT_MINUTES,
    )


@router.post("/settle-expired")
async def settle_expired(current_user: CurrentUser, db: DbSession) -> dict[str, object]:
    """手动触发一次「暂离超时自动签退」兜底扫描。

    正常由后台任务周期性执行；此接口便于运维在需要时立刻收口。
    """
    settled = session_service.auto_settle_expired(db)
    logger.info("手动触发超时结算，收口 %d 个会话（操作人 %s）", settled, current_user.email)
    return {"success": True, "settled": settled, "away_timeout_minutes": settings.AWAY_TIMEOUT_MINUTES}


__all__ = ["router"]
