
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


# 将浏览器打开动作延后执行，避免阻塞服务启动
def _open_browser(url: str) -> None:
    # 定时器回调仅负责外部浏览器调用，异常不影响服务
    def _task() -> None:
        try:
            webbrowser.open(url)
        except Exception:
            print(f"未能自动打开浏览器，请手动访问 {url}")

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
    except Exception as exc:
        print(f"模型自动下载异常：{exc}")
        return
    if failed:
        print("部分模型未能自动补齐：" + ", ".join(failed))


# 将命令行覆盖值交给uvicorn，返回进程退出码
def main(argv: list[str] | None = None) -> int:
    # 终端/重定向都按 UTF-8 输出，避免中文提示乱码
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

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

    print(f"启动 uvicorn host={args.host} port={args.port} reload={args.reload}")
    try:
        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
    except KeyboardInterrupt:  # pragma: no cover
        print("收到中断信号，退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
