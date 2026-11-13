# """架构作用：以OpenCV DNN将原始帧转换为按面积排序的人脸框"""
"""人脸检测（OpenCV DNN，SSD ResNet-10）"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from app.config import settings
from app.vision.base import ThreadSafeModel, guarded, load_dnn_net

# OpenCV face detector的输入规格
_INPUT_SIZE = (300, 300)
# 训练时使用的BGR均值（OpenCV官方示例值）
_MEAN_SUBTRACTION = (104.0, 117.0, 123.0)
_SCALE_FACTOR = 1.0


@dataclass(frozen=True, slots=True)
class FaceBox:
    """一个人脸框"""

    x1: int
    y1: int
    x2: int
    y2: int
    confidence: float

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    def as_list(self) -> list[int]:
        return [self.x1, self.y1, self.x2, self.y2]

    def crop(self, image: np.ndarray) -> np.ndarray:
        return image[self.y1:self.y2, self.x1:self.x2]


class FaceDetector(ThreadSafeModel["FaceDetector"]):
    """OpenCV DNN人脸检测器（线程安全单例）"""

    # """方法：加载检测网络及其阈值、最小人脸尺寸配置"""
    def _build(self) -> None:
        self.net = load_dnn_net(
            settings.face_model_path,
            settings.face_proto_path,
            label="人脸检测",
        )
        self.conf_threshold = settings.FACE_CONF_THRESHOLD
        self.min_face_size = settings.MIN_FACE_SIZE

# """方法：把帧转换为DNN blob，筛选、裁剪并按面积排序检测框"""
    @guarded
    def detect(
        self,
        frame: np.ndarray,
        *,
        conf_threshold: float | None = None,
        apply_min_size: bool = True,
    ) -> list[FaceBox]:
        """检测人脸框，按面积从大到小返回"""
        if frame is None or frame.size == 0:
            return []

        height, width = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(
            frame,
            _SCALE_FACTOR,
            _INPUT_SIZE,
            _MEAN_SUBTRACTION,
            swapRB=True,
            crop=False,
        )
        self.net.setInput(blob)
        detections = self.net.forward()

        threshold = self.conf_threshold if conf_threshold is None else conf_threshold
        boxes: list[FaceBox] = []

        for index in range(detections.shape[2]):
            confidence = float(detections[0, 0, index, 2])
            if confidence <= threshold:
                continue

            x1 = int(detections[0, 0, index, 3] * width)
            y1 = int(detections[0, 0, index, 4] * height)
            x2 = int(detections[0, 0, index, 5] * width)
            y2 = int(detections[0, 0, index, 6] * height)

            # 裁剪到图像范围内
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(width, x2), min(height, y2)
            if x2 <= x1 or y2 <= y1:
                continue

            box = FaceBox(x1, y1, x2, y2, confidence)
            if apply_min_size and min(box.width, box.height) < self.min_face_size:
                continue
            boxes.append(box)

        boxes.sort(key=lambda b: b.area, reverse=True)
        return boxes


def get_face_detector() -> FaceDetector:
    """取回全局检测器（首次调用时加载模型）"""
    return FaceDetector.instance()


__all__ = ["FaceDetector", "FaceBox", "get_face_detector"]

