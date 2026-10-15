"""认证路由。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.api.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SendCodeRequest,
    SendCodeResponse,
    SimpleMessageResponse,
    TokenResponse,
    UpdateProfileRequest,
    UpdateProfileResponse,
    UserProfile,
)
from app.db.repository import get_user_by_email
from app.core.errors import ConflictError
from app.logger import logger
from app.services.auth_service import auth_service
from app.services.email_service import email_service

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/send-code", response_model=SendCodeResponse)
async def send_code(request: SendCodeRequest, db: DbSession) -> SendCodeResponse:
    """发送邮箱验证码。"""
    email = request.email.strip().lower()

    if request.type == "register" and get_user_by_email(db, email) is not None:
        # 注册场景下明确告知，便于用户直接去登录
        raise ConflictError("该邮箱已被注册，请直接登录或使用「忘记密码」")

    await email_service.send_verification_code(db, email, request.type)
    logger.info("验证码已发送 email=%s type=%s", email, request.type)
    return SendCodeResponse(message="验证码已发送，请查收邮件（有效期 10 分钟）")


@router.post("/register", response_model=TokenResponse)
async def register(request: RegisterRequest, db: DbSession) -> TokenResponse:
    """注册并直接登录。"""
    user, token = auth_service.register(
        db,
        email=request.email,
        password=request.password,
        code=request.code,
        nickname=request.nickname,
    )
    return TokenResponse(token=token, user=UserProfile.model_validate(user))


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest, db: DbSession) -> TokenResponse:
    """邮箱 + 密码登录。"""
    user, token = auth_service.login(db, email=request.email, password=request.password)
    return TokenResponse(token=token, user=UserProfile.model_validate(user))


@router.post("/reset-password", response_model=SimpleMessageResponse)
async def reset_password(request: ResetPasswordRequest, db: DbSession) -> SimpleMessageResponse:
    """通过邮箱验证码重置密码。"""
    auth_service.reset_password(
        db,
        email=request.email,
        code=request.code,
        new_password=request.new_password,
    )
    return SimpleMessageResponse(message="密码重置成功，请使用新密码登录")


@router.get("/profile", response_model=UserProfile)
async def get_profile(current_user: CurrentUser) -> UserProfile:
    """获取当前用户资料。"""
    return UserProfile.model_validate(current_user)


@router.put("/profile", response_model=UpdateProfileResponse)
async def update_profile(
    request: UpdateProfileRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> UpdateProfileResponse:
    """更新昵称 / 简介。"""
    user = auth_service.update_profile(
        db,
        user=current_user,
        nickname=request.nickname,
        bio=request.bio,
    )
    return UpdateProfileResponse(user=UserProfile.model_validate(user))


@router.post("/change-password", response_model=SimpleMessageResponse)
async def change_password(
    request: ChangePasswordRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> SimpleMessageResponse:
    """修改密码。"""
    auth_service.change_password(
        db,
        user=current_user,
        old_password=request.old_password,
        new_password=request.new_password,
    )
    return SimpleMessageResponse(message="密码修改成功，请重新登录")


__all__ = ["router"]
