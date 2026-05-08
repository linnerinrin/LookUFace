"""
app/check/schemas.py
数据模型定义
"""
from typing import Optional

from pydantic import BaseModel


class CheckInRequest(BaseModel):
    name: str
    request_times: int
class CheckInResponse(BaseModel):
    success: bool
    name: str
    message: str
    response_times: int

class CheckOutRequest(BaseModel):
    away: bool
    away_time: Optional[int] = None
    name: str
class CheckOutResponse(BaseModel):
    success: bool
    name: str
    message: str
    online_time: int

class PutQRcodeRequest(BaseModel):
    name: str
    online_time:int

class PutQRcodeResponse(BaseModel):
    #此接口使用FileResponse 不使用此数据类
    ...


