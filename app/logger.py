# """架构作用：集中配置应用日志、请求关联ID、文件轮转与WebSocket广播"""
"""日志基础设施：UTF-8控制台、请求关联ID、按天文件轮转与WebSocket广播"""

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


# """函数：生成短UUID请求标识；调用方负责将其放入请求上下文"""
def new_request_id() -> str:
    """生成一个短请求ID"""
    import uuid

    return uuid.uuid4().hex[:8]


# """函数：校验请求标识格式，不合规则以短横线哨兵替代"""
def set_request_id(value: str) -> None:
    _request_id.set(value if _REQUEST_ID_PATTERN.match(value) else "-")


# """函数：读取当前异步上下文中的请求标识"""
def get_request_id() -> str:
    return _request_id.get()


class _RequestIdFilter(logging.Filter):
    """把contextvar里的request_id注入每条日志记录"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            record.request_id = _request_id.get()
        return True


# """模块：请求日志关联与WebSocket推送；广播通过登记的主循环跨线程投递"""
# --------------------------------------------------------------------------- #
# WebSocket广播
# --------------------------------------------------------------------------- #


class LogBroadcaster(logging.Handler):
    """把日志推送给所有已连接的控制台WebSocket

    线程安全：``emit`` 可能来自任意工作线程，因此不直接操作事件循环，
    而是通过 ``call_soon_threadsafe`` 把消息投入绑定主循环的**有界
    asyncio.Queue**，由唯一的消费协程逐个发送。队列满时丢弃新消息并计数
    （丢日志比拖垮事件循环好）
    """

    #: 待广播消息队列上限（背压）
    QUEUE_MAXSIZE = 1000

    def __init__(self) -> None:
        super().__init__()
        self._websockets: set[Any] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue: Optional[asyncio.Queue[str]] = None
        self._dropped = 0
        self._drain_task: Optional[asyncio.Task[None]] = None

    # -------- 生命周期 --------
    # """方法：记录事件循环引用，并启动唯一的有界队列消费任务"""
    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """在应用启动时登记主事件循环并启动广播消费协程

        每次登记都重建队列与消费协程：TestClient等场景会为每个客户端
        新建事件循环，旧循环关闭后消费协程必须跟随新循环重建
        """
        self.detach_loop()
        self._loop = loop
        self._queue = asyncio.Queue(maxsize=self.QUEUE_MAXSIZE)
        self._drain_task = loop.create_task(self._drain(), name="log-broadcaster-drain")

# """方法：取消消费协程并清除事件循环引用，阻止后续日志继续投递"""
    def detach_loop(self) -> None:
        if self._drain_task is not None:
            self._drain_task.cancel()
            self._drain_task = None
        self._loop = None
        self._queue = None

    # -------- 连接管理 --------
# """方法：将WebSocket加入本地连接集合"""
    def add_ws(self, websocket: Any) -> None:
        self._websockets.add(websocket)

# """方法：幂等地从连接集合移除指定WebSocket"""
    def remove_ws(self, websocket: Any) -> None:
        self._websockets.discard(websocket)

# """属性：返回当前广播目标数量，用于运行状态展示"""
    @property
    def connection_count(self) -> int:
        return len(self._websockets)

    #: 因队列满被丢弃的消息数（诊断用）
    @property
    def dropped_count(self) -> int:
        return self._dropped

    # -------- 发送 --------
# """方法：格式化记录后经call_soon_threadsafe投入有界队列；满则丢弃计数（背压）"""
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
            loop.call_soon_threadsafe(self._enqueue, message)
        except RuntimeError:  # 循环已关闭
            self._dropped += 1

# """方法：仅由主循环线程执行：非阻塞入队，满则丢弃计数"""
    def _enqueue(self, message: str) -> None:
        queue = self._queue
        if queue is None:
            self._dropped += 1
            return
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            self._dropped += 1

# """方法：唯一的消费协程：从有界队列取消息并逐个广播"""
    async def _drain(self) -> None:
        assert self._queue is not None
        queue = self._queue
        while True:
            message = await queue.get()
            await self._broadcast(message)

# """方法：逐个发送日志文本，发送失败则丢弃断开的连接"""
    async def _broadcast(self, message: str) -> None:
        for websocket in list(self._websockets):
            try:
                await websocket.send_text(message)
            except Exception:
                # 断开的连接直接摘除，不再重试
                self._websockets.discard(websocket)


# """模块：组装stdout、按日文件及WebSocket三类日志处理器"""
# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #

_CONSOLE_FORMAT = "[%(levelname)s] [%(request_id)s] %(message)s"
_FILE_FORMAT = "%(asctime)s [%(levelname)s] [%(request_id)s] %(name)s: %(message)s"
_FILE_DATEFMT = "%Y/%m/%d %H:%M:%S"

_LOGGER_NAME = "lookuface"
_LOG_FILE_STEM = "app"

_broadcaster: Optional[LogBroadcaster] = None


# """函数：尽力将标准流切换为UTF-8，失败时保留终端默认编码"""
def _force_utf8_streams() -> None:
    """确保控制台输出为UTF-8，消除中文乱码"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - 特殊终端
            pass


# """函数：返回指定日期的日志路径；当日为活动文件app.log，历史为轮转出的app_YYYY-MM-DD.log"""
def daily_log_file(day: str) -> Path:
    """按 ``YYYY-MM-DD`` 返回日志文件路径（当日指向活动文件）"""
    from datetime import datetime

    if day == datetime.now().strftime("%Y-%m-%d"):
        return settings.LOG_DIR / f"{_LOG_FILE_STEM}.log"
    return settings.LOG_DIR / f"{_LOG_FILE_STEM}_{day}.log"


# """函数：清理并重建日志处理器，完成控制台、文件、广播三路数据流；轮转命名与查询/清理规则一致"""
def setup_logging() -> logging.Logger:
    """配置并返回应用主logger。可重复调用（幂等）"""
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

    # 文件（按天轮转，保留14天）
    # 基名固定app.log；轮转出的历史文件统一命名为app_YYYY-MM-DD.log，
    # 与daily_log_file / iter_log_files的约定和backupCount清理规则一致
    # （默认命名app.log.YYYY-MM-DD不匹配app_*.log，清理/查询都会漏）
    file_handler = TimedRotatingFileHandler(
        filename=str(settings.LOG_DIR / f"{_LOG_FILE_STEM}.log"),
        when="midnight",
        interval=1,
        backupCount=14,
        encoding="utf-8",
        delay=False,
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.namer = lambda name: str(
        settings.LOG_DIR / f"{_LOG_FILE_STEM}_{name.split('.log.', 1)[1]}.log"
    )
    file_handler.addFilter(request_filter)
    logger.addHandler(file_handler)

    # WebSocket实时推送
    _broadcaster = LogBroadcaster()
    _broadcaster.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    _broadcaster.addFilter(request_filter)
    logger.addHandler(_broadcaster)

    # 收敛第三方库噪声
    for noisy in ("uvicorn.access", "multipart", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return logger


# """函数：返回当前广播器；未初始化时执行完整日志配置"""
def get_broadcaster() -> LogBroadcaster:
    """取回广播处理器（未初始化时先初始化）"""
    if _broadcaster is None:
        setup_logging()
    assert _broadcaster is not None
    return _broadcaster


# """函数：注册asyncio未捕获异常回调并转写到标准logger"""
def install_asyncio_exception_logging(logger: logging.Logger) -> None:
    """把未捕获的asyncio任务异常写进应用日志"""

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


# """函数：按文件名倒序枚举日志文件供管理端查询；当日活动文件app.log排在最前"""
def iter_log_files() -> Iterable[Path]:
    """按日期倒序列出所有日志文件（含当日活动文件）"""
    if not settings.LOG_DIR.exists():
        return []
    history = sorted(settings.LOG_DIR.glob(f"{_LOG_FILE_STEM}_*.log"), reverse=True)
    active = settings.LOG_DIR / f"{_LOG_FILE_STEM}.log"
    return ([active] if active.exists() else []) + history


logger = setup_logging()

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

