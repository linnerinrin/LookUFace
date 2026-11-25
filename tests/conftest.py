"""pytest 公共夹具。

设计：
* **数据库隔离**：在导入 ``app`` 之前把 ``DATABASE_URL`` 指向临时目录，
  避免污染 ``storage/face_lens.db``。
* **不加载视觉模型**：绝大多数用例只打数据库与业务逻辑，不需要 OpenCV/MediaPipe。
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# --------------------------------------------------------------------------- #
# 在导入 app 之前设置环境变量
# --------------------------------------------------------------------------- #

_TMP_ROOT = Path(tempfile.mkdtemp(prefix="lookuface-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP_ROOT / 'test.db').as_posix()}"
os.environ["STORAGE_DIR"] = str(_TMP_ROOT / "storage")

from app.config import settings  # noqa: E402
from app.db.models import Base, SessionLocal, UserFace, engine  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _prepare_project_paths() -> Iterator[None]:
    """确保运行时目录指向临时目录。"""
    settings.ensure_runtime_dirs()
    yield


@pytest.fixture(autouse=True)
def clean_database() -> Iterator[None]:
    """每个用例前重建表结构，保证互不干扰。"""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    from app.services.analysis import face_analysis_service
    from app.services.session import session_service

    session_service.reset()
    face_analysis_service.clear_cache()
    yield
    session_service.reset()
    face_analysis_service.clear_cache()


@pytest.fixture
def db_session():
    """一个独立的数据库会话。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def face(db_session) -> UserFace:
    """一条测试人脸记录（特征为占位字节）。"""
    from app.vision.face_identity import serialize_features
    import numpy as np

    matrix = np.eye(4, dtype=np.float64)
    record = UserFace(
        name="张三",
        features=serialize_features(matrix),
        screenshot=None,
    )
    db_session.add(record)
    db_session.commit()
    db_session.refresh(record)
    return record


@pytest.fixture
def client(db_session):
    """TestClient。"""
    from fastapi.testclient import TestClient
    from app.db.models import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def anon_client():
    """匿名 TestClient。"""
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def live_http_server() -> Iterator[str]:
    """启动一个真实的 uvicorn 服务器，返回 base_url。"""
    import socket
    import threading
    import time
    import uvicorn
    from app.main import app

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])

    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_config=None, access_log=False
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 60
    while not server.started and time.time() < deadline:
        time.sleep(0.2)
    if not server.started:
        pytest.skip("uvicorn 未能在 60 秒内启动")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=15)
