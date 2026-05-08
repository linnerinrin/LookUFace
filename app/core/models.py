"""
年龄/性别识别模型
- 使用 Caffe 预训练模型
- 年龄分为8个年龄段
- 性别分为男/女
"""

import cv2
import numpy as np
from typing import Tuple
from app.config import settings


class GenderAgeModel:
    """性别年龄识别模型（单例）"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """加载模型"""
        self.gender_net = cv2.dnn.readNet(
            str(settings.gender_model_path),
            str(settings.gender_proto_path)
        )
        self.age_net = cv2.dnn.readNet(
            str(settings.age_model_path),
            str(settings.age_proto_path)
        )
        self.gender_list = ["Male", "Female"]
        self.age_list = ["0-4", "4-8", "8-12", "12-20", "20-38", "38-48", "48-60", "60+"]

    def predict(self, face: np.ndarray) -> Tuple[str, str, float]:
        """预测性别和年龄，返回 (gender, age, confidence)"""
        if face.size == 0 or face.shape[0] < 50 or face.shape[1] < 50:
            return ("Unknown", "Unknown", 0.0)

        blob = cv2.dnn.blobFromImage(face, 1.0, (227, 227), (78, 88, 115), swapRB=False)

        # 性别预测
        self.gender_net.setInput(blob)
        gender_out = self.gender_net.forward()[0]
        gender_idx = gender_out.argmax()
        gender_conf = gender_out[gender_idx]

        # 年龄预测
        self.age_net.setInput(blob)
        age_out = self.age_net.forward()[0]
        age_idx = age_out.argmax()
        age_conf = age_out[age_idx]

        return (
            self.gender_list[gender_idx],
            self.age_list[age_idx],
            float((gender_conf + age_conf) / 2)
        )