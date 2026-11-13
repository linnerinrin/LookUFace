# """架构作用：把视觉检测、人口属性推理、特征库读取与身份匹配封装为帧级分析能力"""
# """模块：输出人脸框、属性、身份和耗时；以特征库快照缓存和ROI匹配缓存减少重复推理"""
"""人脸分析编排：检测 → 性别年龄 → 身份识别，并带识别结果缓存

1. **识别结果缓存**：对 ``(特征库世代, 已知人脸数, 阈值, ROI指纹)`` 做短TTL缓存，
   同一张脸在TTL内只算一次
2. **单次遍历**：检测与识别复用同一个ROI，不重复裁剪
3. **批量载入**：一次取出全部人脸特征，在内存中比对
4. **异常隔离**：单个模型失败不会让整帧500，而是该字段降级为Unknown
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

#: 指纹计算时把ROI缩放到的最长边
_FINGERPRINT_SIZE = 32


@dataclass(slots=True)
class AnalyzedFace:
    """单张人脸的完整分析结果"""

    bbox: list[int]
    gender: str = UNKNOWN
    age: str = UNKNOWN
    confidence: float = 0.0
    identity: str = UNKNOWN
    identity_confidence: float = 0.0
    detection_confidence: float = 0.0

    # 已知身份以统一哨兵字符串判断，供帧流水线筛选可触发会话动作的人脸
    @property
    def is_known(self) -> bool:
        return self.identity != UNKNOWN


@dataclass(slots=True)
class FrameAnalysis:
    """一帧的分析结果"""

    faces: list[AnalyzedFace] = field(default_factory=list)
    processing_ms: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)

    # 数量直接由本帧结果列表派生，避免单独维护计数与列表不同步
    @property
    def face_count(self) -> int:
        return len(self.faces)


class _MatchCache:
    """定长 + TTL的识别结果缓存（LRU淘汰）"""

    # 每个分析服务各持有一个有界缓存，锁只保护容器操作，不覆盖模型推理
    def __init__(self, maxsize: int = 512, ttl_seconds: float = 3.0) -> None:
        self._maxsize = maxsize
        self._ttl = ttl_seconds
        self._data: OrderedDict[str, tuple[float, tuple[str, float]]] = OrderedDict()
        self._lock = threading.Lock()

    # 查询只检查当前键的TTL；命中后移动到队尾，容量满时优先淘汰最久未访问项
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

    # 写入前先批量清理已过期项，让容量/统计不被无人再查的过期键占用
    def put(self, key: str, value: tuple[str, float]) -> None:
        now = time.monotonic()
        with self._lock:
            self._purge_expired(now)
            self._data[key] = (now + self._ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    # 批量过期清理：遍历删除所有TTL已过期的键（put与诊断时触发）
    def _purge_expired(self, now: float) -> None:
        expired = [k for k, (expires_at, _) in self._data.items() if expires_at < now]
        for k in expired:
            self._data.pop(k, None)

    # 显式失效只清空当前内容，不能阻止已经开始的识别稍后重新写回
    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    # 返回未过期条目数（先批量清理过期项），供诊断使用
    def __len__(self) -> int:
        with self._lock:
            self._purge_expired(time.monotonic())
            return len(self._data)


# ROI先等比缩到最长32像素再做短哈希，以牺牲细节换取低成本、可复用的匹配缓存键
def feature_fingerprint(image: np.ndarray) -> str:
    """对缩放后的ROI取哈希，作为识别缓存键"""
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
    """人脸分析服务（无状态，可安全共享）"""

    CACHE_TTL_SECONDS = 3.0

    #: 已知特征的内存缓存有效期（秒）。理由见load_known_features的说明
    KNOWN_FEATURES_TTL_SECONDS = 5.0

    # 分开保存ROI匹配和特征库快照，两者寿命不同，更新人脸库时需要同时失效
    def __init__(self) -> None:
        self._cache = _MatchCache(ttl_seconds=self.CACHE_TTL_SECONDS)
        #: (过期时刻monotonic, 特征列表)。单租户后只有一份，不再是dict
        self._known_cache: tuple[float, list[tuple[str, np.ndarray]]] | None = None
        self._known_lock = threading.Lock()
        #: 特征库世代号：注册/改名/删除时 +1，用于把匹配缓存键与快照回填
        #: 绑定到同一代特征库，避免跨clear_cache的并发载入/识别回填旧快照
        self._generation = 0

    def _generation_snapshot(self) -> int:
        with self._known_lock:
            return self._generation

    # ------------------------------------------------------------------ #
    # 分析
    # ------------------------------------------------------------------ #
    # 一帧只检测一次、读取一次特征库快照；各人脸共享该快照，结果耗时包含所有分析步骤
    def analyze_image(
        self,
        image: np.ndarray,
        *,
        detect_gender_age: bool = True,
        detect_identity: bool = True,
    ) -> FrameAnalysis:
        """分析整帧图像"""
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
        if detect_identity and boxes:
            known = self.load_known_features()

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

    # 同一裁剪ROI依次送入可选属性模型与身份模型；空裁剪保留框信息并返回Unknown字段
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

    # 属性推理失败只降级这一张脸的属性，身份识别仍可继续；业务异常和意外异常分级记录
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
    # 锁内读取/替换快照，昂贵的数据库读取和反序列化在锁外执行，避免阻塞其他命中请求
    def load_known_features(self) -> list[tuple[str, np.ndarray]]:
        """载入全部可用的特征矩阵（带短TTL缓存）"""
        now = time.monotonic()

        with self._known_lock:
            cached = self._known_cache
            if cached is not None and cached[0] > now:
                return cached[1]
            gen_before = self._generation

        loaded = self._load_known_features_uncached()

        with self._known_lock:
            if self._generation != gen_before:
                # 载入期间人脸库已变更（clear_cache），本份快照已过时：
                # 不回填缓存，直接返回给本次调用（其内容不晚于读取时刻）
                return loaded
            self._known_cache = (now + self.KNOWN_FEATURES_TTL_SECONDS, loaded)
        return loaded

    # 独立短生命周期数据库会话取出记录，关闭连接后逐条解析；坏的历史特征只跳过该记录
    def _load_known_features_uncached(self) -> list[tuple[str, np.ndarray]]:
        """真正读库并解析特征"""
        db = SessionLocal()
        try:
            records = list(list_faces(db))
        finally:
            db.close()

        known: list[tuple[str, np.ndarray]] = []
        for record in records:
            try:
                known.append((record.name, deserialize_features(record.features)))
            except StoredFeatureError as exc:
                # 旧pickle记录：不复用、不反序列化
                logger.warning("人脸 %s 的特征不可用（%s）", record.name, exc)

        logger.debug(
            "已载入 %d 条人脸特征（缓存 %.1f 秒）",
            len(known),
            self.KNOWN_FEATURES_TTL_SECONDS,
        )
        return known

    # 缓存未命中才提取探针特征，遍历库中矩阵取最高相似度，再用配置阈值决定是否接受身份
    def _recognize(
        self,
        roi: np.ndarray,
        known: list[tuple[str, np.ndarray]],
    ) -> tuple[str, float]:
        if not known:
            return UNKNOWN, 0.0

        # 缓存键绑定：特征库世代号 + 数量 + 阈值配置 + ROI指纹；
        # 改名/换特征/调阈值后旧键自然失效，不会再返回旧身份
        generation = self._generation_snapshot()
        cache_key = (
            f"g{generation}:n{len(known)}:t{settings.RECOGNIZE_THRESHOLD}:"
            f"{feature_fingerprint(roi)}"
        )
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
        mismatched: list[str] = []
        for name, matrix in known:
            try:
                score = embedder.compare(probe, matrix)
            except ValueError:
                # 维度不一致（不同算法版本）时跳过而不是崩溃
                #
                # 注意：这里是**静默跳过** —— 调用方只会看到「陌生人」，
                # 不会有任何报错。正因如此，特征维度必须保持不变
                # （见app/vision/face_identity.py中关于468/478的说明）：
                # 一旦新旧特征维度不同，识别会无声失效而很难被发现
                mismatched.append(f"{name}({matrix.shape[0]})")
                continue
            if score > best_score:
                best_name, best_score = name, score

        if mismatched:
            logger.warning(
                "有 %d 条人脸特征维度与当前算法不一致，已跳过（这些脸无法被识别，"
                "请重新注册）：%s；当前特征维度=%d",
                len(mismatched),
                ", ".join(mismatched),
                probe.shape[0],
            )

        if best_score < threshold:
            best_name = UNKNOWN

        # 写缓存前校验世代号：比对期间人脸库已变更则不写（结果仍返回本次调用方，
        # 但其对应的是变更前的快照，不应缓存供后续复用）
        if self._generation_snapshot() == generation:
            self._cache.put(cache_key, (best_name, best_score))
        return best_name, best_score

    # ------------------------------------------------------------------ #
    # 注册
    # ------------------------------------------------------------------ #
    # 注册路径复用同一嵌入算法，保留已知质量错误，统一包装底层异常后序列化为数据库字节
    def extract_features(self, image: np.ndarray) -> bytes:
        """提取可存储的特征字节。人脸不合格时抛 ``FaceQualityError``"""
        try:
            matrix = get_embedder().extract(image)
        except FaceQualityError:
            raise
        except AppError:
            raise
        except Exception as exc:
            raise FaceQualityError(f"特征提取失败：{exc}") from exc
        return serialize_features(matrix)

    # 注册、改名、删除后由调用方触发；两层缓存分别加锁清空，并非一次跨缓存原子失效
    def clear_cache(self) -> None:
        """清空全部缓存

        注册 / 重命名 / 删除人脸后必须调用，否则最多 ``TTL`` 秒内仍在用旧特征
        （表现为「刚注册的人认不出来，过几秒又好了」）
        """
        self._cache.clear()
        with self._known_lock:
            self._known_cache = None
            self._generation += 1


face_analysis_service = FaceAnalysisService()

__all__ = [
    "AnalyzedFace",
    "FaceAnalysisService",
    "FrameAnalysis",
    "face_analysis_service",
    "feature_fingerprint",
]
