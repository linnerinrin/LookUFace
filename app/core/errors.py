"""应用级异常类型。

统一异常语义，让路由层只需 ``raise DomainError(...)``，
由 ``app.core.errors`` 里的处理器转换成规范的 HTTP 响应。
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """所有业务异常的基类。"""

    status_code: int = 500
    code: str = "internal_error"
    default_message: str = "服务器内部错误"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)

    def to_payload(self, request_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "detail": self.message,
            "code": self.code,
        }
        if self.details:
            payload["details"] = self.details
        if request_id:
            payload["request_id"] = request_id
        return payload


class ValidationError(AppError):
    status_code = 400
    code = "validation_error"
    default_message = "请求参数不合法"


class AuthError(AppError):
    status_code = 401
    code = "unauthorized"
    default_message = "未认证或凭据无效"


class PermissionError_(AppError):
    status_code = 403
    code = "forbidden"
    default_message = "无权访问该资源"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    default_message = "资源不存在"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    default_message = "资源状态冲突"


class RateLimitError(AppError):
    status_code = 429
    code = "rate_limited"
    default_message = "请求过于频繁，请稍后再试"


class ExternalServiceError(AppError):
    status_code = 502
    code = "external_service_error"
    default_message = "外部服务调用失败"


class ModelUnavailableError(AppError):
    status_code = 503
    code = "model_unavailable"
    default_message = "视觉模型未加载，服务暂不可用"


class FaceQualityError(AppError):
    """人脸质量不满足要求（未检出、多人脸、过小等）。"""

    status_code = 422
    code = "face_quality"
    default_message = "人脸图像质量不满足要求"


class StorageError(AppError):
    status_code = 500
    code = "storage_error"
    default_message = "文件存储失败"


__all__ = [
    "AppError",
    "ValidationError",
    "AuthError",
    "PermissionError_",
    "NotFoundError",
    "ConflictError",
    "RateLimitError",
    "ExternalServiceError",
    "ModelUnavailableError",
    "FaceQualityError",
    "StorageError",
]
