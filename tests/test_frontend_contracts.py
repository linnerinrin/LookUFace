"""前后端契约一致性测试。

**为什么需要它**：重构前项目里就存在真实的契约断裂 ——
后端定义了 ``/check/pay`` 需要 ``online_time``，而**前端从未调用过它**，
整个支付二维码功能是死的；前端还调用过 ``/face/list_with_screenshots``
这样后端并不同名的路径。这类问题只有把「前端实际调用的路径」与
「后端实际注册的路由」放在一起比对才能发现。

做法：静态扫描 app/static/*.js 里的 ``LF.api.<verb>('/path')`` 调用，
与 FastAPI 的路由表逐一匹配（支持 ``{param}`` 占位）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).resolve().parent.parent / "app" / "static"

# 匹配 LF.api.get('/faces')、LF.api.post("/sessions/checkin", ...) 等
# 只捕获字面量或字面量 + 拼接（'/faces/' + name）两种形式
CALL_PATTERN = re.compile(
    r"LF\.api\.(get|post|put|del|upload)\(\s*([a-zA-Z_$][\w$]*\.)?['\"]([^'\"]+)['\"]",
)
# 模板串形式：LF.api.get(`/faces/${name}`)
TEMPLATE_PATTERN = re.compile(
    r"LF\.api\.(get|post|put|del|upload)\(\s*`([^`]+)`",
)
RAW_PATH_PATTERN = re.compile(r"['\"](/api/v1/[A-Za-z0-9_\-/{}.]*)['\"]")

# 方法名到 HTTP 动词的映射（upload 走 POST / multipart）
METHOD_ALIASES = {"UPLOAD": "POST", "DEL": "DELETE"}

# JS 单行注释与块注释：剥掉后再做「是否仍含旧路径」检查，
# 否则说明性注释里的旧路径会产生误报
JS_COMMENT_PATTERN = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


def _strip_js_comments(text: str) -> str:
    return JS_COMMENT_PATTERN.sub("", text)


def _frontend_calls() -> list[tuple[str, str, str]]:
    """返回 [(文件, HTTP 方法, 路径)]。

    注意：路径可能以 ``+`` 与变量拼接（如 ``'/faces/' + name``），
    此时只取字面前缀，后续按前缀匹配。
    """
    calls: list[tuple[str, str, str]] = []
    for path in sorted(STATIC_DIR.glob("*.js")):
        text = _strip_js_comments(path.read_text(encoding="utf-8"))

        for method, _ignore, target in CALL_PATTERN.findall(text):
            calls.append((path.name, METHOD_ALIASES.get(method.upper(), method.upper()), target))

        for method, body in TEMPLATE_PATTERN.findall(text):
            # 模板串：把 ${...} 视为路径片段
            generic = re.sub(r"\$\{[^}]*\}", "{param}", body)
            calls.append((path.name, METHOD_ALIASES.get(method.upper(), method.upper()), generic))

        for raw in RAW_PATH_PATTERN.findall(text):
            calls.append((path.name, "ANY", raw))
    return calls


def _backend_routes() -> list[tuple[str, str]]:
    """返回 [(方法, 路径)]，来自 FastAPI 路由表。"""
    from app.main import app

    routes: list[tuple[str, str]] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        methods = getattr(route, "methods", None)
        if methods:
            for method in methods:
                routes.append((method.upper(), path))
        else:
            # WebSocket 路由
            routes.append(("WS", path))
    return routes


def _matches(method: str, target: str, routes: list[tuple[str, str]]) -> bool:
    """判断前端路径是否有对应的后端路由。

    支持三种情况：
    * 精确路径（``/sessions/checkin``）
    * 前缀 + 拼接（``/faces/`` 用于 ``'/faces/' + name``）
    * 占位符（``/faces/{param}``，来自模板串或拼接）
    """
    normalized = target if target.startswith("/api/v1") else "/api/v1" + target
    # 统一占位符表示
    normalized = re.sub(r"\{[^}]*\}", "{param}", normalized)

    for route_method, route_path in routes:
        if route_method == "WS":
            continue  # WS 路径单独由 test_camera_websocket_paths_match 校验
        if method != "ANY" and route_method != method:
            continue

        pattern = "^" + re.sub(r"\{[^}]+\}", "[^/]+", route_path) + "$"

        if re.match(pattern, normalized):
            return True
        # 前缀拼接：target 以 '/' 结尾（如 '/faces/'），匹配其任意子路径
        if normalized.endswith("/"):
            if re.match(pattern, normalized + "x"):
                return True
        # 尾部占位符：'/faces/{param}'
        if normalized.endswith("{param}") and re.match(pattern, normalized):
            return True
    return False


def test_static_js_files_exist() -> None:
    files = {p.name for p in STATIC_DIR.glob("*.js")}
    assert {"api.js", "camera-core.js", "auth.js", "manager.js",
            "account.js", "console.js", "idx.js", "in.js", "out.js"} <= files


def test_frontend_calls_match_backend_routes() -> None:
    """前端引用的每个 API 路径都必须真实存在于后端路由表。

    WebSocket 路径不在此校验（它们不在 REST 路由表里，由专门的用例覆盖）。
    """
    routes = _backend_routes()
    assert routes, "未能读取到后端路由表"

    missing: list[str] = []
    for filename, method, target in _frontend_calls():
        if "/api/v1/ws/" in target:
            continue
        if not _matches(method, target, routes):
            missing.append(f"{filename}: {method} {target}")

    assert not missing, "以下前端调用在后端不存在：\n" + "\n".join(missing)


def test_frontend_has_no_legacy_paths() -> None:
    """旧版路径必须已全部替换。

    实现方式不是维护一份「旧路径黑名单」（那样永远漏），而是：
    前端引用的每一个路径都必须能映射到后端路由；
    任何遗留的旧路径因为后端已不存在，会立刻被识别出来。
    """
    routes = _backend_routes()
    stale: list[str] = []

    for filename, method, target in _frontend_calls():
        if "/api/v1/ws/" in target:
            continue
        if target == "/api/v1":
            continue
        if not _matches(method, target, routes):
            stale.append(f"{filename}: {method} {target}")

    assert not stale, "以下前端路径在后端不存在（疑似旧版残留）：\n" + "\n".join(stale)


def test_no_page_uses_bare_api_base_in_template_literal() -> None:
    """禁止恢复 ``${API_BASE}/xxx`` 这种拼接（漏引 api.js 即运行时崩溃）。"""
    violations: list[str] = []
    for path in sorted(STATIC_DIR.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        if "API_BASE}" in text:
            violations.append(path.name)
    assert not violations, f"以下文件仍在使用 ${{API_BASE}} 拼接：{violations}"


def test_payment_flow_is_wired_in_frontend() -> None:
    """支付流程必须已接入前端。

    回归背景：旧版后端有 ``/check/pay``，但**前端从未调用过**，功能实际是死的。
    现在按需求改为：签退结算成功后**自动弹出**支付弹窗（二维码 + 支付链接），
    不再需要单独点「账单」按钮。

    因此这里断言的是「前端确实有完整的支付闭环」：
      * 有支付弹窗逻辑，且由签退响应驱动（不再二次查询账单）；
      * 二维码为占位内容「开发中」；
      * 支付链接指向占位页 pay-dev.html。
    """
    texts = "\n".join(p.read_text(encoding="utf-8") for p in STATIC_DIR.glob("*.js"))

    assert "showPayModal" in texts, "缺少支付弹窗逻辑"
    assert "buildPlaceholderQrSvg" in texts, "缺少二维码占位图生成"
    assert "pay-dev.html" in texts, "支付链接未指向占位页"
    assert "开发中" in texts, "二维码占位内容应写明「开发中」"

    # 签退成功后必须调用支付弹窗（否则支付入口又变成"死"的）
    manager = (STATIC_DIR / "manager.js").read_text(encoding="utf-8")
    checkout_handler = manager[manager.find("confirmCheckoutBtn"):]
    assert "showPayModal(" in checkout_handler, "签退成功后未弹出支付弹窗"

    # 后端仍保留账单接口（供 API 集成与调试）
    routes = _backend_routes()
    assert any(
        method == "POST" and path == "/api/v1/billing/quote" for method, path in routes
    ), "后端未注册 /api/v1/billing/quote"


def test_billing_quote_endpoint_still_enforces_payable() -> None:
    """账单接口仍须坚持「无应付金额则不给付款入口」。

    前端 UI 已不再单独调用它，但接口语义要保持正确 ——
    否则将来一旦有人接回前端，就会重现「0 元也能出账」的问题。
    """
    import inspect

    from app.api.routes import billing

    source = inspect.getsource(billing)
    assert "payable" in source, "账单接口丢失 payable 判断"
    assert 'qrcode_url=""' in source or 'qrcode_url=""' in source.replace(" ", ""), (
        "无应付金额时未清空二维码"
    )
    assert "pay_url=\"\"" in source or 'pay_url=""' in source, "无应付金额时未清空支付链接"


def test_camera_websocket_paths_match() -> None:
    """摄像头 WS 路径必须与后端一致，且带 token 查询参数。"""
    routes = _backend_routes()
    ws_paths = {path for method, path in routes if method == "WS"}
    assert "/api/v1/ws/camera" in ws_paths
    assert "/api/v1/ws/camera-exit" in ws_paths
    assert "/api/v1/ws/logs" in ws_paths

    core = (STATIC_DIR / "camera-core.js").read_text(encoding="utf-8")
    assert "?token=" in core, "摄像头 WS 必须携带 token（旧版完全不认证）"


#: 独立占位页，不参与业务、也不需要 api.js
STANDALONE_PAGES = {"pay-dev.html"}


def test_all_html_files_load_api_js_first() -> None:
    """业务页面必须首先加载 api.js，且顺序在业务脚本之前。

    ``pay-dev.html`` 是纯静态占位页（不调用任何接口），故豁免；
    它刻意不依赖 api.js，以免"占位页也要能独立打开"这一前提被破坏。
    """
    pages = sorted(STATIC_DIR.glob("*.html"))
    assert pages, "未找到任何页面"

    for page in pages:
        if page.name in STANDALONE_PAGES:
            continue
        text = page.read_text(encoding="utf-8")
        scripts = re.findall(r"<script src=\"([^\"]+)\"", text)
        if "api.js" not in scripts:
            pytest.fail(f"{page.name} 未引入 api.js")
        assert scripts.index("api.js") == 0, (
            f"{page.name} 中 api.js 不是第一个脚本：{scripts}"
        )


def test_camera_pages_load_camera_core() -> None:
    """入口/出口页面必须加载 camera-core.js，避免逻辑再次分叉。"""
    for name in ("in.html", "out.html"):
        text = (STATIC_DIR / name).read_text(encoding="utf-8")
        assert "camera-core.js" in text, f"{name} 未引入 camera-core.js"


def test_no_console_hijacking_in_frontend() -> None:
    """禁止再次劫持 console（旧版把所有调试输出灌进服务端日志）。"""
    violations: list[str] = []
    for path in sorted(STATIC_DIR.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        if re.search(r"console\.(log|warn|error)\s*=", text):
            violations.append(path.name)
    assert not violations, f"以下文件仍在劫持 console：{violations}"


def test_no_inline_onclick_with_user_data() -> None:
    """禁止把用户数据拼进 onclick 属性（旧版 XSS）。"""
    text = (STATIC_DIR / "manager.js").read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in text.splitlines()
        if "onclick=" in line and "${" in line
    ]
    assert not offenders, "manager.js 仍在 onclick 里拼接动态数据：\n" + "\n".join(offenders)


def test_no_duplicate_top_level_declarations() -> None:
    """禁止跨文件重复的顶层声明。

    这是**真实致命缺陷**的回归防护：``const LF = window.LookUFace;`` 曾同时出现在
    auth.js / manager.js / account.js 中，浏览器抛
    ``Uncaught SyntaxError: Identifier 'LF' has already been declared``，
    导致整个脚本文件不执行、页面交互全部失效。
    ``node --check`` 单文件检查无法发现这类问题，只有真实浏览器能暴露。

    规则：
      * ``const`` / ``let`` / ``class`` 重复声明 -> 立即 SyntaxError，必须禁止；
      * 同名 ``function`` 声明 -> 后者覆盖前者，属于隐蔽 bug，也禁止；
      * ``var`` -> 允许重复（语义明确，且本项目用它做共享的全局引用）。
    """
    declaration = re.compile(
        r"^(?P<kind>const|let|var|function|async\s+function|class)\s+(?P<name>[A-Za-z_$][\w$]*)",
        re.MULTILINE,
    )

    owners: dict[str, list[str]] = {}
    for path in sorted(STATIC_DIR.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        # IIFE 包裹的文件自有作用域，不参与全局冲突
        if text.lstrip().startswith(("(function", "(async function")):
            continue
        for match in declaration.finditer(text):
            if match.group("kind") == "var":
                continue  # var 允许重复声明
            owners.setdefault(match.group("name"), []).append(path.name)

    conflicts = {name: files for name, files in owners.items() if len(files) > 1}
    assert not conflicts, (
        "存在跨文件顶层声明冲突（会导致 SyntaxError 或静默覆盖）：\n"
        + "\n".join(f"  {name}: {files}" for name, files in sorted(conflicts.items()))
    )


def test_shared_globals_use_var_or_iife() -> None:
    """共享的全局引用必须用 var（或 IIFE 隔离），以便多脚本安全重复引用。"""
    for name in ("auth.js", "manager.js", "account.js"):
        text = (STATIC_DIR / name).read_text(encoding="utf-8")
        assert "var LF = window.LookUFace;" in text, (
            f"{name} 应使用 var 声明共享的 LF 引用（const/let 会与同页其他脚本冲突）"
        )


def test_account_page_does_not_touch_missing_elements() -> None:
    """account.html 没有 statusDot/statusText，访问它们必须做空值保护。

    旧实现直接 ``statusDot.className = ...`` 会抛
    ``Cannot set properties of null``（真实浏览器已复现）。
    """
    account_html = (STATIC_DIR / "account.html").read_text(encoding="utf-8")
    assert 'id="statusDot"' not in account_html

    account_js = (STATIC_DIR / "account.js").read_text(encoding="utf-8")
    assert "if (statusDot)" in account_js, "account.js 必须对 statusDot 做空值保护"
    assert "if (statusText)" in account_js, "account.js 必须对 statusText 做空值保护"


def test_scripts_do_not_redeclare_lf() -> None:
    """LF 必须只由 api.js 挂到 window，各业务脚本不得再次 const 声明。"""
    offenders = []
    for path in sorted(STATIC_DIR.glob("*.js")):
        if path.name == "api.js":
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"^\s*(?:const|let)\s+LF\s*=", text, re.MULTILINE):
            offenders.append(path.name)
    assert not offenders, f"以下文件重复声明了 LF：{offenders}"
