"""数据访问层：所有UserFace的读写集中在此，人脸记录以 ``name`` 唯一"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import SessionRecord, UserFace


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
    return face


#: 哨兵值：区分「调用方未提供该字段」与「显式传入None（要清空）」
UNSET: Any = object()


# 写入在场标记、签到时间等状态字段：CAS防并发覆盖，不commit（事务由业务层收口）
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
        return False
    return True


# --------------------------------------------------------------------------- #
# SessionRecord（进出场流水）
# --------------------------------------------------------------------------- #


# 签到写一条未闭合流水；事务由调用方收口
def create_session_record(
    db: Session,
    *,
    name: str,
    checkin_at: datetime,
    source: str = "manual",
) -> SessionRecord:
    """写入一条「已进场、未出场」的流水"""
    record = SessionRecord(
        name=name,
        checkin_at=checkin_at,
        source=source,
    )
    db.add(record)
    db.flush()
    return record


# 取某人最近一条未闭合流水（正常业务下每次在场所对应唯一一条）
def open_session_record(db: Session, *, name: str) -> SessionRecord | None:
    return db.execute(
        select(SessionRecord)
        .where(SessionRecord.name == name)
        .where(SessionRecord.checkout_at.is_(None))
        .order_by(SessionRecord.checkin_at.desc())
        .limit(1)
    ).scalar_one_or_none()


# 闭合流水：只在未闭合时写入，返回是否实际更新（CAS语义，避免覆盖历史记录）
def close_session_record(
    db: Session,
    *,
    record_id: int,
    checkout_at: datetime,
    duration_seconds: int,
    duration_minutes: int,
) -> bool:
    """补齐出场时刻与时长；已闭合的记录不会被二次改写"""
    result = db.execute(
        update(SessionRecord)
        .where(SessionRecord.id == record_id)
        .where(SessionRecord.checkout_at.is_(None))
        .values(
            checkout_at=checkout_at,
            duration_seconds=int(duration_seconds),
            duration_minutes=int(duration_minutes),
        )
    )
    return result.rowcount > 0


# 按「日」或「人」筛选流水，按签到时间倒序；不提交
def list_session_records(
    db: Session,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    name: str | None = None,
    limit: int = 500,
) -> Sequence[SessionRecord]:
    stmt = select(SessionRecord)
    if start is not None:
        stmt = stmt.where(SessionRecord.checkin_at >= start)
    if end is not None:
        stmt = stmt.where(SessionRecord.checkin_at < end)
    if name is not None:
        stmt = stmt.where(SessionRecord.name == name)
    stmt = stmt.order_by(SessionRecord.checkin_at.desc()).limit(max(1, int(limit)))
    return db.execute(stmt).scalars().all()


# 返回全部姓名，用于清理已删除记录对应的失效运行态
def all_face_names(db: Session) -> set[str]:
    """返回所有人脸名，用于清理失效运行态"""
    rows = db.execute(select(UserFace.name)).all()
    return {str(n) for (n,) in rows}


# 返回所有在场记录
def online_faces(db: Session) -> Sequence[UserFace]:
    return db.execute(select(UserFace).where(UserFace.is_online.is_(True))).scalars().all()


__all__ = [
    "UNSET",
    "all_face_names",
    "close_session_record",
    "create_session_record",
    "delete_face",
    "get_face",
    "get_face_by_id",
    "list_faces",
    "list_session_records",
    "online_faces",
    "open_session_record",
    "rename_face",
    "set_face_state",
    "upsert_face",
]
