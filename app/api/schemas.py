"""统一的API请求/响应模型

* **路径**：kebab-case，资源式（``/faces``、``/sessions/checkin``）
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
# 会话
# --------------------------------------------------------------------------- #


# 签到请求：只接受姓名，不接受客户端计时，起点由服务端写入
class CheckInRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


# 签退请求：checkout_time/checkin_time/duration_minutes 为补录参数，冲突时由服务解析
class CheckOutRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    #: 自定义签退时间（可选）。缺省表示「就是现在」
    #:
    #: 使用场景：用户已经离开、事后在仓库管理页补签退 —— 此时应按**真实离开时间**
    #: 结算，而不是按「管理员几点点的按钮」结算
    #:
    #: 接受 ISO8601（``2026-10-16T03:20``）或 ``YYYY-MM-DD HH:MM[:SS]``，
    #: 也接受 ``datetime-local`` 控件的取值。``Z`` 后缀按 UTC 解释
    checkout_time: datetime | None = None
    #: 修正本次的**签到时刻**（可选）。缺省表示不改动现有起点
    #: 一般不需要用 —— 前端用 ``duration_minutes`` 更方便
    checkin_time: datetime | None = None
    #: **在场时长（分钟）**（可选）。这是补录时首选的方式
    #:
    #: 服务端会把签到时间顺推为 ``结算时刻 - duration_minutes``，
    #: 因此「填 600 就是 10 小时」永远算得出结果，
    #: 不会出现「填了未来时刻导致 0 分钟」这种让人困惑的情况
    duration_minutes: int | None = Field(default=None, ge=0, le=60 * 24 * 365)


# 状态查询请求：按姓名查询当前会话
class SessionStatusRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


# 状态响应：持久状态加服务端实时预估
class SessionStatusResponse(BaseModel):
    name: str
    is_online: bool
    checkin_time: datetime | None = None
    preview_minutes: int = 0
    online_time: int = 0
    server_now: datetime | None = None
    elapsed_minutes: int = 0
    elapsed_seconds: int = 0


# 签到响应：deduplicated标记本次请求被视为重复
class CheckInResponse(BaseModel):
    success: bool
    name: str
    message: str
    is_online: bool = False
    deduplicated: bool = False


# 签退响应
class CheckOutResponse(BaseModel):
    success: bool
    name: str
    message: str
    session_minutes: int = 0
    total_minutes: int = 0
    is_online: bool = False
    settled: bool = False
    checkin_time: datetime | None = None
    checkout_time: datetime | None = None
    elapsed_minutes: int = 0
    elapsed_seconds: int = 0


# --------------------------------------------------------------------------- #
# 流水记录（数据看板补录与查询）
# --------------------------------------------------------------------------- #


# 补录请求：一段已完成的进出场区间，只写流水不改当前在场状态
class RecordCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    checkin_at: datetime
    checkout_at: datetime
    source: str = Field(default="manual", max_length=16)


# 流水单项：一条进出场记录
class RecordItem(BaseModel):
    id: int
    name: str
    checkin_at: datetime
    checkout_at: datetime | None = None
    duration_seconds: int = 0
    duration_minutes: int = 0
    source: str = "manual"


# 流水列表响应
class RecordListResponse(BaseModel):
    records: list[RecordItem]


# 看板 KPI：今日维度加当前在场
class StatsKpis(BaseModel):
    today_visits: int = 0
    online_now: int = 0
    today_minutes: int = 0
    avg_minutes: int = 0


# 看板统计响应：kpis 为数值，charts 为图表名到 PNG data URI 的映射
class StatsOverviewResponse(BaseModel):
    kpis: StatsKpis
    charts: dict[str, str]
    days: int = 7
    total_records: int = 0


# --------------------------------------------------------------------------- #
# 分析（一次性上传分析，供调试与第三方集成）
# --------------------------------------------------------------------------- #


# 单脸分析结果：框、身份及各自置信度，未识别统一用Unknown字符串
class FaceInfo(BaseModel):
    bbox: list[int]
    identity: str = "Unknown"
    identity_confidence: float = 0.0
    detection_confidence: float = 0.0


# 一次性分析响应：request_id与响应的 X-Request-ID 对应，processing_ms为整帧耗时
class DetectResponse(BaseModel):
    request_id: str
    faces: list[FaceInfo]
    face_count: int
    processing_ms: float
    timestamp: datetime


# --------------------------------------------------------------------------- #
# 场地设置
# --------------------------------------------------------------------------- #

# 场地设置当前值：字段范围与config.Settings的校验保持一致
class SiteSettings(BaseModel):
    """可在线调整的业务参数。取值范围与 ``Settings`` 里的约束保持一致"""

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
