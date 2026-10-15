"""日志路由：前端上报 + 历史日志查询。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query

from app.api.schemas import (
    FrontendLogRequest,
    FrontendLogResponse,
    GetLogsRequest,
    GetLogsResponse,
)
from app.config import settings
from app.logger import daily_log_file, iter_log_files, logger

router = APIRouter(prefix="/logs", tags=["日志"])

#: 日志级别到文件内标记的映射（文件格式为 ``[LEVEL]``）
_LEVEL_MARKERS = {
    "info": "[INFO]",
    "warn": "[WARNING]",
    "error": "[ERROR]",
    "debug": "[DEBUG]",
    "critical": "[CRITICAL]",
}


@router.post("/frontend", response_model=FrontendLogResponse)
async def ingest_frontend_log(request: FrontendLogRequest) -> FrontendLogResponse:
    """接收前端 console 输出并写入应用日志。

    注意：旧版前端通过劫持 ``console.log/warn/error`` 把**所有**输出都上报，
    包括调试信息，造成日志噪声。现在前端只在必要时上报，服务端也不再用
    logger 的 exception 分支放大噪声。
    """
    message = f"[前端 {request.page}] {request.message}"
    if request.level == "error":
        logger.error(message)
    elif request.level == "warn":
        logger.warning(message)
    else:
        logger.info(message)
    return FrontendLogResponse()


@router.post("/recent", response_model=GetLogsResponse)
async def recent_logs(request: GetLogsRequest) -> GetLogsResponse:
    """读取最近 N 行日志，可按级别与日期筛选。"""
    if request.date:
        log_file = daily_log_file(request.date)
    else:
        log_file = daily_log_file(datetime.now().strftime("%Y-%m-%d"))

    if not log_file.exists():
        return GetLogsResponse(logs=[], file=log_file.name, total=0)

    try:
        with log_file.open("r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        logger.exception("读取日志文件失败 %s", log_file)
        return GetLogsResponse(logs=[], file=log_file.name, total=0)

    if request.level != "all":
        marker = _LEVEL_MARKERS.get(request.level)
        if marker:
            lines = [line for line in lines if marker in line]

    total = len(lines)
    recent = lines[-request.lines:] if total > request.lines else lines
    return GetLogsResponse(
        logs=[line.rstrip("\n") for line in recent],
        file=log_file.name,
        total=total,
    )


@router.get("/files")
async def log_files() -> dict[str, object]:
    """列出可用日志文件，供前端日期选择器使用。"""
    files = iter_log_files()
    return {
        "directory": str(settings.LOG_DIR),
        "files": [path.name for path in files[:90]],
    }


@router.get("/level")
async def log_level() -> dict[str, str]:
    """返回当前日志级别。"""
    return {"level": settings.LOG_LEVEL}


__all__ = ["router"]
