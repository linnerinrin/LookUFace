"""主题：四个入口页都会初始化主题并绑定切换按钮。"""

from __future__ import annotations

import re

import pytest

from app.config import settings

#: 页面 -> 对应的脚本（页面自身不内联主题逻辑，统一走 api.js 的 LF.theme）
PAGE_SCRIPTS = {
    "idx.html": "idx.js",
    "manager.html": "manager.js",
    "dashboard.html": "dashboard.js",
    "settings.html": "settings.js",
}


def _read(name: str) -> str:
    return (settings.STATIC_DIR / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("page,script", sorted(PAGE_SCRIPTS.items()))
def test_page_has_theme_toggle_button(page, script):
    html = _read(page)
    assert 'id="themeToggle"' in html
    # 允许脚本带版本查询串（破浏览器缓存用）
    assert re.search(r'src="' + re.escape(script) + r'(?:\?[^"]*)?"', html)


@pytest.mark.parametrize("page,script", sorted(PAGE_SCRIPTS.items()))
def test_page_script_initialises_theme(page, script):
    js = _read(script)
    assert "LF.theme.init()" in js, f"{script} 没有初始化主题"


@pytest.mark.parametrize("page,script", sorted(PAGE_SCRIPTS.items()))
def test_page_script_binds_theme_toggle(page, script):
    js = _read(script)
    assert "themeToggle" in js, f"{script} 没有绑定 #themeToggle"
    assert "LF.theme.toggle()" in js, f"{script} 没有调用 LF.theme.toggle()"


def test_theme_module_lives_in_api_js_only():
    """主题实现只在 api.js 里，页面不得自行重写一份。"""
    api = _read("api.js")
    assert "lookuface-theme" in api or "KEY: 'theme'" in api
    for script in PAGE_SCRIPTS.values():
        js = _read(script)
        assert "classList.toggle('dark'" not in js
        assert 'classList.toggle("dark"' not in js


def test_theme_key_is_namespaced():
    api = _read("api.js")
    match = re.search(r"KEY:\s*'([^']+)'", api)
    assert match is not None
    assert match.group(1)


def test_light_theme_variables_are_not_transparent_typos():
    """浅色主题下卡片/侧栏/输入框不能是 0.1 透明度，否则正文浮在背景图上。"""
    css = _read("manager.css")
    light_block = css.split("body.dark")[0]
    for var in ("--card-bg", "--sidebar-bg", "--input-bg", "--status-bg"):
        match = re.search(re.escape(var) + r":\s*rgba\([^)]*?,\s*([0-9.]+)\)", light_block)
        assert match is not None, f"缺少 {var}"
        assert float(match.group(1)) >= 0.5, f"{var} 透明度过低：{match.group(1)}"


def test_dark_theme_overrides_every_surface():
    css = _read("manager.css")
    dark_block = css.split("body.dark", 1)[1]
    for var in ("--card-bg", "--sidebar-bg", "--input-bg", "--text-primary"):
        assert var in dark_block, f"深色主题缺少 {var}"


def test_idx_and_manager_share_the_same_var_names():
    idx = _read("idx.css")
    manager = _read("manager.css")
    shared = ["--sidebar-bg", "--text-primary", "--text-secondary", "--nav-hover"]
    for var in shared:
        assert var in idx and var in manager, f"{var} 未在两边统一定义"
