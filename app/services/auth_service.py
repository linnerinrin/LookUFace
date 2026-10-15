"""认证业务流程（注册 / 登录 / 改密 / 资料）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.errors import AuthError, ConflictError, NotFoundError, ValidationError
from app.db.models import User
from app.db.repository import create_user, email_exists, get_user_by_email
from app.logger import logger
from app.services.email_service import email_service
from app.services.security import (
    create_access_token,
    get_password_hash,
    validate_password_strength,
    verify_password,
)


class AuthService:
    """认证服务。"""

    # ------------------------------------------------------------------ #
    # 注册
    # ------------------------------------------------------------------ #
    def register(
        self,
        db: Session,
        *,
        email: str,
        password: str,
        code: str,
        nickname: str | None = None,
    ) -> tuple[User, str]:
        email = email.strip().lower()
        validate_password_strength(password)

        # 先校验验证码再查重：避免通过「邮箱是否已注册」的报错差异枚举用户
        if not email_service.verify_code(db, email, code, "register"):
            raise ValidationError("验证码无效或已过期")

        if email_exists(db, email):
            raise ConflictError("该邮箱已被注册")

        display_name = (nickname or "").strip() or email.split("@")[0]
        user = create_user(
            db,
            email=email,
            password_hash=get_password_hash(password),
            nickname=display_name[:50],
        )
        token = create_access_token({"user_id": user.id, "email": user.email})
        logger.info("新用户注册 email=%s nickname=%s", user.email, user.nickname)
        return user, token

    # ------------------------------------------------------------------ #
    # 登录
    # ------------------------------------------------------------------ #
    def login(self, db: Session, *, email: str, password: str) -> tuple[User, str]:
        email = email.strip().lower()
        user = get_user_by_email(db, email)
        # 统一的错误信息，不区分「邮箱不存在」与「密码错误」，避免账号枚举
        if user is None or not verify_password(password, user.password_hash):
            logger.warning("登录失败 email=%s", email)
            raise AuthError("邮箱或密码错误")

        user.updated_at = datetime.now()
        db.commit()
        db.refresh(user)

        token = create_access_token({"user_id": user.id, "email": user.email})
        logger.info("用户登录 email=%s", user.email)
        return user, token

    # ------------------------------------------------------------------ #
    # 重置密码
    # ------------------------------------------------------------------ #
    def reset_password(
        self,
        db: Session,
        *,
        email: str,
        code: str,
        new_password: str,
    ) -> None:
        email = email.strip().lower()
        validate_password_strength(new_password)

        if not email_service.verify_code(db, email, code, "reset"):
            raise ValidationError("验证码无效或已过期")

        user = get_user_by_email(db, email)
        if user is None:
            raise NotFoundError("用户不存在")

        user.password_hash = get_password_hash(new_password)
        user.updated_at = datetime.now()
        db.commit()
        logger.info("密码已重置 email=%s", email)

    # ------------------------------------------------------------------ #
    # 修改密码
    # ------------------------------------------------------------------ #
    def change_password(
        self,
        db: Session,
        *,
        user: User,
        old_password: str,
        new_password: str,
    ) -> None:
        if not verify_password(old_password, user.password_hash):
            raise ValidationError("原密码错误")
        validate_password_strength(new_password)
        if old_password == new_password:
            raise ValidationError("新密码不能与原密码相同")

        user.password_hash = get_password_hash(new_password)
        user.updated_at = datetime.now()
        db.commit()
        logger.info("密码已修改 email=%s", user.email)

    # ------------------------------------------------------------------ #
    # 资料
    # ------------------------------------------------------------------ #
    def update_profile(
        self,
        db: Session,
        *,
        user: User,
        nickname: str | None = None,
        bio: str | None = None,
    ) -> User:
        if nickname is not None:
            cleaned = nickname.strip()
            if not cleaned:
                raise ValidationError("昵称不能为空")
            user.nickname = cleaned[:50]
        if bio is not None:
            user.bio = bio.strip()[:500]
        user.updated_at = datetime.now()
        db.commit()
        db.refresh(user)
        logger.info("更新资料 email=%s", user.email)
        return user


auth_service = AuthService()

__all__ = ["AuthService", "auth_service"]
