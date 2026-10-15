"""前端样式一致性测试。

**为什么需要它**：仓库管理页的「账单」按钮曾在 HTML 里使用 ``class="btn-billing"``，
但 ``manager.css`` 中**完全没有这条规则** —— 按钮因此没有任何样式，与同排其他
按钮（预览/修改/签到/签退/删除）外观不一致；同时 ``#billingModal`` 的
label / input 也没有像其他弹窗那样的样式覆盖。

这类「类名写了但样式没定义」的问题不会报错、不会 500，只会静默地显示成裸样式，
在代码审查里也很容易被忽略。因此用测试把「用到的类必须有真实样式规则」固化下来。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).resolve().parent.parent / "app" / "static"

#: 只校验有实际视觉意义的按钮类，避免把纯语义类当成样式缺失
CLASS_PREFIXES = ("btn-",)

#: 这些 CSS 文件里解析出来的类名视为"已定义"
CSS_FILES = ("idx.css", "manager.css", "account.css")

# 匹配 "选择器 { ...属性... }"，要求大括号内至少有一个 "属性: 值"
RULE_PATTERN = re.compile(r"([^{}]+)\{([^{}]*)\}", re.DOTALL)
CLASS_IN_SELECTOR = re.compile(r"\.([A-Za-z_][\w-]*)")


def _defined_classes() -> set[str]:
    """返回所有 CSS 文件中「有真实样式声明」的类名。"""
    defined: set[str] = set()

    for name in CSS_FILES:
        path = STATIC_DIR / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # 去掉注释，避免注释里的 ".foo {" 被误判成规则
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)

        for selector, body in RULE_PATTERN.findall(text):
            # 必须至少有一条真实声明（形如 prop: value;）
            if not re.search(r"[\w-]+\s*:\s*[^;]+", body):
                continue
            for cls in CLASS_IN_SELECTOR.findall(selector):
                defined.add(cls)

    return defined


def _used_classes() -> dict[str, list[str]]:
    """返回前端代码里用到的类名 -> 出现位置列表。

    扫描两类来源：
      * HTML 里的 ``class="..."``
      * JS 里拼进 HTML 的 ``class="..."`` / ``className = '...'``
        （例如卡片按钮是动态生成的，不在 HTML 里）
    """
    usage: dict[str, list[str]] = {}

    def record(cls: str, where: str) -> None:
        usage.setdefault(cls, []).append(where)

    patterns = [
        re.compile(r'class\s*=\s*"([^"]*)"'),
        re.compile(r"class\s*=\s*'([^']*)'"),
        re.compile(r"className\s*=\s*'([^']*)'"),
    ]

    for path in sorted(list(STATIC_DIR.glob("*.html")) + list(STATIC_DIR.glob("*.js"))):
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in patterns:
            for match in pattern.findall(text):
                for cls in match.split():
                    if cls.startswith(CLASS_PREFIXES):
                        record(cls, path.name)

    return usage


def test_css_files_exist() -> None:
    for name in CSS_FILES:
        assert (STATIC_DIR / name).exists(), f"缺少样式文件 {name}"


def test_every_used_button_class_has_real_styles() -> None:
    """每个用到的 ``btn-*`` 类都必须在某个 CSS 文件里有真实样式规则。

    这条用例正是为了捕获「账单按钮没有样式」这类缺陷。
    """
    defined = _defined_classes()
    used = _used_classes()

    missing = {
        cls: places
        for cls, places in used.items()
        if cls not in defined
    }

    assert not missing, (
        "以下按钮类名在前端被使用，但没有任何 CSS 规则（会显示成裸样式）：\n"
        + "\n".join(f"  .{cls}  <- 出现于 {sorted(set(places))}" for cls, places in sorted(missing.items()))
    )


def test_billing_button_is_styled_like_sibling_buttons() -> None:
    """账单按钮必须与同排其他按钮保持一致的尺寸与过渡。

    回归用例：``.btn-billing`` 曾完全缺失，按钮无样式。
    """
    css = (STATIC_DIR / "manager.css").read_text(encoding="utf-8")
    css_no_comment = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)

    match = re.search(r"\.btn-billing\s*\{([^}]*)\}", css_no_comment)
    assert match, ".btn-billing 未定义"

    body = match.group(1)
    for prop in ("background", "color", "padding", "border-radius", "cursor", "font-weight"):
        assert prop in body, f".btn-billing 缺少 {prop}，与同排按钮不一致"

    # 与 .btn-modify 的尺寸/过渡保持一致
    sibling = re.search(r"\.btn-modify\s*\{([^}]*)\}", css_no_comment)
    assert sibling, ".btn-modify 未定义（对比基准缺失）"
    sibling_body = sibling.group(1)
    for prop in ("padding", "border-radius", "font-weight", "transition"):
        assert prop in body and prop in sibling_body, f"账单按钮缺少 {prop}"
        assert _value_of(body, prop) == _value_of(sibling_body, prop), (
            f".btn-billing 与 .btn-modify 的 {prop} 不一致："
            f"{_value_of(body, prop)!r} vs {_value_of(sibling_body, prop)!r}"
        )

    # 必须有 hover 态
    assert re.search(r"\.btn-billing:hover", css_no_comment), ".btn-billing 缺少 hover 样式"
    assert re.search(r"\.btn-billing:disabled", css_no_comment), ".btn-billing 缺少 disabled 样式"


def test_billing_modal_has_form_styles() -> None:
    """账单弹窗的 label / input 必须与其他弹窗一样有样式覆盖。"""
    css = (STATIC_DIR / "manager.css").read_text(encoding="utf-8")
    css_no_comment = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)

    for selector in (
        r"#billingModal \.modal-content",
        r"#billingModal \.form-group",
        r"#billingModal \.form-group label",
        r"#billingModal \.auth-input",
    ):
        assert re.search(selector, css_no_comment), f"缺少样式规则：{selector}"

    # 深色主题也要覆盖
    assert re.search(r"body\.dark\s+#billingModal", css_no_comment), (
        "账单弹窗缺少深色主题覆盖"
    )


def test_billing_modal_css_matches_checkout_modal() -> None:
    """账单弹窗与签退弹窗的表单样式应保持一致（同一套设计语言）。"""
    css = (STATIC_DIR / "manager.css").read_text(encoding="utf-8")
    css_no_comment = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)

    def declarations(selector: str) -> str:
        match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css_no_comment)
        return match.group(1) if match else ""

    billing_label = declarations("#billingModal .form-group label")
    checkout_label = declarations("#checkoutModal .form-group label")
    assert billing_label and checkout_label
    for prop in ("display", "margin-bottom", "font-size", "font-weight", "color"):
        assert _value_of(billing_label, prop) == _value_of(checkout_label, prop), (
            f"账单弹窗与签退弹窗的 label {prop} 不一致"
        )

    billing_input = declarations("#billingModal .auth-input")
    checkout_input = declarations("#checkoutModal .auth-input")
    assert billing_input and checkout_input
    for prop in ("width", "padding", "border", "border-radius", "background", "color", "font-size"):
        assert _value_of(billing_input, prop) == _value_of(checkout_input, prop), (
            f"账单弹窗与签退弹窗的 input {prop} 不一致"
        )


def test_primary_and_danger_buttons_are_defined() -> None:
    """``.btn-primary`` / ``.btn-danger`` 被 HTML 引用，必须有定义。

    ``.btn-primary`` 此前在 manager.css 中缺失，导致账单弹窗里的
    「打开支付链接」按钮没有配色。
    """
    defined = _defined_classes()
    for cls in ("btn-primary", "btn-danger", "btn-outline"):
        assert cls in defined, f".{cls} 未定义"


def _value_of(body: str, prop: str) -> str | None:
    """从一段 CSS 声明里取出某个属性的值。"""
    match = re.search(rf"(?<![\w-]){re.escape(prop)}\s*:\s*([^;]+)", body)
    return match.group(1).strip() if match else None


# --------------------------------------------------------------------------- #
# 摄像头识别框坐标系
# --------------------------------------------------------------------------- #

CAMERA_CORE = STATIC_DIR / "camera-core.js"


def _function_body(source: str, name: str) -> str:
    """粗粒度提取 ``X.prototype.NAME = function ... { ... }`` 的函数体。"""
    marker = f".prototype.{name} = function"
    start = source.find(marker)
    assert start != -1, f"未找到 {name} 的实现"

    brace = source.find("{", start)
    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace:index + 1]
    raise AssertionError(f"{name} 的函数体未正常闭合")


def test_video_elements_are_css_mirrored() -> None:
    """前提：``<video>`` 由 CSS 水平镜像显示。

    识别框的坐标系推导依赖这个前提；如果哪天去掉 ``scaleX(-1)``，
    ``drawFaces`` 就必须改为自己镜像，否则框与脸又会错位。
    """
    for name in ("in.html", "out.html"):
        text = (STATIC_DIR / name).read_text(encoding="utf-8")
        assert "scaleX(-1)" in text, (
            f"{name} 的 <video> 不再镜像显示；"
            "若有意改动，请同步修改 camera-core.js 的 drawFaces 坐标系说明与实现"
        )


def test_frame_is_flipped_before_sending() -> None:
    """发送前必须把帧镜像一次，使服务端看到的就是用户看到的画面。

    与上面的 CSS 镜像共同构成「bbox 已是显示空间坐标」这一前提。
    """
    source = CAMERA_CORE.read_text(encoding="utf-8")
    body = _function_body(source, "captureAndSend")
    assert "scale(-1, 1)" in body, (
        "captureAndSend 必须先镜像帧再发送，否则服务端返回的坐标与显示画面不一致"
    )


def test_registration_snapshot_is_flipped_too() -> None:
    """注册时截取的参考图也要镜像，与发送给服务端的帧保持同一方向。

    曾经的不一致：识别链路发的是镜像帧，而注册截图截的是未镜像的原始帧，
    导致存下来的注册截图与界面里看到的方向左右相反。
    """
    source = CAMERA_CORE.read_text(encoding="utf-8")
    body = _function_body(source, "registerCurrentFace")
    assert "scale(-1, 1)" in body, (
        "registerCurrentFace 截帧前必须镜像，保持与 captureAndSend 一致"
    )


def test_draw_faces_does_not_mirror_again() -> None:
    """**回归用例**：drawFaces 不得再做一次水平镜像。

    曾经的问题：``<video>`` 已由 CSS 镜像、发送的帧也已镜像，
    ``drawFaces`` 又做了一次 ``videoWidth - x2``，等于翻了两次，
    导致识别框与人脸左右相反。

    因此 drawFaces 里不允许出现 ``videoWidth - x`` 这类镜像计算，
    也不应再引用 ``videoWidth``。
    """
    source = CAMERA_CORE.read_text(encoding="utf-8")
    body = _function_body(source, "drawFaces")

    forbidden = [
        ("videoWidth -", "又在做水平镜像计算（会导致翻两次）"),
        ("mirroredX", "残留的镜像变量名"),
        ("overlay.width -", "以画布宽度做镜像计算"),
        ("scale(-1", "在覆盖层上做了翻转"),
    ]
    for needle, reason in forbidden:
        assert needle not in body, f"drawFaces 中出现 {needle!r}：{reason}"

    # 必须通过 computeBoxRect 取矩形（该函数本身由下一个用例保证不做翻转）
    assert "computeBoxRect" in body, "drawFaces 应通过 computeBoxRect 取矩形"


def test_compute_box_rect_uses_raw_coordinates() -> None:
    """``computeBoxRect`` 必须原样返回 bbox 坐标（不做任何翻转）。"""
    source = CAMERA_CORE.read_text(encoding="utf-8")
    body = _function_body(source, "computeBoxRect")

    assert "bbox[0]" in body and "bbox[1]" in body
    assert "bbox[2] - bbox[0]" in body, "宽度应为 x2 - x1"
    assert "bbox[3] - bbox[1]" in body, "高度应为 y2 - y1"
    assert "videoWidth" not in body, "computeBoxRect 不应涉及画布宽度"


def test_coordinate_pipeline_math_both_ways() -> None:
    """用真实数值验证整条坐标链路，并证明「再翻一次」是错的。

    链路（三重关系必须自洽）：

        摄像头原始帧          人脸在 x=400
        ① <video> CSS 镜像     用户看到 x = W-400 = 240          （W=640）
        ② captureAndSend 镜像  发给服务端的帧上 x = 240
        ③ 服务端检测           返回 bbox 的 x 就是 240
        ④ overlay 画框         应画在 240  ← 与用户所见一致

    下面分别推导三种画法，证明只有「原样使用」能对齐，
    并说明历史上多翻一次（``W - x``）恰好翻回 400，正是用户看到的左右相反。
    """
    width = 640
    camera_x = 400  # 人脸在摄像头原始帧上的位置

    # ①②：显示画面与发送帧都做了水平镜像
    displayed_x = width - camera_x
    sent_x = width - camera_x
    assert displayed_x == sent_x == 240, "镜像链路本身应一致"

    # ③：服务端在它收到的帧上检出人脸，坐标即 sent_x
    server_bbox_x = sent_x

    # ④a 正确画法：原样使用 —— 与用户所见一致
    correct_draw_x = server_bbox_x
    assert correct_draw_x == displayed_x, "原样使用才能与人脸对齐"

    # ④b 错误画法（历史上的 bug）：又翻一次
    wrong_draw_x = width - server_bbox_x
    assert wrong_draw_x == camera_x == 400, "多翻一次会翻回摄像头原始位置"

    # 等价地说：多翻一次与正确位置关于画面中心对称 —— 这就是「左右镜像」的准确含义
    assert wrong_draw_x == width - correct_draw_x, "错误画法恰好是正确位置的水平镜像"
    assert wrong_draw_x != correct_draw_x


def test_bounding_box_symmetry_holds_for_arbitrary_positions() -> None:
    """对任意位置都成立：多翻一次必然落到镜像侧。"""
    width = 640
    for camera_x in (10, 100, 320, 500, 630):
        displayed = width - camera_x
        wrong = width - displayed  # 多翻一次
        assert wrong == camera_x, "多翻一次等于回到摄像头原始坐标"
        assert abs(wrong - displayed) == abs(width - 2 * displayed), "偏差随位置变化，不是固定偏移"
        # 只有当人脸正好在画面中心时，两种画法才会重合 —— 说明这个 bug 容易被漏测
        if camera_x == width // 2:
            assert wrong == displayed
