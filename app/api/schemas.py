"""
Pydantic 数据模型定义
- 人脸信息（FaceInfo）
- 检测响应（DetectResponse）
- 健康检查响应（HealthResponse）
- 任务状态（TaskStatus）
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class FaceInfo(BaseModel):
    """单个人脸信息"""
    bbox: List[int]                    # 人脸边界框 [x1, y1, x2, y2]
    gender: str = "Unknown"            # 性别
    age: str = "Unknown"               # 年龄段
    confidence: float = 0.0            # 置信度
    identity: str = "Unknown"          # 识别的身份
    identity_confidence: float = 0.0   # 身份识别置信度


class DetectResponse(BaseModel):
    """人脸检测响应"""
    request_id: str
    faces: List[FaceInfo]
    face_count: int
    processing_ms: float
    timestamp: datetime


class TaskStatus(BaseModel):
    """异步任务状态"""
    task_id: str
    status: str                        # pending, processing, completed, failed
    progress: int = 0
    result: Optional[DetectResponse] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str
    version: str
    models_loaded: bool
    registered_faces: int = 0


class RegisterFaceRequest(BaseModel):
    """人脸注册请求"""
    name: str


class RegisterFaceResponse(BaseModel):
    """人脸注册响应"""
    success: bool
    name: str
    message: str


class DetectRequest(BaseModel):
    """Base64 图片检测请求"""
    image_base64: Optional[str] = None
    detect_gender_age: bool = Field(default=True)