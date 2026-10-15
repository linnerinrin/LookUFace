"""应用工厂与入口的测试。"""

from __future__ import annotations

import pytest


def test_create_app_mounts_static_under_prefix(anon_client) -> None:
    """静态资源必须在 /static 下，不能占用根路径。

    旧版 ``app.mount("/", StaticFiles(...))`` 会让所有未匹配路径落到静态目录，
    拼错的 API 路径返回 HTML 而不是 JSON 错误。
    """
    assert anon_client.get("/static/idx.html").status_code == 200
    # 任意未匹配路径必须是 JSON 404，而不是静态目录的 404 页面
    response = anon_client.get("/no/such/page")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_openapi_schema_is_valid(anon_client) -> None:
    """OpenAPI 文档必须能生成（schema 定义有误时会失败）。"""
    response = anon_client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"]
    paths = schema["paths"]
    # 关键接口都要出现在文档里
    for path in (
        "/api/v1/auth/login",
        "/api/v1/faces",
        "/api/v1/sessions/checkin",
        "/api/v1/billing/quote",
    ):
        assert path in paths, f"OpenAPI 中缺少 {path}"


def test_cors_headers_for_allowed_origin(anon_client) -> None:
    response = anon_client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://127.0.0.1:8000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code in (200, 204)
    assert response.headers.get("access-control-allow-origin") == "http://127.0.0.1:8000"


def test_internal_error_handler_registered(anon_client) -> None:
    """全局异常处理器必须已注册（旧版在 async 处理器里裸 raise，会污染异常栈）。"""
    from app.main import app
    from app.core.errors import AppError

    handlers = app.exception_handlers
    assert Exception in handlers, "必须有兜底的 Exception 处理器"
    assert AppError in handlers, "业务异常应有专属处理器"


def test_internal_error_is_not_leaked_via_real_server(live_http_server) -> None:
    """未捕获异常必须返回统一结构，且不泄露内部细节。

    注意：这里用**真实 uvicorn 服务器**而不是 TestClient ——
    ``TestClient`` 默认 ``raise_server_exceptions=True``，
    异常会直接被抛出，无法观察到客户端实际收到的响应体。
    """
    import json
    import urllib.error
    import urllib.request

    from app.main import app

    @app.get("/api/v1/_boom")
    async def boom() -> None:  # pragma: no cover - 仅用于测试
        raise RuntimeError("内部细节：数据库密码是 hunter2")

    try:
        try:
            urllib.request.urlopen(live_http_server + "/api/v1/_boom", timeout=20)
            raise AssertionError("应当返回 500")
        except urllib.error.HTTPError as exc:
            assert exc.code == 500
            raw = exc.read().decode("utf-8")
            body = json.loads(raw)
            assert body["code"] == "internal_error"
            assert body["detail"] == "服务器内部错误"
            assert body["request_id"], "错误响应必须带 request_id 便于排查"
            assert "hunter2" not in raw, "不得泄露内部细节"
            assert "Traceback" not in raw
    finally:
        app.router.routes = [
            route
            for route in app.router.routes
            if getattr(route, "path", None) != "/api/v1/_boom"
        ]


def test_body_size_limit_rejects_early(anon_client) -> None:
    """超限请求体应在读取前被拒绝（413）。"""
    huge = b"0" * (11 * 1024 * 1024)
    response = anon_client.post(
        "/api/v1/logs/frontend",
        content=huge,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"
