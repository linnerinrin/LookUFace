# """模块：命令行入口与浏览器启动"""
# """架构作用：读取启动参数，启动ASGI服务并可选打开本地页面"""

"""程序入口

用法：
    python -m app                     # 开发：自动打开浏览器
    python -m app --no-browser        # 不打开浏览器
    uvicorn app.main:app --reload     # 直接走uvicorn
"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from app.config import settings
from app.logger import logger


# 将浏览器打开动作延后执行，避免阻塞服务启动
def _open_browser(url: str) -> None:
    # 定时器回调仅负责外部浏览器调用，异常不影响服务
    def _task() -> None:
        try:
            webbrowser.open(url)
        except Exception:
            logger.warning("未能自动打开浏览器，请手动访问 %s", url)

    threading.Timer(1.5, _task).start()


# 启动前检查模型文件，缺失时自动下载补齐；下载失败不阻塞启动，由预检在health里暴露
def _ensure_models() -> None:
    from app.download_models import MODEL_SOURCES, ensure_models

    def _ready(name: str) -> bool:
        path = settings.MODEL_DIR / name
        return path.exists() and path.stat().st_size > 0

    if all(_ready(name) for name in MODEL_SOURCES):
        return
    try:
        failed = ensure_models()
    except Exception:
        logger.exception("模型自动下载异常")
        return
    if failed:
        logger.warning("部分模型未能自动补齐：%s", ", ".join(failed))


# 将命令行覆盖值交给uvicorn，返回进程退出码
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

    _ensure_models()

    logger.info("启动 uvicorn host=%s port=%s reload=%s", args.host, args.port, args.reload)
    try:
        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_config=None,  # 使用我们自己的日志配置
            access_log=False,  # 访问日志由RequestContextMiddleware记录
        )
    except KeyboardInterrupt:  # pragma: no cover
        logger.info("收到中断信号，退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
