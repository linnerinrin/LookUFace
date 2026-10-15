"""邮箱验证码服务。

相对旧版的修复：
1. **不再吞掉发送失败**：旧版 ``send_verification_code`` 先把验证码写入数据库，
   再发邮件；邮件失败时 ``send_email_sync`` 返回 False，但**验证码仍留在库里**
   （虽然路由会抛 500，用户却可能拿到一个永远收不到验证码的记录）。
   现在先发信、成功后才落库。
2. **SMTP 未配置时降级**：本地开发不再因为没填 SMTP 而整个注册流程不可用，
   而是把验证码打到日志里（并显式标注这是开发模式）。
3. **连接超时**：旧版 ``smtplib.SMTP(...)`` 无超时，SMTP 不通时请求会挂住。
4. **发送频率限制**：同一邮箱同一类型 60 秒内只能发一次，防止被当成短信轰炸机。
5. **HTML 转义**：验证码进 HTML 前做转义（虽然是数字，但保持安全习惯）。
"""

from __future__ import annotations

import asyncio
import html
import secrets
import smtplib
import threading
import time
from datetime import datetime, timedelta
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import ExternalServiceError, RateLimitError, ValidationError
from app.db.repository import consume_code, create_code, invalidate_codes, purge_expired_codes
from app.logger import logger

SMTP_TIMEOUT_SECONDS = 10
RESEND_INTERVAL_SECONDS = 60

_CODE_TYPES = {"register", "reset"}


class _RateLimiter:
    """按 key 的最小发送间隔限制。"""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def check_and_mark(self, key: str, interval: float) -> bool:
        now = time.monotonic()
        with self._lock:
            previous = self._last.get(key)
            if previous is not None and (now - previous) < interval:
                return False
            self._last[key] = now
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._last.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._last.clear()


class EmailService:
    """验证码签发与校验。"""

    def __init__(self) -> None:
        self._limiter = _RateLimiter()

    # ------------------------------------------------------------------ #
    # 验证码
    # ------------------------------------------------------------------ #
    @staticmethod
    def generate_code() -> str:
        """生成 6 位数字验证码。

        使用 ``secrets`` 而非 ``random``：``random`` 的 Mersenne Twister
        状态可由连续输出推断，验证码属于安全敏感值。
        """
        return f"{secrets.randbelow(900000) + 100000}"

    @staticmethod
    def normalize_type(code_type: str) -> str:
        value = (code_type or "").strip().lower()
        if value not in _CODE_TYPES:
            raise ValidationError(
                f"验证码类型必须是 {sorted(_CODE_TYPES)} 之一，收到 {code_type!r}"
            )
        return value

    # ------------------------------------------------------------------ #
    # 发送
    # ------------------------------------------------------------------ #
    def send_email_sync(self, to_email: str, subject: str, body: str) -> bool:
        """同步发送 HTML 邮件。"""
        if not settings.SMTP_ENABLED:
            logger.warning(
                "[开发模式] SMTP 未配置，验证码不会真实发送。收件人=%s 主题=%s",
                to_email,
                subject,
            )
            return False

        message = MIMEMultipart()
        message["From"] = settings.SMTP_USER
        message["To"] = to_email
        message["Subject"] = Header(subject, "utf-8")
        message.attach(MIMEText(body, "html", "utf-8"))

        server = None
        try:
            server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS)
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(message)
            logger.info("验证码邮件已发送至 %s", to_email)
            return True
        except smtplib.SMTPAuthenticationError:
            logger.error("SMTP 认证失败：请检查 SMTP_USER / SMTP_PASSWORD（QQ 邮箱需使用授权码）")
            return False
        except (smtplib.SMTPException, OSError) as exc:
            logger.error("发送邮件失败：%s", exc, exc_info=True)
            return False
        finally:
            if server is not None:
                try:
                    server.quit()
                except Exception:  # pragma: no cover
                    pass

    def _build_body(self, code: str) -> str:
        safe_code = html.escape(code)
        ttl = settings.EMAIL_CODE_TTL_MINUTES
        return f"""\
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #4a90e2;">LookUFace Verification Code</h2>
            <p>Your verification code is:</p>
            <div style="background: #f0f0f5; padding: 15px; border-radius: 8px; margin: 20px 0;">
                <span style="font-size: 32px; font-weight: bold; letter-spacing: 4px;">{safe_code}</span>
            </div>
            <p>This code will expire in <strong>{ttl} minutes</strong>.</p>
        </body>
        </html>
        """

    async def send_verification_code(self, db: Session, email: str, code_type: str) -> bool:
        """签发并发送验证码。只有邮件真正发出后才写入数据库。"""
        code_type = self.normalize_type(code_type)

        if not self._limiter.check_and_mark(f"{email}:{code_type}", RESEND_INTERVAL_SECONDS):
            raise RateLimitError(f"发送过于频繁，请 {RESEND_INTERVAL_SECONDS} 秒后再试")

        code = self.generate_code()
        subject = "LookUFace Verification Code"
        body = self._build_body(code)

        loop = asyncio.get_running_loop()
        sent = await loop.run_in_executor(
            None, self.send_email_sync, email, subject, body
        )

        if not sent:
            # 允许用户立刻重试（否则要等 60 秒）
            self._limiter.reset(f"{email}:{code_type}")
            if settings.SMTP_ENABLED:
                raise ExternalServiceError("验证码邮件发送失败，请稍后重试或联系管理员")
            # 开发模式：把验证码写进日志，方便本地联调
            logger.warning(
                "[开发模式] SMTP 未启用，%s 的 %s 验证码为：%s", email, code_type, code
            )
            invalidate_codes(db, email=email, code_type=code_type)
            create_code(
                db,
                email=email,
                code=code,
                code_type=code_type,
                expires_at=datetime.now() + timedelta(minutes=settings.EMAIL_CODE_TTL_MINUTES),
            )
            return True

        invalidate_codes(db, email=email, code_type=code_type)
        create_code(
            db,
            email=email,
            code=code,
            code_type=code_type,
            expires_at=datetime.now() + timedelta(minutes=settings.EMAIL_CODE_TTL_MINUTES),
        )
        return True

    # ------------------------------------------------------------------ #
    # 校验
    # ------------------------------------------------------------------ #
    def verify_code(self, db: Session, email: str, code: str, code_type: str) -> bool:
        """校验并一次性消费验证码。"""
        code_type = self.normalize_type(code_type)
        ok = consume_code(
            db,
            email=email,
            code=(code or "").strip(),
            code_type=code_type,
            now=datetime.now(),
        )
        if ok:
            logger.info("验证码校验通过 %s（%s）", email, code_type)
        else:
            logger.warning("验证码校验失败 %s（%s）", email, code_type)
        return ok

    def reset_limiter(self) -> None:
        self._limiter.clear()


# --------------------------------------------------------------------------- #
# 后台清理
# --------------------------------------------------------------------------- #


async def cleanup_expired_codes() -> None:
    """定期清理过期验证码。"""
    from app.db.models import SessionLocal

    interval = settings.EMAIL_CODE_CLEANUP_INTERVAL_SECONDS
    while True:
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.debug("过期验证码清理任务已停止")
            raise

        db = SessionLocal()
        try:
            removed = purge_expired_codes(db, now=datetime.now())
            if removed:
                logger.info("已清理 %d 条过期验证码", removed)
        except Exception:
            logger.exception("清理过期验证码失败")
        finally:
            db.close()


email_service = EmailService()

__all__ = [
    "EmailService",
    "cleanup_expired_codes",
    "email_service",
    "RESEND_INTERVAL_SECONDS",
]
