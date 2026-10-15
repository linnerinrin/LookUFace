"""密码哈希与 JWT。

相对旧版的修复：
1. **UTC 时间**：``python-jose`` 按 RFC 7519 把 ``exp`` 当作 **UTC** 解释。旧版
   ``create_access_token`` 用 ``datetime.utcnow()``（UTC），但验证码过期、
   ``checkin_time`` 等业务时间用 ``datetime.now()``（本地时间，CST=UTC+8），
   两套时间混用容易出错。本模块统一显式使用 ``datetime.now(timezone.utc)``。
2. **密码长度上限**：不再依赖 bcrypt（passlib 1.7.4 与 bcrypt>=4.1 不兼容，
   且 bcrypt 5.0 对 >72 字节直接抛 ``ValueError``）。统一使用
   ``pbkdf2_sha256``，并对超长密码给出明确校验错误。
3. **令牌负载**：显式加入 ``iat`` 与 ``typ``，便于将来做吊销与刷新。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings
from app.core.errors import AuthError, ValidationError
from app.logger import logger

MAX_PASSWORD_LENGTH = 128
MIN_PASSWORD_LENGTH = 6

# pbkdf2_sha256：无外部 C 依赖、无 bcrypt 的 72 字节与版本兼容问题
pwd_context = CryptContext(
    schemes=["pbkdf2_sha256"],
    deprecated="auto",
    pbkdf2_sha256__default_rounds=29000,
)


def validate_password_strength(password: str) -> str:
    """校验密码，返回清洗后的值。"""
    if not password:
        raise ValidationError("密码不能为空")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(f"密码长度至少 {MIN_PASSWORD_LENGTH} 位")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValidationError(f"密码长度不能超过 {MAX_PASSWORD_LENGTH} 位")
    return password


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except (ValueError, TypeError):
        # 哈希串损坏或算法不匹配时不抛栈，只判定失败
        logger.warning("密码校验异常（哈希格式可能不兼容）")
        return False


def create_access_token(data: dict[str, Any], *, expires_minutes: int | None = None) -> str:
    """签发 JWT。时间一律使用 UTC。"""
    now = datetime.now(timezone.utc)
    minutes = expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    payload: dict[str, Any] = {
        **data,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict[str, Any] | None:
    """解码 JWT，失败返回 ``None``。"""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None


def decode_token_or_raise(token: str) -> dict[str, Any]:
    payload = decode_token(token)
    if not payload:
        raise AuthError("无效或已过期的令牌")
    if payload.get("typ") not in (None, "access"):
        raise AuthError("令牌类型不正确")
    return payload


__all__ = [
    "MAX_PASSWORD_LENGTH",
    "MIN_PASSWORD_LENGTH",
    "create_access_token",
    "decode_token",
    "decode_token_or_raise",
    "get_password_hash",
    "pwd_context",
    "validate_password_strength",
    "verify_password",
]
