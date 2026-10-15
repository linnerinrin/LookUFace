"""pytest 公共夹具。

设计：
* **数据库隔离**：在导入 ``app`` 之前把 ``DATABASE_URL`` 指向临时目录，
  避免污染 ``storage/face_lens.db``。
* **不加载视觉模型**：绝大多数用例只打数据库与业务逻辑，不需要 OpenCV/MediaPipe。
  需要视觉的用例用 ``pytest.importorskip`` 并在模型缺失时跳过。
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
# 注意：不能包含 secret / changeme 等弱值标记，否则会被 config 的校验器拒绝
os.environ.setdefault(
    "SECRET_KEY",
    "pytest-only-key-" + "abcdefghijklmnopqrstuvwxyz0123456789" * 2,
)
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP_ROOT / 'test.db').as_posix()}"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["SMTP_ENABLED"] = "False"
os.environ["SMTP_USER"] = ""
os.environ["SMTP_PASSWORD"] = ""
os.environ["LOG_DIR"] = str(_TMP_ROOT / "logs")
os.environ["STORAGE_DIR"] = str(_TMP_ROOT / "storage")

from app.config import settings  # noqa: E402
from app.db.models import Base, SessionLocal, User, UserFace, engine  # noqa: E402
from app.services.security import get_password_hash  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _prepare_project_paths() -> Iterator[None]:
    """确保运行时目录指向临时目录。"""
    settings.ensure_runtime_dirs()
    yield


@pytest.fixture(autouse=True)
def clean_database() -> Iterator[None]:
    """每个用例前重建表结构，保证互不干扰。

    同时清空会话服务的进程内运行态（注册表 / 去抖窗口 / 锁）。
    这些是模块级单例，若不清理会跨用例泄漏，典型症状是「上一个用例的
    去抖窗口把下一个用例的首次签到错误抑制掉」。
    """
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
def user(db_session) -> User:
    """一个已注册用户。"""
    record = User(
        email="tester@example.com",
        password_hash=get_password_hash("secret123"),
        nickname="tester",
    )
    db_session.add(record)
    db_session.commit()
    db_session.refresh(record)
    return record


@pytest.fixture
def face(user, db_session) -> UserFace:
    """一条绑定在 ``user`` 上的人脸记录（特征为占位字节）。"""
    from app.vision.face_identity import serialize_features

    import numpy as np

    matrix = np.eye(4, dtype=np.float64)
    record = UserFace(
        user_id=user.id,
        name="张三",
        features=serialize_features(matrix),
        screenshot=None,
    )
    db_session.add(record)
    db_session.commit()
    db_session.refresh(record)
    return record


@pytest.fixture
def client(user, db_session):
    """已登录的 TestClient（携带有效 JWT）。"""
    from fastapi.testclient import TestClient

    from app.db.models import get_db
    from app.main import app
    from app.services.security import create_access_token

    app.dependency_overrides[get_db] = lambda: db_session
    token = create_access_token({"user_id": user.id, "email": user.email})
    with TestClient(app) as test_client:
        test_client.headers.update({"Authorization": f"Bearer {token}"})
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
    """启动一个真实的 uvicorn 服务器，返回 base_url。

    用于验证依赖真实网络栈的行为：全局异常处理器的客户端可见响应、
    静态资源可达性、以及无头浏览器加载（浏览器需要真实 HTTP 服务）。
    ``TestClient`` 会直接抛出服务端异常，看不到真实响应体。
    """
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
