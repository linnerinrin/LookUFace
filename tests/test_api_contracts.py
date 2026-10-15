"""API 契约测试。

覆盖旧版暴露出来的契约问题：
* 旧版 ``/face/list_with_screenshots`` / ``/check/checkin`` / ``/auth/change-password``
  三种命名风格混用 —— 现在统一到 ``/faces`` / ``/sessions`` / ``/auth``；
* 旧版 ``/check/pay`` 需要前端传 ``online_time`` 且前端从未调用 —— 现在
  ``/billing/quote`` 只接受人名，分钟数由服务端算；
* 旧版缺 token 时返回 403 与 401 混用 —— 现在统一 401。
"""

from __future__ import annotations

import pytest


# --------------------------------------------------------------------------- #
# 系统
# --------------------------------------------------------------------------- #


def test_health_reports_model_status(anon_client) -> None:
    response = anon_client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {"status", "version", "database", "models", "models_loaded"}
    assert body["database"] is True
    # 模型文件在仓库中已存在，应全部就绪
    assert isinstance(body["models"], dict)


def test_version_does_not_leak_secrets(anon_client) -> None:
    response = anon_client.get("/api/v1/version")
    assert response.status_code == 200
    body = response.json()
    assert "SECRET_KEY" not in body
    assert "smtp_password" not in {k.lower() for k in body}


def test_request_id_header_present(anon_client) -> None:
    response = anon_client.get("/api/v1/version")
    assert response.headers.get("X-Request-ID")
    assert "X-Content-Type-Options" in response.headers


def test_api_errors_are_json_not_html(anon_client) -> None:
    """旧版把 StaticFiles 挂在 "/"，拼错的 API 路径会返回 HTML。"""
    response = anon_client.get("/api/v1/definitely-not-a-route")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert "code" in response.json()


def test_docs_still_available(anon_client) -> None:
    """静态资源改挂 /static 后，/docs 不应被抢占。"""
    assert anon_client.get("/docs").status_code == 200
    assert anon_client.get("/openapi.json").status_code == 200


def test_root_redirects_to_app(anon_client) -> None:
    response = anon_client.get("/", follow_redirects=False)
    assert response.status_code in (307, 302)
    assert response.headers["location"].endswith("/static/idx.html")


# --------------------------------------------------------------------------- #
# 认证
# --------------------------------------------------------------------------- #


def test_missing_token_returns_401(anon_client) -> None:
    """旧版 HTTPBearer(auto_error=True) 在缺 header 时抛的是 403。"""
    response = anon_client.get("/api/v1/auth/profile")
    assert response.status_code == 401
    assert response.json()["code"] in {"unauthorized", "http_403"}


def test_invalid_token_returns_401(anon_client) -> None:
    response = anon_client.get(
        "/api/v1/auth/profile", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


def test_profile_with_valid_token(client) -> None:
    response = client.get("/api/v1/auth/profile")
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "tester@example.com"
    assert "password_hash" not in body


def test_login_success_and_failure(anon_client, user) -> None:
    ok = anon_client.post(
        "/api/v1/auth/login",
        json={"email": "tester@example.com", "password": "secret123"},
    )
    assert ok.status_code == 200
    assert ok.json()["token"]

    bad = anon_client.post(
        "/api/v1/auth/login",
        json={"email": "tester@example.com", "password": "wrong-password"},
    )
    assert bad.status_code == 401
    # 不区分「邮箱不存在」与「密码错误」，避免账号枚举
    assert bad.json()["detail"] == "邮箱或密码错误"


def test_login_unknown_email_same_message(anon_client) -> None:
    response = anon_client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "whatever"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "邮箱或密码错误"


def test_register_without_valid_code_rejected(anon_client) -> None:
    response = anon_client.post(
        "/api/v1/auth/register",
        json={
            "email": "new@example.com",
            "password": "secret123",
            "code": "111111",
            "nickname": "newbie",
        },
    )
    assert response.status_code == 400
    assert "验证码" in response.json()["detail"]


def test_validation_error_shape(anon_client) -> None:
    """请求体校验失败应返回统一的 {detail, code, details}。"""
    response = anon_client.post("/api/v1/auth/login", json={"email": "not-an-email"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert isinstance(body["details"], dict)


def test_change_password_flow(client, db_session, user) -> None:
    wrong = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "nope", "new_password": "brandnew1"},
    )
    assert wrong.status_code == 400

    ok = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "secret123", "new_password": "brandnew1"},
    )
    assert ok.status_code == 200

    # 同密码应被拒
    same = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "brandnew1", "new_password": "brandnew1"},
    )
    assert same.status_code == 400


# --------------------------------------------------------------------------- #
# 人脸库
# --------------------------------------------------------------------------- #


def test_faces_list_requires_auth(anon_client) -> None:
    assert anon_client.get("/api/v1/faces").status_code == 401


def test_faces_list_returns_items(client, face) -> None:
    response = client.get("/api/v1/faces")
    assert response.status_code == 200
    faces = response.json()["faces"]
    assert len(faces) == 1
    item = faces[0]
    assert item["name"] == "张三"
    assert item["is_online"] is False
    # 当前算法版本写入的特征应被标记为可用
    assert item["features_current"] is True


def test_legacy_pickle_feature_flagged(client, db_session, user) -> None:
    """旧 pickle 特征必须被标记为需要重新注册（不反序列化）。"""
    import pickle

    import numpy as np

    from app.db.models import UserFace

    record = UserFace(
        user_id=user.id,
        name="旧数据",
        features=pickle.dumps(np.eye(5)),
    )
    db_session.add(record)
    db_session.commit()

    response = client.get("/api/v1/faces")
    assert response.status_code == 200
    by_name = {item["name"]: item for item in response.json()["faces"]}
    assert by_name["旧数据"]["features_current"] is False


def test_delete_face(client, face) -> None:
    response = client.delete(f"/api/v1/faces/{face.name}")
    assert response.status_code == 200
    assert response.json()["success"] is True

    assert client.get("/api/v1/faces").json()["faces"] == []


def test_delete_unknown_face_404(client) -> None:
    assert client.delete("/api/v1/faces/查无此人").status_code == 404


def test_rename_face(client, face) -> None:
    response = client.put(
        f"/api/v1/faces/{face.name}",
        json={"old_name": face.name, "new_name": "李四"},
    )
    assert response.status_code == 200
    names = [item["name"] for item in client.get("/api/v1/faces").json()["faces"]]
    assert names == ["李四"]


def test_rename_into_existing_name_rejected(client, db_session, user, face) -> None:
    from app.db.models import UserFace
    from app.vision.face_identity import serialize_features

    import numpy as np

    db_session.add(
        UserFace(
            user_id=user.id,
            name="李四",
            features=serialize_features(np.eye(4)),
        )
    )
    db_session.commit()

    response = client.put(
        f"/api/v1/faces/{face.name}",
        json={"old_name": face.name, "new_name": "李四"},
    )
    assert response.status_code == 400


def test_register_rejects_non_image(client) -> None:
    response = client.post(
        "/api/v1/faces/register",
        files={"file": ("not-an-image.txt", b"hello world", "text/plain")},
        data={"name": "测试"},
    )
    assert response.status_code == 400
    assert "解码" in response.json()["detail"]


def test_register_rejects_oversized_payload(client) -> None:
    """BodySizeLimitMiddleware 应在读取请求体之前拒绝。"""
    big = b"x" * (11 * 1024 * 1024)
    response = client.post(
        "/api/v1/faces/register",
        files={"file": ("big.jpg", big, "image/jpeg")},
        data={"name": "测试"},
    )
    assert response.status_code == 413


# --------------------------------------------------------------------------- #
# 会话与计费
# --------------------------------------------------------------------------- #


def test_sessions_contract_has_no_client_supplied_duration(client, face) -> None:
    """旧版 checkin 需要 request_times、checkout 需要 away_time，均已移除。"""
    response = client.post("/api/v1/sessions/checkin", json={"name": face.name})
    assert response.status_code == 200
    assert response.json()["is_online"] is True

    # 多余字段会被忽略而不是报错（向前兼容）
    extra = client.post(
        "/api/v1/sessions/checkin",
        json={"name": face.name, "request_times": 999},
    )
    assert extra.status_code == 200


def test_checkout_returns_server_computed_amount(client, face) -> None:
    client.post("/api/v1/sessions/checkin", json={"name": face.name})
    response = client.post(
        "/api/v1/sessions/checkout", json={"name": face.name, "away": False}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    # 金额由服务端计算，刚签到即签退应为 0
    assert body["amount"] == 0.0
    assert body["session_minutes"] >= 0
    assert body["total_minutes"] >= 0


def test_status_endpoint_returns_preview(client, face) -> None:
    client.post("/api/v1/sessions/checkin", json={"name": face.name})
    response = client.post("/api/v1/sessions/status", json={"name": face.name})
    assert response.status_code == 200
    body = response.json()
    assert body["is_online"] is True
    assert body["preview_minutes"] >= 0
    assert body["away_timeout_minutes"] > 0


def test_billing_quote_ignores_client_duration(client, face) -> None:
    """旧版 /check/pay 由前端传 online_time，可直接免单。"""
    client.post("/api/v1/sessions/checkin", json={"name": face.name})
    response = client.post(
        "/api/v1/billing/quote",
        json={"name": face.name, "online_time": 0, "minutes": 0},
    )
    assert response.status_code == 200
    body = response.json()
    # 服务端按真实会话计算，客户端传 0 不生效
    assert body["minutes"] >= 0
    assert body["rate_per_minute"] > 0
    assert body["qrcode_url"].startswith("/api/v1/billing/qrcode/")


def test_billing_qrcode_path_traversal_blocked(client) -> None:
    """旧版二维码文件名直接拼接用户输入，可穿越目录。"""
    response = client.get("/api/v1/billing/qrcode/..%2F..%2F..%2Fevil.png")
    assert response.status_code == 404


def test_billing_quote_unknown_face_404(client) -> None:
    response = client.post("/api/v1/billing/quote", json={"name": "查无此人"})
    assert response.status_code == 404


def test_settle_expired_endpoint_returns_count(client) -> None:
    response = client.post("/api/v1/sessions/settle-expired")
    assert response.status_code == 200
    assert response.json()["settled"] == 0


# --------------------------------------------------------------------------- #
# 日志
# --------------------------------------------------------------------------- #


def test_frontend_log_ingest(anon_client) -> None:
    response = anon_client.post(
        "/api/v1/logs/frontend",
        json={"level": "error", "message": "前端测试消息", "page": "test.html"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_recent_logs_shape(anon_client) -> None:
    response = anon_client.post(
        "/api/v1/logs/recent", json={"lines": 10, "level": "all", "date": None}
    )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["logs"], list)
    assert "total" in body


def test_recent_logs_rejects_bad_date(anon_client) -> None:
    response = anon_client.post(
        "/api/v1/logs/recent", json={"lines": 10, "level": "all", "date": "../../etc/passwd"}
    )
    assert response.status_code == 422


def test_frontend_log_rejects_unknown_level(anon_client) -> None:
    response = anon_client.post(
        "/api/v1/logs/frontend",
        json={"level": "critical", "message": "x", "page": "t"},
    )
    assert response.status_code == 422
