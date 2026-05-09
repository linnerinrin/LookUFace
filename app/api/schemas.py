"""
app/api/schemas.py
数据模型定义
Faceinfo 人脸信息
"""

from pydantic import BaseModel
from typing import List, Dict
from datetime import datetime



class FaceInfo(BaseModel):
    """单个人脸信息"""
    bbox: List[int]
    gender: str = "Unknown"
    age: str = "Unknown"
    confidence: float = 0.0
    identity: str = "Unknown"
    identity_confidence: float = 0.0


class DetectResponse(BaseModel):
    request_id: str
    faces: List[FaceInfo]
    face_count: int
    processing_ms: float
    timestamp: datetime

class HealthRequest(BaseModel):
    # 此数据类不使用 接口不需要请求数据
    ...
class HealthResponse(BaseModel):
    status: str
    version: str
    models_loaded: bool

class FaceRegisterRequest(BaseModel):
    # 此数据类不使用 接口使用multi/file&form接收数据 Pydantic不兼容
    ...
class FaceRegisterResponse(BaseModel):
    success: bool
    name: str
    screenshot: str
    message: str

class FaceListRequest(BaseModel):
    #此数据类不使用 接口不需要请求数据
    ...
class FacesListResponse(BaseModel):
    faces: List[Dict]

class GetScreenshotRequest(BaseModel):
    name: str
class GetScreenshotResponse(BaseModel):
    #此数据类不使用 接口使用FileResponse
    ...

class DeleteFaceRequest(BaseModel):
    name: str
class DeleteFaceResponse(BaseModel):
    success: bool
    name: str

