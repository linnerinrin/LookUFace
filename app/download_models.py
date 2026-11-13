"""模型文件下载器：缺失或为空时从镜像下载models目录所需权重

用法：
    python -m app.download_models            # 补齐缺失文件
    python -m app.download_models --force    # 强制重新下载全部
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

from app.config import settings
from app.logger import logger

# 每个模型文件的候选下载地址，按序回退
MODEL_SOURCES: dict[str, list[str]] = {
    "opencv_face_detector.pbtxt": [
        "https://raw.githubusercontent.com/opencv/opencv/master/samples/dnn/face_detector/opencv_face_detector.pbtxt",
    ],
    "opencv_face_detector_uint8.pb": [
        "https://raw.githubusercontent.com/opencv/opencv_3rdparty/dnn_samples_face_detector_20180220_uint8/opencv_face_detector_uint8.pb",
    ],
    "age_deploy.prototxt": [
        "https://raw.githubusercontent.com/smahesh29/Gender-and-Age-Detection/master/age_deploy.prototxt",
    ],
    "age_net.caffemodel": [
        "https://raw.githubusercontent.com/smahesh29/Gender-and-Age-Detection/master/age_net.caffemodel",
        "https://raw.githubusercontent.com/opencv/opencv_3rdparty/dnn_samples_gender_age/age_net.caffemodel",
    ],
    "gender_deploy.prototxt": [
        "https://raw.githubusercontent.com/smahesh29/Gender-and-Age-Detection/master/gender_deploy.prototxt",
    ],
    "gender_net.caffemodel": [
        "https://raw.githubusercontent.com/smahesh29/Gender-and-Age-Detection/master/gender_net.caffemodel",
        "https://raw.githubusercontent.com/opencv/opencv_3rdparty/dnn_samples_gender_age/gender_net.caffemodel",
    ],
}

_TIMEOUT_SECONDS = 60
_CHUNK_SIZE = 1 << 16


# 判断文件是否可用：存在且非空
def _is_ready(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


# 下载单个文件到临时文件再原子改名，依次尝试候选地址，返回是否成功
def _download_one(name: str, target: Path) -> bool:
    tmp = target.with_suffix(target.suffix + ".tmp")
    for url in MODEL_SOURCES[name]:
        logger.info("下载 %s <- %s", name, url)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "lookuface-model-downloader"})
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as resp, tmp.open("wb") as fh:
                while True:
                    chunk = resp.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    fh.write(chunk)
        except (urllib.error.URLError, OSError) as exc:
            logger.warning("下载失败（%s）：%s", name, exc)
            tmp.unlink(missing_ok=True)
            continue
        if _is_ready(tmp):
            tmp.replace(target)
            logger.info("%s 下载完成（%d字节）", name, target.stat().st_size)
            return True
        logger.warning("下载内容为空：%s", url)
        tmp.unlink(missing_ok=True)
    return False


# 遍历全部必需模型，补齐缺失项，返回失败文件名列表
def ensure_models(force: bool = False) -> list[str]:
    """补齐models目录下的缺失文件，返回下载失败的文件名列表"""
    model_dir = settings.MODEL_DIR
    model_dir.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    for name in MODEL_SOURCES:
        target = model_dir / name
        if not force and _is_ready(target):
            logger.info("%s 已存在，跳过", name)
            continue
        if not _download_one(name, target):
            failed.append(name)
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下载models目录所需的模型文件")
    parser.add_argument("--force", action="store_true", help="强制重新下载全部模型")
    args = parser.parse_args(argv)

    failed = ensure_models(force=args.force)
    if failed:
        logger.error("以下模型下载失败，请手动放入 %s：%s", settings.MODEL_DIR, ", ".join(failed))
        return 1
    logger.info("全部模型文件就绪：%s", settings.MODEL_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())

__all__ = ["ensure_models", "main", "MODEL_SOURCES"]
