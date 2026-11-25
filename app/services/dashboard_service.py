"""数据看板聚合与图表渲染

把进出场流水聚合为 KPI 与多维度图表。图表用 matplotlib 渲染成 PNG，
经 base64 变成 data URI 交给前端 ``<img>``，无需任何前端图表库、离线可用。

渲染放进线程池执行（见路由层的 ``asyncio.to_thread``），不阻塞事件循环；
每张图单独建 ``Figure`` 并配 ``FigureCanvasAgg``，避免 pyplot 的全局状态
在并发请求间互相干扰。
"""

from __future__ import annotations

import base64
import io
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from app.db.models import SessionRecord  # noqa: E402

# 选一个能渲染中文的字体；找不到就退回默认（图仍可用，只是中文可能缺字）
_CANDIDATES = ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "SimSun")
_INSTALLED = {f.name for f in font_manager.fontManager.ttflist}
_FONT = next((c for c in _CANDIDATES if c in _INSTALLED), "DejaVu Sans")

matplotlib.rcParams["font.sans-serif"] = [_FONT, "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

# 图表统一配色
_PALETTE = ("#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
            "#edc949", "#af7aa1", "#ff9da7", "#9c755f", "#bab0ab")


# KPI 与图表聚合结果
def aggregate(
    records: Iterable[SessionRecord],
    *,
    online_now: int,
    days: int,
    today: date | None = None,
) -> dict:
    today = today or datetime.now().date()
    rows = list(records)
    closed = [r for r in rows if r.checkout_at is not None]

    today_visits = sum(1 for r in rows if r.checkin_at and r.checkin_at.date() == today)
    today_minutes = sum(
        int(r.duration_minutes or 0) for r in rows if r.checkin_at and r.checkin_at.date() == today
    )
    avg_minutes = (
        round(sum(int(r.duration_minutes or 0) for r in closed) / len(closed)) if closed else 0
    )

    return {
        "days": days,
        "today": today,
        "kpis": {
            "today_visits": today_visits,
            "online_now": online_now,
            "today_minutes": today_minutes,
            "avg_minutes": avg_minutes,
        },
        "total_records": len(rows),
        "closed": closed,
    }


# 按主题给图表上色：背景、标题、刻度、坐标轴文字都跟随浅色/深色
def _apply_theme(fig: Figure, dark: bool) -> None:
    face = "#1c1c2e" if dark else "white"
    text = "#e8e8f0" if dark else "#262626"
    edge = "#5a5a70" if dark else "#cccccc"
    fig.patch.set_facecolor(face)
    for ax in fig.axes:
        ax.set_facecolor(face)
        ax.title.set_color(text)
        ax.xaxis.label.set_color(text)
        ax.yaxis.label.set_color(text)
        ax.tick_params(colors=text)
        for spine in ax.spines.values():
            spine.set_color(edge)
        for label in ax.get_xticklabels() + ax.get_yticklabels():
            label.set_color(text)
        # 图例文字（趋势图有双轴图例）
        legend = ax.get_legend()
        if legend:
            for item in legend.get_texts():
                item.set_color(text)


# 把 Figure 渲染成 base64 data URI
def _to_data_uri(fig: Figure, dark: bool = False) -> str:
    _apply_theme(fig, dark)
    buf = io.BytesIO()
    canvas = FigureCanvasAgg(fig)
    canvas.print_png(buf)
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


# 无数据时的占位图，避免前端出现空白破图
def _empty_fig(title: str) -> Figure:
    fig = Figure(figsize=(6, 3.4), dpi=100)
    ax = fig.add_subplot(111)
    ax.set_title(title, fontsize=13)
    ax.text(0.5, 0.5, "暂无数据", ha="center", va="center", fontsize=16, color="#999")
    ax.set_axis_off()
    return fig


# ① 用户时长/次数排行（横向条形：时长为主，次数用文字标注）
def _chart_user_ranking(closed: list[SessionRecord]) -> Figure:
    if not closed:
        return _empty_fig("用户在场时长排行")
    minutes: dict[str, int] = defaultdict(int)
    counts: Counter[str] = Counter()
    for r in closed:
        minutes[r.name] += int(r.duration_minutes or 0)
        counts[r.name] += 1
    names = sorted(minutes, key=lambda n: minutes[n], reverse=True)[:10]
    vals = [minutes[n] for n in names]

    fig = Figure(figsize=(6.4, 3.6), dpi=100)
    ax = fig.add_subplot(111)
    bars = ax.barh(names[::-1], vals[::-1], color=_PALETTE[0])
    ax.set_title("用户在场时长排行（分钟）", fontsize=13)
    ax.set_xlabel("累计分钟")
    for bar, name in zip(bars, names[::-1]):
        ax.text(bar.get_width(), bar.get_y() + bar.get_height() / 2,
                f" {int(bar.get_width())} 分 / {counts[name]} 次",
                va="center", fontsize=9)
    ax.set_xlim(0, max(vals) * 1.35 or 1)
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    return fig


# ② 近 N 日趋势（次数折线，时长柱形，双轴）
def _chart_daily_trend(closed: list[SessionRecord], today: date, days: int) -> Figure:
    if not closed:
        return _empty_fig("近 %d 日到场趋势" % days)
    start = today - timedelta(days=days - 1)
    axis_dates = [start + timedelta(days=i) for i in range(days)]
    visits = Counter()
    minutes: dict[date, int] = defaultdict(int)
    for r in closed:
        d = r.checkin_at.date()
        if start <= d <= today:
            visits[d] += 1
            minutes[d] += int(r.duration_minutes or 0)

    labels = [d.strftime("%m-%d") for d in axis_dates]
    vvals = [visits[d] for d in axis_dates]
    mvals = [minutes[d] for d in axis_dates]

    fig = Figure(figsize=(6.8, 3.4), dpi=100)
    ax = fig.add_subplot(111)
    ax.bar(labels, mvals, color=_PALETTE[4], alpha=0.55, label="在场分钟")
    ax.set_ylabel("在场分钟", color=_PALETTE[4])
    ax2 = ax.twinx()
    ax2.plot(labels, vvals, color=_PALETTE[2], marker="o", linewidth=2, label="到场次数")
    ax2.set_ylabel("到场次数", color=_PALETTE[2])
    ax.set_title("近 %d 日到场趋势" % days, fontsize=13)
    ax.tick_params(axis="x", labelrotation=45, labelsize=8)
    fig.tight_layout()
    return fig


# ③ 24 小时时段分布（按签到时刻所在小时）
def _chart_hour_distribution(closed: list[SessionRecord]) -> Figure:
    hours = Counter(r.checkin_at.hour for r in closed)
    fig = Figure(figsize=(6.8, 3.2), dpi=100)
    ax = fig.add_subplot(111)
    vals = [hours.get(h, 0) for h in range(24)]
    ax.bar(range(24), vals, color=_PALETTE[1])
    ax.set_title("到场时段分布（按签到小时）", fontsize=13)
    ax.set_xlabel("小时")
    ax.set_ylabel("次数")
    ax.set_xticks(range(0, 24, 3))
    ax.set_xlim(-0.5, 23.5)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    return fig


# ④ 单次时长区间分布（饼图）
def _chart_duration_buckets(closed: list[SessionRecord]) -> Figure:
    if not closed:
        return _empty_fig("单次在场时长分布")
    buckets = Counter()
    for r in closed:
        m = int(r.duration_minutes or 0)
        if m < 60:
            buckets["< 1 小时"] += 1
        elif m < 180:
            buckets["1-3 小时"] += 1
        elif m < 360:
            buckets["3-6 小时"] += 1
        else:
            buckets["> 6 小时"] += 1
    order = ["< 1 小时", "1-3 小时", "3-6 小时", "> 6 小时"]
    vals = [buckets.get(k, 0) for k in order]

    fig = Figure(figsize=(5.4, 3.6), dpi=100)
    ax = fig.add_subplot(111)
    ax.pie([v for v in vals if v] or [1],
           labels=[k for k, v in zip(order, vals) if v] or ["暂无数据"],
           colors=_PALETTE[:4], autopct="%1.1f%%", startangle=90)
    ax.set_title("单次在场时长分布", fontsize=13)
    ax.axis("equal")
    return fig


# ⑤ 用户 × 日期热力图
def _chart_heatmap(closed: list[SessionRecord], today: date, days: int) -> Figure:
    if not closed:
        return _empty_fig("用户 × 日期到场热力图")
    start = today - timedelta(days=days - 1)
    axis_dates = [start + timedelta(days=i) for i in range(days)]
    names = sorted({r.name for r in closed})[:12]
    cell: dict[tuple[str, date], int] = defaultdict(int)
    for r in closed:
        d = r.checkin_at.date()
        if start <= d <= today and r.name in names:
            cell[(r.name, d)] += 1

    matrix = [[cell.get((n, d), 0) for d in axis_dates] for n in names]
    fig = Figure(figsize=(7.2, 0.6 + 0.45 * len(names)), dpi=100)
    ax = fig.add_subplot(111)
    im = ax.imshow(matrix, aspect="auto", cmap="YlGnBu")
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=9)
    step = max(1, days // 8)
    ticks = list(range(0, days, step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([axis_dates[i].strftime("%m-%d") for i in ticks], fontsize=8, rotation=45)
    ax.set_title("用户 × 日期到场热力图", fontsize=13)
    for y in range(len(names)):
        for x in range(days):
            if matrix[y][x]:
                ax.text(x, y, str(matrix[y][x]), ha="center", va="center", fontsize=8)
    fig.colorbar(im, ax=ax, shrink=0.8, label="次数")
    fig.tight_layout()
    return fig


# 一次构建 KPI 与全部图表（供路由在线程中调用）
def build_overview(
    records: Iterable[SessionRecord],
    *,
    online_now: int,
    days: int,
    today: date | None = None,
    dark: bool = False,
) -> dict:
    agg = aggregate(records, online_now=online_now, days=days, today=today)
    closed = agg["closed"]
    today = agg["today"]

    figures = {
        "user_ranking": _chart_user_ranking(closed),
        "daily_trend": _chart_daily_trend(closed, today, days),
        "hour_distribution": _chart_hour_distribution(closed),
        "duration_buckets": _chart_duration_buckets(closed),
        "heatmap": _chart_heatmap(closed, today, days),
    }
    charts = {name: _to_data_uri(fig, dark=dark) for name, fig in figures.items()}

    return {
        "kpis": agg["kpis"],
        "charts": charts,
        "days": days,
        "total_records": agg["total_records"],
    }


__all__ = ["aggregate", "build_overview"]
