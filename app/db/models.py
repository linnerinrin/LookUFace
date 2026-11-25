"""数据库引擎、会话与ORM模型"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """所有ORM模型的基类"""


# --------------------------------------------------------------------------- #
# 模型
# --------------------------------------------------------------------------- #


class UserFace(Base):
    """人脸记录表（承载当前在场状态）

    单租户：``name`` 唯一

    本表只保存**当前状态**，历史流水见 :class:`SessionRecord`：

    * ``is_online``     是否处于在场会话中
    * ``checkin_time``  本次会话的签到时刻（签退后置NULL）
    * ``online_time``   历史累计在场分钟数
    * ``is_away`` / ``away_since`` / ``away_seconds``
      暂离语义已移除，三列仅为兼容既有库表而保留，业务代码不再读写

    时区约定：所有 ``DateTime`` 列一律保存**部署机器的本地naive时间**
    （``datetime.now()``）；部署方需保证宿主机时区即业务时区，
    不做跨时区转换
    """

    __tablename__ = "user_faces"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    features = Column(LargeBinary, nullable=False)
    screenshot = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    online_time = Column(Integer, default=0)
    is_online = Column(Boolean, default=False)
    is_away = Column(Boolean, default=False)
    # 下面三列是暂离时代的遗留列，保留只为兼容既有库表
    checkin_time = Column(DateTime, nullable=True)
    away_since = Column(DateTime, nullable=True)
    away_seconds = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("name", name="uq_user_face_name"),
    )


class SessionRecord(Base):
    """进出场流水表"""

    __tablename__ = "session_records"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, index=True)
    checkin_at = Column(DateTime, nullable=False)
    checkout_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, default=0)
    duration_minutes = Column(Integer, default=0)
    source = Column(String(16), default="manual")
    created_at = Column(DateTime, default=datetime.now)


# --------------------------------------------------------------------------- #
# 引擎与会话
# --------------------------------------------------------------------------- #

_is_sqlite = settings.DATABASE_URL.startswith("sqlite")

engine: Engine = create_engine(
    settings.DATABASE_URL,
    # SQLite在FastAPI的线程池里跨线程使用，必须关闭同线程检查
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=True,
    future=True,
)

if _is_sqlite:

    # SQLite连接建立时设置外键、WAL与锁等待策略
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        """开启外键约束与WAL，显著改善并发读写表现"""
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            # 排序/去重这类操作会建临时文件，默认落在系统TEMP。进程长期运行时
            # TEMP指向的目录可能被清理，此时SQLite会抛出「unable to open
            # database file」，看起来像数据库坏了，实际是临时文件建不出来。
            # 内存临时存储把这条路彻底去掉，数据量（当前百级记录）完全放得下。
            cursor.execute("PRAGMA temp_store=MEMORY")
        finally:
            cursor.close()


SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
)


# --------------------------------------------------------------------------- #
# 单租户迁移
# --------------------------------------------------------------------------- #

#: 迁移后旧表的保留名（含 ``user_id``），确认无误后可手工DROP
LEGACY_FACE_TABLE = "user_faces_legacy_pre_single_tenant"

_FACE_COLUMNS = (
    "name",
    "features",
    "screenshot",
    "created_at",
    "online_time",
    "is_online",
    "is_away",
    "checkin_time",
)


# 一次性迁移：旧版user_faces的user_id列并入单场地表，保留原姓名数据
def migrate_to_single_tenant() -> None:
    """把旧版 ``user_faces``（含 ``user_id`` 列）迁移为单租户表结构

    步骤（单事务内完成）：读出全部行 → 以 ``CREATE TABLE ... AS SELECT`` 存一份
    **纯数据**备份 → 删旧表 → 用当前metadata建新表 → 逐行写回

    为什么不用 ``ALTER TABLE ... RENAME`` 保存旧表：SQLite的索引名是**全局**的，
    ``ix_user_faces_id`` 与唯一约束自动索引 ``sqlite_autoindex_user_faces_1``
    会跟着旧表一起保留，导致新建同名索引时直接 ``index ... already exists``
    改用 ``CREATE TABLE AS SELECT`` 只快照数据、不复制索引，从根上避开冲突

    为什么逐行搬迁而不是一条 ``INSERT ... SELECT``：
    旧库里可能存在「不同user_id下同名的人脸」，而新的唯一约束是 ``name``
    逐行搬迁时遇到重名会自动加 `` (2)`` / `` (3)`` 后缀并打警告，
    保证数据不丢、迁移不失败

    该函数是**幂等**的：没有 ``user_id`` 列时直接返回
    """
    if not _is_sqlite:
        # 非SQLite部署需要各自的DDL工具（当前项目只支持SQLite单文件库）
        return

    with engine.begin() as conn:
        info = conn.exec_driver_sql('PRAGMA table_info("user_faces")').fetchall()
        columns = {row[1] for row in info}
        if not columns or "user_id" not in columns:
            return


        selected = ", ".join(_FACE_COLUMNS)
        legacy_rows = conn.exec_driver_sql(
            f'SELECT {selected} FROM "user_faces" ORDER BY id'
        ).fetchall()

        # 备份：只留数据，不带索引 / 约束 / 外键
        conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{LEGACY_FACE_TABLE}"')
        conn.exec_driver_sql(
            f'CREATE TABLE "{LEGACY_FACE_TABLE}" AS SELECT * FROM "user_faces"'
        )

        # 旧表上的显式索引不会被自动清理，必须手动删（自动索引随表消失）
        conn.exec_driver_sql('DROP INDEX IF EXISTS "ix_user_faces_id"')
        conn.exec_driver_sql('DROP TABLE "user_faces"')

        # 用当前metadata重建（含unique(name) 与ix_user_faces_id）
        Base.metadata.tables["user_faces"].create(bind=conn)

        used: set[str] = set()
        renamed = 0
        placeholders = ", ".join("?" for _ in _FACE_COLUMNS)
        for row in legacy_rows:
            name = str(row[0])
            original = name
            suffix = 2
            while name in used:
                name = f"{original} ({suffix})"
                suffix += 1
            used.add(name)
            if name != original:
                renamed += 1
            values = (name, *row[1:])
            conn.exec_driver_sql(
                f'INSERT INTO "user_faces" ({selected}) VALUES ({placeholders})',
                values,
            )


# 轻量列迁移：旧库缺列时ALTER TABLE补齐（create_all不会给已存在的表加列）
def migrate_add_columns() -> None:
    """为已存在的 ``user_faces`` 表补齐新增列（幂等）

    历史迁移：补齐 ``away_since`` / ``away_seconds`` 两列（暂离时代的遗留列）
    """
    if not _is_sqlite:
        return
    additions = {
        "away_since": "ALTER TABLE user_faces ADD COLUMN away_since DATETIME",
        "away_seconds": "ALTER TABLE user_faces ADD COLUMN away_seconds INTEGER DEFAULT 0",
    }
    with engine.begin() as conn:
        info = conn.exec_driver_sql('PRAGMA table_info("user_faces")').fetchall()
        columns = {row[1] for row in info}
        if not columns:
            return
        for name, ddl in additions.items():
            if name not in columns:
                conn.exec_driver_sql(ddl)


# 建表入口：create_all补齐缺失表，再执行单场地结构迁移与列补齐
def init_database() -> None:
    """创建缺失的表，并把旧版表结构迁移到当前形态"""
    settings.ensure_runtime_dirs()
    Base.metadata.create_all(bind=engine)
    migrate_to_single_tenant()
    migrate_add_columns()


# FastAPI依赖：每个请求新建会话，请求结束关闭
def get_db() -> Iterator[Session]:
    """FastAPI依赖：每个请求一个会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 脚本/后台任务用上下文管理器：异常回滚、正常提交、最终关闭
@contextmanager
def session_scope() -> Iterator[Session]:
    """脚本/后台任务用的会话上下文，自动提交与回滚"""
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
    "LEGACY_FACE_TABLE",
    "UserFace",
    "engine",
    "get_db",
    "init_database",
    "migrate_add_columns",
    "migrate_to_single_tenant",
    "session_scope",
    "SessionLocal",
]
