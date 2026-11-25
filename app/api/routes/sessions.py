"""在线会话（签到 / 签退 / 状态）路由，人脸名即业务键"""

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
from app.services.session import session_service

router = APIRouter(prefix="/sessions", tags=["打卡会话"])


# 签到端点
@router.post("/checkin", response_model=CheckInResponse)
async def check_in(
    request: CheckInRequest,
    db: DbSession,
) -> CheckInResponse:
    """进场打卡（签到）"""
    result = session_service.check_in(db, name=request.name)
    return CheckInResponse(
        success=result.success,
        name=result.name,
        message=result.message,
        is_online=result.state.is_online,
        deduplicated=result.deduplicated,
    )


# 签退端点
@router.post("/checkout", response_model=CheckOutResponse)
async def check_out(
    request: CheckOutRequest,
    db: DbSession,
) -> CheckOutResponse:
    """出场打卡（签退）"""
    result = session_service.check_out(
        db,
        name=request.name,
        checkout_time=request.checkout_time,
        checkin_time=request.checkin_time,
        duration_minutes=request.duration_minutes,
    )
    return CheckOutResponse(
        success=result.success,
        name=result.name,
        message=result.message,
        session_minutes=result.session_minutes,
        total_minutes=result.total_minutes,
        is_online=result.state.is_online,
        settled=result.success and not result.deduplicated,
        checkin_time=result.checkin_at,
        checkout_time=result.checkout_at,
        elapsed_minutes=result.session_minutes,
        elapsed_seconds=result.duration_seconds,
    )


# 状态端点
@router.post("/status", response_model=SessionStatusResponse)
async def session_status(
    request: SessionStatusRequest,
    db: DbSession,
) -> SessionStatusResponse:
    """查询实时打卡状态"""
    state = session_service.get_status(db, name=request.name)
    now = datetime.now()
    minutes = 0
    seconds = 0
    if state.is_online and state.checkin_at:
        seconds = max(0, int((now - state.checkin_at).total_seconds()))
        minutes = seconds // 60

    return SessionStatusResponse(
        name=request.name,
        is_online=state.is_online,
        checkin_time=state.checkin_at,
        preview_minutes=minutes,
        online_time=int(state.accumulated_minutes or 0),
        server_now=now,
        elapsed_minutes=minutes,
        elapsed_seconds=seconds,
    )


__all__ = ["router"]
