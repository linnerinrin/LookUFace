# """架构作用：承接上传字节、图像解码校验、受控路径解析与文件落盘"""
"""图像与文件的存储、校验（含上传安全加固）

* 所有落盘文件名一律由服务端生成（UUID + 白名单扩展名），用户输入永不参与路径；
* ``safe_join`` 断言最终路径位于允许的根目录之内；
* 上传内容按字节数 + 解码结果 + 边长三重校验
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


# """函数：清理用户标签为可读文件名片段；它不能替代目录边界校验"""
def sanitize_label(value: str, *, max_length: int = 40, fallback: str = "unnamed") -> str:
    """把任意字符串变成安全的文件名片段（永不用于路径拼接的安全边界，但便于人读）"""
    cleaned = _SAFE_NAME_PATTERN.sub("_", (value or "").strip())
    cleaned = cleaned.strip("._")
    if not cleaned:
        return fallback
    return cleaned[:max_length]


# """函数：解析根目录与文件名并验证规范化路径仍在根目录下"""
def safe_join(root: Path, filename: str) -> Path:
    """在 ``root`` 下安全拼接文件名，越界时抛异常"""
    root = root.resolve()
    candidate = (root / filename).resolve()
    if candidate != root and root not in candidate.parents:
        raise StorageError(
            "非法文件路径：目标越出允许的目录",
            details={"root": str(root), "candidate": str(candidate)},
        )
    return candidate


# """函数：不解码仅从文件头读取图像宽高（支持PNG/JPEG），无法识别时返回None"""
def _header_dimensions(contents: bytes) -> tuple[int, int] | None:
    """从图像头部元数据解析 (width, height)，避免为查尺寸而完整解码大图"""
    try:
        # PNG：8字节签名 + IHDR长度/类型，宽高在第16..24字节
        if contents.startswith(b"\x89PNG\r\n\x1a\n") and len(contents) >= 24:
            width = int.from_bytes(contents[16:20], "big")
            height = int.from_bytes(contents[20:24], "big")
            if width > 0 and height > 0:
                return width, height
            return None
        # JPEG：扫描SOF0-SOF15（排除DHT/DAC/RST）标记段
        if contents.startswith(b"\xff\xd8"):
            i = 2
            while i + 9 < len(contents):
                if contents[i] != 0xFF:
                    i += 1
                    continue
                marker = contents[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    height = int.from_bytes(contents[i + 5 : i + 7], "big")
                    width = int.from_bytes(contents[i + 7 : i + 9], "big")
                    if width > 0 and height > 0:
                        return width, height
                    return None
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seg_len = int.from_bytes(contents[i + 2 : i + 4], "big")
                if seg_len < 2:
                    return None
                i += 2 + seg_len
    except (IndexError, ValueError):
        return None
    return None


# """函数：先按字节限额与头部尺寸预检，再解码并校验内容有效性"""
def decode_image(contents: bytes, *, max_bytes: int | None = None) -> np.ndarray:
    """把上传字节解码为BGR图像，并做尺寸与内容校验

    尺寸检查分两步：先解析文件头（不分配像素内存）拒绝超大图，
    再解码后复核，防止解压炸弹式大图造成内存峰值
    """
    limit = max_bytes or settings.MAX_FILE_SIZE
    if not contents:
        raise ValidationError("上传内容为空")
    if len(contents) > limit:
        raise ValidationError(
            f"文件过大：{len(contents)} 字节，上限 {limit} 字节",
            code="file_too_large",
        )

    # 解码前预检：能从头部拿到尺寸就先拦截超大图，避免峰值内存
    header_dims = _header_dimensions(contents)
    if header_dims is not None and max(header_dims) > MAX_IMAGE_DIMENSION:
        raise ValidationError(f"图片尺寸过大：{header_dims[0]}x{header_dims[1]}")

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


# """函数：生成服务端文件名、写入JPEG，并返回可解析的相对路径"""
def save_screenshot(image: np.ndarray, *, prefix: str = "") -> str:
    """保存截图，返回相对路径（POSIX分隔）

    路径基准：优先相对 ``BASE_DIR``（兼容历史数据）；
    当 ``STORAGE_DIR`` 配置在 ``BASE_DIR`` 之外时，退而相对 ``STORAGE_DIR``，
    由 ``resolve_stored_path`` 按同一规则还原
    """
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
    base_dir = settings.BASE_DIR.resolve()
    if path == base_dir or base_dir in path.parents:
        return path.relative_to(base_dir).as_posix()
    return path.relative_to(settings.STORAGE_DIR.resolve()).as_posix()


# """函数：把数据库存储值解析为绝对路径并限制在STORAGE_DIR内"""
def resolve_stored_path(stored: str | None) -> Path | None:
    """把数据库里的存储路径还原为绝对路径，并做越界校验

    兼容历史数据里可能是绝对路径或反斜杠分隔的情况；
    相对路径先按 ``BASE_DIR`` 还原（历史格式），越界时再按 ``STORAGE_DIR``
    还原（外部存储目录格式），最终结果必须落在 ``STORAGE_DIR`` 内
    """
    if not stored:
        return None

    raw = Path(stored.replace("\\", "/"))
    allowed_root = settings.STORAGE_DIR.resolve()

    candidates = [raw] if raw.is_absolute() else [settings.BASE_DIR / raw, allowed_root / raw]
    for item in candidates:
        candidate = item.resolve()
        # 截图只允许落在storage目录下
        if candidate == allowed_root or allowed_root in candidate.parents:
            return candidate
    logger.warning("拒绝访问越界的截图路径：%s", stored)
    return None


# """函数：解析并删除允许目录内的文件，返回是否实际删除"""
def delete_file(stored: str | None) -> bool:
    """删除 ``storage`` 下的文件。越界路径直接拒绝"""
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

