"""进出场流水路由（数据看板的数据来源）

补录只写 ``session_records``，不触碰 ``user_faces`` 的当前在场状态 —— 历史
区间与「此刻谁在场」是两件事，混在一起会污染打卡状态。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from fastapi import APIRouter

from app.api.deps import DbSession
from app.api.schemas import (
    RecordCreateRequest,
    RecordItem,
    RecordListResponse,
    StatsOverviewResponse,
)
from app.core.errors import NotFoundError, ValidationError
from app.db.repository import (
    close_session_record,
    create_session_record,
    get_face,
    list_session_records,
    online_faces,
)
from app.services.dashboard_service import build_overview

records_router = APIRouter(prefix="/records", tags=["进出场流水"])
stats_router = APIRouter(prefix="/stats", tags=["数据看板"])

# 单次补录的最大跨度，防止误填把图表拉爆
_MAX_SPAN = timedelta(days=365)


# 补录一段已完成的进出场区间
@records_router.post("", response_model=RecordItem)
async def create_record(request: RecordCreateRequest, db: DbSession) -> RecordItem:
    name = request.name.strip()
    if get_face(db, name) is None:
        raise NotFoundError(f"人脸未注册：{name}", code="face_not_found")

    checkin, checkout = request.checkin_at, request.checkout_at
    # datetime-local 可能带时区，统一转成 naive 本地时间再比较
    if checkin.tzinfo is not None:
        checkin = checkin.astimezone().replace(tzinfo=None)
    if checkout.tzinfo is not None:
        checkout = checkout.astimezone().replace(tzinfo=None)
    if checkout < checkin:
        raise ValidationError("退出时间不能早于签到时间")
    if checkout - checkin > _MAX_SPAN:
        raise ValidationError("单次时长不能超过 365 天")

    seconds = int((checkout - checkin).total_seconds())
    record = create_session_record(db, name=name, checkin_at=checkin, source=request.source.strip() or "manual")
    close_session_record(
        db,
        record_id=record.id,
        checkout_at=checkout,
        duration_seconds=seconds,
        duration_minutes=seconds // 60,
    )
    db.commit()
    db.refresh(record)
    return RecordItem(
        id=record.id,
        name=record.name,
        checkin_at=record.checkin_at,
        checkout_at=record.checkout_at,
        duration_seconds=record.duration_seconds,
        duration_minutes=record.duration_minutes,
        source=record.source,
    )


# 流水列表：最近的在前面
@records_router.get("", response_model=RecordListResponse)
async def list_records(db: DbSession, name: str | None = None, limit: int = 500) -> RecordListResponse:
    rows = list_session_records(db, name=name, limit=limit)
    return RecordListResponse(
        records=[
            RecordItem(
                id=r.id,
                name=r.name,
                checkin_at=r.checkin_at,
                checkout_at=r.checkout_at,
                duration_seconds=int(r.duration_seconds or 0),
                duration_minutes=int(r.duration_minutes or 0),
                source=r.source or "manual",
            )
            for r in rows
        ]
    )


# 看板总览：KPI + 5 张图的 data URI，绘图放线程池不阻塞事件循环
@stats_router.get("/overview", response_model=StatsOverviewResponse)
async def stats_overview(db: DbSession, days: int = 7, dark: bool = False) -> StatsOverviewResponse:
    days = min(max(int(days), 1), 90)
    records = list_session_records(db, limit=2000)
    online_now = len(online_faces(db))
    overview = await asyncio.to_thread(
        build_overview, records, online_now=online_now, days=days, dark=dark
    )
    return StatsOverviewResponse(
        kpis=overview["kpis"],
        charts=overview["charts"],
        days=overview["days"],
        total_records=overview["total_records"],
    )


__all__ = ["records_router", "stats_router"]
