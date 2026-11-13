# """模块：日志上报与历史查询"""
# """架构作用：接收客户端日志，读取服务日志并输出筛选结果"""
"""日志路由：前端上报 + 历史日志查询"""

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

#: 历史查询单次最多回读的字节数（尾部读取，避免整文件载入内存）
_TAIL_READ_BYTES = 2 * 1024 * 1024


# 控制字符转义：防止上报文本伪造日志行（换行/回车/制表符等）
def _sanitize_log_text(text: str) -> str:
    return "".join(
        ch if ch.isprintable() or ch == " " else f"\\x{ord(ch):02x}"
        for ch in text
    )


# 上报端点：把前端日志写入服务日志（含page/level）
@router.post("/frontend", response_model=FrontendLogResponse)
async def ingest_frontend_log(request: FrontendLogRequest) -> FrontendLogResponse:
    """接收前端console输出并写入应用日志"""
    message = f"[前端 {_sanitize_log_text(request.page)}] {_sanitize_log_text(request.message)}"
    if request.level == "error":
        logger.error(message)
    elif request.level == "warn":
        logger.warning(message)
    else:
        logger.info(message)
    return FrontendLogResponse()


# 查询端点：按日期/级别/行数读取日志文件尾部
@router.post("/recent", response_model=GetLogsResponse)
async def recent_logs(request: GetLogsRequest) -> GetLogsResponse:
    """读取最近N行日志，可按级别与日期筛选"""
    if request.date:
        log_file = daily_log_file(request.date)
    else:
        log_file = daily_log_file(datetime.now().strftime("%Y-%m-%d"))

    if not log_file.exists():
        return GetLogsResponse(logs=[], file=log_file.name, total=0)

    # 尾部读取：只回读文件末尾一段字节，避免日志增大后整文件载入内存
    # 因按级别筛选会丢弃行，按请求行数放大回读窗口（有上限）
    window = _TAIL_READ_BYTES if request.level == "all" else min(
        _TAIL_READ_BYTES, max(64 * 1024, request.lines * 512)
    )
    try:
        size = log_file.stat().st_size
        with log_file.open("rb") as handle:
            handle.seek(max(0, size - window))
            chunk = handle.read()
        lines = chunk.decode("utf-8", errors="replace").splitlines(keepends=True)
        if size > window and lines:
            lines = lines[1:]  # 丢弃可能被截断的首行
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


# 文件列表端点：枚举日志目录供控制台切换日期
@router.get("/files")
async def log_files() -> dict[str, object]:
    """列出可用日志文件，供前端日期选择器使用"""
    files = iter_log_files()
    return {
        "directory": str(settings.LOG_DIR),
        "files": [path.name for path in files[:90]],
    }


# 级别端点：返回当前日志级别
@router.get("/level")
async def log_level() -> dict[str, str]:
    """返回当前日志级别"""
    return {"level": settings.LOG_LEVEL}


__all__ = ["router"]
