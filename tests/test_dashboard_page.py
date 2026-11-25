"""看板页面：文件存在、可经静态服务访问、侧边栏入口一致。"""

from __future__ import annotations

import re

from app.config import settings

PAGES = ["idx.html", "manager.html", "dashboard.html", "settings.html"]
NAV_TARGETS = ["idx.html", "manager.html", "dashboard.html", "settings.html"]


def _read(name: str) -> str:
    return (settings.STATIC_DIR / name).read_text(encoding="utf-8")


def test_dashboard_files_exist():
    for name in ("dashboard.html", "dashboard.css", "dashboard.js"):
        assert (settings.STATIC_DIR / name).is_file(), f"缺少 {name}"


def test_dashboard_served_over_http(client):
    resp = client.get("/static/dashboard.html")
    assert resp.status_code == 200
    assert "数据看板" in resp.text


def test_dashboard_assets_served(client):
    assert client.get("/static/dashboard.js").status_code == 200
    assert client.get("/static/dashboard.css").status_code == 200


def test_every_page_links_the_same_nav_set():
    for page in PAGES:
        html = _read(page)
        for target in NAV_TARGETS:
            assert f'href="{target}"' in html, f"{page} 缺少 {target} 入口"


def test_no_page_links_removed_console():
    for page in PAGES:
        html = _read(page)
        assert "console.html" not in html


def test_dashboard_marks_itself_active():
    html = _read("dashboard.html")
    block = re.search(r'href="dashboard\.html"\s+class="([^"]*)"', html)
    assert block is not None
    assert "active" in block.group(1)


def test_other_pages_do_not_mark_dashboard_active():
    for page in ("idx.html", "manager.html", "settings.html"):
        html = _read(page)
        block = re.search(r'href="dashboard\.html"\s+class="([^"]*)"', html)
        assert block is not None
        assert "active" not in block.group(1)


def test_dashboard_has_theme_toggle_and_scripts():
    html = _read("dashboard.html")
    assert 'id="themeToggle"' in html
    # 允许带版本查询串（破浏览器缓存用）
    assert re.search(r'src="api\.js(?:\?[^"]*)?"', html)
    assert re.search(r'src="dashboard\.js(?:\?[^"]*)?"', html)


def test_dashboard_has_add_form():
    """上方添加数据表单：用户下拉 + 签到/退出时间"""
    html = _read("dashboard.html")
    assert 'id="addForm"' in html
    assert 'id="recordName"' in html
    assert 'id="checkinAt"' in html
    assert 'id="checkoutAt"' in html
    assert 'type="datetime-local"' in html
    assert "添加数据" in html


def test_dashboard_has_kpis_and_charts():
    """主体：KPI 四卡 + 五张多维图表容器"""
    html = _read("dashboard.html")
    for kpi in ("kpiVisits", "kpiOnline", "kpiMinutes", "kpiAverage"):
        assert f'id="{kpi}"' in html
    assert 'id="kpiGrid"' in html
    assert 'id="chartGrid"' in html
    for chart in (
        "user_ranking", "daily_trend", "hour_distribution",
        "duration_buckets", "heatmap",
    ):
        assert f'id="chart_{chart}"' in html


def test_dashboard_has_day_range_switch():
    html = _read("dashboard.html")
    assert 'id="daysRange"' in html
    assert 'id="refreshBtn"' in html
    assert 'value="7"' in html and 'value="30"' in html
