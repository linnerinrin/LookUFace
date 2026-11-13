# """模块：人脸数据仓储"""
# """架构作用：集中查询、修改人脸记录和会话状态列"""
"""数据访问层：所有UserFace的读写集中在此，人脸记录以 ``name`` 唯一"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import UserFace
from app.logger import logger


# --------------------------------------------------------------------------- #
# UserFace
# --------------------------------------------------------------------------- #


# 按注册时间倒序返回全部人脸记录（单场地，不分页）
def list_faces(db: Session) -> Sequence[UserFace]:
    return (
        db.execute(select(UserFace).order_by(UserFace.created_at.asc()))
        .scalars()
        .all()
    )


# 按姓名取单条记录，不存在返回None（运行时状态以姓名为键）
def get_face(db: Session, name: str) -> UserFace | None:
    return db.execute(
        select(UserFace).where(UserFace.name == name)
    ).scalar_one_or_none()


# 按主键取记录，供内部更新与删除复用
def get_face_by_id(db: Session, face_id: int) -> UserFace | None:
    return db.get(UserFace, face_id)


# 注册/覆盖写入：同名则更新特征与人口属性，否则插入新记录（不提交，事务由业务层收口）
def upsert_face(
    db: Session,
    *,
    name: str,
    features: bytes,
    screenshot: str | None,
) -> UserFace:
    """按 ``name`` 唯一约束新增或更新

    只flush不commit：并发注册同名时借助SAVEPOINT兜底改为更新，
    不破坏业务层外层事务，由业务层统一commit / rollback
    """
    face = get_face(db, name)
    if face is None:
        face = UserFace(name=name, features=features, screenshot=screenshot)
        db.add(face)
        try:
            with db.begin_nested():  # SAVEPOINT，失败不拖垮外层事务
                db.flush()
        except IntegrityError:
            # 并发注册同名的兜底：改为更新已存在的记录
            face = get_face(db, name)
            if face is None:
                raise
            face.features = features
            if screenshot:
                face.screenshot = screenshot
            db.flush()
    else:
        face.features = features
        if screenshot:
            face.screenshot = screenshot
        db.flush()
    db.refresh(face)
    return face


# 改名：校验目标名未被占用后修改展示名，可选同时修正注册时间（不提交）
def rename_face(
    db: Session,
    *,
    old_name: str,
    new_name: str,
    register_time: datetime | None = None,
) -> bool:
    """改名 / 改注册时间。返回是否成功（含冲突判定）。只flush不commit"""
    face = get_face(db, old_name)
    if face is None:
        return False

    if new_name and new_name != old_name:
        if get_face(db, new_name) is not None:
            return False
        face.name = new_name

    if register_time is not None:
        face.created_at = register_time

    try:
        db.flush()
    except IntegrityError:
        return False
    return True


# 删除记录并返回被删对象，调用方据此清理截图文件（不提交）
def delete_face(db: Session, *, name: str) -> UserFace | None:
    """删除人脸记录，返回被删除的对象（用于清理截图文件）。只flush不commit"""
    face = get_face(db, name)
    if face is None:
        return None
    screenshot = face.screenshot
    db.delete(face)
    db.flush()
    logger.info("已删除人脸记录 name=%s screenshot=%s", name, screenshot)
    return face


#: 哨兵值：区分「调用方未提供该字段」与「显式传入None（要清空）」
UNSET: Any = object()


# 写入在线/暂离/签到时间等状态字段：CAS防并发覆盖，不commit（事务由业务层收口）
def set_face_state(
    db: Session,
    *,
    face_id: int,
    is_online: bool | None = None,
    is_away: bool | None = None,
    checkin_at: datetime | None | Any = UNSET,
    away_since: datetime | None | Any = UNSET,
    away_seconds: int | None = None,
    accumulated_minutes: int | None = None,
    expected_online: bool | None = None,
    expected_away: bool | None = None,
) -> bool:
    """更新会话状态列，返回是否实际写入（CAS失败或无可写字段时为False）

    * 只有显式传入的字段会被写入；``checkin_at=None`` / ``away_since=None``
      是**有效值**（清空对应列），不传则保持原值
    * ``expected_online`` / ``expected_away`` 是乐观并发控制（CAS）条件：
      传入后UPDATE附带旧状态条件，影响行数为0说明记录已被并发修改，
      本函数返回False，调用方应放弃本次变更并重读
    * 不执行commit，事务边界由业务层统一管理
    """
    values: dict[str, object] = {}
    if is_online is not None:
        values["is_online"] = is_online
    if is_away is not None:
        values["is_away"] = is_away
    if checkin_at is not UNSET:
        values["checkin_time"] = checkin_at
    if away_since is not UNSET:
        values["away_since"] = away_since
    if away_seconds is not None:
        values["away_seconds"] = int(away_seconds)
    if accumulated_minutes is not None:
        values["online_time"] = int(accumulated_minutes)
    if not values:
        return False

    stmt = update(UserFace).where(UserFace.id == face_id)
    if expected_online is not None:
        stmt = stmt.where(UserFace.is_online.is_(expected_online))
    if expected_away is not None:
        stmt = stmt.where(UserFace.is_away.is_(expected_away))
    result = db.execute(stmt.values(**values))
    if result.rowcount == 0:
        # CAS条件未命中：记录已被并发修改，拒绝覆盖
        logger.warning(
            "set_face_state CAS 未命中 face_id=%s expected_online=%s expected_away=%s",
            face_id, expected_online, expected_away,
        )
        return False
    return True


# 返回全部姓名，用于清理已删除记录对应的失效运行态
def all_face_names(db: Session) -> set[str]:
    """返回所有人脸名，用于清理失效运行态"""
    rows = db.execute(select(UserFace.name)).all()
    return {str(n) for (n,) in rows}


# 返回所有在线记录，供超时暂离的兜底扫描
def online_faces(db: Session) -> Sequence[UserFace]:
    return db.execute(select(UserFace).where(UserFace.is_online.is_(True))).scalars().all()


__all__ = [
    "UNSET",
    "all_face_names",
    "delete_face",
    "get_face",
    "get_face_by_id",
    "list_faces",
    "online_faces",
    "rename_face",
    "set_face_state",
    "upsert_face",
]
