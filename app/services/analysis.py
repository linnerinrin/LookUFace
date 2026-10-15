"""人脸分析编排：检测 → 性别年龄 → 身份识别，并带识别结果缓存。

相对旧版的修复：
1. **识别结果缓存（解决 P0 请求风暴的关键一环）**：旧版每个 WS 帧都会对每个人脸
   重新做一次 MediaPipe 推理 + 一次数据库全表扫描。现在对
   ``(已知人脸数, ROI 指纹)`` 做短 TTL 缓存，同一张脸在 TTL 内只算一次。
2. **单次遍历**：检测与识别复用同一个 ROI，不再重复裁剪。
3. **批量载入**：一次取出该用户的全部人脸特征，在内存中比对。
4. **异常隔离**：单个模型失败不会让整帧 500，而是该字段降级为 Unknown。
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime

import cv2
import numpy as np

from app.config import settings
from app.core.errors import AppError, FaceQualityError
from app.db.models import SessionLocal, UserFace
from app.db.repository import list_faces
from app.logger import logger
from app.vision.base import inference_lock
from app.vision.demographics import Demographics, get_gender_age_model
from app.vision.detection import FaceBox, get_face_detector
from app.vision.face_identity import (
    StoredFeatureError,
    deserialize_features,
    get_embedder,
    serialize_features,
)

UNKNOWN = "Unknown"

#: 指纹计算时把 ROI 缩放到的最长边
_FINGERPRINT_SIZE = 32


@dataclass(slots=True)
class AnalyzedFace:
    """单张人脸的完整分析结果。"""

    bbox: list[int]
    gender: str = UNKNOWN
    age: str = UNKNOWN
    confidence: float = 0.0
    identity: str = UNKNOWN
    identity_confidence: float = 0.0
    detection_confidence: float = 0.0

    @property
    def is_known(self) -> bool:
        return self.identity != UNKNOWN


@dataclass(slots=True)
class FrameAnalysis:
    """一帧的分析结果。"""

    faces: list[AnalyzedFace] = field(default_factory=list)
    processing_ms: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def face_count(self) -> int:
        return len(self.faces)


class _MatchCache:
    """定长 + TTL 的识别结果缓存（LRU 淘汰）。"""

    def __init__(self, maxsize: int = 512, ttl_seconds: float = 3.0) -> None:
        self._maxsize = maxsize
        self._ttl = ttl_seconds
        self._data: OrderedDict[str, tuple[float, tuple[str, float]]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> tuple[str, float] | None:
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at < now:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    def put(self, key: str, value: tuple[str, float]) -> None:
        with self._lock:
            self._data[key] = (time.monotonic() + self._ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


def feature_fingerprint(image: np.ndarray) -> str:
    """对缩放后的 ROI 取哈希，作为识别缓存键。"""
    if image is None or image.size == 0:
        return "empty"

    height, width = image.shape[:2]
    longest = max(height, width)
    if longest > _FINGERPRINT_SIZE:
        scale = _FINGERPRINT_SIZE / float(longest)
        new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        with inference_lock:
            resized = cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)
    else:
        resized = image
    return hashlib.blake2b(resized.tobytes(), digest_size=12).hexdigest()


class FaceAnalysisService:
    """人脸分析服务（无状态，可安全共享）。"""

    CACHE_TTL_SECONDS = 3.0

    def __init__(self) -> None:
        self._cache = _MatchCache(ttl_seconds=self.CACHE_TTL_SECONDS)

    # ------------------------------------------------------------------ #
    # 分析
    # ------------------------------------------------------------------ #
    def analyze_image(
        self,
        image: np.ndarray,
        *,
        detect_gender_age: bool = True,
        detect_identity: bool = True,
        user_id: int | None = None,
    ) -> FrameAnalysis:
        """分析整帧图像。"""
        started = time.perf_counter()
        if image is None or image.size == 0:
            return FrameAnalysis(faces=[], processing_ms=0.0)

        try:
            boxes: list[FaceBox] = get_face_detector().detect(image)
        except AppError:
            raise
        except Exception:
            logger.exception("人脸检测失败")
            boxes = []

        # 识别所需的已知特征只查一次
        known: list[tuple[str, np.ndarray]] = []
        if detect_identity and user_id is not None and boxes:
            known = self.load_known_features(user_id)

        faces: list[AnalyzedFace] = []
        for box in boxes:
            faces.append(
                self._analyze_single_face(
                    image,
                    box,
                    detect_gender_age=detect_gender_age,
                    detect_identity=detect_identity,
                    known=known,
                )
            )

        elapsed_ms = (time.perf_counter() - started) * 1000
        return FrameAnalysis(faces=faces, processing_ms=round(elapsed_ms, 2))

    def _analyze_single_face(
        self,
        image: np.ndarray,
        box: FaceBox,
        *,
        detect_gender_age: bool,
        detect_identity: bool,
        known: list[tuple[str, np.ndarray]],
    ) -> AnalyzedFace:
        roi = box.crop(image)
        result = AnalyzedFace(bbox=box.as_list(), detection_confidence=round(box.confidence, 4))

        if roi.size == 0:
            return result

        if detect_gender_age:
            demo = self._safe_demographics(roi)
            result.gender = demo.gender
            result.age = demo.age
            result.confidence = round(demo.confidence, 4)

        if detect_identity:
            name, score = self._recognize(roi, known)
            result.identity = name
            result.identity_confidence = round(score, 4)

        return result

    @staticmethod
    def _safe_demographics(roi: np.ndarray) -> Demographics:
        try:
            return get_gender_age_model().predict(roi)
        except AppError as exc:
            logger.warning("性别年龄不可用：%s", exc.message)
            return Demographics()
        except Exception:
            logger.exception("性别年龄推理异常")
            return Demographics()

    # ------------------------------------------------------------------ #
    # 识别
    # ------------------------------------------------------------------ #
    @staticmethod
    def load_known_features(user_id: int) -> list[tuple[str, np.ndarray]]:
        """从数据库载入该用户所有可用的特征矩阵。"""
        db = SessionLocal()
        try:
            records = list(list_faces(db, user_id))
        finally:
            db.close()

        known: list[tuple[str, np.ndarray]] = []
        for record in records:
            try:
                known.append((record.name, deserialize_features(record.features)))
            except StoredFeatureError as exc:
                # 旧 pickle 记录：不复用、不反序列化
                logger.warning("人脸 %s 的特征不可用（%s）", record.name, exc)
        return known

    def _recognize(
        self,
        roi: np.ndarray,
        known: list[tuple[str, np.ndarray]],
    ) -> tuple[str, float]:
        if not known:
            return UNKNOWN, 0.0

        cache_key = f"{len(known)}:{feature_fingerprint(roi)}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        embedder = get_embedder()
        try:
            probe = embedder.extract(roi)
        except FaceQualityError:
            return UNKNOWN, 0.0
        except AppError:
            return UNKNOWN, 0.0
        except Exception:
            logger.exception("特征提取异常")
            return UNKNOWN, 0.0

        threshold = settings.RECOGNIZE_THRESHOLD
        best_name, best_score = UNKNOWN, 0.0
        for name, matrix in known:
            try:
                score = embedder.compare(probe, matrix)
            except ValueError:
                # 维度不一致（不同算法版本），跳过而不是崩溃
                continue
            if score > best_score:
                best_name, best_score = name, score

        if best_score < threshold:
            best_name = UNKNOWN

        self._cache.put(cache_key, (best_name, best_score))
        return best_name, best_score

    # ------------------------------------------------------------------ #
    # 注册
    # ------------------------------------------------------------------ #
    def extract_features(self, image: np.ndarray) -> bytes:
        """提取可存储的特征字节。人脸不合格时抛 ``FaceQualityError``。"""
        try:
            matrix = get_embedder().extract(image)
        except FaceQualityError:
            raise
        except AppError:
            raise
        except Exception as exc:
            raise FaceQualityError(f"特征提取失败：{exc}") from exc
        return serialize_features(matrix)

    def clear_cache(self) -> None:
        self._cache.clear()


face_analysis_service = FaceAnalysisService()

__all__ = [
    "AnalyzedFace",
    "FaceAnalysisService",
    "FrameAnalysis",
    "face_analysis_service",
    "feature_fingerprint",
]
