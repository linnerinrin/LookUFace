"""认证依赖。

相对旧版的修复：
1. 旧版 ``get_current_user`` 直接用 ``HTTPBearer()``（默认 ``auto_error=True``），
   缺 header 时抛的是 Starlette 的 403，与 401 语义混用。现在统一返回 401，
   并在响应里带 ``WWW-Authenticate``。
2. 令牌解析失败、用户不存在、用户被删除三种情况分别给出明确 ``code``，
   便于前端区分「需要重新登录」与「账号异常」。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import AuthError
from app.db.models import User, get_db
from app.services.security import decode_token_or_raise

# auto_error=False：自己抛统一异常，而不是让 Starlette 抛 403
_bearer = HTTPBearer(auto_error=False, description="JWT 访问令牌")

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: DbSession,
) -> User:
    """从 ``Authorization: Bearer <token>`` 解析当前用户。"""
    if credentials is None or not credentials.credentials:
        raise AuthError("缺少访问令牌，请先登录")

    payload = decode_token_or_raise(credentials.credentials)
    user_id = payload.get("user_id")
    if not isinstance(user_id, int):
        raise AuthError("令牌内容不合法")

    user = db.get(User, user_id)
    if user is None:
        raise AuthError("账号不存在或已被删除", code="user_not_found")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]

__all__ = ["CurrentUser", "DbSession", "get_current_user", "optional_current_user"]


def optional_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: DbSession,
) -> User | None:
    """允许匿名访问的依赖：令牌无效时返回 ``None`` 而不是抛异常。"""
    if credentials is None or not credentials.credentials:
        return None
    try:
        payload = decode_token_or_raise(credentials.credentials)
    except AuthError:
        return None
    user_id = payload.get("user_id")
    if not isinstance(user_id, int):
        return None
    return db.get(User, user_id)
