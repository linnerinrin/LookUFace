"""程序入口。

用法：
    python -m app                     # 开发：自动打开浏览器
    python -m app --no-browser        # 不打开浏览器
    uvicorn app.main:app --reload     # 直接走 uvicorn
"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from app.config import settings
from app.logger import logger


def _open_browser(url: str) -> None:
    def _task() -> None:
        try:
            webbrowser.open(url)
        except Exception:
            logger.warning("未能自动打开浏览器，请手动访问 %s", url)

    threading.Timer(1.5, _task).start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lookuface", description="LookUFace 服务")
    parser.add_argument("--host", default=settings.HOST)
    parser.add_argument("--port", type=int, default=settings.PORT)
    parser.add_argument("--reload", action="store_true", default=settings.DEBUG)
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args(argv)

    import uvicorn

    display_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    url = f"http://{display_host}:{args.port}/static/idx.html"

    if not args.no_browser:
        _open_browser(url)

    logger.info("启动 uvicorn host=%s port=%s reload=%s", args.host, args.port, args.reload)
    try:
        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_config=None,  # 使用我们自己的日志配置
            access_log=False,  # 访问日志由 RequestContextMiddleware 记录
        )
    except KeyboardInterrupt:  # pragma: no cover
        logger.info("收到中断信号，退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
