"""
app/auth/routes.py
认证API路由
get_current_user 获取jwt并解码出用户id 然后提取用户信息
send_code 发验证码
register 注册
login 登录
reset_password 密码重置
get_profile 获取用户信息
update_profile 更新信息
change_password 改密码
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from datetime import datetime

from app.auth.schemas import SendCodeRequest, RegisterRequest, ResetPasswordRequest, LoginRequest, ChangePasswordRequest, \
    UpdateProfileRequest, SendCodeResponse, RegisterResponse, ResetPasswordResponse, ChangePasswordResponse, \
    LoginResponse, UpdateProfileResponse, UserProfile
from app.database import get_db, User
from app.core.auth import verify_password, get_password_hash, create_access_token, decode_token
from app.services.email_service import email_service

router = APIRouter(prefix="/auth", tags=["认证"])
security = HTTPBearer()


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)):
    """
    从token获得当前用户
    :param credentials: 注入认证依赖
    :param db:注入数据库会话依赖
    :return:
    """

    # 解码获得jwt字符串
    payload = decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="无效的token")

    user_id = payload.get("user_id")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


@router.post("/send-code", response_model=SendCodeResponse)
async def send_code(request: SendCodeRequest, db: Session = Depends(get_db)):
    """发送邮箱验证码"""
    if request.type == "register":
        existing = db.query(User).filter(User.email == request.email).first()
        if existing:
            raise HTTPException(status_code=400, detail="邮箱已被注册")

    success = await email_service.send_verification_code(db, request.email, request.type)
    if success:
        return SendCodeResponse(
            success=True,
            message="验证码已发送")
    else:
        raise HTTPException(status_code=500, detail="发送失败")


@router.post("/register", response_model=RegisterResponse)
async def register(request: RegisterRequest, db: Session = Depends(get_db)):
    """用户注册（使用邮箱）"""
    if not email_service.verify_code(db, request.email, request.code, "register"):
        raise HTTPException(status_code=400, detail="验证码无效或已过期")

    # db查询邮箱是否存在
    existing = db.query(User).filter(User.email == request.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="邮箱已被注册")

    # 昵称默认使用邮箱前缀
    nickname = request.nickname if request.nickname else request.email.split('@')[0]
    # 写入db
    user = User(
        email=request.email,
        password_hash=get_password_hash(request.password),
        nickname=nickname
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token({"user_id": user.id, "email": user.email})

    return RegisterResponse(
        success=True,
        token=token,
        user=user
    )


@router.post("/login", response_model=LoginResponse)
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

    return LoginResponse(
        success=True,
        token=token,
        user=user
    )


@router.post("/reset-password",response_model=ResetPasswordResponse)
async def reset_password(request: ResetPasswordRequest, db: Session = Depends(get_db)):
    """重置密码"""
    if not email_service.verify_code(db, request.email, request.code, "reset"):
        raise HTTPException(status_code=400, detail="验证码无效或已过期")

    user = db.query(User).filter(User.email == request.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    user.password_hash = get_password_hash(request.new_password)
    user.updated_at = datetime.now()
    db.commit()

    return ResetPasswordResponse(
        success=True,
        message="密码修改成功"
    )


@router.get("/profile",response_model=UserProfile)
async def get_profile(current_user: User = Depends(get_current_user)):
    """获取当前用户信息"""
    return current_user


@router.put("/profile",response_model=UpdateProfileResponse)
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

    return UpdateProfileResponse(
        success=True,
        user=current_user
    )


@router.post("/change-password",response_model=ChangePasswordResponse)
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

    return ChangePasswordResponse(
        success=True,
        message="密码修改成功"
    )
