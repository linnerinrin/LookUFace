# """模块：应用配置"""
# """架构作用：加载环境设置，校验参数并派生资源路径"""
"""应用配置

**安全边界**：系统**没有任何身份校验**，因此 ``HOST`` 默认 ``127.0.0.1``，
只监听本机。仅在完全可信的内网里、并且明确接受「任何人都能读写人脸库与结算
账单」这一后果时，才应改成 ``0.0.0.0``
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# app/config.py -> app/ -> 项目根目录
BASE_DIR: Path = Path(__file__).resolve().parent.parent

#: ``.env`` 的位置。既用于读取配置，也用于「场地设置」页的落盘
#:
#: 可用环境变量 ``LOOKUFACE_ENV_FILE`` 覆盖 —— 测试必须这么做，
#: 否则「保存设置」的往返用例会把开发者的真实 ``.env`` 改掉
ENV_FILE: Path = Path(
    os.environ.get("LOOKUFACE_ENV_FILE", str(BASE_DIR / ".env"))
).expanduser()


class Settings(BaseSettings):
    """全局配置。字段名即环境变量名（大小写不敏感）"""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------- 应用 ----------------
    APP_NAME: str = "CV Face Analysis Service"
    APP_VERSION: str = "1.2.0"
    DEBUG: bool = False
    HOST: str = "127.0.0.1"
    PORT: int = 8835
    LOG_LEVEL: str = "INFO"

    # CORS：默认仅本机。注意：这里必须与PORT保持一致 —— 用
    # http://127.0.0.1:8835打开页面时，页面对同源API的请求会带Origin头；
    # 若允许列表里没有该端口，中间件会按跨源拒绝，浏览器表现为 "Failed to fetch"
    ALLOWED_ORIGINS: str = "http://127.0.0.1:8835,http://localhost:8835"

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

    # ---------------- 视觉阈值 ----------------
    # OpenCV DNN人脸检测置信度，默认0.5
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
    # 同一张脸在该时长内的连续识别只触发一次业务动作
    CHECKIN_DEBOUNCE_SECONDS: int = Field(default=5, ge=0, le=300)
    CHECKOUT_DEBOUNCE_SECONDS: int = Field(default=5, ge=0, le=300)
    # 暂离超时：超时自动签退结算
    AWAY_TIMEOUT_MINUTES: int = Field(default=30, gt=0, le=1440)
    # 计费单价（元/分钟）
    BILLING_RATE_PER_MINUTE: float = Field(default=0.5, ge=0)
    # 单次会话最长计费时长，防止异常数据产生天价账单
    MAX_SESSION_MINUTES: int = Field(default=1440, gt=0)

    # ---------------- 校验 ----------------
    # 校验LOG_LEVEL合法值，非法输入在启动阶段直接报错
    @field_validator("LOG_LEVEL")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
        upper = (value or "INFO").upper()
        if upper not in allowed:
            raise ValueError(f"LOG_LEVEL 必须是 {sorted(allowed)} 之一，当前为 {value!r}")
        return upper

    # 后置校验：解析并创建运行目录，缓存派生路径供属性复用
    @model_validator(mode="after")
    def _resolve_paths(self) -> Settings:
        """把相对路径统一解析到项目根目录，并保证运行时目录存在"""
        self.BASE_DIR = Path(self.BASE_DIR).resolve()

        for name in ("MODEL_DIR", "LOG_DIR", "UPLOAD_DIR", "STORAGE_DIR", "STATIC_DIR"):
            raw = Path(getattr(self, name))
            resolved = raw if raw.is_absolute() else (self.BASE_DIR / raw)
            setattr(self, name, resolved.resolve())

        self.DATABASE_URL = self._resolve_database_url(self.DATABASE_URL)
        return self

    # 把sqlite:/// 相对路径解析为项目根下的绝对路径
    @staticmethod
    def _resolve_database_url(url: str) -> str:
        """把sqlite相对路径锚定到项目根目录"""
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
    # .env文件位置，配置落盘与reload的目标
    @property
    def env_file_path(self) -> Path:
        """正在使用的 ``.env`` 路径（「场地设置」页写回同一个文件）"""
        return ENV_FILE

    # OpenCV人脸检测网络的结构文件路径
    @property
    def face_proto_path(self) -> Path:
        return self.MODEL_DIR / self.FACE_PROTO

    # 人脸检测网络的权重文件路径
    @property
    def face_model_path(self) -> Path:
        return self.MODEL_DIR / self.FACE_MODEL

    # 年龄推理网络的结构文件路径
    @property
    def age_proto_path(self) -> Path:
        return self.MODEL_DIR / self.AGE_PROTO

    # 年龄推理网络的权重文件路径
    @property
    def age_model_path(self) -> Path:
        return self.MODEL_DIR / self.AGE_MODEL

    # 性别推理网络的结构文件路径
    @property
    def gender_proto_path(self) -> Path:
        return self.MODEL_DIR / self.GENDER_PROTO

    # 性别推理网络的权重文件路径
    @property
    def gender_model_path(self) -> Path:
        return self.MODEL_DIR / self.GENDER_MODEL

    # 人脸注册截图目录，由storage写入、路由读取
    @property
    def screenshot_dir(self) -> Path:
        return self.STORAGE_DIR / "screenshots"

    # 上传临时目录，保存解码前的原始字节
    @property
    def temp_upload_dir(self) -> Path:
        return self.UPLOAD_DIR / "temp"

    # 二维码PNG输出目录，每次报价生成新文件
    @property
    def qrcode_dir(self) -> Path:
        """计费二维码目录"""
        return self.STORAGE_DIR / "qrcodes"

    # 解析逗号分隔的CORS允许源并去空
    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    # 通配源与credentials互斥，避免浏览器拒绝该组合
    @property
    def cors_allow_credentials(self) -> bool:
        """通配符来源 + credentials是无效组合，此时关闭credentials"""
        origins = self.allowed_origins_list
        return "*" not in origins

    # 健康预检使用的模型文件清单（名称到路径）
    def required_model_files(self) -> dict[str, Path]:
        """返回启动时需要存在的模型文件，用于一次性预检"""
        return {
            "face_proto": self.face_proto_path,
            "face_model": self.face_model_path,
            "age_proto": self.age_proto_path,
            "age_model": self.age_model_path,
            "gender_proto": self.gender_proto_path,
            "gender_model": self.gender_model_path,
        }

    # 启动时确保运行目录存在，缺失则创建
    def ensure_runtime_dirs(self) -> None:
        for path in (self.LOG_DIR, self.STORAGE_DIR, self.screenshot_dir,
                     self.qrcode_dir, self.UPLOAD_DIR, self.temp_upload_dir):
            path.mkdir(parents=True, exist_ok=True)


# 将环境配置加载失败转换为明确的启动退出信息
def _load_settings() -> Settings:
    """加载配置，把pydantic的报错转成人能看懂的一句话"""
    try:
        return Settings()  # type: ignore[call-arg]
    except Exception as exc:  # pragma: no cover - 启动期路径
        raise SystemExit(
            f"\n[配置错误] 无法加载 {ENV_FILE}\n"
            f"{exc}\n\n"
            "提示：复制 .env.example 为 .env（所有字段都有默认值，通常无需改动）。\n"
        ) from exc


settings = _load_settings()

__all__ = ["Settings", "settings", "BASE_DIR", "ENV_FILE"]
