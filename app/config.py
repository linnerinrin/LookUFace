"""应用配置。

设计要点（相对旧版 ``app/config.py`` 的修复）：

1. ``env_file`` 使用**绝对路径**，无论从哪个工作目录启动都能读到 ``.env``。
   旧版 ``env_file=".env"`` 是相对路径，从项目根目录之外启动会直接抛
   ``ValidationError: 17 validation errors``。
2. 所有非敏感字段都带**默认值**，缺少 ``.env`` 也不会崩；只有凭据类字段
   （``SECRET_KEY``）强制要求，并在校验器里拒绝弱值。
3. SQLite 相对路径统一解析到项目根目录，保证数据库文件位置可预期。
4. 业务参数（去抖时长、暂离超时、计费单价、单次计费上限）进入配置，
   不再散落在代码里的魔法数（旧版 ``rate_per_minute = 0.5`` 写在函数体内）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# app/config.py -> app/ -> 项目根目录
BASE_DIR: Path = Path(__file__).resolve().parent.parent

# 弱密钥黑名单：旧版 .env 里的硬编码值必须被拒绝
_INSECURE_SECRET_MARKERS = (
    "lookuface-sk-",
    "changeme",
    "secret",
    "your-secret",
)
_MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """全局配置。字段名即环境变量名（大小写不敏感）。"""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------- 应用 ----------------
    APP_NAME: str = "CV Face Analysis Service"
    APP_VERSION: str = "1.1.0"
    DEBUG: bool = False
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    # CORS：默认仅本机，避免旧版默认 "*" + credentials 的不安全组合
    ALLOWED_ORIGINS: str = "http://127.0.0.1:8000,http://localhost:8000"

    # ---------------- 路径 ----------------
    BASE_DIR: Path = BASE_DIR
    MODEL_DIR: Path = BASE_DIR / "models"
    LOG_DIR: Path = BASE_DIR / "logs"
    UPLOAD_DIR: Path = BASE_DIR / "uploads"
    STORAGE_DIR: Path = BASE_DIR / "storage"
    STATIC_DIR: Path = BASE_DIR / "app" / "static"

    # ---------------- 模型文件 ----------------
    FACE_PROTO: str = "opencv_face_detector.pbtxt"
    FACE_MODEL: str = "opencv_face_detector_uint8.pb"
    AGE_PROTO: str = "age_deploy.prototxt"
    AGE_MODEL: str = "age_net.caffemodel"
    GENDER_PROTO: str = "gender_deploy.prototxt"
    GENDER_MODEL: str = "gender_net.caffemodel"

    # ---------------- 数据库 ----------------
    DATABASE_URL: str = "sqlite:///./storage/face_lens.db"

    # ---------------- 认证 ----------------
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=10080, gt=0)
    EMAIL_CODE_TTL_MINUTES: int = Field(default=10, gt=0, le=60)
    EMAIL_CODE_CLEANUP_INTERVAL_SECONDS: int = Field(default=3600, gt=0)

    # ---------------- 邮件 ----------------
    SMTP_HOST: str = "smtp.qq.com"
    SMTP_PORT: int = Field(default=587, gt=0, lt=65536)
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_ENABLED: bool = True

    # ---------------- 视觉阈值 ----------------
    # OpenCV DNN 人脸检测置信度。旧版 0.925 过高导致大量漏检，默认回到 0.5
    FACE_CONF_THRESHOLD: float = Field(default=0.5, gt=0, le=1)
    # 余弦相似度阈值
    RECOGNIZE_THRESHOLD: float = Field(default=0.75, gt=0, le=1)
    DETECTION_CONFIDENCE: float = Field(default=0.5, gt=0, le=1)
    TRACKING_CONFIDENCE: float = Field(default=0.5, gt=0, le=1)
    # 人脸框小于该边长（像素）时跳过性别年龄/识别，避免无意义计算
    MIN_FACE_SIZE: int = Field(default=50, gt=0)

    # ---------------- 并发 ----------------
    MAX_WORKERS: int = Field(default=4, gt=0, le=32)
    # 视频流背压：客户端未回包时最多缓冲的帧数，超出则丢帧而不是排队
    MAX_PENDING_FRAMES: int = Field(default=1, ge=0, le=16)

    # ---------------- 业务/计费 ----------------
    MAX_FILE_SIZE: int = Field(default=10 * 1024 * 1024, gt=0)
    # 同一张脸在该时长内的连续识别只触发一次业务动作（替代旧版帧计数去抖）
    CHECKIN_DEBOUNCE_SECONDS: int = Field(default=5, ge=0, le=300)
    CHECKOUT_DEBOUNCE_SECONDS: int = Field(default=5, ge=0, le=300)
    # 暂离超时：超时自动签退结算
    AWAY_TIMEOUT_MINUTES: int = Field(default=30, gt=0, le=1440)
    # 计费单价（元/分钟）
    BILLING_RATE_PER_MINUTE: float = Field(default=0.5, ge=0)
    # 单次会话最长计费时长，防止异常数据产生天价账单
    MAX_SESSION_MINUTES: int = Field(default=1440, gt=0)

    # ---------------- 校验 ----------------
    @field_validator("SECRET_KEY")
    @classmethod
    def _validate_secret_key(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError(
                "SECRET_KEY 未配置。请在 .env 中设置，"
                '生成方式：python -c "import secrets; print(secrets.token_urlsafe(64))"'
            )
        lowered = value.lower()
        for marker in _INSECURE_SECRET_MARKERS:
            if marker in lowered:
                raise ValueError(
                    f"SECRET_KEY 包含已知弱值标记 {marker!r}，任何知道该值的人都能伪造 JWT。"
                    '请更换为随机值：python -c "import secrets; print(secrets.token_urlsafe(64))"'
                )
        if len(value) < _MIN_SECRET_LENGTH:
            raise ValueError(
                f"SECRET_KEY 长度不足 {_MIN_SECRET_LENGTH}（当前 {len(value)}），"
                "熵过低易被暴力破解。"
            )
        return value

    @field_validator("LOG_LEVEL")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
        upper = (value or "INFO").upper()
        if upper not in allowed:
            raise ValueError(f"LOG_LEVEL 必须是 {sorted(allowed)} 之一，当前为 {value!r}")
        return upper

    @model_validator(mode="after")
    def _resolve_paths(self) -> Settings:
        """把相对路径统一解析到项目根目录，并保证运行时目录存在。"""
        self.BASE_DIR = Path(self.BASE_DIR).resolve()

        for name in ("MODEL_DIR", "LOG_DIR", "UPLOAD_DIR", "STORAGE_DIR", "STATIC_DIR"):
            raw = Path(getattr(self, name))
            resolved = raw if raw.is_absolute() else (self.BASE_DIR / raw)
            setattr(self, name, resolved.resolve())

        self.DATABASE_URL = self._resolve_database_url(self.DATABASE_URL)

        # SMTP 未配置完整时静默降级为「控制台打印验证码」，便于本地开发
        if not (self.SMTP_USER and self.SMTP_PASSWORD):
            object.__setattr__(self, "SMTP_ENABLED", False)

        return self

    @staticmethod
    def _resolve_database_url(url: str) -> str:
        """把 sqlite 相对路径锚定到项目根目录。"""
        prefix = "sqlite:///"
        if not url.startswith(prefix):
            return url
        raw_path = url[len(prefix):]
        if raw_path in (":memory:", "") or raw_path.startswith("file:"):
            return url
        path = Path(raw_path)
        if not path.is_absolute():
            path = BASE_DIR / path
        return f"{prefix}{path.resolve().as_posix()}"

    # ---------------- 派生路径 ----------------
    @property
    def face_proto_path(self) -> Path:
        return self.MODEL_DIR / self.FACE_PROTO

    @property
    def face_model_path(self) -> Path:
        return self.MODEL_DIR / self.FACE_MODEL

    @property
    def age_proto_path(self) -> Path:
        return self.MODEL_DIR / self.AGE_PROTO

    @property
    def age_model_path(self) -> Path:
        return self.MODEL_DIR / self.AGE_MODEL

    @property
    def gender_proto_path(self) -> Path:
        return self.MODEL_DIR / self.GENDER_PROTO

    @property
    def gender_model_path(self) -> Path:
        return self.MODEL_DIR / self.GENDER_MODEL

    @property
    def screenshot_dir(self) -> Path:
        return self.STORAGE_DIR / "screenshots"

    @property
    def temp_upload_dir(self) -> Path:
        return self.UPLOAD_DIR / "temp"

    @property
    def qrcode_dir(self) -> Path:
        """计费二维码目录。旧版放在系统临时目录，容器/多实例下不可预期。"""
        return self.STORAGE_DIR / "qrcodes"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def cors_allow_credentials(self) -> bool:
        """通配符来源 + credentials 是无效组合，此时关闭 credentials。"""
        origins = self.allowed_origins_list
        return "*" not in origins

    def required_model_files(self) -> dict[str, Path]:
        """返回启动时需要存在的模型文件，用于一次性预检。"""
        return {
            "face_proto": self.face_proto_path,
            "face_model": self.face_model_path,
            "age_proto": self.age_proto_path,
            "age_model": self.age_model_path,
            "gender_proto": self.gender_proto_path,
            "gender_model": self.gender_model_path,
        }

    def ensure_runtime_dirs(self) -> None:
        for path in (self.LOG_DIR, self.STORAGE_DIR, self.screenshot_dir,
                     self.qrcode_dir, self.UPLOAD_DIR, self.temp_upload_dir):
            path.mkdir(parents=True, exist_ok=True)


def _load_settings() -> Settings:
    """加载配置，把 pydantic 的报错转成人能看懂的一句话。"""
    try:
        return Settings()  # type: ignore[call-arg]
    except Exception as exc:  # pragma: no cover - 启动期路径
        raise SystemExit(
            f"\n[配置错误] 无法加载 {BASE_DIR / '.env'}\n"
            f"{exc}\n\n"
            "提示：复制 .env.example 为 .env 并填写 SECRET_KEY、SMTP_PASSWORD。\n"
            '生成密钥：python -c "import secrets; print(secrets.token_urlsafe(64))"\n'
        ) from exc


settings = _load_settings()

__all__ = ["Settings", "settings", "BASE_DIR"]
