"""数据库引擎、会话与 ORM 模型。

**表结构保持不变**（User / VerificationCode / UserFace 三张表、列名与类型完全一致），
因此既有 ``storage/face_lens.db`` 无需迁移即可直接使用。

相对旧版的修复：
1. SQLite 开启 ``foreign_keys`` pragma（旧版外键约束实际上不生效）。
2. ``DATABASE_URL`` 的相对路径已由 config 锚定到项目根目录，
   不再依赖启动时的工作目录。
3. ``expire_on_commit=False``：避免提交后访问 ORM 属性触发意外查询。
4. 会话统一通过 ``session_scope()`` 上下文管理，杜绝忘记 ``close()``。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


# --------------------------------------------------------------------------- #
# 模型（列定义与旧版完全一致，勿随意改动）
# --------------------------------------------------------------------------- #


class User(Base):
    """用户表（使用邮箱作为登录账号）。"""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    nickname = Column(String(50), nullable=True)
    avatar = Column(String(255), nullable=True)
    bio = Column(Text, nullable=True)
    face_identity = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class VerificationCode(Base):
    """邮箱验证码表。"""

    __tablename__ = "verification_codes"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(100), nullable=False)
    code = Column(String(6), nullable=False)
    type = Column(String(20), nullable=False)  # register / reset
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)


class UserFace(Base):
    """用户人脸记录表（同时承载签到/计费状态）。

    注意：本表同时充当「计费状态机」的持久化载体。为满足「表结构不变」的约束，
    没有新建 sessions 表，而是继续使用下面四个状态列：

    * ``is_online``     是否处于在线会话中
    * ``is_away``       在线会话中是否处于暂离
    * ``checkin_time``  本次会话**开始计费的时刻**（暂离期间不推进）
    * ``online_time``   历史累计计费分钟数
    """

    __tablename__ = "user_faces"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(100), nullable=False)
    features = Column(LargeBinary, nullable=False)
    screenshot = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    online_time = Column(Integer, default=0)
    is_online = Column(Boolean, default=False)
    is_away = Column(Boolean, default=False)
    checkin_time = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_user_face_name"),
    )


# --------------------------------------------------------------------------- #
# 引擎与会话
# --------------------------------------------------------------------------- #

_is_sqlite = settings.DATABASE_URL.startswith("sqlite")

engine: Engine = create_engine(
    settings.DATABASE_URL,
    # SQLite 在 FastAPI 的线程池里跨线程使用，必须关闭同线程检查
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=True,
    future=True,
)

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        """开启外键约束与 WAL，显著改善并发读写表现。"""
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            # WAL 允许读写并发，缓解旧版「每帧写库」导致的 database is locked
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()


SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
)


def init_database() -> None:
    """创建缺失的表（不修改已存在表的结构）。"""
    settings.ensure_runtime_dirs()
    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：每个请求一个会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """脚本/后台任务用的会话上下文，自动提交与回滚。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


__all__ = [
    "Base",
    "User",
    "VerificationCode",
    "UserFace",
    "engine",
    "SessionLocal",
    "get_db",
    "session_scope",
    "init_database",
]
