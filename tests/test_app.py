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


# --------------------------------------------------------------------------- #
# CORS / 跨源（对应用户遇到的 "Failed to fetch"）
# --------------------------------------------------------------------------- #


def test_cors_preflight_allows_whitelisted_origin_with_requested_with(anon_client) -> None:
    """白名单 Origin 的预检必须放行，且要允许 X-Requested-With。

    前端连通性探测（api.js 的 diagnoseConnectivity）会带这个头。
    如果它不在 allow_headers 里，探测自身的预检就会失败，
    进而把「CORS 正常」误判成「CORS 被拒」。
    """
    response = anon_client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://127.0.0.1:8000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-requested-with",
        },
    )
    assert response.status_code == 200
    allow_headers = response.headers.get("access-control-allow-headers", "").lower()
    assert "x-requested-with" in allow_headers
    assert response.headers.get("access-control-allow-origin") == "http://127.0.0.1:8000"


def test_cors_preflight_rejects_unlisted_origin(anon_client) -> None:
    """不在白名单的 Origin 必须被拒 —— 浏览器端表现为 "Failed to fetch"。

    这正是用户遇到的现象来源之一：用 192.168.x.x 或机器名访问时，
    Origin 不在 ALLOWED_ORIGINS 内，页面能加载但所有 API 调用都会失败。
    """
    response = anon_client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://192.168.2.102:8000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 400
    assert response.headers.get("access-control-allow-origin") is None


def _capture_app_logs():
    """把应用 logger 的记录收集到列表里。

    不能直接用 pytest 的 ``caplog``：应用 logger 设了 ``propagate = False``，
    记录不会冒泡到 root logger，因此 caplog 的 handler 收不到。
    """
    import logging
    from contextlib import contextmanager

    from app.logger import logger as app_logger

    @contextmanager
    def _ctx():
        messages: list[str] = []

        class _Handler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                try:
                    messages.append(record.getMessage())
                except Exception:  # pragma: no cover
                    pass

        handler = _Handler()
        handler.setLevel(logging.DEBUG)
        app_logger.addHandler(handler)
        previous_level = app_logger.level
        app_logger.setLevel(logging.DEBUG)
        try:
            yield messages
        finally:
            app_logger.setLevel(previous_level)
            app_logger.removeHandler(handler)

    return _ctx()


def test_rejected_origin_is_logged_with_actionable_message(anon_client) -> None:
    """跨源被拒时必须留下可定位的告警。

    用户只看浏览器只会看到 "Failed to fetch"，所以服务端必须把
    「Origin 不在白名单」这个结论直接写进日志。
    """
    with _capture_app_logs() as messages:
        anon_client.post(
            "/api/v1/auth/login",
            json={"email": "a@b.c", "password": "x"},
            headers={"Origin": "http://192.168.2.102:8000"},
        )

    joined = "\n".join(messages)
    assert "跨源请求被拒绝" in joined, f"缺少跨源告警，实际日志：\n{joined}"
    assert "192.168.2.102:8000" in joined
    assert "ALLOWED_ORIGINS" in joined


def test_same_origin_request_is_not_flagged_as_cross_origin(anon_client) -> None:
    """同源请求不应告警，即使该地址不在 ALLOWED_ORIGINS 里。

    浏览器访问 http://192.168.2.102:8000 时，页面对同源 API 的请求
    不会触发 CORS 检查，因此不应产生噪声告警。
    """
    with _capture_app_logs() as messages:
        anon_client.get(
            "/api/v1/health",
            headers={"Origin": "http://testserver", "Host": "testserver"},
        )

    joined = "\n".join(messages)
    assert "跨源请求被拒绝" not in joined, f"同源请求被误报：\n{joined}"
