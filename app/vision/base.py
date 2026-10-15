"""视觉模型基座：线程安全的单例加载。

相对旧版的修复（**P0 级**）：

旧版 ``FaceDetector._instance`` / ``GenderAgeModel._instance`` / ``FaceIdentity._instance``
都是裸单例，且被 ``ThreadPoolExecutor(max_workers=4)`` 的多个工作线程同时调用
``cv2.dnn.Net.forward()`` 与 ``mediapipe FaceMesh.process()``。这两者**都不是线程安全的**，
并发调用会返回错乱结果甚至直接崩溃。旧代码没有任何锁。

本模块提供：

* ``ThreadSafeModel``：带双检锁的懒加载单例基类；
* ``inference_lock``：可重入的推理锁，供所有模型共享（推理本身很快，
  串行化带来的吞吐损失远小于竞态导致的错误结果）；
* 启动期 ``preflight()``：一次性校验模型文件是否齐备，缺失时给出明确报错，
  而不是在第一个请求进来时才 ``raise``。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, ClassVar, Generic, TypeVar

from app.config import settings
from app.core.errors import ModelUnavailableError, NotFoundError
from app.logger import logger

T = TypeVar("T")

#: 全局推理锁。cv2.dnn 与 mediapipe 的实例都不是线程安全的，
#: 所有对模型实例的 forward/process 调用必须在这把锁内进行。
inference_lock = threading.RLock()


class ThreadSafeModel(Generic[T]):
    """线程安全的懒加载单例。

    子类需实现 ``_build()``，并通过 ``instance()`` 获取实例。
    """

    _instances: ClassVar[dict[type, Any]] = {}
    _instances_lock: ClassVar[threading.Lock] = threading.Lock()

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

    @classmethod
    def reset(cls) -> None:
        """丢弃单例（仅测试使用）。"""
        with cls._instances_lock:
            cls._instances.pop(cls, None)

    def _build(self) -> None:  # pragma: no cover - 抽象
        raise NotImplementedError

    # 便捷访问器
    def _require_file(self, path: Path, label: str) -> Path:
        if not path.exists():
            raise NotFoundError(
                f"模型文件缺失：{label} -> {path}",
                code="model_file_missing",
            )
        return path


def load_dnn_net(model_path: Path, config_path: Path, label: str) -> Any:
    """加载 OpenCV DNN 网络，失败时抛出可读异常。"""
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


def preflight() -> dict[str, bool]:
    """启动期模型预检，返回每个文件的就绪状态。

    不在缺失时直接崩溃，而是把状态交给调用方决定（health 接口会暴露出来）。
    """
    result: dict[str, bool] = {}
    for label, path in settings.required_model_files().items():
        exists = path.exists()
        result[label] = exists
        if not exists:
            logger.error("模型文件缺失：%s -> %s", label, path)
    missing = [k for k, ok in result.items() if not ok]
    if missing:
        logger.warning("有 %d 个模型文件缺失，相关功能不可用：%s", len(missing), ", ".join(missing))
    else:
        logger.info("全部 %d 个模型文件校验通过", len(result))
    return result


def guarded(fn: Callable[..., T]) -> Callable[..., T]:
    """把函数体包进全局推理锁。"""

    def wrapper(*args: Any, **kwargs: Any) -> T:
        with inference_lock:
            return fn(*args, **kwargs)

    wrapper.__name__ = getattr(fn, "__name__", "guarded")
    wrapper.__doc__ = fn.__doc__
    return wrapper


__all__ = ["inference_lock", "ThreadSafeModel", "load_dnn_net", "preflight", "guarded"]
