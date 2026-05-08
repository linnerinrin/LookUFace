"""
认证 API 路由
- 用户注册/登录
- 个人信息管理（获取/修改）
- 密码修改
- 邮箱验证码发送
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from datetime import datetime

from app.database import get_db, User
from app.auth.auth import verify_password, get_password_hash, create_access_token, decode_token
from app.auth.email_service import send_verification_code, verify_code

router = APIRouter(prefix="/auth", tags=["认证"])
security = HTTPBearer()


# ========== 请求模型 ==========
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    nickname: str = None                    # 昵称可选
    code: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class SendCodeRequest(BaseModel):
    email: EmailStr
    type: str  # register 或 reset


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str
    new_password: str


class UpdateProfileRequest(BaseModel):
    nickname: str = None
    bio: str = None


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


# ========== 辅助函数 ==========
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)):
    """从 token 获取当前用户"""
    payload = decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="无效的token")

    user_id = payload.get("user_id")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


# ========== API ==========
@router.post("/send-code")
async def send_code(request: SendCodeRequest, db: Session = Depends(get_db)):
    """发送邮箱验证码"""
    if request.type == "register":
        existing = db.query(User).filter(User.email == request.email).first()
        if existing:
            raise HTTPException(status_code=400, detail="邮箱已被注册")

    success = await send_verification_code(db, request.email, request.type)
    if success:
        return {"success": True, "message": "验证码已发送"}
    else:
        raise HTTPException(status_code=500, detail="发送失败")


@router.post("/register")
async def register(request: RegisterRequest, db: Session = Depends(get_db)):
    """用户注册（使用邮箱）"""
    if not verify_code(db, request.email, request.code, "register"):
        raise HTTPException(status_code=400, detail="验证码无效或已过期")

    existing = db.query(User).filter(User.email == request.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="邮箱已被注册")

    # 昵称默认使用邮箱前缀
    nickname = request.nickname if request.nickname else request.email.split('@')[0]

    user = User(
        email=request.email,
        password_hash=get_password_hash(request.password),
        nickname=nickname
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token({"user_id": user.id, "email": user.email})

    return {
        "success": True,
        "token": token,
        "user": {
            "id": user.id,
            "email": user.email,
            "nickname": user.nickname
        }
    }


@router.post("/login")
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    """用户登录（使用邮箱）"""
    user = db.query(User).filter(User.email == request.email).first()
    if not user:
        raise HTTPException(status_code=401, detail="邮箱或密码错误")

    if not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")

    user.updated_at = datetime.now()
    db.commit()

    token = create_access_token({"user_id": user.id, "email": user.email})

    return {
        "success": True,
        "token": token,
        "user": {
            "id": user.id,
            "email": user.email,
            "nickname": user.nickname,
            "avatar": user.avatar,
            "bio": user.bio,
            "created_at": user.created_at.isoformat()
        }
    }


@router.post("/reset-password")
async def reset_password(request: ResetPasswordRequest, db: Session = Depends(get_db)):
    """重置密码"""
    if not verify_code(db, request.email, request.code, "reset"):
        raise HTTPException(status_code=400, detail="验证码无效或已过期")

    user = db.query(User).filter(User.email == request.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    user.password_hash = get_password_hash(request.new_password)
    user.updated_at = datetime.now()
    db.commit()

    return {"success": True, "message": "密码重置成功"}


@router.get("/profile")
async def get_profile(current_user: User = Depends(get_current_user)):
    """获取当前用户信息"""
    return {
        "id": current_user.id,
        "email": current_user.email,
        "nickname": current_user.nickname,
        "avatar": current_user.avatar,
        "bio": current_user.bio,
        "face_identity": current_user.face_identity,
        "created_at": current_user.created_at.isoformat()
    }


@router.put("/profile")
async def update_profile(
    request: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新个人信息"""
    if request.nickname is not None:
        current_user.nickname = request.nickname
    if request.bio is not None:
        current_user.bio = request.bio
    current_user.updated_at = datetime.now()
    db.commit()

    return {"success": True, "user": {
        "nickname": current_user.nickname,
        "bio": current_user.bio
    }}


@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """修改密码"""
    if not verify_password(request.old_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="原密码错误")

    current_user.password_hash = get_password_hash(request.new_password)
    current_user.updated_at = datetime.now()
    db.commit()

    return {"success": True, "message": "密码修改成功"}