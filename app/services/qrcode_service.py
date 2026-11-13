# """架构作用：把服务端计费报价转换为支付链接和可落盘二维码"""
"""计费支付二维码生成

* 文件名由UUID生成，用户输入只经过 ``sanitize_label`` 变成可读后缀；
* 落盘前用 ``safe_join`` 校验；
* 失败抛 ``StorageError``，由全局异常处理器转成规范响应；
* 二维码落盘到 ``storage/qrcodes``；
* **金额由服务端会话状态决定**
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

#: 模拟支付网关。接入真实支付时替换为收银台下单接口
PAYMENT_GATEWAY_URL = "https://pay.example.com/pay"

#: 二维码文件保留策略：超过该秒数的落盘二维码在下次生成时清理
QRCODE_RETENTION_SECONDS = 24 * 3600

#: 报价（二维码）有效期：过期后读取端点拒绝返回
QUOTE_TTL_SECONDS = 15 * 60


@dataclass(frozen=True, slots=True)
class PaymentQuote:
    """一次计费报价（由服务端计算）"""

    name: str
    minutes: int
    amount: float
    pay_url: str
    qrcode_path: Path | None = None


class QRCodeService:
    """生成支付二维码"""

    # """方法：费率每次报价时实时读取运行时配置，动态变更立即生效"""
    def __init__(self) -> None:
        #: 报价状态登记表：文件名 -> (人脸名, 过期时刻time.time())，供读取端点校验
        self._quotes: dict[str, tuple[str, float]] = {}

    @property
    def _rate(self) -> float:
        return settings.BILLING_RATE_PER_MINUTE

    # """方法：显式刷新预留接口——当前费率实时读取，调用此方法无副作用，仅为兼容"""
    def refresh_rate(self) -> float:
        return self._rate

    # """方法：按服务端分钟数计算金额，并组成支付网关URL"""
    def build_quote(self, *, name: str, minutes: int) -> PaymentQuote:
        """按分钟数生成报价与支付链接。金额只来自服务端"""
        if minutes < 0:
            raise ValidationError("计费时长不能为负")
        amount = compute_amount(int(minutes), self._rate)
        label = sanitize_label(name, fallback="user")
        pay_url = f"{PAYMENT_GATEWAY_URL}?name={label}&minutes={int(minutes)}&amount={amount:.2f}"
        return PaymentQuote(name=name, minutes=int(minutes), amount=amount, pay_url=pay_url)

    # """方法：将报价中的支付URL编码为PNG字节，供保存层消费"""
    def render_png(self, quote: PaymentQuote) -> bytes:
        """把支付链接渲染成PNG字节"""
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

    # """方法：渲染二维码并写入qrcodes目录，返回绝对路径。落盘前先清理过期文件"""
    def save(self, quote: PaymentQuote) -> Path:
        """生成并落盘，返回绝对路径"""
        settings.qrcode_dir.mkdir(parents=True, exist_ok=True)
        self._purge_expired(settings.qrcode_dir)
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
        self._register_quote(path.name, quote.name)
        return path

    # """方法：登记一份报价的文件名、会话归属与过期时刻"""
    def _register_quote(self, filename: str, name: str) -> None:
        import time as _time

        self._quotes[filename] = (name, _time.time() + QUOTE_TTL_SECONDS)
        # 顺手清理过期登记，避免表无界增长
        expired = [k for k, (_, exp) in self._quotes.items() if exp < _time.time()]
        for k in expired:
            self._quotes.pop(k, None)

    # """方法：校验二维码文件对应的报价仍在有效期内且会话名匹配"""
    def validate_quote(self, filename: str, *, name: str | None = None) -> bool:
        import time as _time

        entry = self._quotes.get(filename)
        if entry is None:
            return False
        owner, expires_at = entry
        if expires_at < _time.time():
            self._quotes.pop(filename, None)
            return False
        return name is None or owner == name

    # """方法：按保留期清理目录内过期的二维码PNG；单个文件删除失败只告警"""
    @staticmethod
    def _purge_expired(directory: Path, *, now: float | None = None) -> int:
        import time as _time

        now_ts = _time.time() if now is None else now
        removed = 0
        for entry in directory.glob("*.png"):
            try:
                if now_ts - entry.stat().st_mtime > QRCODE_RETENTION_SECONDS:
                    entry.unlink()
                    removed += 1
            except OSError as exc:
                logger.warning("清理过期二维码失败 %s: %s", entry.name, exc)
        if removed:
            logger.info("已清理 %d 个过期支付二维码", removed)
        return removed

    # """方法：串联报价和落盘，返回包含二维码路径的报价对象"""
    def generate_for(self, *, name: str, minutes: int) -> PaymentQuote:
        """报价并落盘，返回带路径的报价"""
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

