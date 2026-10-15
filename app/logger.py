"""日志基础设施。

相对旧版的修复：

1. **中文乱码**：旧版 ``StreamHandler`` 写入 ``sys.stdout``，不继承 ``FileHandler``
   的 ``encoding="utf-8"``，Windows 上控制台代码页不匹配即产生
   ``鍒濆鍖栬璇佹ā鍧?`` 这类乱码。现在显式把 stdout/stderr 重配为 UTF-8。
2. **线程安全广播**：旧版 ``WebSocketLogHandler.emit`` 直接调
   ``asyncio.get_running_loop()``，在工作线程（如 ``run_in_executor``）里必然
   ``RuntimeError``，日志静默丢失。现在通过 ``LogBroadcaster`` 持有主事件循环，
   用 ``run_coroutine_threadsafe`` 跨线程投递。
3. **请求链路**：引入 ``request_id`` contextvar，所有日志自动带上关联 ID。
4. **文件轮转**：``TimedRotatingFileHandler`` 按天轮转并保留历史，
   文件名保持 ``app_YYYY-MM-DD.log`` 以兼容控制台页面的按日期查询。
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from contextvars import ContextVar
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Iterable, Optional

from app.config import settings

# --------------------------------------------------------------------------- #
# 请求上下文
# --------------------------------------------------------------------------- #

_request_id: ContextVar[str] = ContextVar("request_id", default="-")

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def new_request_id() -> str:
    """生成一个短请求 ID。"""
    import uuid

    return uuid.uuid4().hex[:8]


def set_request_id(value: str) -> None:
    _request_id.set(value if _REQUEST_ID_PATTERN.match(value) else "-")


def get_request_id() -> str:
    return _request_id.get()


class _RequestIdFilter(logging.Filter):
    """把 contextvar 里的 request_id 注入每条日志记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            record.request_id = _request_id.get()
        return True


# --------------------------------------------------------------------------- #
# WebSocket 广播
# --------------------------------------------------------------------------- #


class LogBroadcaster(logging.Handler):
    """把日志推送给所有已连接的控制台 WebSocket。

    线程安全：``emit`` 可能来自任意工作线程，因此不直接操作事件循环，
    而是把协程投递到启动时登记的主循环上。
    """

    def __init__(self) -> None:
        super().__init__()
        self._websockets: set[Any] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # -------- 生命周期 --------
    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """在应用启动时登记主事件循环。"""
        self._loop = loop

    def detach_loop(self) -> None:
        self._loop = None

    # -------- 连接管理 --------
    def add_ws(self, websocket: Any) -> None:
        self._websockets.add(websocket)

    def remove_ws(self, websocket: Any) -> None:
        self._websockets.discard(websocket)

    @property
    def connection_count(self) -> int:
        return len(self._websockets)

    # -------- 发送 --------
    def emit(self, record: logging.LogRecord) -> None:
        loop = self._loop
        if loop is None or loop.is_closed() or not self._websockets:
            return
        try:
            message = self.format(record)
        except Exception:  # pragma: no cover - 格式化异常不应影响主流程
            self.handleError(record)
            return
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast(message), loop)
        except RuntimeError:
            # 循环正在关闭
            pass

    async def _broadcast(self, message: str) -> None:
        for websocket in list(self._websockets):
            try:
                await websocket.send_text(message)
            except Exception:
                # 断开的连接直接摘除，不再重试
                self._websockets.discard(websocket)


# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #

_CONSOLE_FORMAT = "[%(levelname)s] [%(request_id)s] %(message)s"
_FILE_FORMAT = "%(asctime)s [%(levelname)s] [%(request_id)s] %(name)s: %(message)s"
_FILE_DATEFMT = "%Y/%m/%d %H:%M:%S"

_LOGGER_NAME = "lookuface"
_LOG_FILE_STEM = "app"

_broadcaster: Optional[LogBroadcaster] = None


def _force_utf8_streams() -> None:
    """确保控制台输出为 UTF-8，消除中文乱码。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - 特殊终端
            pass


def daily_log_file(day: str) -> Path:
    """按 ``YYYY-MM-DD`` 返回当日日志文件路径。"""
    return settings.LOG_DIR / f"{_LOG_FILE_STEM}_{day}.log"


def setup_logging() -> logging.Logger:
    """配置并返回应用主 logger。可重复调用（幂等）。"""
    global _broadcaster

    _force_utf8_streams()
    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(getattr(logging, settings.LOG_LEVEL, logging.INFO))
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    request_filter = _RequestIdFilter()

    # 控制台
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    console_handler.addFilter(request_filter)
    logger.addHandler(console_handler)

    # 文件（按天轮转，保留 14 天）
    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    file_handler = TimedRotatingFileHandler(
        filename=str(daily_log_file(today)),
        when="midnight",
        interval=1,
        backupCount=14,
        encoding="utf-8",
        delay=False,
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(logging.Formatter(_FILE_FORMAT, datefmt=_FILE_DATEFMT))
    file_handler.addFilter(request_filter)
    logger.addHandler(file_handler)

    # WebSocket 实时推送
    _broadcaster = LogBroadcaster()
    _broadcaster.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    _broadcaster.addFilter(request_filter)
    logger.addHandler(_broadcaster)

    # 收敛第三方库噪声
    for noisy in ("uvicorn.access", "multipart", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return logger


def get_broadcaster() -> LogBroadcaster:
    """取回广播处理器（未初始化时先初始化）。"""
    if _broadcaster is None:
        setup_logging()
    assert _broadcaster is not None
    return _broadcaster


def install_asyncio_exception_logging(logger: logging.Logger) -> None:
    """把未捕获的 asyncio 任务异常写进应用日志。"""

    def handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
        exc = context.get("exception")
        message = context.get("message", "未处理的 asyncio 异常")
        if exc is not None:
            logger.error("asyncio: %s", message, exc_info=exc)
        else:
            logger.error("asyncio: %s", message)

    try:
        asyncio.get_running_loop().set_exception_handler(handler)
    except RuntimeError:  # pragma: no cover
        pass


def iter_log_files() -> Iterable[Path]:
    """按日期倒序列出所有日志文件。"""
    if not settings.LOG_DIR.exists():
        return []
    return sorted(settings.LOG_DIR.glob(f"{_LOG_FILE_STEM}_*.log"), reverse=True)


logger = setup_logging()

# 向后兼容：旧代码 from app.logger import ws_log_handler
ws_log_handler = get_broadcaster()

__all__ = [
    "logger",
    "ws_log_handler",
    "setup_logging",
    "get_broadcaster",
    "daily_log_file",
    "iter_log_files",
    "new_request_id",
    "set_request_id",
    "get_request_id",
    "install_asyncio_exception_logging",
]
