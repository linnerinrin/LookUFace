"""数据访问层：所有 UserFace / User / VerificationCode 的读写集中在此。

集中化的好处：
* 路由与服务层不再各写一份 ``db.query(...).filter_by(...)``，避免条件漂移；
* 唯一约束冲突、过期验证码清理等边界只在一处处理；
* 便于单测（可针对仓储写测试，无需起 HTTP 层）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import User, UserFace, VerificationCode
from app.logger import logger


# --------------------------------------------------------------------------- #
# User
# --------------------------------------------------------------------------- #


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.execute(select(User).where(User.email == email)).scalar_one_or_none()


def email_exists(db: Session, email: str) -> bool:
    return get_user_by_email(db, email) is not None


def create_user(db: Session, *, email: str, password_hash: str, nickname: str) -> User:
    user = User(email=email, password_hash=password_hash, nickname=nickname)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# --------------------------------------------------------------------------- #
# UserFace
# --------------------------------------------------------------------------- #


def list_faces(db: Session, user_id: int) -> Sequence[UserFace]:
    return (
        db.execute(
            select(UserFace).where(UserFace.user_id == user_id).order_by(UserFace.created_at.asc())
        )
        .scalars()
        .all()
    )


def get_face(db: Session, user_id: int, name: str) -> UserFace | None:
    return db.execute(
        select(UserFace).where(UserFace.user_id == user_id, UserFace.name == name)
    ).scalar_one_or_none()


def get_face_by_id(db: Session, face_id: int) -> UserFace | None:
    return db.get(UserFace, face_id)


def upsert_face(
    db: Session,
    *,
    user_id: int,
    name: str,
    features: bytes,
    screenshot: str | None,
) -> UserFace:
    """按 ``(user_id, name)`` 唯一约束新增或更新。"""
    face = get_face(db, user_id, name)
    if face is None:
        face = UserFace(user_id=user_id, name=name, features=features, screenshot=screenshot)
        db.add(face)
    else:
        face.features = features
        if screenshot:
            face.screenshot = screenshot
    try:
        db.commit()
    except IntegrityError:
        # 并发注册同名时的兜底：回滚后改为更新
        db.rollback()
        face = get_face(db, user_id, name)
        if face is None:
            raise
        face.features = features
        if screenshot:
            face.screenshot = screenshot
        db.commit()
    db.refresh(face)
    return face


def rename_face(
    db: Session,
    *,
    user_id: int,
    old_name: str,
    new_name: str,
    register_time: datetime | None = None,
) -> bool:
    """改名 / 改注册时间。返回是否成功（含冲突判定）。"""
    face = get_face(db, user_id, old_name)
    if face is None:
        return False

    if new_name and new_name != old_name:
        if get_face(db, user_id, new_name) is not None:
            return False
        face.name = new_name

    if register_time is not None:
        face.created_at = register_time

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    return True


def delete_face(db: Session, *, user_id: int, name: str) -> UserFace | None:
    """删除人脸记录，返回被删除的对象（用于清理截图文件）。"""
    face = get_face(db, user_id, name)
    if face is None:
        return None
    screenshot = face.screenshot
    db.delete(face)
    db.commit()
    logger.info("已删除人脸记录 name=%s screenshot=%s", name, screenshot)
    return face


#: 哨兵值：区分「调用方未提供该字段」与「显式传入 None（要清空）」。
#: 早期实现用 ``None`` 同时表达这两种含义，导致签退时 ``checkin_time``
#: 永远无法被置空（UPDATE 里被跳过），会话结束后仍留着旧的签到时间。
UNSET: Any = object()


def set_face_state(
    db: Session,
    *,
    face_id: int,
    is_online: bool | None = None,
    is_away: bool | None = None,
    checkin_at: datetime | None | Any = UNSET,
    accumulated_minutes: int | None = None,
) -> None:
    """更新会话状态列。只有显式传入的字段会被写入。

    ``checkin_at=None`` 是**有效值**，表示清空签到时间；不传则保持原值。
    """
    values: dict[str, object] = {}
    if is_online is not None:
        values["is_online"] = is_online
    if is_away is not None:
        values["is_away"] = is_away
    if checkin_at is not UNSET:
        values["checkin_time"] = checkin_at
    if accumulated_minutes is not None:
        values["online_time"] = int(accumulated_minutes)
    if not values:
        return
    db.execute(update(UserFace).where(UserFace.id == face_id).values(**values))
    db.commit()


def all_face_keys(db: Session) -> set[tuple[int, str]]:
    """返回所有 ``(user_id, name)``，用于清理失效运行态。"""
    rows = db.execute(select(UserFace.user_id, UserFace.name)).all()
    return {(int(u), str(n)) for u, n in rows}


def online_faces(db: Session) -> Sequence[UserFace]:
    return db.execute(select(UserFace).where(UserFace.is_online.is_(True))).scalars().all()


# --------------------------------------------------------------------------- #
# VerificationCode
# --------------------------------------------------------------------------- #


def invalidate_codes(db: Session, *, email: str, code_type: str) -> None:
    db.execute(
        delete(VerificationCode).where(
            VerificationCode.email == email,
            VerificationCode.type == code_type,
            VerificationCode.used.is_(False),
        )
    )
    db.commit()


def create_code(
    db: Session,
    *,
    email: str,
    code: str,
    code_type: str,
    expires_at: datetime,
) -> None:
    db.add(VerificationCode(email=email, code=code, type=code_type, expires_at=expires_at))
    db.commit()


def consume_code(db: Session, *, email: str, code: str, code_type: str, now: datetime) -> bool:
    """校验并一次性消费验证码。"""
    record = db.execute(
        select(VerificationCode).where(
            VerificationCode.email == email,
            VerificationCode.code == code,
            VerificationCode.type == code_type,
            VerificationCode.used.is_(False),
            VerificationCode.expires_at > now,
        )
    ).scalar_one_or_none()
    if record is None:
        return False
    db.delete(record)
    db.commit()
    return True


def purge_expired_codes(db: Session, *, now: datetime) -> int:
    result = db.execute(delete(VerificationCode).where(VerificationCode.expires_at < now))
    db.commit()
    return int(result.rowcount or 0)


__all__ = [
    "all_face_keys",
    "consume_code",
    "create_code",
    "create_user",
    "delete_face",
    "email_exists",
    "get_face",
    "get_face_by_id",
    "get_user_by_email",
    "get_user_by_id",
    "invalidate_codes",
    "list_faces",
    "online_faces",
    "purge_expired_codes",
    "rename_face",
    "set_face_state",
    "upsert_face",
]
