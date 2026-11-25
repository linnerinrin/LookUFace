"""死功能守门：控制台、计费收款、性别年龄都不该再出现在仓库里。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"
STATIC_DIR = settings.STATIC_DIR
MODEL_DIR = settings.MODEL_DIR

CODE_SUFFIXES = {".py", ".js", ".html", ".css"}
SKIP_DIRS = {"__pycache__"}


def _iter_code():
    for path in APP_DIR.rglob("*"):
        if not path.is_file() or path.suffix not in CODE_SUFFIXES:
            continue
        if SKIP_DIRS & set(path.parts):
            continue
        yield path


def _hits(needles: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for path in _iter_code():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in needles:
            if needle in text:
                found.append(f"{path.relative_to(PROJECT_ROOT)}: {needle}")
    return found


# --------------------------------------------------------------------------- #
# 控制台
# --------------------------------------------------------------------------- #


def test_console_files_are_gone():
    for name in ("console.html", "console.js"):
        assert not (STATIC_DIR / name).exists(), f"{name} 仍存在"


def test_logs_route_is_gone():
    assert _hits(("/logs/recent", "/logs/frontend", "/logs/files", "/logs/level")) == []


def test_log_broadcaster_is_gone():
    assert _hits(("LogBroadcaster", "get_broadcaster", "/ws/logs")) == []


def test_custom_logger_module_is_gone():
    assert not (APP_DIR / "logger.py").exists(), "app/logger.py 仍存在"
    assert _hits(("app.logger", "app/logger", "setup_logging", "daily_log_file")) == []


def test_no_log_level_or_log_dir_config():
    assert _hits(("LOG_LEVEL", "LOG_DIR")) == []


def test_no_console_link_in_pages():
    for html in STATIC_DIR.glob("*.html"):
        assert "console.html" not in html.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 计费 / 收款
# --------------------------------------------------------------------------- #


def test_billing_modules_are_gone():
    for rel in (
        "api/routes/billing.py",
        "services/qrcode_service.py",
        "domain/registry.py",
    ):
        assert not (APP_DIR / rel).exists(), f"app/{rel} 仍存在"


def test_payment_page_is_gone():
    assert not (STATIC_DIR / "pay-dev.html").exists()


def test_no_billing_symbols_left():
    assert _hits(
        (
            "BILLING_RATE",
            "rate_per_minute",
            "MAX_SESSION_MINUTES",
            "max_session_minutes",
            "buildPlaceholderQrSvg",
            "settle_expired",
        )
    ) == []


def test_no_money_wording_in_ui():
    for html in STATIC_DIR.glob("*.html"):
        text = html.read_text(encoding="utf-8")
        for word in ("金额", "计费", "账单", "收款", "元。</strong>"):
            assert word not in text, f"{html.name} 仍含「{word}」"


def test_settle_fields_absent_from_session_schema():
    from app.api.schemas import CheckOutResponse, SessionStatusResponse

    assert "amount" not in CheckOutResponse.model_fields
    assert "rate_per_minute" not in CheckOutResponse.model_fields
    assert "is_away" not in SessionStatusResponse.model_fields


# --------------------------------------------------------------------------- #
# 性别 / 年龄
# --------------------------------------------------------------------------- #


def test_demographics_module_is_gone():
    assert not (APP_DIR / "vision" / "demographics.py").exists()


@pytest.mark.parametrize(
    "weight",
    [
        "age_deploy.prototxt",
        "age_net.caffemodel",
        "gender_deploy.prototxt",
        "gender_net.caffemodel",
    ],
)
def test_demographics_weights_are_gone(weight):
    assert not (MODEL_DIR / weight).exists(), f"{weight} 仍存在"


def test_face_weights_are_present():
    for weight in ("opencv_face_detector.pbtxt", "opencv_face_detector_uint8.pb"):
        assert (MODEL_DIR / weight).exists(), f"缺少 {weight}"


def test_no_gender_age_symbols_left():
    assert _hits(
        (
            "detect_gender_age",
            "GenderAgeModel",
            "get_gender_age_model",
            "age_net.caffemodel",
            "gender_net.caffemodel",
        )
    ) == []


def test_frontend_does_not_render_demographics():
    for js in STATIC_DIR.glob("*.js"):
        text = js.read_text(encoding="utf-8")
        for word in ("face.gender", "face.age", "性别", "年龄"):
            assert word not in text, f"{js.name} 仍渲染「{word}」"


def test_required_model_files_only_covers_face_detector():
    assert set(settings.required_model_files()) == {"face_proto", "face_model"}
