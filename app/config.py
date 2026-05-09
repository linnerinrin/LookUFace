"""
app/config.py
统一配置管理
"""

from pydantic_settings import BaseSettings,SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    """应用配置类"""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore") #加载环境变量

    #api
    APP_NAME: str
    APP_VERSION: str
    DEBUG: bool
    HOST: str
    PORT: int

    #路径
    BASE_DIR: Path = Path(__file__).parent.parent #项目根目录
    MODEL_DIR: Path = BASE_DIR / "models" #模型目录
    FACE_PROTO: str = "opencv_face_detector.pbtxt"
    FACE_MODEL: str = "opencv_face_detector_uint8.pb"
    AGE_PROTO: str = "deploy_age.prototxt"
    AGE_MODEL: str = "age_net.caffemodel"
    GENDER_PROTO: str = "deploy_gender.prototxt"
    GENDER_MODEL: str = "gender_net.caffemodel"

    #并发
    MAX_WORKERS: int = 4

    #存储
    UPLOAD_DIR: Path = BASE_DIR / "uploads"
    STORAGE_DIR: Path = BASE_DIR / "storage"
    MAX_FILE_SIZE: int = 10 * 1024 * 1024  #10MB

    #数据库
    DATABASE_URL: str

    #验证
    SECRET_KEY:str
    ALGORITHM :str
    ACCESS_TOKEN_EXPIRE_MINUTES:int
    SMTP_HOST:str
    SMTP_PORT:int
    SMTP_USER:str
    SMTP_PASSWORD:str

    #数据
    FACE_CONF_THRESHOLD:float
    RECOGNIZE_THRESHOLD:float
    DETECTION_CONFIDENCE:float
    TRACKING_CONFIDENCE:float
    CHECKIN_REQUEST_TIME: int=30
    CHECKOUT_REQUEST_TIME: int=30

    @property #接下来6个都是模型路径
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

    @property #人脸图路径
    def screenshot_dir(self) -> Path:
        return self.STORAGE_DIR / "screenshots"

    @property #临时目录
    def temp_upload_dir(self) -> Path:
        return self.UPLOAD_DIR / "temp"

settings = Settings()