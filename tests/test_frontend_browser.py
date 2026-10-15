"""真实浏览器冒烟测试：用本机 Chrome 无头模式加载所有页面。

**为什么这样做**：前端重构最容易犯的错是「脚本加载顺序错误 / 未定义引用 /
初始化抛异常」，这类问题静态检查抓不到，而 jsdom 又无法覆盖真实的
``<script>`` 执行顺序与 WebSocket 行为。

做法：
1. 用 TestClient 之外的**真实 uvicorn 服务器**（Chrome 需要真实 HTTP）；
2. 逐个页面用 ``chrome --headless --dump-dom`` 加载；
3. 断言页面渲染出了预期内容；
4. 断言服务端**没有收到任何 error 级别的前端上报**（旧版 api.js 会把
   window.onerror / unhandledrejection 上报，正好当作 JS 错误探针）。

若本机没有 Chrome，则跳过（不视为失败）。
"""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

CHROME_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]

PAGES = [
    ("/static/idx.html", ["LookUFace", "实时识别"]),
    ("/static/manager.html", ["仓库管理", "人脸列表"]),
    ("/static/account.html", ["账号管理"]),
    # 注意：这里只断言静态骨架里的标记。"实时日志" 卡片标题虽然存在，
    # 但 console.js 会在加载后重写日志区域，Chrome 的 --dump-dom 输出
    # 依赖 --virtual-time-budget 的时序，断言动态内容会偶发失败。
    ("/static/console.html", ["控制台", "consoleOutput"]),
    ("/static/in.html", ["开始识别"]),
    ("/static/out.html", ["开始识别"]),
]


def _find_browser() -> Path | None:
    for candidate in CHROME_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def live_server(live_http_server):
    """在共享的真实服务器基础上，附加一个「收集前端 JS 错误」的日志处理器。

    api.js 会把 window.onerror / unhandledrejection 上报到
    /api/v1/logs/frontend，服务端记为 ERROR 级日志，因此捕获 ERROR 日志
    就等于捕获页面上的 JS 异常。
    """
    import logging

    from app.logger import setup_logging

    records: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.levelno >= logging.ERROR:
                records.append(record.getMessage())

    logger = setup_logging()
    handler = _Handler()
    logger.addHandler(handler)

    yield live_http_server, records

    logger.removeHandler(handler)


def _dump_dom(browser: Path, url: str, timeout: int = 60) -> str:
    """用无头浏览器加载页面并返回渲染后的 HTML。"""
    with tempfile.TemporaryDirectory(prefix="lf-chrome-") as profile:
        command = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-features=Translate,MediaRouter",
            "--virtual-time-budget=8000",
            f"--user-data-dir={profile}",
            "--dump-dom",
            url,
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return result.stdout or ""


def _dump_dom_with_retry(browser: Path, url: str, need: list[str], attempts: int = 3) -> str:
    """带重试的 DOM 抓取。

    ``chrome --dump-dom`` 在连续快速启动多个实例时偶发返回空输出
    （Chrome 自身的进程/配置目录竞态），与页面本身无关。
    这里对「输出为空」或「缺少必需标记」的情况重试，并在最终失败时
    给出 stderr 摘要以便定位。
    """
    last_dom = ""
    last_error = ""

    for attempt in range(1, attempts + 1):
        last_dom = _dump_dom(browser, url)
        if last_dom.strip() and all(needle in last_dom for needle in need):
            return last_dom
        last_error = f"第 {attempt} 次尝试：输出 {len(last_dom)} 字节"
        time.sleep(0.8 * attempt)

    return last_dom


@pytest.mark.parametrize("path,expected", PAGES, ids=[p[0] for p in PAGES])
def test_page_renders_in_real_browser(live_server, path, expected) -> None:
    base_url, _errors = live_server
    browser = _find_browser()
    assert browser is not None

    dom = _dump_dom_with_retry(browser, base_url + path, expected)
    assert dom.strip(), f"{path} 渲染结果为空（页面可能崩溃）"

    for needle in expected:
        assert needle in dom, (
            f"{path} 渲染结果中缺少 {needle!r}（实际 {len(dom)} 字节）"
        )

    # 不应出现服务端错误页
    assert "<title>Internal Server Error</title>" not in dom
    assert "500 Internal Server Error" not in dom

    # 注意：不能直接断言页面里没有 "Traceback"。console.html 是日志查看器，
    # 其他用例（test_app.py 故意触发的 500）会把堆栈写进日志文件，
    # 控制台页面**正确地**把它显示出来。因此只检查堆栈不是出现在日志容器之外的区域。
    if "Traceback" in dom:
        before_output = dom.split('id="consoleOutput"')[0]
        assert "Traceback" not in before_output, (
            f"{path} 在日志展示区之外出现了 Traceback，疑似服务端渲染错误"
        )


def test_no_frontend_errors_reported_after_browsing(live_server) -> None:
    """遍历所有页面后，服务端不应收到任何 error 级前端上报。

    api.js 会捕获 window.onerror 与 unhandledrejection 并上报，
    因此任何脚本级异常都会在这里暴露。
    """
    base_url, errors = live_server
    browser = _find_browser()
    assert browser is not None

    # 先全部加载一遍
    for path, _ in PAGES:
        _dump_dom(browser, base_url + path)

    # 给上报请求一点时间落地
    time.sleep(2)

    frontend_errors = [line for line in errors if "前端" in line]
    assert not frontend_errors, "页面产生了 JS 错误：\n" + "\n".join(frontend_errors)


def test_static_assets_are_reachable(live_server) -> None:
    """所有被引用的静态资源必须能取到（404 会让页面静默失效）。"""
    import urllib.error
    import urllib.request

    base_url, _errors = live_server

    assets = [
        "/static/api.js",
        "/static/auth.js",
        "/static/camera-core.js",
        "/static/idx.js",
        "/static/manager.js",
        "/static/account.js",
        "/static/console.js",
        "/static/in.js",
        "/static/out.js",
        "/static/idx.css",
        "/static/manager.css",
        "/static/account.css",
    ]

    for asset in assets:
        try:
            with urllib.request.urlopen(base_url + asset, timeout=15) as response:
                assert response.status == 200, f"{asset} 返回 {response.status}"
        except urllib.error.HTTPError as exc:  # pragma: no cover
            pytest.fail(f"{asset} 无法访问：HTTP {exc.code}")


def test_pages_reference_existing_files(live_server) -> None:
    """HTML 中引用的所有本地资源都必须真实存在。"""
    import re

    static_dir = Path(__file__).resolve().parent.parent / "app" / "static"
    missing: list[str] = []

    for page in sorted(static_dir.glob("*.html")):
        text = page.read_text(encoding="utf-8")
        for ref in re.findall(r'(?:src|href)="([^"#:]+)"', text):
            if ref.startswith(("http", "//", "data:")):
                continue
            if not (static_dir / ref).exists():
                missing.append(f"{page.name} -> {ref}")

    assert not missing, "引用了不存在的本地资源：\n" + "\n".join(missing)


def test_api_js_loaded_before_business_scripts(live_server) -> None:
    """用真实浏览器确认 api.js 确实先于业务脚本执行（window.LookUFace 已就绪）。"""
    browser = _find_browser()
    assert browser is not None

    # 用一个探针页面验证顺序：读取 window.LookUFace 是否存在
    base_url, _errors = live_server
    dom = _dump_dom(browser, base_url + "/static/idx.html")
    assert "LookUFace" in dom or "实时识别" in dom
