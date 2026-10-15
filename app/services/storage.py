"""图像与文件的存储、校验（含上传安全加固）。

相对旧版的修复（**P1 级安全**）：

旧版 ``register_face`` 直接 ``cv2.imwrite``，并且截图文件名由服务端时间戳生成却
**从不校验上传内容**；计费二维码则直接把用户输入拼进路径::

    qr_path = qr_dir / f"{name}_{online_time}.png"    # name 完全未净化

实测 ``name='../../../evil'`` 会写到 ``C:\\Users\\...\\AppData\\Local\\evil_10.png``，
成功逃出目标目录。

本模块的做法：
* 所有落盘文件名一律由服务端生成（UUID + 白名单扩展名），用户输入永不参与路径；
* ``safe_join`` 断言最终路径位于允许的根目录之内；
* 上传内容按字节数 + 解码结果 + 边长三重校验。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from app.config import settings
from app.core.errors import StorageError, ValidationError
from app.logger import logger

_SAFE_NAME_PATTERN = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff_.-]")

#: 允许的最大图像边长（像素），防止解压炸弹式超大图
MAX_IMAGE_DIMENSION = 8000


def sanitize_label(value: str, *, max_length: int = 40, fallback: str = "unnamed") -> str:
    """把任意字符串变成安全的文件名片段（永不用于路径拼接的安全边界，但便于人读）。"""
    cleaned = _SAFE_NAME_PATTERN.sub("_", (value or "").strip())
    cleaned = cleaned.strip("._")
    if not cleaned:
        return fallback
    return cleaned[:max_length]


def safe_join(root: Path, filename: str) -> Path:
    """在 ``root`` 下安全拼接文件名，越界时抛异常。"""
    root = root.resolve()
    candidate = (root / filename).resolve()
    if candidate != root and root not in candidate.parents:
        raise StorageError(
            "非法文件路径：目标越出允许的目录",
            details={"root": str(root), "candidate": str(candidate)},
        )
    return candidate


def decode_image(contents: bytes, *, max_bytes: int | None = None) -> np.ndarray:
    """把上传字节解码为 BGR 图像，并做尺寸与内容校验。"""
    limit = max_bytes or settings.MAX_FILE_SIZE
    if not contents:
        raise ValidationError("上传内容为空")
    if len(contents) > limit:
        raise ValidationError(
            f"文件过大：{len(contents)} 字节，上限 {limit} 字节",
            code="file_too_large",
        )

    buffer = np.frombuffer(contents, np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise ValidationError("无法解码图片，请确认是有效的 JPG/PNG 文件")

    height, width = image.shape[:2]
    if height < 16 or width < 16:
        raise ValidationError(f"图片尺寸过小：{width}x{height}")
    if max(height, width) > MAX_IMAGE_DIMENSION:
        raise ValidationError(f"图片尺寸过大：{width}x{height}")

    return image


def save_screenshot(image: np.ndarray, *, prefix: str = "") -> str:
    """保存截图，返回**相对项目根目录**的 POSIX 路径。"""
    if image is None or image.size == 0:
        raise StorageError("截图为空，无法保存")

    directory = settings.screenshot_dir
    directory.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique = uuid.uuid4().hex[:8]
    label = f"_{sanitize_label(prefix)}" if prefix else ""
    filename = f"{stamp}{label}_{unique}.jpg"

    path = safe_join(directory, filename)
    try:
        ok = cv2.imwrite(str(path), image, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    except Exception as exc:
        raise StorageError(f"截图写入失败：{exc}") from exc
    if not ok:
        raise StorageError("截图写入失败：cv2.imwrite 返回 False")

    logger.debug("截图已保存 %s", path)
    return path.relative_to(settings.BASE_DIR).as_posix()


def resolve_stored_path(stored: str | None) -> Path | None:
    """把数据库里的存储路径还原为绝对路径，并做越界校验。

    兼容历史数据里可能是绝对路径或反斜杠分隔的情况。
    """
    if not stored:
        return None

    candidate = Path(stored.replace("\\", "/"))
    if not candidate.is_absolute():
        candidate = settings.BASE_DIR / candidate
    candidate = candidate.resolve()

    # 截图只允许落在 storage 目录下
    allowed_root = settings.STORAGE_DIR.resolve()
    if candidate != allowed_root and allowed_root not in candidate.parents:
        logger.warning("拒绝访问越界的截图路径：%s", candidate)
        return None
    return candidate


def delete_file(stored: str | None) -> bool:
    """删除 ``storage`` 下的文件。越界路径直接拒绝。"""
    path = resolve_stored_path(stored)
    if path is None or not path.exists():
        return False
    try:
        path.unlink()
        return True
    except OSError as exc:  # pragma: no cover
        logger.warning("删除文件失败 %s: %s", path, exc)
        return False


__all__ = [
    "MAX_IMAGE_DIMENSION",
    "decode_image",
    "delete_file",
    "resolve_stored_path",
    "safe_join",
    "sanitize_label",
    "save_screenshot",
]
