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

#: 界面文件（会真正渲染给用户看的）
UI_FILES = (
    "manager.html", "manager.js", "manager.css",
    "account.html", "account.js", "account.css",
    "auth.js", "api.js", "idx.html", "idx.js", "idx.css",
    "in.html", "in.js", "out.html", "out.js",
    "camera-core.js", "console.html", "console.js",
)

#: 已知会在本机被渲染成**彩色图形**的字符。
#:
#: 用 Chrome 截图实测确认（本机装有 seguiemj.ttf）：
#:   * 所有 U+1F000 及以上的 emoji
#:   * \u25cf 实心圆点 —— 虽不是 emoji 码位，但实际渲染成彩色实心圆点
#: 这些都不应出现在界面代码里。
COLOR_GLYPH_CHARS = ("\u25cf",)

#: 明确要求移除的 emoji（写成转义，避免本文件里又出现 emoji）
REMOVED_EMOJI = (
    "\U0001F464",  # 人像轮廓
    "\U0001F465",  # 双人轮廓
    "\U0001F7E2",  # 绿色圆形
)

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


def test_billing_ui_has_been_removed() -> None:
    """「账单」按钮与账单弹窗已按需求删除，不得在代码里残留。

    现在改为：签退结算成功后自动弹出支付弹窗（二维码 + 支付链接）。
    """
    manager_js = (STATIC_DIR / "manager.js").read_text(encoding="utf-8")
    manager_html = (STATIC_DIR / "manager.html").read_text(encoding="utf-8")
    manager_css = (STATIC_DIR / "manager.css").read_text(encoding="utf-8")

    for name, text in (("manager.js", manager_js), ("manager.html", manager_html)):
        for token in ("billingModal", "showBillingModal", "closeBillingModal", "btn-billing"):
            assert token not in text, f"{name} 仍残留已删除的账单 UI：{token}"

    # CSS 里也不应再有账单相关选择器（说明：注释里提到旧名是允许的，故只查选择器形态）
    css_no_comment = re.sub(r"/\*.*?\*/", "", manager_css, flags=re.DOTALL)
    assert not re.search(r"\.btn-billing\s*\{", css_no_comment), "CSS 仍残留 .btn-billing 规则"
    assert not re.search(r"#billingModal\s+\S+\s*\{", css_no_comment), (
        "CSS 仍残留 #billingModal 规则"
    )


def test_pay_modal_exists_and_has_form_styles() -> None:
    """支付弹窗必须存在，且 label / input 与其他弹窗一样有样式覆盖。"""
    html = (STATIC_DIR / "manager.html").read_text(encoding="utf-8")
    assert 'id="payModal"' in html, "缺少支付弹窗"

    css = (STATIC_DIR / "manager.css").read_text(encoding="utf-8")
    css_no_comment = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)

    for selector in (
        r"#payModal \.modal-content",
        r"#payModal \.form-group",
        r"#payModal \.form-group label",
        r"#payModal \.auth-input",
    ):
        assert re.search(selector, css_no_comment), f"缺少样式规则：{selector}"

    assert re.search(r"body\.dark\s+#payModal", css_no_comment), "支付弹窗缺少深色主题覆盖"


def test_pay_modal_css_matches_checkout_modal() -> None:
    """支付弹窗与签退弹窗的表单样式应保持一致（同一套设计语言）。"""
    css = (STATIC_DIR / "manager.css").read_text(encoding="utf-8")
    css_no_comment = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)

    def declarations(selector: str) -> str:
        match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css_no_comment)
        return match.group(1) if match else ""

    pay_label = declarations("#payModal .form-group label")
    checkout_label = declarations("#checkoutModal .form-group label")
    assert pay_label and checkout_label
    for prop in ("display", "margin-bottom", "font-size", "font-weight", "color"):
        assert _value_of(pay_label, prop) == _value_of(checkout_label, prop), (
            f"支付弹窗与签退弹窗的 label {prop} 不一致"
        )

    pay_input = declarations("#payModal .auth-input")
    checkout_input = declarations("#checkoutModal .auth-input")
    assert pay_input and checkout_input
    for prop in ("width", "padding", "border", "border-radius", "background", "color", "font-size"):
        assert _value_of(pay_input, prop) == _value_of(checkout_input, prop), (
            f"支付弹窗与签退弹窗的 input {prop} 不一致"
        )


def test_checkout_modal_shows_amount_not_duration() -> None:
    """按需求：结账弹窗里原来的「本次时长」格子改为直接显示**应付金额**。

    时长明细改为放在字段下方的说明行里，所以 ``checkoutAmount`` 必须存在，
    而 ``checkoutDuration`` 不应再作为输入框存在。
    """
    html = (STATIC_DIR / "manager.html").read_text(encoding="utf-8")
    manager_js = (STATIC_DIR / "manager.js").read_text(encoding="utf-8")

    assert 'id="checkoutAmount"' in html, "结账弹窗缺少「应付金额」字段"
    assert 'id="checkoutDuration"' not in html, "「本次时长」输入框应已被金额字段替换"
    assert "checkoutAmount" in manager_js, "JS 未使用金额字段"

    # 金额字段的标签必须是「应付金额」
    match = re.search(r'id="checkoutAmount"[\s\S]{0,200}?</div>', html)
    assert match, "未找到金额字段所在的表单块"
    block_start = html.rfind("<label", 0, html.find('id="checkoutAmount"'))
    label_text = html[block_start:html.find('id="checkoutAmount"')]
    assert "应付金额" in label_text, f"金额字段标签不正确：{label_text!r}"


def test_checkout_title_has_no_parentheses() -> None:
    """结账弹窗的标题不应带括号。

    实测弹窗标题曾被写成「暂离中 · 签退结算」之类，标题里再塞
    「（可自定义，默认现在）」这类说明会显得杂乱。
    """
    html = (STATIC_DIR / "manager.html").read_text(encoding="utf-8")
    manager_js = (STATIC_DIR / "manager.js").read_text(encoding="utf-8")

    # 标题初始文案
    match = re.search(r'id="checkoutTitle"[^>]*>([^<]*)<', html)
    assert match, "未找到结账弹窗标题"
    assert "（" not in match.group(1) and "(" not in match.group(1), (
        f"标题含括号：{match.group(1)!r}"
    )

    # JS 里给标题赋值的文案也不应含括号。
    # 注意：代码里标题是通过局部变量赋值的（title.textContent = ...），
    # 因此这里匹配 ".textContent = '...'" 这种形式，而不是 getElementById('checkoutTitle')。
    title_assignments = re.findall(
        r"title\.textContent\s*=\s*([^;]+);",
        manager_js,
    )
    assert title_assignments, "未找到给标题赋值的代码"

    for assignment in title_assignments:
        for literal in re.findall(r"'([^']*)'", assignment):
            assert "（" not in literal and "(" not in literal, (
                f"标题文案含括号：{literal!r}"
            )


def test_frontend_clamps_stale_checkout_time_on_submit() -> None:
    """提交前必须处理已过期的签退时间。

    回归背景（实测）：弹窗打开时控件被填入"当时"的时间，过了约 9 分钟才点确认，
    该时间已成未来，提交被服务端拒绝：
        「当前约 03:36:59，你填的是 03:45:37」
    前端在提交时刷新 max 并纠正过期值，服务端再收敛一次做兜底。
    """
    manager_js = (STATIC_DIR / "manager.js").read_text(encoding="utf-8")
    handler = manager_js[manager_js.find("confirmCheckoutBtn"):]

    assert "customEl.max = nowValue" in handler, "提交时未重新同步时间上限"
    assert "customEl.value > nowValue" in handler, (
        "提交时未纠正已过期的签退时间（会撞上服务端校验）"
    )


def test_datetime_input_disables_autocomplete() -> None:
    """签退时间控件必须关闭自动填充。

    **关键回归**：Chrome 的"表单值恢复"会把上一次的值回填进
    ``datetime-local`` 控件，甚至覆盖脚本刚赋的默认值。
    实测后果是"签退后账单全是 0"—— 控件里留着上一次会话的旧时刻，
    用户直接点确认，服务端就按那个旧时刻结算（日志里计费秒数=3 / 11）。
    """
    html = (STATIC_DIR / "manager.html").read_text(encoding="utf-8")
    match = re.search(r"<input[^>]*id=\"checkoutCustomTime\"[^>]*>", html)
    assert match, "未找到签退时间控件"
    assert 'autocomplete="off"' in match.group(0), (
        "签退时间控件缺少 autocomplete=off，浏览器会回填旧值"
    )


def test_default_checkout_time_is_not_sent_to_server() -> None:
    """未手动改过签退时间时，**不应**把该字段传给服务端。

    这样"现在签退"就完全由服务端用当前时间处理 ——
    既不受浏览器回填旧值影响，也不受本机与服务器时钟差影响。
    用户手动改过时仍要完整支持（事后补签退）。
    """
    manager_js = (STATIC_DIR / "manager.js").read_text(encoding="utf-8")
    handler = manager_js[manager_js.find("confirmCheckoutBtn"):]

    assert "dataset.edited === '1'" in handler, (
        "提交逻辑未检查「用户是否手动改过」，可能把浏览器回填的旧值发给服务端"
    )
    assert "if (checkoutTime)" in handler, "未对 checkout_time 做条件性发送"

    # 打开弹窗时必须先清空再赋值（否则回填的旧值会被保留）
    opener = manager_js[manager_js.find("function showCheckoutModal"):]
    assert "customEl.value = '';" in opener, (
        "打开弹窗时未先清空签退时间控件，Chrome 回填的旧值会残留"
    )


def test_editing_checkout_time_marks_it_as_user_set() -> None:
    """用户改动签退时间（输入或选择）时必须标记为"用户已指定"。"""
    manager_js = (STATIC_DIR / "manager.js").read_text(encoding="utf-8")
    opener = manager_js[manager_js.find("function showCheckoutModal"):]

    assert "dataset.edited = '0'" in opener, "打开弹窗时未重置「用户已改」标记"
    assert "dataset.edited = '1'" in opener, "输入变化时未标记「用户已改」"
    assert "customEl.oninput" in opener and "customEl.onchange" in opener, (
        "必须同时监听 input 与 change（不同交互方式触发的事件不同）"
    )


def test_payment_placeholder_page_exists() -> None:
    """支付链接指向的占位页必须存在，且写明「开发中」。"""
    page = STATIC_DIR / "pay-dev.html"
    assert page.exists(), "缺少支付占位页 pay-dev.html"

    text = page.read_text(encoding="utf-8")
    assert "开发中" in text, "占位页必须写明「开发中」"

    manager_js = (STATIC_DIR / "manager.js").read_text(encoding="utf-8")
    assert "pay-dev.html" in manager_js, "支付弹窗必须把链接指向 pay-dev.html"


def test_payment_placeholder_page_has_no_css_or_script() -> None:
    """占位页按需求**不加任何样式**，只有「开发中」三个字。

    要求原文：「开发中的那个二维码和 html 不要加 css，直接写三个字开发中就行了」。
    """
    page = STATIC_DIR / "pay-dev.html"
    text = page.read_text(encoding="utf-8")

    assert "<style" not in text, "占位页不应包含 <style>"
    assert "style=" not in text, "占位页不应包含内联 style 属性"
    assert "<script" not in text, "占位页不应包含脚本"
    assert "class=" not in text, "占位页不应引用 CSS 类"

    # 正文里必须直接出现「开发中」
    body = text[text.find("<body>"):text.find("</body>")]
    assert "开发中" in body, "占位页正文应直接写「开发中」"

    # 应当非常精简（没有样式也就没有多余内容）
    assert len(text) < 400, f"占位页过于臃肿（{len(text)} 字符），可能又加回了样式"


def test_qrcode_placeholder_is_plain_text_only() -> None:
    """二维码占位图只写「开发中」三个字，不加任何装饰。

    要求原文同上。这里检查生成的 SVG **只有一行文字**：
    不得有矩形、边框、配色、说明文字等装饰元素。
    """
    manager_js = (STATIC_DIR / "manager.js").read_text(encoding="utf-8")

    match = re.search(r"function buildPlaceholderQrSvg[\s\S]*?\n\}", manager_js)
    assert match, "未找到二维码占位图生成函数"
    body = match.group(0)

    # 必须包含「开发中」
    assert "开发中" in body, "二维码占位内容必须包含「开发中」"

    # 不得有任何装饰元素
    for forbidden in ("<rect", "<circle", "<path", "<line", "stroke", "fill=", "font-weight"):
        assert forbidden not in body, f"二维码占位图不应包含装饰元素：{forbidden}"

    # 只允许一个 <text> 元素
    assert body.count("<text") == 1, "二维码占位图应只有一行文字"
    assert "支付通道未接入" not in body, "不应再附加说明文字"


def test_pay_modal_has_no_extra_caption_under_qrcode() -> None:
    """支付弹窗里二维码下方不应再有额外的说明文案。"""
    html = (STATIC_DIR / "manager.html").read_text(encoding="utf-8")
    assert "业务开发中" not in html, "不应再显示「二维码内容为占位（业务开发中）」这类说明"
    assert "支付通道未接入" not in html, "不应再显示「支付通道未接入」这类说明"


def test_qrcode_placeholder_says_under_development() -> None:
    """二维码内容为占位，必须写明「开发中」。"""
    manager_js = (STATIC_DIR / "manager.js").read_text(encoding="utf-8")
    assert "buildPlaceholderQrSvg" in manager_js, "缺少二维码占位图生成函数"
    # 占位图里必须出现「开发中」文案
    match = re.search(r"function buildPlaceholderQrSvg[\s\S]*?\n\}", manager_js)
    assert match, "未找到 buildPlaceholderQrSvg 实现"
    assert "开发中" in match.group(0), "二维码占位内容必须包含「开发中」"


def test_primary_and_danger_buttons_are_defined() -> None:
    """``.btn-primary`` / ``.btn-danger`` 被 HTML 引用，必须有定义。

    ``.btn-primary`` 此前在 manager.css 中缺失，导致弹窗里的主按钮没有配色。
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


# --------------------------------------------------------------------------- #
# emoji
# --------------------------------------------------------------------------- #


def _strip_comments(text: str, suffix: str) -> str:
    """去掉注释，只保留会真正渲染/执行的代码。

    注释里提到某个字符是允许的（例如「不再使用 U+25CF」这样的说明），
    但那不算界面里出现了 emoji。
    """
    if suffix == ".js":
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        text = re.sub(r"(?m)^\s*//.*$", "", text)
        return text
    if suffix == ".css":
        return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    if suffix in (".html", ".htm"):
        return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return text


def _read_ui_source(name: str) -> str:
    path = STATIC_DIR / name
    assert path.exists(), f"界面文件不存在：{name}"
    return _strip_comments(path.read_text(encoding="utf-8"), path.suffix)


def test_no_color_glyphs_in_ui_code() -> None:
    """界面代码里不得出现会被渲染成彩色图形的字符。

    需求原文：「把所有 emoji 去掉，太难看了」。

    实测（本项目机器装了 seguiemj.ttf）：
      * 所有 U+1F000 及以上的字符都会渲染成彩色 emoji；
      * U+25CF（●）虽然不是 emoji 码位，但实际渲染为**彩色实心圆点**，
        观感与 emoji 一致 —— 已用 Chrome 截图确认，因此也一并禁止。

    通过的条件是「去掉注释后」不含这些字符 ——
    注释里说明"曾经用过 ●"是允许的。
    """
    problems: list[str] = []

    for name in UI_FILES:
        source = _read_ui_source(name)
        for index, line in enumerate(source.splitlines(), 1):
            for char in line:
                if ord(char) >= 0x1F000 or char in COLOR_GLYPH_CHARS:
                    problems.append(
                        f"{name}:{index} 出现 {char!r} (U+{ord(char):04X})：{line.strip()[:70]}"
                    )

    assert not problems, "界面代码里仍有彩色图形字符（emoji）：\n  " + "\n  ".join(problems)


def test_specific_removed_emoji_are_gone() -> None:
    """点名核对曾经用过的几个 emoji 已全部移除。"""
    for char in REMOVED_EMOJI:
        for name in UI_FILES:
            source = _read_ui_source(name)
            assert char not in source, (
                f"{name} 仍含已被要求移除的 emoji U+{ord(char):04X}"
            )


def test_online_badge_uses_css_dot_not_colored_glyph() -> None:
    """在线状态徽标必须用纯 CSS 圆点，而不是 ● 之类的彩色字符。"""
    js = _read_ui_source("manager.js")
    for cls in ("badge badge-online", "badge badge-away", "badge badge-offline"):
        assert cls in js, f"状态徽标缺少样式类：{cls}"
    assert "badge-dot" in js, "状态徽标缺少 CSS 圆点元素"
    assert "badge-stale" in js, "「需重新注册」标注缺少样式类"

    css = _read_ui_source("manager.css")
    for selector in (
        r"\.badge\s*\{",
        r"\.badge-dot\s*\{",
        r"\.badge-online\s*\{",
        r"\.badge-away\s*\{",
        r"\.badge-offline\s*\{",
        r"\.badge-stale\s*\{",
    ):
        assert re.search(selector, css), f"manager.css 缺少徽标样式：{selector}"


def test_stats_section_is_removed_entirely() -> None:
    """统计卡片整块已按需求删除，只保留标题 div 与人脸列表。

    需求原文：「把显示注册人脸数和在线数的那个div直接删了，只留标题 div和列表」。

    因此 HTML 里不得再有 ``.stats-grid`` / ``.stat-card`` 及其数字元素，
    CSS 里也不得留下没人引用的统计样式（包括媒体查询里的孤儿规则）。
    """
    html = _read_ui_source("manager.html")

    for token in ("stats-grid", "stat-card", "stat-info", "stat-icon",
                  "totalCount", "onlineCount", "已注册人脸", "当前在线"):
        assert token not in html, f"统计区域仍残留：{token}"

    # 「只留标题 div 和列表」—— 这两块必须还在
    assert '<div class="faces-container">' in html, "人脸列表容器丢失"
    assert 'class="faces-header"' in html, "列表标题 div 丢失"
    assert "<h3>人脸列表</h3>" in html, "列表标题文字丢失"
    assert 'id="facesGrid"' in html, "人脸列表网格丢失"
    assert 'id="facesCount"' in html, "列表条数丢失"

    css = _read_ui_source("manager.css")
    for selector in ("stats-grid", "stat-card", "stat-info", "stat-value", "stat-label"):
        assert selector not in css, f"manager.css 仍残留孤儿统计样式：{selector}"


def test_removed_stats_ids_are_accessed_safely() -> None:
    """被删除元素的 id 不得再被无条件访问。

    回归风险：``manager.js`` 原先是
    ``document.getElementById('totalCount').innerText = ...``，
    元素一旦删除就会因 null 抛错，并且**中断后面的渲染逻辑**
    （表现为列表一直显示"加载中…"）。
    """
    js = _read_ui_source("manager.js")

    # 用正则找出对这些 id 的直接点号访问（没有 if 包裹、也没有可选链）
    for element_id in ("totalCount", "onlineCount"):
        dangerous = re.findall(
            r"getElementById\(['\"]" + element_id + r"['\"]\)\s*\.\s*\w+",
            js,
        )
        assert not dangerous, (
            f"仍有无保护的 getElementById('{element_id}').… 调用：{dangerous}"
        )

    # 必须走安全包装
    assert "function setTextIfPresent" in js, "缺少 setTextIfPresent 安全包装"


def test_face_card_avatar_is_blank() -> None:
    """人脸卡片的紫色头像已删除，换成空白。

    需求原文：「把那个紫色头像给删了，直接换成空白」。

    原先那个紫色渐变圆形头像（``.face-avatar`` + 名字首字）不应再存在，
    也不能留下它的样式或取首字的死代码。
    """
    js = _read_ui_source("manager.js")
    assert "face-avatar-blank" in js, "头像位置未改为空白占位"
    assert "avatar-initial" not in js, "仍在使用已删除的头像首字类"
    assert "avatarInitial" not in js, "仍残留取名字首字的死代码"

    css = _read_ui_source("manager.css")
    assert ".face-avatar-blank" in css, "manager.css 缺少空白占位样式"
    # 紫色圆形头像的规则必须删掉
    assert not re.search(r"\.face-avatar\s*\{", css), "CSS 里仍残留 .face-avatar 规则"
    assert not re.search(r"\.avatar-initial\s*\{", css), "CSS 里仍残留 .avatar-initial 规则"

    # 空格缩进改由 .face-details 承担，保证两行文字对齐
    match = re.search(r"\.face-details\s*\{([^}]*)\}", css)
    assert match, "未找到 .face-details 规则"
    assert "padding-left" in match.group(1), (
        "头像删除后未用 padding-left 保持两行文字对齐"
    )


def test_sidebar_logo_gradient_survives_avatar_removal() -> None:
    """删头像时不要误伤侧边栏 logo —— 它也用同一个紫色渐变。

    这是一条"反向"保护：删除与头像共享配色的规则时，
    很容易连 logo 的样式一起删掉。
    """
    css = _read_ui_source("manager.css")
    for selector in (r"\.logo-icon\s*\{", r"\.logo-text\s*\{"):
        assert re.search(selector, css), f"侧边栏 logo 样式被误删：{selector}"
    # 渐变配色确实还在（logo 在用）
    assert "#4a90e2 0%, #f06292" in css, "侧边栏 logo 的渐变配色被误删"

