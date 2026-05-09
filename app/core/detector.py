"""
app/core/detector.py
人脸检测器（OpenCV DNN）
- 使用 OpenCV 预训练模型检测人脸
- 返回边界框坐标列表
- 基底模型 检测脸显示并给后边的模型用
"""

import cv2
import numpy as np
from typing import List, Tuple
from app.config import settings


class FaceDetector:
    """OpenCV DNN 人脸检测器（单例）"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """加载模型"""
        self.net = cv2.dnn.readNet(
            str(settings.face_model_path),
            str(settings.face_proto_path)
        )
        self.conf_threshold = settings.FACE_CONF_THRESHOLD

    def detect(self, frame: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """检测人脸，返回 [(x1, y1, x2, y2), ...]"""
        h, w = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(frame, 1.0, (300, 300), [104, 117, 123], True, False)
        self.net.setInput(blob)
        detections = self.net.forward()

        boxes = []
        for i in range(detections.shape[2]):
            confidence = detections[0, 0, i, 2]
            if confidence > self.conf_threshold:
                x1 = int(detections[0, 0, i, 3] * w)
                y1 = int(detections[0, 0, i, 4] * h)
                x2 = int(detections[0, 0, i, 5] * w)
                y2 = int(detections[0, 0, i, 6] * h)
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2), min(h, y2)
                if x2 > x1 and y2 > y1:
                    boxes.append((x1, y1, x2, y2))
        return boxes
face_detector=FaceDetector()