# """架构作用：负责人脸关键点归一化、特征序列化及身份相似度计算"""
"""人脸特征向量提取与比对

1. **相似变换归一化**：去质心 + 除以RMS距离，消除平移与缩放影响
2. **成对距离矩阵**：对归一化后的点集计算468×468的两两欧氏距离，
   再除以矩阵最大值。该表示天然对**旋转、平移、缩放**不变，且对表情
   等局部形变具有平滑的鲁棒性
3. **相似度** = ``1 - mean(|D1 - D2|)``，落在 [0, 1]，语义直观、可直接标定阈值

序列化格式
----------
特征以 ``b"v2|" + json`` 存储：

* 不对来自数据库的字节流执行pickle反序列化；
* 格式可读、可版本化。读取旧pickle记录时不反序列化，直接判为
  「需重新注册」并给出明确提示
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

#: 当前特征算法标识，便于排查「注册版本vs识别版本」不一致
IDENTITY_ALGORITHM = "landmark-distance-v2"

#: 特征编码版本号：维度/后端变更时递增，旧版本特征判为「需重新注册」
FEATURE_VERSION = 2

# --------------------------------------------------------------------------- #
# 关键点数量
# --------------------------------------------------------------------------- #
#: 面部网格关键点数（也是特征矩阵的固定维度）
LANDMARK_COUNT = 468

#: 开启refine_landmarks时的总点数（多出的10个为虹膜点）
REFINED_LANDMARK_COUNT = 478

#: 可接受的点数集合。超出该集合说明mediapipe行为变化，需要重新评估算法
ACCEPTED_LANDMARK_COUNTS = (LANDMARK_COUNT, REFINED_LANDMARK_COUNT)


class StoredFeatureError(ValueError):
    """存储的特征无法解析（通常是旧版pickle记录）"""


class FaceMeshUnavailable(ModelUnavailableError):
    default_message = "人脸关键点模型不可用"


# """模块：特征编码；方阵转四位小数JSON，带版本号与维度元数据，加v2前缀供数据库保存"""
def serialize_features(matrix: np.ndarray) -> bytes:
    """把距离矩阵序列化为可存储字节（含编码版本与维度，供后端迁移时校验）"""
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise ValueError(f"特征必须是方阵，收到 shape={matrix.shape}")
    # 保留4位小数，精度足够且体积约为float64文本的三分之一
    payload = [[round(float(v), 4) for v in row] for row in matrix.tolist()]
    envelope = {"v": FEATURE_VERSION, "n": matrix.shape[0], "d": payload}
    return FEATURE_MAGIC + json.dumps(envelope, separators=(",", ":")).encode("utf-8")


# """函数：校验版本前缀、编码版本与维度，并拒绝pickle，将JSON恢复为二维方阵"""
def deserialize_features(blob: bytes) -> np.ndarray:
    """从存储字节恢复距离矩阵。拒绝反序列化旧pickle记录"""
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

    # 版本与信封完整性校验：显式版本号不一致时说明后端已变更，旧特征不可比，
    # 必须显式判为「需重新注册」而非静默失效；n与实际形状不一致说明数据损坏
    # 无版本号的历史数据按当前版本兼容处理（同为方阵布局）
    version = data.get("v", FEATURE_VERSION)
    if version != FEATURE_VERSION:
        raise StoredFeatureError(
            f"特征编码版本不一致：存储 v{version}，当前 v{FEATURE_VERSION}，请重新注册"
        )
    dim = data.get("n")
    if dim is not None and int(dim) != matrix.shape[0]:
        raise StoredFeatureError(
            f"特征信封维度与数据不符：n={dim}，实际 shape={matrix.shape}"
        )
    return matrix


# --------------------------------------------------------------------------- #
# 特征提取
# --------------------------------------------------------------------------- #


# """模块：几何特征流水线；点坐标先去中心再按RMS缩放"""
def spatial_normalize(points: np.ndarray) -> np.ndarray:
    """相似变换归一化：去质心 + 按RMS距离缩放

    消除平移与缩放，使同一张脸在不同距离下得到一致的形状表示
    """
    centered = points - points.mean(axis=0, keepdims=True)
    rms = float(np.sqrt((centered ** 2).sum(axis=1).mean()))
    if rms < 1e-9:
        return centered
    return centered / rms


# """函数：以内积展开距离平方，截断浮点负误差并按最大距离归一化"""
def pairwise_distance_matrix(points: np.ndarray) -> np.ndarray:
    """计算两两欧氏距离并归一化到 [0, 1]"""
    squared_norms = (points ** 2).sum(axis=1)
    # |a-b|^2 = |a|^2 + |b|^2 - 2 a·b，向量化后比双重循环快两个数量级
    sq = squared_norms[:, None] + squared_norms[None, :] - 2.0 * (points @ points.T)
    np.maximum(sq, 0.0, out=sq)
    distances = np.sqrt(sq)
    peak = float(distances.max())
    if peak < 1e-9:
        return distances
    return distances / peak


# """函数：相同形状矩阵的平均绝对差映射为相似度；阈值需实测标定"""
def similarity_from_matrices(left: np.ndarray, right: np.ndarray) -> float:
    """两个距离矩阵的相似度，值域 [0, 1]"""
    if left.shape != right.shape:
        raise ValueError(f"特征维度不一致：{left.shape} vs {right.shape}")
    return float(1.0 - np.abs(left - right).mean())


#: 未匹配时的展示用哨兵名（仅展示，不参与匹配判断）
UNKNOWN_NAME = "Unknown"


@dataclass(frozen=True, slots=True)
class MatchResult:
    name: str = UNKNOWN_NAME
    confidence: float = 0.0
    #: 独立匹配标识：不依赖名字哨兵，真实姓名恰好叫 "Unknown" 也不会冲突
    matched: bool = False


class IdentityEmbedder(Protocol):
    """识别后端接口

    当前默认实现为 ``LandmarkDistanceEmbedder``（MediaPipe关键点距离矩阵）
    后续若接入InsightFace等深度嵌入模型，只需实现本协议并在
    ``get_embedder()`` 中切换，调用方无需改动
    """

    def extract(self, image: np.ndarray) -> np.ndarray:
        """从图像提取特征；未检出人脸时抛 ``FaceQualityError``"""
        ...

    def compare(self, left: np.ndarray, right: np.ndarray) -> float:
        """返回 [0, 1] 相似度"""
        ...


class LandmarkDistanceEmbedder:
    """基于MediaPipe FaceMesh关键点距离矩阵的识别后端"""

    def __init__(self) -> None:
        self._mesh: Any = None
        self._mesh_lock = threading.Lock()

    # -------- 模型生命周期 --------
    # """方法：双检锁延迟加载FaceMesh，导入或构造失败映射为模型异常"""
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

    # """方法：先撤下模型引用，再尽力关闭底层FaceMesh资源"""
    def close(self) -> None:
        mesh, self._mesh = self._mesh, None
        if mesh is not None:
            try:
                mesh.close()
            except Exception:  # pragma: no cover
                pass

    # -------- 特征 --------
    # """方法：验证468/478点输出，仅保留前468点以固定存储维度"""
    @staticmethod
    def normalize_landmarks(landmarks: Any) -> np.ndarray:
        """把mediapipe关键点转成 **(468, 3)** 坐标数组

        无论模型返回468还是478点，都只取前 ``LANDMARK_COUNT`` 个面部
        网格点，丢弃末尾的虹膜点
        """
        points = np.asarray(
            [[lm.x, lm.y, lm.z] for lm in landmarks], dtype=np.float64
        )

        count = points.shape[0]
        if count not in ACCEPTED_LANDMARK_COUNTS:
            # 点数既不是468也不是478，说明mediapipe行为已变化
            # 此时**不能**盲目截断或补零，会得到无意义的特征，必须显式报错
            raise FaceQualityError(
                f"关键点数量异常：期望 {LANDMARK_COUNT}（面部网格）"
                f"或 {REFINED_LANDMARK_COUNT}（含虹膜细化），实际 {count}。"
                "若 mediapipe 版本已升级，请重新核对特征算法与 RECOGNIZE_THRESHOLD。",
                code="unexpected_landmark_count",
                details={"expected": list(ACCEPTED_LANDMARK_COUNTS), "actual": int(count)},
            )

        # 478 -> 468：丢弃末尾10个虹膜点，保证特征维度恒定
        return points[:LANDMARK_COUNT]

    # """方法：BGR转RGB后加全局推理锁，模型输出转换为固定点数坐标"""
    def _detect_landmarks(self, image: np.ndarray) -> list[np.ndarray]:
        """检测并返回图中每张脸的 (468, 3) 坐标"""
        if image is None or image.size == 0:
            raise FaceQualityError("图像为空")

        mesh = self._ensure_mesh()
        # cv2读入为BGR，mediapipe需要RGB
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        # mediapipe的C++ 实现持有内部状态，必须串行
        with inference_lock:
            results = mesh.process(rgb)

        if not results.multi_face_landmarks:
            raise FaceQualityError("未检测到人脸")

        return [
            self.normalize_landmarks(face.landmark)
            for face in results.multi_face_landmarks
        ]

    # """方法：取检测列表首张脸的关键点，未检出由下游抛质量异常"""
    def extract_landmarks(self, image: np.ndarray) -> np.ndarray:
        """返回 (468, 3) 归一化坐标；未检出人脸时抛 ``FaceQualityError``"""
        return self._detect_landmarks(image)[0]

    def extract(self, image: np.ndarray) -> np.ndarray:
        points = self.extract_landmarks(image)
        return pairwise_distance_matrix(spatial_normalize(points))

    # """方法：逐脸生成距离矩阵；当前FaceMesh配置max_num_faces=1"""
    def extract_many(self, image: np.ndarray) -> list[np.ndarray]:
        """一张图里可能有多张脸，返回所有特征"""
        return [
            pairwise_distance_matrix(spatial_normalize(points))
            for points in self._detect_landmarks(image)
        ]

    def compare(self, left: np.ndarray, right: np.ndarray) -> float:
        return similarity_from_matrices(left, right)


_embedder: IdentityEmbedder | None = None


# """模块：后端装配；首次访问创建默认关键点距离后端"""
def get_embedder() -> IdentityEmbedder:
    """取回全局识别后端（当前为关键点距离矩阵实现）"""
    global _embedder
    if _embedder is None:
        _embedder = LandmarkDistanceEmbedder()
    return _embedder


# """函数：替换全局后端引用；调用方承担旧实例的资源关闭责任"""
def set_embedder(embedder: IdentityEmbedder | None) -> None:
    """替换识别后端（测试或切换到深度嵌入模型时使用）"""
    global _embedder
    _embedder = embedder


__all__ = [
    "ACCEPTED_LANDMARK_COUNTS",
    "FEATURE_MAGIC",
    "FEATURE_VERSION",
    "IDENTITY_ALGORITHM",
    "LANDMARK_COUNT",
    "LandmarkDistanceEmbedder",
    "MatchResult",
    "REFINED_LANDMARK_COUNT",
    "StoredFeatureError",
    "UNKNOWN_NAME",
    "deserialize_features",
    "get_embedder",
    "pairwise_distance_matrix",
    "serialize_features",
    "set_embedder",
    "similarity_from_matrices",
    "spatial_normalize",
]

