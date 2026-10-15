"""人脸特征向量提取与比对（**核心算法修复**）。

旧版的做法与缺陷
----------------
旧版 ``_extract_features`` 仅做去中心化::

    points = np.array([[lm.x, lm.y, lm.z] for lm in landmarks])
    points -= points.mean(axis=0)
    return points.flatten()          # 然后直接算余弦相似度

MediaPipe 的 ``lm.x / lm.y`` 是**归一化图像坐标**，人脸离镜头越近，关键点跨度越大。
只减均值、不除以尺度，会让「同一个人距离变化」造成的向量差异远大于
「不同人同距离」的差异。而阈值 0.925 又落在 468 点形状向量余弦相似度的
天然 0.9+ 密集区间内，因此旧实现实际上无法区分身份。

本模块的做法
------------
1. **相似变换归一化**：去质心 + 除以 RMS 距离，消除平移与缩放影响。
2. **成对距离矩阵**：对归一化后的点集计算 468×468 的两两欧氏距离，
   再除以矩阵最大值。该表示天然对**旋转、平移、缩放**不变，且对表情
   等局部形变具有平滑的鲁棒性。
3. **相似度** = ``1 - mean(|D1 - D2|)``，落在 [0, 1]，语义直观、可直接标定阈值。

序列化格式
----------
特征以 ``b"v2|" + json`` 存储，而不是旧版的 ``pickle.dumps``：

* 不再对**来自数据库的字节流执行反序列化代码**（pickle 反序列化任意对象
  是典型的远程代码执行面）；
* 格式可读、可版本化。读取旧 pickle 记录时不反序列化，直接判为
  「需重新注册」并给出明确提示。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, Protocol

import cv2
import numpy as np

from app.config import settings
from app.core.errors import FaceQualityError, ModelUnavailableError
from app.logger import logger
from app.vision.base import inference_lock

# --------------------------------------------------------------------------- #
# 特征序列化
# --------------------------------------------------------------------------- #

FEATURE_MAGIC = b"v2|"
LEGACY_PICKLE_PREFIXES = (b"\x80", b"c", b"(")  # pickle protocol 2+ / 0 / 0

#: 当前特征算法标识，便于排查「注册版本 vs 识别版本」不一致
IDENTITY_ALGORITHM = "landmark-distance-v2"

#: MediaPipe FaceMesh 关键点数量
LANDMARK_COUNT = 468


class StoredFeatureError(ValueError):
    """存储的特征无法解析（通常是旧版 pickle 记录）。"""


class FaceMeshUnavailable(ModelUnavailableError):
    default_message = "人脸关键点模型不可用"


def serialize_features(matrix: np.ndarray) -> bytes:
    """把距离矩阵序列化为可存储字节。"""
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise ValueError(f"特征必须是方阵，收到 shape={matrix.shape}")
    # 保留 4 位小数，精度足够且体积约为 float64 文本的三分之一
    payload = [[round(float(v), 4) for v in row] for row in matrix.tolist()]
    return FEATURE_MAGIC + json.dumps({"d": payload}, separators=(",", ":")).encode("utf-8")


def deserialize_features(blob: bytes) -> np.ndarray:
    """从存储字节恢复距离矩阵。拒绝反序列化旧 pickle 记录。"""
    if not blob:
        raise StoredFeatureError("特征为空")
    if blob.startswith(LEGACY_PICKLE_PREFIXES) and not blob.startswith(FEATURE_MAGIC):
        raise StoredFeatureError(
            "该人脸使用旧版 pickle 格式存储，出于安全考虑不再反序列化，请重新注册"
        )
    if not blob.startswith(FEATURE_MAGIC):
        raise StoredFeatureError("无法识别的特征格式")

    try:
        data = json.loads(blob[len(FEATURE_MAGIC):].decode("utf-8"))
        matrix = np.asarray(data["d"], dtype=np.float64)
    except Exception as exc:  # noqa: BLE001
        raise StoredFeatureError(f"特征解析失败：{exc}") from exc

    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise StoredFeatureError(f"特征矩阵形状非法：{matrix.shape}")
    return matrix


# --------------------------------------------------------------------------- #
# 特征提取
# --------------------------------------------------------------------------- #


def spatial_normalize(points: np.ndarray) -> np.ndarray:
    """相似变换归一化：去质心 + 按 RMS 距离缩放。

    消除平移与缩放，使同一张脸在不同距离下得到一致的形状表示。
    """
    centered = points - points.mean(axis=0, keepdims=True)
    rms = float(np.sqrt((centered ** 2).sum(axis=1).mean()))
    if rms < 1e-9:
        return centered
    return centered / rms


def pairwise_distance_matrix(points: np.ndarray) -> np.ndarray:
    """计算两两欧氏距离并归一化到 [0, 1]。"""
    squared_norms = (points ** 2).sum(axis=1)
    # |a-b|^2 = |a|^2 + |b|^2 - 2 a·b，向量化后比双重循环快两个数量级
    sq = squared_norms[:, None] + squared_norms[None, :] - 2.0 * (points @ points.T)
    np.maximum(sq, 0.0, out=sq)
    distances = np.sqrt(sq)
    peak = float(distances.max())
    if peak < 1e-9:
        return distances
    return distances / peak


def similarity_from_matrices(left: np.ndarray, right: np.ndarray) -> float:
    """两个距离矩阵的相似度，值域 [0, 1]。"""
    if left.shape != right.shape:
        raise ValueError(f"特征维度不一致：{left.shape} vs {right.shape}")
    return float(1.0 - np.abs(left - right).mean())


@dataclass(frozen=True, slots=True)
class MatchResult:
    name: str = "Unknown"
    confidence: float = 0.0

    @property
    def matched(self) -> bool:
        return self.name != "Unknown"


class IdentityEmbedder(Protocol):
    """识别后端接口。

    当前默认实现为 ``LandmarkDistanceEmbedder``（MediaPipe 关键点距离矩阵）。
    后续若接入 InsightFace 等深度嵌入模型，只需实现本协议并在
    ``get_embedder()`` 中切换，调用方无需改动。
    """

    def extract(self, image: np.ndarray) -> np.ndarray:
        """从图像提取特征；未检出人脸时抛 ``FaceQualityError``。"""
        ...

    def compare(self, left: np.ndarray, right: np.ndarray) -> float:
        """返回 [0, 1] 相似度。"""
        ...


class LandmarkDistanceEmbedder:
    """基于 MediaPipe FaceMesh 关键点距离矩阵的识别后端。"""

    def __init__(self) -> None:
        self._mesh: Any = None
        self._mesh_lock = threading.Lock()

    # -------- 模型生命周期 --------
    def _ensure_mesh(self) -> Any:
        if self._mesh is not None:
            return self._mesh
        with self._mesh_lock:
            if self._mesh is not None:
                return self._mesh
            try:
                import mediapipe as mp
            except ImportError as exc:  # pragma: no cover
                raise FaceMeshUnavailable(f"mediapipe 未安装：{exc}") from exc

            try:
                self._mesh = mp.solutions.face_mesh.FaceMesh(
                    static_image_mode=True,
                    max_num_faces=1,
                    refine_landmarks=True,
                    min_detection_confidence=settings.DETECTION_CONFIDENCE,
                    min_tracking_confidence=settings.TRACKING_CONFIDENCE,
                )
            except Exception as exc:
                raise FaceMeshUnavailable(f"FaceMesh 初始化失败：{exc}") from exc
            logger.info("人脸关键点模型加载成功（LandmarkDistanceEmbedder）")
        return self._mesh

    def close(self) -> None:
        mesh, self._mesh = self._mesh, None
        if mesh is not None:
            try:
                mesh.close()
            except Exception:  # pragma: no cover
                pass

    # -------- 特征 --------
    def extract_landmarks(self, image: np.ndarray) -> np.ndarray:
        """返回 (468, 3) 归一化关键点；未检出人脸时抛 ``FaceQualityError``。"""
        if image is None or image.size == 0:
            raise FaceQualityError("图像为空")

        mesh = self._ensure_mesh()
        # cv2 读入为 BGR，mediapipe 需要 RGB
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        # mediapipe 的 C++ 实现持有内部状态，必须串行
        with inference_lock:
            results = mesh.process(rgb)

        if not results.multi_face_landmarks:
            raise FaceQualityError("未检测到人脸")

        landmarks = results.multi_face_landmarks[0].landmark
        points = np.asarray([[lm.x, lm.y, lm.z] for lm in landmarks], dtype=np.float64)
        if points.shape[0] != LANDMARK_COUNT:
            raise FaceQualityError(
                f"关键点数量异常：期望 {LANDMARK_COUNT}，实际 {points.shape[0]}"
            )
        return points

    def extract(self, image: np.ndarray) -> np.ndarray:
        points = self.extract_landmarks(image)
        return pairwise_distance_matrix(spatial_normalize(points))

    def extract_many(self, image: np.ndarray) -> list[np.ndarray]:
        """一张图里可能有多张脸，返回所有特征。"""
        if image is None or image.size == 0:
            raise FaceQualityError("图像为空")

        mesh = self._ensure_mesh()
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        with inference_lock:
            results = mesh.process(rgb)
        if not results.multi_face_landmarks:
            raise FaceQualityError("未检测到人脸")

        out: list[np.ndarray] = []
        for face_landmarks in results.multi_face_landmarks:
            points = np.asarray(
                [[lm.x, lm.y, lm.z] for lm in face_landmarks.landmark], dtype=np.float64
            )
            if points.shape[0] != LANDMARK_COUNT:
                continue
            out.append(pairwise_distance_matrix(spatial_normalize(points)))
        if not out:
            raise FaceQualityError("未检测到有效人脸")
        return out

    def compare(self, left: np.ndarray, right: np.ndarray) -> float:
        return similarity_from_matrices(left, right)


_embedder: IdentityEmbedder | None = None


def get_embedder() -> IdentityEmbedder:
    """取回全局识别后端（当前为关键点距离矩阵实现）。"""
    global _embedder
    if _embedder is None:
        _embedder = LandmarkDistanceEmbedder()
    return _embedder


def set_embedder(embedder: IdentityEmbedder | None) -> None:
    """替换识别后端（测试或切换到深度嵌入模型时使用）。"""
    global _embedder
    _embedder = embedder


__all__ = [
    "FEATURE_MAGIC",
    "IDENTITY_ALGORITHM",
    "LANDMARK_COUNT",
    "LandmarkDistanceEmbedder",
    "MatchResult",
    "StoredFeatureError",
    "deserialize_features",
    "get_embedder",
    "pairwise_distance_matrix",
    "serialize_features",
    "set_embedder",
    "similarity_from_matrices",
    "spatial_normalize",
]
