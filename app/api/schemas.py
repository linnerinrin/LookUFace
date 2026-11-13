# """模块：API请求与响应契约"""
# """架构作用：以Pydantic校验输入字段并序列化业务输出"""
"""统一的API请求/响应模型

* **路径**：kebab-case，资源式（``/faces``、``/sessions/checkin``、``/billing/quote``）
* **JSON字段**：snake_case（与Python一致）
* 所有错误响应统一为 ``{detail, code, request_id, details?}``
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# 通用
# --------------------------------------------------------------------------- #


# 错误响应契约：detail/code/request_id/details与全局异常处理器输出一致
class ErrorResponse(BaseModel):
    """统一错误结构"""

    detail: str
    code: str = "error"
    request_id: str | None = None
    details: dict[str, Any] | None = None


# 健康检查契约：database为SELECT 1结果；models/models_loaded来自预检的文件存在性
class HealthResponse(BaseModel):
    status: Literal["healthy", "degraded"]
    version: str
    database: bool
    models: dict[str, bool]
    models_loaded: bool


# --------------------------------------------------------------------------- #
# 人脸
# --------------------------------------------------------------------------- #


# 人脸列表单项：持久字段（姓名/截图/注册时间/会话状态）加特征版本标记
class FaceItem(BaseModel):
    name: str
    screenshot: str | None = None
    register_time: datetime | None = None
    online_time: int = 0
    is_online: bool = False
    is_away: bool = False
    checkin_time: datetime | None = None
    #: 特征是否为当前算法版本（False表示需要重新注册）
    features_current: bool = True


# 列表响应：faces为全部人脸记录，单场地数据量有限，不分页
class FacesListResponse(BaseModel):
    faces: list[FaceItem]


# 注册响应：回传姓名与可访问截图路径，message供前端提示
class FaceRegisterResponse(BaseModel):
    success: bool
    name: str
    screenshot: str | None = None
    message: str


# 改名请求：新旧姓名限长1-100，register_time可选用于修正注册时间
class FaceRenameRequest(BaseModel):
    old_name: str = Field(min_length=1, max_length=100)
    new_name: str = Field(min_length=1, max_length=100)
    register_time: datetime | None = None


# 删除请求：按姓名定位记录，截图与运行态由路由清理
class FaceDeleteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


# 删除响应：success/message供前端提示，name用于确认
class FaceDeleteResponse(BaseModel):
    success: bool
    name: str
    message: str


# 截图查询请求：按姓名定位人脸记录
class FaceScreenshotRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


# 通用修改响应：仅返回成功标记与提示文案
class FaceModifyResponse(BaseModel):
    success: bool
    message: str


# --------------------------------------------------------------------------- #
# 会话/计费
# --------------------------------------------------------------------------- #


# 签到请求：只接受姓名，不接受客户端计时，起点由服务端写入
class CheckInRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


# 签退请求：away=true表示暂离；checkout_time/checkin_time/duration_minutes为补录参数，冲突时由服务解析
class CheckOutRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    away: bool = False
    #: 自定义签退时间（可选）。缺省表示「就是现在」
    #:
    #: 使用场景：用户已经离开、事后在仓库管理页补签退 —— 此时应按**真实离开时间**
    #: 结算，而不是按「管理员几点点的按钮」结算
    #:
    #: 接受ISO8601（``2026-10-16T03:20``）或 ``YYYY-MM-DD HH:MM[:SS]``，
    #: 也接受 ``datetime-local`` 控件的取值。``Z`` 后缀按UTC解释
    #:
    #: 注意：暂离（``away=true``）不使用该字段 —— 暂离不结算
    checkout_time: datetime | None = None
    #: 修正本次的**签到时刻**（可选）。缺省表示不改动现有计费起点
    #: 一般不需要用 —— 前端用 ``duration_minutes`` 更方便
    checkin_time: datetime | None = None
    #: **在场时长（分钟）**（可选）。这是补录时首选的方式
    #:
    #: 服务端会把签到时间顺推为 ``结算时刻 - duration_minutes``，
    #: 因此「填600就是10小时」永远算得出结果，
    #: 不会出现"填了未来时刻导致0元账单"这种让人困惑的情况
    duration_minutes: int | None = Field(default=None, ge=0, le=60 * 24 * 365)


# 状态查询请求：按姓名查询当前会话
class SessionStatusRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


# 状态响应：持久状态加服务端实时预估；preview_minutes仅供展示，结算以签退为准
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
    #: 不依赖浏览器本地时钟（两者可能有偏差，会误判"未来时间"）
    server_now: datetime | None = None
    #: 计费算式明细，便于排查「为什么是0分钟」
    elapsed_minutes: int = 0
    #: 签到至今精确经过的秒数（不扣暂离）
    elapsed_seconds: int = 0
    away_minutes: int = 0
    rate_per_minute: float = 0.0
    amount: float = 0.0


# 签到响应：deduplicated标记本次请求被去抖窗口视为重复
class CheckInResponse(BaseModel):
    success: bool
    name: str
    message: str
    is_online: bool = False
    is_away: bool = False
    deduplicated: bool = False


# 签退响应：is_away_action/settled区分暂离与结算，并回传实际签到/签退时刻与算式明细
class CheckOutResponse(BaseModel):
    success: bool
    name: str
    message: str
    session_minutes: int = 0
    total_minutes: int = 0
    amount: float = 0.0
    is_online: bool = False
    is_away: bool = False
    #: 本次动作是「暂离」还是「签退结算」
    #: 前端必须据此区分提示文案 —— 暂离不结算，此时session_minutes/amount必然为0
    is_away_action: bool = False
    #: 是否真的发生了结算（暂离、重复请求都不会结算）
    settled: bool = False
    #: 计费算式明细，便于排查「为什么是0分钟」
    checkin_time: datetime | None = None
    #: 计算实际使用的结算时刻。用户可能自定义签退时间，服务端也可能把
    #: 未来时刻收敛为当前时刻，因此必须回传真实使用的值 —— 前端据此把
    #: 「签到 -> 签退 -> 计费」的时间账原样展示，用户可自行核对
    checkout_time: datetime | None = None
    elapsed_minutes: int = 0
    #: 签到到签退**精确经过的秒数**（不扣暂离）
    #: 用来解释「不足1分钟为何是0」—— 例如实际只过了7秒
    elapsed_seconds: int = 0
    away_minutes: int = 0
    rate_per_minute: float = 0.0


# 报价请求：只接受姓名，分钟数完全由服务端计算
class BillingQuoteRequest(BaseModel):
    """计费报价请求

    注意：**不接受前端传入的时长**。分钟数由服务端按当前会话状态计算
    """

    name: str = Field(min_length=1, max_length=100)


# 报价响应：payable=false时二维码与支付链接留空，避免无应付金额仍展示入口
class BillingQuoteResponse(BaseModel):
    name: str
    minutes: int
    amount: float
    rate_per_minute: float
    #: 二维码与支付链接。金额为0或未签到时**故意留空** ——
    #: 此时没有应付金额，不应该给出付款入口
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
    #: 展示用文案（例如「本次在场1小时5分钟（其中暂离20分钟），应付22.50元」）
    summary: str = ""


# --------------------------------------------------------------------------- #
# 分析（一次性上传分析，供调试与第三方集成）
# --------------------------------------------------------------------------- #


# 单脸分析结果：框、人口属性、身份及各自置信度，未识别统一用Unknown字符串
class FaceInfo(BaseModel):
    bbox: list[int]
    gender: str = "Unknown"
    age: str = "Unknown"
    confidence: float = 0.0
    identity: str = "Unknown"
    identity_confidence: float = 0.0
    detection_confidence: float = 0.0


# 一次性分析响应：request_id关联服务日志，processing_ms为整帧耗时
class DetectResponse(BaseModel):
    request_id: str
    faces: list[FaceInfo]
    face_count: int
    processing_ms: float
    timestamp: datetime


# --------------------------------------------------------------------------- #
# 日志
# --------------------------------------------------------------------------- #


# 前端日志上报：message上限2000字符，page标记来源页面
class FrontendLogRequest(BaseModel):
    level: Literal["info", "warn", "error"] = "info"
    message: str = Field(max_length=2000)
    page: str = Field(default="unknown", max_length=100)


# 上报确认：固定ok=true，正文写入服务日志
class FrontendLogResponse(BaseModel):
    ok: bool = True


# 日志查询：lines限1-2000，date需YYYY-MM-DD，level在读取阶段过滤
class GetLogsRequest(BaseModel):
    lines: int = Field(default=100, ge=1, le=2000)
    level: Literal["all", "info", "warn", "error", "debug", "critical"] = "all"
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


# 日志查询响应：logs为文本行，file指出来源文件，total为读取总行数
class GetLogsResponse(BaseModel):
    logs: list[str]
    file: str | None = None
    total: int = 0


# 场地设置当前值：字段范围与config.Settings的校验保持一致
class SiteSettings(BaseModel):
    """可在线调整的业务参数。取值范围与 ``Settings`` 里的约束保持一致"""

    billing_rate_per_minute: float = Field(ge=0, le=1000)
    away_timeout_minutes: int = Field(ge=1, le=1440)
    max_session_minutes: int = Field(ge=1, le=100000)
    checkin_debounce_seconds: int = Field(ge=0, le=300)
    checkout_debounce_seconds: int = Field(ge=0, le=300)
    recognize_threshold: float = Field(gt=0, le=1)
    face_conf_threshold: float = Field(gt=0, le=1)


# 设置响应：可调参数之外附带持久化状态与部署元信息
class SiteSettingsResponse(SiteSettings):
    """当前生效值 + 说明性元信息"""

    persisted: bool = True
    env_file: str | None = None
    host: str = "127.0.0.1"


# 局部更新请求：全部字段可选，未传字段保持原值
class UpdateSiteSettingsRequest(BaseModel):
    """局部更新：只传要改的字段，未传的保持原值"""

    billing_rate_per_minute: float | None = Field(default=None, ge=0, le=1000)
    away_timeout_minutes: int | None = Field(default=None, ge=1, le=1440)
    max_session_minutes: int | None = Field(default=None, ge=1, le=100000)
    checkin_debounce_seconds: int | None = Field(default=None, ge=0, le=300)
    checkout_debounce_seconds: int | None = Field(default=None, ge=0, le=300)
    recognize_threshold: float | None = Field(default=None, gt=0, le=1)
    face_conf_threshold: float | None = Field(default=None, gt=0, le=1)


# --------------------------------------------------------------------------- #
# WebSocket消息
# --------------------------------------------------------------------------- #


# 摄像头WS结果帧：faces为本次识别结果，处理耗时为整帧
class WsFaceMessage(BaseModel):
    success: bool = True
    face_count: int
    faces: list[FaceInfo]
    processing_ms: float


# WS错误帧：单帧错误用此结构返回，不必直接断开连接
class WsErrorMessage(BaseModel):
    success: bool = False
    detail: str
    code: str = "error"


__all__ = [name for name in dir() if name.endswith(("Request", "Response", "Item", "Message"))]
