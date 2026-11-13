# """架构作用：从检测到的人脸图像推断性别、年龄标签及模型置信度"""
"""性别 / 年龄估计（Caffe模型，Gil Levi & Tal Hassner）"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from app.config import settings
from app.logger import logger
from app.vision.base import ThreadSafeModel, guarded, load_dnn_net

_INPUT_SIZE = (227, 227)
# 官方均值，RGB顺序；配合swapRB=True使用
_MEAN_SUBTRACTION = (78.4263377603, 87.7689143744, 114.895847746)

_GENDER_LABELS = ("Male", "Female")
# 模型训练时的8个年龄段
_AGE_LABELS = (
    "(0-2)",
    "(4-6)",
    "(8-12)",
    "(15-20)",
    "(25-32)",
    "(38-43)",
    "(48-53)",
    "(60-100)",
)

UNKNOWN = "Unknown"


@dataclass(frozen=True, slots=True)
class Demographics:
    """性别年龄估计结果"""

    gender: str = UNKNOWN
    age: str = UNKNOWN
    confidence: float = 0.0

    @property
    def is_known(self) -> bool:
        return self.gender != UNKNOWN or self.age != UNKNOWN


class GenderAgeModel(ThreadSafeModel["GenderAgeModel"]):
    """性别年龄模型（线程安全单例）"""

    # """方法：加载性别和年龄网络，并缓存输入门限与标签"""
    def _build(self) -> None:
        self.gender_net = load_dnn_net(
            settings.gender_model_path,
            settings.gender_proto_path,
            label="性别识别",
        )
        self.age_net = load_dnn_net(
            settings.age_model_path,
            settings.age_proto_path,
            label="年龄识别",
        )
        self.min_face_size = settings.MIN_FACE_SIZE
        self.gender_labels = _GENDER_LABELS
        self.age_labels = _AGE_LABELS

# """方法：将人脸裁剪图预处理后依次前向推理，聚合两个输出置信度"""
    @guarded
    def predict(self, face: np.ndarray) -> Demographics:
        """预测性别与年龄，返回 (gender, age, confidence)"""
        if face is None or face.size == 0:
            return Demographics()
        if min(face.shape[0], face.shape[1]) < self.min_face_size:
            return Demographics()

        try:
            blob = cv2.dnn.blobFromImage(
                face,
                1.0,
                _INPUT_SIZE,
                _MEAN_SUBTRACTION,
                swapRB=True,
                crop=False,
            )

            self.gender_net.setInput(blob)
            gender_out = self.gender_net.forward()[0]
            gender_index = int(np.argmax(gender_out))
            gender_conf = float(gender_out[gender_index])

            self.age_net.setInput(blob)
            age_out = self.age_net.forward()[0]
            age_index = int(np.argmax(age_out))
            age_conf = float(age_out[age_index])
        except Exception:
            logger.exception("性别年龄推理失败")
            return Demographics()

        gender = self.gender_labels[gender_index] if 0 <= gender_index < len(self.gender_labels) else UNKNOWN
        age = self.age_labels[age_index] if 0 <= age_index < len(self.age_labels) else UNKNOWN
        return Demographics(gender=gender, age=age, confidence=(gender_conf + age_conf) / 2.0)


def get_gender_age_model() -> GenderAgeModel:
    return GenderAgeModel.instance()


__all__ = ["GenderAgeModel", "Demographics", "get_gender_age_model", "UNKNOWN"]

