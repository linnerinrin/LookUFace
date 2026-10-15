"""人脸特征加载缓存的测试。

**为什么需要缓存**：特征矩阵是 ``468×468``，序列化后实测约 **1.44 MB/人**，
反序列化一次约 **28 ms**。而 ``analyze_image`` 是**每帧**调用的（约 7 fps），
若每次都读库 + 解析 JSON：

    10 个人 -> 每帧解析 ~14 MB，约 0.28 秒 CPU/帧
    按 7 fps 算 -> 每秒约 2 秒 CPU —— 单是解析就会把帧率拖垮

因此必须缓存已知特征，并在人脸发生变更时**立即失效**，
否则会出现「刚注册的人认不出来、过几秒又好了」这种难以复现的问题。
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.analysis import FaceAnalysisService
from app.vision.face_identity import serialize_features


def _add_face(db_session, user_id: int, name: str) -> None:
    from app.db.models import UserFace

    db_session.add(
        UserFace(user_id=user_id, name=name, features=serialize_features(np.eye(4)))
    )
    db_session.commit()


def _rename_in_db_bypassing_orm(db_session, face_id: int, new_name: str) -> None:
    """直接改库，绕过 ORM。

    注意：不能用 ``query().update()`` —— 它会**同步内存中的 ORM 对象**，
    导致测试里对比的 ``face.name`` 也一起变了，断言就失去意义。
    这里用原生 SQL，保证只有数据库被改到。
    """
    from sqlalchemy import text

    db_session.execute(
        text("UPDATE user_faces SET name = :name WHERE id = :id"),
        {"name": new_name, "id": face_id},
    )
    db_session.commit()
    db_session.expire_all()


def test_second_load_is_served_from_cache(db_session, user, face) -> None:
    """第二次载入必须命中缓存（不再读库）。"""
    service = FaceAnalysisService()
    original_name = str(face.name)

    first = service.load_known_features(user.id)
    assert [n for n, _ in first] == [original_name]

    # 绕过 ORM 直接改库 —— 缓存命中时应看不到这个改动
    _rename_in_db_bypassing_orm(db_session, face.id, "被改名了")

    cached = service.load_known_features(user.id)
    assert cached[0][0] == original_name, "同一 TTL 内应返回缓存内容，而不是重新读库"


def test_clear_cache_forces_reload(db_session, user, face) -> None:
    """``clear_cache()`` 之后必须重新读库（否则注册新人脸后认不出来）。"""
    service = FaceAnalysisService()
    service.load_known_features(user.id)

    _rename_in_db_bypassing_orm(db_session, face.id, "新名字")
    service.clear_cache()

    reloaded = service.load_known_features(user.id)
    assert reloaded[0][0] == "新名字", "clear_cache 后必须重新读库"


def test_newly_added_face_visible_after_clear(db_session, user, face) -> None:
    """新增人脸 + clear_cache 后，新特征必须可见。

    这对应真实流程：注册人脸的路由会调用 clear_cache()。
    """
    service = FaceAnalysisService()
    assert len(service.load_known_features(user.id)) == 1

    _add_face(db_session, user.id, "第二个人")
    service.clear_cache()

    assert len(service.load_known_features(user.id)) == 2


def test_cache_is_per_user(db_session, user, face) -> None:
    """不同用户的缓存必须互相独立。"""
    from app.db.repository import create_user
    from app.services.security import get_password_hash

    other = create_user(
        db_session, email="other@example.com", password_hash=get_password_hash("x" * 12),
        nickname="other",
    )
    _add_face(db_session, other.id, "另一个人的脸")

    service = FaceAnalysisService()
    mine = service.load_known_features(user.id)
    theirs = service.load_known_features(other.id)

    assert [n for n, _ in mine] == [face.name]
    assert [n for n, _ in theirs] == ["另一个人的脸"]


def test_legacy_pickle_records_are_skipped(db_session, user) -> None:
    """旧 pickle 记录必须被跳过（不反序列化），且不影响其它记录。"""
    import pickle

    from app.db.models import UserFace

    db_session.add(
        UserFace(user_id=user.id, name="旧数据", features=pickle.dumps(np.eye(5)))
    )
    _add_face(db_session, user.id, "新数据")
    db_session.commit()

    service = FaceAnalysisService()
    names = [name for name, _ in service.load_known_features(user.id)]

    assert "旧数据" not in names, "旧 pickle 记录不得被载入"
    assert "新数据" in names


def test_ttl_expiry_triggers_reload(db_session, user, face, monkeypatch) -> None:
    """TTL 到期后应重新读库（兜底：别的进程改了库也能生效）。"""
    service = FaceAnalysisService()
    monkeypatch.setattr(FaceAnalysisService, "KNOWN_FEATURES_TTL_SECONDS", 0.0)

    service.load_known_features(user.id)
    _rename_in_db_bypassing_orm(db_session, face.id, "TTL 后改名")

    reloaded = service.load_known_features(user.id)
    assert reloaded[0][0] == "TTL 后改名"


def test_cache_is_thread_safe(db_session, user, face) -> None:
    """并发调用不应抛错，也不应返回损坏的结果。"""
    import threading

    service = FaceAnalysisService()
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(50):
                result = service.load_known_features(user.id)
                assert len(result) >= 1
                service.clear_cache()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"并发访问出错：{errors}"


def test_clear_cache_also_clears_match_cache(db_session, user, face) -> None:
    """``clear_cache()`` 必须同时清掉识别结果缓存。

    否则会出现：改了人脸信息，但旧帧的识别结果仍在 3 秒 TTL 内被复用。
    """
    service = FaceAnalysisService()
    service._cache.put("probe-key", ("张三", 0.99))
    assert len(service._cache) == 1

    service.clear_cache()
    assert len(service._cache) == 0
