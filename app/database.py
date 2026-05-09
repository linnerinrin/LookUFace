"""
app/database.py
数据库模型定义（SQLAlchemy ORM）
User 用户表
VerificationCode 验证码表 用于注册
UserFace 脸的表
get_db() 创建与数据库的连接
"""

from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text, ForeignKey, LargeBinary, \
    UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from app.config import settings

DATABASE_URL = settings.DATABASE_URL or "sqlite:///./face_lens.db" #确定地址
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}) #创建引擎 通过此引擎创建的表都存储在url中
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine) #创建会话
Base = declarative_base() #基类 所有会话继承于他


class User(Base):
    """用户表（使用邮箱作为登录账号）"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(100), unique=True, nullable=False)      # 邮箱作为登录账号
    password_hash = Column(String(255), nullable=False)           # 密码哈希
    nickname = Column(String(50), nullable=True)                  # 昵称（可改，不唯一）
    avatar = Column(String(255), nullable=True)                   # 头像路径
    bio = Column(Text, nullable=True)                             # 个人简介
    face_identity = Column(String(100), nullable=True)            # 关联的人脸名称
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class VerificationCode(Base):
    """验证码表"""
    __tablename__ = "verification_codes"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(100), nullable=False)
    code = Column(String(6), nullable=False)
    type = Column(String(20), nullable=False)  # register, reset
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)


class UserFace(Base):
    """用户人脸特征表"""
    __tablename__ = "user_faces"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(100), nullable=False)
    features = Column(LargeBinary, nullable=False)
    screenshot = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    online_time=Column(Integer, default=0) #总登入时间
    is_online= Column(Boolean,default=False)
    checkin_time=Column(Integer, default=0) #登入当前时间

    # 同一用户下名称唯一，不同用户可以有同名
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_user_face_name"),
    )

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()