# """架构作用：统一视觉模型生命周期、并发锁、DNN加载与启动预检"""
"""视觉模型基座：线程安全的单例加载

本模块提供：

* ``ThreadSafeModel``：带双检锁的懒加载单例基类；
* ``inference_lock``：可重入的推理锁，供所有模型共享（推理本身很快，
  串行化带来的吞吐损失远小于竞态导致的错误结果）；
* 启动期 ``preflight()``：一次性校验模型文件是否齐备，缺失时给出明确报错，
  而不是在第一个请求进来时才 ``raise``
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Callable, ClassVar, Generic, TypeVar

from app.config import settings
from app.core.errors import ModelUnavailableError, NotFoundError
from app.logger import logger

T = TypeVar("T")

#: 全局推理锁。cv2.dnn与mediapipe的实例都不是线程安全的，
#: 所有对模型实例的forward/process调用必须在这把锁内进行
inference_lock = threading.RLock()


class ThreadSafeModel(Generic[T]):
    """线程安全的懒加载单例

    子类需实现 ``_build()``，并通过 ``instance()`` 获取实例
    """

    _instances: ClassVar[dict[type, Any]] = {}
    _instances_lock: ClassVar[threading.Lock] = threading.Lock()

    # """方法：双检锁懒加载每个子类的模型实例"""
    @classmethod
    def instance(cls) -> T:
        # 快路径：已构造则直接返回，避免每次取锁
        existing = cls._instances.get(cls)
        if existing is not None:
            return existing
        with cls._instances_lock:
            # 双检：可能在等锁期间已被其他线程构造
            existing = cls._instances.get(cls)
            if existing is not None:
                return existing
            obj = cls()
            obj._build()
            cls._instances[cls] = obj
            return obj

    # """方法：移除缓存引用并主动释放实例资源（若实例提供close()）"""
    @classmethod
    def reset(cls) -> None:
        """丢弃单例并释放其资源（仅测试使用）"""
        with cls._instances_lock:
            obj = cls._instances.pop(cls, None)
        if obj is not None:
            close = getattr(obj, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    logger.exception("释放模型资源失败：%s", cls.__name__)

    # """方法：报告该子类实例是否已成功构建（加载就绪）"""
    @classmethod
    def is_loaded(cls) -> bool:
        return cls._instances.get(cls) is not None

    def _build(self) -> None:  # pragma: no cover - 抽象
        raise NotImplementedError

    # 便捷访问器
    # """方法：确认模型文件存在，否则抛出可定位的业务异常"""
    def _require_file(self, path: Path, label: str) -> Path:
        if not path.exists():
            raise NotFoundError(
                f"模型文件缺失：{label} -> {path}",
                code="model_file_missing",
            )
        return path


# """函数：读取权重和结构文件，构造OpenCV网络并包装加载错误"""
def load_dnn_net(model_path: Path, config_path: Path, label: str) -> Any:
    """加载OpenCV DNN网络，失败时抛出可读异常"""
    import cv2

    for path, kind in ((model_path, "权重"), (config_path, "结构")):  # noqa: B007
        if not path.exists():
            raise NotFoundError(
                f"{label} 模型文件缺失（{kind}）：{path}",
                code="model_file_missing",
            )
    try:
        net = cv2.dnn.readNet(str(model_path), str(config_path))
    except Exception as exc:
        raise ModelUnavailableError(
            f"{label} 模型加载失败：{exc}",
            details={"model_path": str(model_path), "config_path": str(config_path)},
        ) from exc
    logger.info("%s 模型加载成功", label)
    return net


# """函数：遍历必需模型文件，做存在性+可读性+非空完整性检查并汇总"""
def preflight() -> dict[str, bool]:
    """启动期模型预检，返回每个文件的就绪状态

    校验三个层次：文件存在、进程可读、内容非空
    不在缺失时直接崩溃，而是把状态交给调用方决定（health接口会暴露出来）
    注意：这是文件级预检，真正的加载就绪状态见 ``load_readiness()``
    """
    result: dict[str, bool] = {}
    for label, path in settings.required_model_files().items():
        ok = False
        if path.exists():
            try:
                # 可读 + 非空：打开读1字节验证权限，size验证非空
                ok = path.stat().st_size > 0 and os.access(path, os.R_OK)
            except OSError:
                ok = False
        result[label] = ok
        if not ok:
            logger.error("模型文件不可用（缺失/空/不可读）：%s -> %s", label, path)
    missing = [k for k, ok in result.items() if not ok]
    if missing:
        logger.warning("有 %d 个模型文件不可用，相关功能不可用：%s", len(missing), ", ".join(missing))
    else:
        logger.info("全部 %d 个模型文件校验通过", len(result))
    return result


# """函数：报告各模型子类的实际加载就绪状态（是否已构建实例）"""
def load_readiness() -> dict[str, bool]:
    """每个ThreadSafeModel子类是否已加载就绪（成功构建过实例）"""
    with ThreadSafeModel._instances_lock:
        return {cls.__name__: True for cls in ThreadSafeModel._instances}


# """装饰器：让一次模型推理在全局可重入锁中串行执行"""
def guarded(fn: Callable[..., T]) -> Callable[..., T]:
    """把函数体包进全局推理锁"""

    def wrapper(*args: Any, **kwargs: Any) -> T:
        with inference_lock:
            return fn(*args, **kwargs)

    wrapper.__name__ = getattr(fn, "__name__", "guarded")
    wrapper.__doc__ = fn.__doc__
    return wrapper


__all__ = ["inference_lock", "ThreadSafeModel", "load_dnn_net", "preflight", "load_readiness", "guarded"]

