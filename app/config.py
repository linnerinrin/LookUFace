"""
统一配置管理
- 模型路径配置
- 服务配置（主机/端口）
- 文件大小限制等
"""

from pydantic_settings import BaseSettings
from pydantic import ConfigDict
from pathlib import Path


class Settings(BaseSettings):
    """应用配置类"""
    model_config = ConfigDict(env_file=".env", extra="ignore")

    # 服务配置
    APP_NAME: str = "CV Face Analysis Service"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # 路径配置
    BASE_DIR: Path = Path(__file__).parent.parent  # detector/
    MODEL_DIR: Path = BASE_DIR / "models"          # 预训练模型目录

    # 模型文件名
    DLIB_LANDMARK: str = "shape_predictor_68_face_landmarks.dat"
    FACE_PROTO: str = "opencv_face_detector.pbtxt"
    FACE_MODEL: str = "opencv_face_detector_uint8.pb"
    AGE_PROTO: str = "deploy_age.prototxt"
    AGE_MODEL: str = "age_net.caffemodel"
    GENDER_PROTO: str = "deploy_gender.prototxt"
    GENDER_MODEL: str = "gender_net.caffemodel"

    # 检测参数
    FACE_CONF_THRESHOLD: float = 0.7
    LAUGH_MAR_THRESHOLD: float = 0.5
    SMILE_MJR_THRESHOLD: float = 0.45

    # 并发配置
    MAX_WORKERS: int = 4

    # 存储配置
    UPLOAD_DIR: Path = BASE_DIR / "uploads"
    STORAGE_DIR: Path = BASE_DIR / "storage"
    MAX_FILE_SIZE: int = 10 * 1024 * 1024  # 10MB

    # 数据库配置
    DATABASE_URL: str = "sqlite:///./face_lens.db"

    @property
    def dlib_path(self) -> Path:
        return self.MODEL_DIR / self.DLIB_LANDMARK

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


settings = Settings()