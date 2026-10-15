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
    """查询账单：**在场时间 + 应付价格**。分钟数与金额由服务端计算。

    设计约定（按用户要求）：

    * **只有存在应付金额时**才返回二维码与支付链接；
    * 金额为 0（未计时、不足一分钟、已结算离线）时
      ``qrcode_url`` 与 ``pay_url`` 都留空、``payable=False``，
      业务上不允许触发生成二维码 —— 避免"0 元也能出账"。
    """
    state, minutes, detail, amount, payable = session_service.quote(
        db,
        user_id=current_user.id,
        name=request.name,
    )

    summary = _summary(state, minutes, detail, amount, payable)

    if not payable:
        # 没有应付金额：不生成二维码、不给支付链接
        logger.info(
            "查询账单 name=%s 无应付金额（在线=%s 计费分钟=%d）user=%s",
            request.name,
            state.is_online,
            minutes,
            current_user.email,
        )
        return BillingQuoteResponse(
            name=request.name,
            minutes=minutes,
            amount=amount,
            rate_per_minute=settings.BILLING_RATE_PER_MINUTE,
            qrcode_url="",
            pay_url="",
            payable=False,
            is_online=state.is_online,
            is_away=state.is_away,
            checkin_time=state.checkin_at,
            elapsed_minutes=detail.elapsed_minutes,
            away_minutes=detail.away_minutes,
            summary=summary,
        )

    quote = qrcode_service.generate_for(name=request.name, minutes=minutes)
    logger.info(
        "生成账单二维码 name=%s minutes=%d amount=%.2f 已流逝=%d分钟 暂离=%d分钟 user=%s",
        request.name,
        quote.minutes,
        quote.amount,
        detail.elapsed_minutes,
        detail.away_minutes,
        current_user.email,
    )
    return BillingQuoteResponse(
        name=request.name,
        minutes=minutes,
        amount=amount,
        rate_per_minute=settings.BILLING_RATE_PER_MINUTE,
        qrcode_url=f"/api/v1/billing/qrcode/{quote.qrcode_path.name}" if quote.qrcode_path else "",
        pay_url=quote.pay_url,
        payable=True,
        is_online=state.is_online,
        is_away=state.is_away,
        checkin_time=state.checkin_at,
        elapsed_minutes=detail.elapsed_minutes,
        away_minutes=detail.away_minutes,
        summary=summary,
    )


def _summary(state, minutes: int, detail, amount: float, payable: bool) -> str:  # noqa: ANN001
    """拼一句可读的账单说明，把「为什么是这个金额」讲清楚。"""
    if not state.is_online:
        return "当前未在计时，没有应付金额。"

    hours, mins = divmod(detail.elapsed_minutes, 60)
    elapsed_text = f"{hours} 小时 {mins} 分钟" if hours else f"{mins} 分钟"

    parts = [f"本次在场 {elapsed_text}"]
    if detail.away_minutes:
        parts.append(f"其中暂离 {detail.away_minutes} 分钟")
    parts.append(f"计费 {minutes} 分钟")
    parts.append(f"应付 {amount:.2f} 元")

    head = "，".join(parts)

    if not payable:
        if detail.billable_seconds <= 0:
            return head + "。不足 1 分钟，按规则不计费，无需支付。"
        return head + "。无需支付。"
    if detail.capped:
        head += "（已触达单次计费上限）"
    return head + "。"


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
