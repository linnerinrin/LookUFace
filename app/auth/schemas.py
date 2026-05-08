"""
app/auth/schemas.py
数据模型定义
UserProfile 用户信息
"""


from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr


class UserProfile(BaseModel):
    id: int
    email: str
    nickname: Optional[str] = None
    avatar: Optional[str] = None
    bio: Optional[str] = None
    face_identity: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}



class SendCodeRequest(BaseModel):
    email: EmailStr
    type: str
class SendCodeResponse(BaseModel):
    success: bool
    message: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    nickname: str = None
    code: str
class RegisterResponse(BaseModel):
    success: bool
    token: str
    user: UserProfile


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
class LoginResponse(BaseModel):
    success: bool
    token: str
    user: UserProfile


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str
    new_password: str
class ResetPasswordResponse(BaseModel):
    success: bool
    message: str


class UpdateProfileRequest(BaseModel):
    nickname: str = None
    bio: str = None
class UpdateProfileResponse(BaseModel):
    success: bool
    user: UserProfile


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str
class ChangePasswordResponse(BaseModel):
    success: bool
    message: str