"""
app/logger.py
"""

import logging
import sys
from pathlib import Path
from datetime import datetime

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

today = datetime.now().strftime("%Y-%m-%d")
LOG_FILE = LOG_DIR / f"app_{today}.log"


class WebSocketLogHandler(logging.Handler):
    """自定义日志处理器，将日志广播到所有连接的 WebSocket"""

    def __init__(self):
        super().__init__()
        self.websockets = set()

    def add_ws(self, ws):
        self.websockets.add(ws)

    def remove_ws(self, ws):
        self.websockets.discard(ws)

    async def broadcast(self, message: str):
        dead = set()
        for ws in self.websockets:
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        self.websockets -= dead

    def emit(self, record):
        try:
            msg = self.format(record)
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.broadcast(msg))
            except RuntimeError:
                pass
        except Exception:
            self.handleError(record)


def setup_logger():
    logger = logging.getLogger("lookuface")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    simple_formatter = logging.Formatter("[%(levelname)s] %(message)s")

    # 控制台输出
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(simple_formatter)
    logger.addHandler(console_handler)

    # 文件输出
    file_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="[%Y/%m/%d %H:%M:%S]"
    )
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(file_formatter)  # ← 用 file_formatter 而不是 simple_formatter
    logger.addHandler(file_handler)

    # WebSocket 广播
    ws_handler = WebSocketLogHandler()
    ws_handler.setFormatter(simple_formatter)
    logger.addHandler(ws_handler)

    return logger


logger = setup_logger()

# 获取 WebSocket 处理器实例
ws_log_handler = None
for handler in logger.handlers:
    if isinstance(handler, WebSocketLogHandler):
        ws_log_handler = handler
        break