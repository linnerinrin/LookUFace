"""
数据库模型定义（SQLAlchemy ORM）
- 用户表（users）
- 验证码表（verification_codes）
- 用户人脸关联表（user_faces）
"""

from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from app.config import settings

DATABASE_URL = settings.DATABASE_URL or "sqlite:///./face_lens.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


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
    """用户人脸关联表"""
    __tablename__ = "user_faces"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    face_name = Column(String(100), nullable=False)
    screenshot_path = Column(String(255), nullable=True)
    registered_at = Column(DateTime, default=datetime.now)


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()