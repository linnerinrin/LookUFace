"""统一的 API 请求/响应模型。

命名一致性（修复旧版三套风格混用的问题）
----------------------------------------
旧版同一层 API 里同时存在 ``/face/list_with_screenshots``（下划线）、
``/check/checkin``（无分隔）、``/auth/change-password``（连字符）三种风格，
请求体字段也是 ``old_name`` 与 ``is_online`` 混用。

现在统一为：

* **路径**：kebab-case，资源式（``/auth/change-password``、``/faces``、``/sessions/checkin``）
* **JSON 字段**：snake_case（与 Python 一致）
* 所有错误响应统一为 ``{detail, code, request_id, details?}``
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# --------------------------------------------------------------------------- #
# 通用
# --------------------------------------------------------------------------- #


class ErrorResponse(BaseModel):
    """统一错误结构。"""

    detail: str
    code: str = "error"
    request_id: str | None = None
    details: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    status: Literal["healthy", "degraded"]
    version: str
    database: bool
    models: dict[str, bool]
    models_loaded: bool


# --------------------------------------------------------------------------- #
# 认证
# --------------------------------------------------------------------------- #


class UserProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    nickname: str | None = None
    avatar: str | None = None
    bio: str | None = None
    face_identity: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class SendCodeRequest(BaseModel):
    email: EmailStr
    type: Literal["register", "reset"] = "register"


class SendCodeResponse(BaseModel):
    success: bool = True
    message: str = "验证码已发送"


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    code: str = Field(min_length=6, max_length=6)
    nickname: str | None = Field(default=None, max_length=50)

    @field_validator("code")
    @classmethod
    def _digits_only(cls, value: str) -> str:
        value = value.strip()
        if not value.isdigit():
            raise ValueError("验证码必须是 6 位数字")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    success: bool = True
    token: str
    user: UserProfile


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)
    new_password: str = Field(min_length=6, max_length=128)


class SimpleMessageResponse(BaseModel):
    success: bool = True
    message: str


class UpdateProfileRequest(BaseModel):
    nickname: str | None = Field(default=None, max_length=50)
    bio: str | None = Field(default=None, max_length=500)


class UpdateProfileResponse(BaseModel):
    success: bool = True
    user: UserProfile


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


# --------------------------------------------------------------------------- #
# 人脸
# --------------------------------------------------------------------------- #


class FaceItem(BaseModel):
    name: str
    screenshot: str | None = None
    register_time: datetime | None = None
    online_time: int = 0
    is_online: bool = False
    is_away: bool = False
    checkin_time: datetime | None = None
    #: 特征是否为当前算法版本（False 表示需要重新注册）
    features_current: bool = True


class FacesListResponse(BaseModel):
    faces: list[FaceItem]


class FaceRegisterResponse(BaseModel):
    success: bool
    name: str
    screenshot: str | None = None
    message: str


class FaceRenameRequest(BaseModel):
    old_name: str = Field(min_length=1, max_length=100)
    new_name: str = Field(min_length=1, max_length=100)
    register_time: datetime | None = None


class FaceDeleteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class FaceDeleteResponse(BaseModel):
    success: bool
    name: str
    message: str


class FaceScreenshotRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class FaceModifyResponse(BaseModel):
    success: bool
    message: str


# --------------------------------------------------------------------------- #
# 会话 / 计费
# --------------------------------------------------------------------------- #


class CheckInRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class CheckOutRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    away: bool = False
    #: 自定义签退时间（可选）。缺省表示「就是现在」。
    #:
    #: 使用场景：用户已经离开、事后在仓库管理页补签退 —— 此时应按**真实离开时间**
    #: 结算，而不是按「管理员几点点的按钮」结算。
    #:
    #: 接受 ISO8601（``2026-10-16T03:20``）或 ``YYYY-MM-DD HH:MM[:SS]``，
    #: 也接受 ``datetime-local`` 控件的取值。``Z`` 后缀按 UTC 解释。
    #:
    #: 注意：暂离（``away=true``）不使用该字段 —— 暂离不结算。
    checkout_time: datetime | None = None


class SessionStatusRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class SessionStatusResponse(BaseModel):
    name: str
    is_online: bool
    is_away: bool
    checkin_time: datetime | None = None
    #: 前端展示用的实时预估分钟数（结算以服务端签退结果为准）
    preview_minutes: int = 0
    online_time: int = 0
    away_timeout_minutes: int = 0
    #: 服务端当前时刻。前端用它设置「签退时间」的上限 ——
    #: 不依赖浏览器本地时钟（两者可能有偏差，会误判"未来时间"）。
    server_now: datetime | None = None
    #: 计费算式明细，便于排查「为什么是 0 分钟」
    elapsed_minutes: int = 0
    #: 签到至今精确经过的秒数（不扣暂离）
    elapsed_seconds: int = 0
    away_minutes: int = 0
    rate_per_minute: float = 0.0
    amount: float = 0.0


class CheckInResponse(BaseModel):
    success: bool
    name: str
    message: str
    is_online: bool = False
    is_away: bool = False
    deduplicated: bool = False


class CheckOutResponse(BaseModel):
    success: bool
    name: str
    message: str
    session_minutes: int = 0
    total_minutes: int = 0
    amount: float = 0.0
    is_online: bool = False
    is_away: bool = False
    #: 本次动作是「暂离」还是「签退结算」。
    #: 前端必须据此区分提示文案 —— 暂离不结算，此时 session_minutes/amount 必然为 0。
    is_away_action: bool = False
    #: 是否真的发生了结算（暂离、重复请求都不会结算）
    settled: bool = False
    #: 计费算式明细，便于排查「为什么是 0 分钟」
    checkin_time: datetime | None = None
    #: 计算实际使用的结算时刻。用户可能自定义签退时间，服务端也可能把
    #: 未来时刻收敛为当前时刻，因此必须回传真实使用的值 —— 前端据此把
    #: 「签到 -> 签退 -> 计费」的时间账原样展示，用户可自行核对。
    checkout_time: datetime | None = None
    elapsed_minutes: int = 0
    #: 签到到签退**精确经过的秒数**（不扣暂离）。
    #: 用来解释「不足 1 分钟为何是 0」—— 例如实际只过了 7 秒。
    elapsed_seconds: int = 0
    away_minutes: int = 0
    rate_per_minute: float = 0.0


class BillingQuoteRequest(BaseModel):
    """计费报价请求。

    注意：**不接受前端传入的时长**。分钟数由服务端按当前会话状态计算，
    避免旧版 ``online_time`` 由客户端任意指定导致的免单/超收。
    """

    name: str = Field(min_length=1, max_length=100)


class BillingQuoteResponse(BaseModel):
    name: str
    minutes: int
    amount: float
    rate_per_minute: float
    #: 二维码与支付链接。金额为 0 或未签到时**故意留空** ——
    #: 此时没有应付金额，不应该给出付款入口。
    qrcode_url: str = ""
    pay_url: str = ""
    #: 是否存在应付金额（前端据此决定是否显示二维码/支付入口）
    payable: bool = False
    #: 是否正在计时（未计时则谈不上"在场时间"）
    is_online: bool = False
    is_away: bool = False
    checkin_time: datetime | None = None
    #: 计费算式明细：已流逝时间、暂离时间、实际计费分钟数
    elapsed_minutes: int = 0
    away_minutes: int = 0
    #: 展示用文案（例如「本次在场 1 小时 5 分钟（其中暂离 20 分钟），应付 22.50 元」）
    summary: str = ""


# --------------------------------------------------------------------------- #
# 分析（一次性上传分析，供调试与第三方集成）
# --------------------------------------------------------------------------- #


class FaceInfo(BaseModel):
    bbox: list[int]
    gender: str = "Unknown"
    age: str = "Unknown"
    confidence: float = 0.0
    identity: str = "Unknown"
    identity_confidence: float = 0.0
    detection_confidence: float = 0.0


class DetectResponse(BaseModel):
    request_id: str
    faces: list[FaceInfo]
    face_count: int
    processing_ms: float
    timestamp: datetime


# --------------------------------------------------------------------------- #
# 日志
# --------------------------------------------------------------------------- #


class FrontendLogRequest(BaseModel):
    level: Literal["info", "warn", "error"] = "info"
    message: str = Field(max_length=2000)
    page: str = Field(default="unknown", max_length=100)


class FrontendLogResponse(BaseModel):
    ok: bool = True


class GetLogsRequest(BaseModel):
    lines: int = Field(default=100, ge=1, le=2000)
    level: Literal["all", "info", "warn", "error", "debug", "critical"] = "all"
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class GetLogsResponse(BaseModel):
    logs: list[str]
    file: str | None = None
    total: int = 0


# --------------------------------------------------------------------------- #
# WebSocket 消息
# --------------------------------------------------------------------------- #


class WsFaceMessage(BaseModel):
    success: bool = True
    face_count: int
    faces: list[FaceInfo]
    processing_ms: float


class WsErrorMessage(BaseModel):
    success: bool = False
    detail: str
    code: str = "error"


__all__ = [name for name in dir() if name.endswith(("Request", "Response", "Item", "Message"))]
