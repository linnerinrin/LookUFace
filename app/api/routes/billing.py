"""计费与支付二维码路由。

相对旧版的修复（**P0 功能缺失 + P1 安全**）：

* 旧版 ``/check/pay`` 要求请求体带 ``online_time``，而**前端从未调用过该接口**，
  功能实际是死的；且时长由客户端任意指定，可直接免单。
* 现在 ``/billing/quote`` 只接受人脸名，分钟数由服务端按会话状态计算，
  再生成二维码。
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.api.deps import CurrentUser, DbSession
from app.api.schemas import BillingQuoteRequest, BillingQuoteResponse
from app.core.errors import AppError, NotFoundError
from app.config import settings
from app.logger import logger
from app.services.qrcode_service import qrcode_service
from app.services.session import session_service

router = APIRouter(prefix="/billing", tags=["计费"])


@router.post("/quote", response_model=BillingQuoteResponse)
async def create_quote(
    request: BillingQuoteRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> BillingQuoteResponse:
    """为某个人脸生成支付报价与二维码（分钟数由服务端计算）。"""
    minutes = session_service.current_minutes(db, user_id=current_user.id, name=request.name)

    quote = qrcode_service.generate_for(name=request.name, minutes=minutes)
    logger.info(
        "生成报价 name=%s minutes=%d amount=%.2f user=%s",
        request.name,
        quote.minutes,
        quote.amount,
        current_user.email,
    )
    return BillingQuoteResponse(
        name=quote.name,
        minutes=quote.minutes,
        amount=quote.amount,
        rate_per_minute=settings.BILLING_RATE_PER_MINUTE,
        pay_url=quote.pay_url,
        qrcode_url=f"/api/v1/billing/qrcode/{quote.qrcode_path.name}" if quote.qrcode_path else "",
    )


@router.get("/qrcode/{filename}")
async def get_qrcode(filename: str, current_user: CurrentUser) -> FileResponse:
    """取回已生成的二维码图片。文件名经过越界校验，只允许 storage/qrcodes 下的文件。"""
    from app.services.storage import resolve_stored_path, safe_join

    try:
        path = safe_join(settings.qrcode_dir, filename)
    except AppError:
        raise NotFoundError("二维码不存在") from None

    if not path.exists():
        raise NotFoundError("二维码不存在或已过期")

    # safe_join 已保证路径在目录内；此处再确认一次落在 storage 之下
    if resolve_stored_path(str(path)) is None:
        raise NotFoundError("二维码不存在")

    return FileResponse(str(path), media_type="image/png", filename=path.name)


__all__ = ["router"]
