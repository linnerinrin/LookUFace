"""计费支付二维码生成。

相对旧版的修复（**P1 安全**）：

旧版 ``checking_service.drawing_costQRcode`` 直接拼接用户输入::

    qr_path = qr_dir / f"{name}_{online_time}.png"

实测 ``name='../../../evil'`` 可写出 ``C:\\Users\\...\\AppData\\Local\\evil_10.png``，
构成路径穿越；出错时返回空字符串 ``""``，调用方 ``FileResponse("")`` 会抛未处理异常。

现在：
* 文件名由 UUID 生成，用户输入只经过 ``sanitize_label`` 变成可读后缀；
* 落盘前用 ``safe_join`` 校验；
* 失败抛 ``StorageError``，由全局异常处理器转成规范响应；
* 目录改到 ``storage/qrcodes``（旧版在系统临时目录，容器/多实例下不可预期）；
* **金额由服务端会话状态决定**，不再接受前端传入的 ``online_time`` 作为结算依据。
"""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from pathlib import Path

import qrcode
from qrcode.image.pil import PilImage

from app.config import settings
from app.core.errors import StorageError, ValidationError
from app.domain.session import compute_amount
from app.logger import logger
from app.services.storage import safe_join, sanitize_label

#: 模拟支付网关。接入真实支付时替换为收银台下单接口。
PAYMENT_GATEWAY_URL = "https://pay.example.com/pay"


@dataclass(frozen=True, slots=True)
class PaymentQuote:
    """一次计费报价（由服务端计算）。"""

    name: str
    minutes: int
    amount: float
    pay_url: str
    qrcode_path: Path | None = None


class QRCodeService:
    """生成支付二维码。"""

    def __init__(self) -> None:
        self._rate = settings.BILLING_RATE_PER_MINUTE

    def build_quote(self, *, name: str, minutes: int) -> PaymentQuote:
        """按分钟数生成报价与支付链接。金额只来自服务端。"""
        if minutes < 0:
            raise ValidationError("计费时长不能为负")
        amount = compute_amount(int(minutes), self._rate)
        label = sanitize_label(name, fallback="user")
        pay_url = f"{PAYMENT_GATEWAY_URL}?name={label}&minutes={int(minutes)}&amount={amount:.2f}"
        return PaymentQuote(name=name, minutes=int(minutes), amount=amount, pay_url=pay_url)

    def render_png(self, quote: PaymentQuote) -> bytes:
        """把支付链接渲染成 PNG 字节。"""
        try:
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=10,
                border=3,
            )
            qr.add_data(quote.pay_url)
            qr.make(fit=True)
            image: PilImage = qr.make_image(fill_color="black", back_color="white")
        except Exception as exc:  # pragma: no cover
            raise StorageError(f"二维码渲染失败：{exc}") from exc

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def save(self, quote: PaymentQuote) -> Path:
        """生成并落盘，返回绝对路径。"""
        settings.qrcode_dir.mkdir(parents=True, exist_ok=True)
        label = sanitize_label(quote.name, fallback="user")
        filename = f"{label}_{quote.minutes}min_{uuid.uuid4().hex[:8]}.png"
        path = safe_join(settings.qrcode_dir, filename)

        try:
            path.write_bytes(self.render_png(quote))
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError(f"二维码写入失败：{exc}") from exc

        logger.info(
            "生成支付二维码 name=%s minutes=%d amount=%.2f path=%s",
            quote.name,
            quote.minutes,
            quote.amount,
            path.name,
        )
        return path

    def generate_for(self, *, name: str, minutes: int) -> PaymentQuote:
        """报价并落盘，返回带路径的报价。"""
        quote = self.build_quote(name=name, minutes=minutes)
        path = self.save(quote)
        return PaymentQuote(
            name=quote.name,
            minutes=quote.minutes,
            amount=quote.amount,
            pay_url=quote.pay_url,
            qrcode_path=path,
        )


qrcode_service = QRCodeService()

__all__ = ["PAYMENT_GATEWAY_URL", "PaymentQuote", "QRCodeService", "qrcode_service"]
