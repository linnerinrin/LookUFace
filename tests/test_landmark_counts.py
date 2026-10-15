"""关键点数量兼容性测试（468 / 478）。

**背景**：MediaPipe FaceMesh 在 ``refine_landmarks=True`` 时返回 **478** 点
（468 个面部网格点 + 末尾 10 个虹膜点），``=False`` 时返回 **468** 点。

本项目为了更高的关键点定位精度开启了 refine_landmarks，因此实际拿到 478 点。
但旧实现硬编码 ``points.shape[0] != 468`` 就报错，导致：

    关键点数量异常：期望 468，实际 478

进而注册与识别全部失败。

**为什么必须把维度钉死为 468（而不是让维度跟着点数走）**：
特征矩阵是 ``点数 × 点数`` 的距离矩阵。若新注册得到 478×478、
库里已有 468×468，两者无法比对；更糟的是 ``_recognize`` 对维度不一致是
**跳过**该条记录，识别会静默失效（界面只显示"陌生人"，无任何报错）。
因此统一只取前 468 个面部网格点，丢弃虹膜点。

这些用例用**合成关键点对象**驱动，不依赖 mediapipe 真人脸检测，因此运行很快
且在任何机器上结果一致。
"""

from __future__ import annotations

import numpy as np
import pytest

from app.core.errors import FaceQualityError
from app.vision.face_identity import (
    ACCEPTED_LANDMARK_COUNTS,
    LANDMARK_COUNT,
    REFINED_LANDMARK_COUNT,
    LandmarkDistanceEmbedder,
    pairwise_distance_matrix,
    spatial_normalize,
)


class _FakeLandmark:
    """模拟 mediapipe 的 NormalizedLandmark（只有 x/y/z 三个属性）。"""

    __slots__ = ("x", "y", "z")

    def __init__(self, x: float, y: float, z: float) -> None:
        self.x = x
        self.y = y
        self.z = z


def _make_landmarks(count: int, seed: int = 0) -> list[_FakeLandmark]:
    """确定性生成 count 个关键点。

    末尾 10 个（虹膜）刻意使用**明显不同**的分布，这样如果实现没有正确丢弃
    虹膜点，特征矩阵就会变化，测试即可发现。
    """
    rng = np.random.default_rng(seed)
    base = rng.normal(0.5, 0.1, size=(count, 3))

    if count == REFINED_LANDMARK_COUNT:
        # 虹膜点：靠近眼部的极小范围抖动，与面部网格分布截然不同
        base[LANDMARK_COUNT:] = rng.normal(0.5, 0.002, size=(count - LANDMARK_COUNT, 3))

    return [_FakeLandmark(float(x), float(y), float(z)) for x, y, z in base]


# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #


def test_landmark_constants() -> None:
    assert LANDMARK_COUNT == 468, "面部网格点数应为 468"
    assert REFINED_LANDMARK_COUNT == 478, "开启虹膜细化后应为 478"
    assert set(ACCEPTED_LANDMARK_COUNTS) == {468, 478}


# --------------------------------------------------------------------------- #
# 归一化：两种点数都能接受，且结果维度一致
# --------------------------------------------------------------------------- #


def test_accepts_468_landmarks() -> None:
    points = LandmarkDistanceEmbedder.normalize_landmarks(_make_landmarks(468))
    assert points.shape == (468, 3)


def test_accepts_478_landmarks_and_truncates_to_468() -> None:
    """**核心回归**：478 点必须被接受，并截断为 (468, 3)，不再抛异常。"""
    points = LandmarkDistanceEmbedder.normalize_landmarks(_make_landmarks(478))
    assert points.shape == (468, 3), "必须丢弃末尾 10 个虹膜点，把维度钉死为 468"


def test_478_and_468_produce_identical_features_for_same_face_points() -> None:
    """同一张脸的 468 点与「478 点（前 468 相同）」必须得到完全相同的特征。

    这证明截断逻辑没有引入任何额外变换，新旧特征可以直接比对。
    """
    face_468 = _make_landmarks(468, seed=42)
    face_478 = _make_landmarks(478, seed=42)

    # 478 版本的前 468 个点与 468 版本逐点相同
    for index in range(LANDMARK_COUNT):
        assert face_478[index].x == face_468[index].x

    left = LandmarkDistanceEmbedder.normalize_landmarks(face_468)
    right = LandmarkDistanceEmbedder.normalize_landmarks(face_478)

    assert np.array_equal(left, right), "两条路径必须得到逐位相同的坐标"

    feature_left = pairwise_distance_matrix(spatial_normalize(left))
    feature_right = pairwise_distance_matrix(spatial_normalize(right))
    assert feature_left.shape == (468, 468)
    assert np.array_equal(feature_left, feature_right)


def test_iris_points_are_actually_discarded() -> None:
    """确认丢弃的是虹膜点：改动虹膜点不应影响特征结果。"""
    normal = _make_landmarks(478, seed=7)

    tampered = _make_landmarks(478, seed=7)
    # 把最后 10 个（虹膜）点全部改成完全不同的位置
    for index in range(LANDMARK_COUNT, REFINED_LANDMARK_COUNT):
        tampered[index] = _FakeLandmark(x=99.0, y=-99.0, z=0.0)

    a = LandmarkDistanceEmbedder.normalize_landmarks(normal)
    b = LandmarkDistanceEmbedder.normalize_landmarks(tampered)
    assert np.array_equal(a, b), "虹膜点必须被完全忽略"


# --------------------------------------------------------------------------- #
# 非预期点数：必须显式报错，不得静默截断或补零
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("count", [0, 1, 100, 467, 469, 477, 479, 512, 1000])
def test_unexpected_landmark_count_raises(count: int) -> None:
    """既不是 468 也不是 478 时必须报错。

    盲目截断或补零会产生**无意义的特征**，那比直接失败更危险 ——
    会表现为识别准确率莫名下降而不是报错。
    """
    landmarks = _make_landmarks(count) if count else []
    with pytest.raises(FaceQualityError) as excinfo:
        LandmarkDistanceEmbedder.normalize_landmarks(landmarks)

    error = excinfo.value
    assert error.code == "unexpected_landmark_count"
    assert str(count) in error.message, "错误信息应包含实际点数"
    assert "468" in error.message and "478" in error.message, "应说明两种可接受的点数"
    assert error.details == {"expected": [468, 478], "actual": count}


def test_error_message_mentions_mediapipe_upgrade_path() -> None:
    """错误信息应指出后续动作（升级 mediapipe 后要重新评估算法与阈值）。"""
    with pytest.raises(FaceQualityError) as excinfo:
        LandmarkDistanceEmbedder.normalize_landmarks(_make_landmarks(500))
    assert "mediapipe" in excinfo.value.message


# --------------------------------------------------------------------------- #
# 真实检测路径（需要 mediapipe，模型缺失时跳过）
# --------------------------------------------------------------------------- #


def _real_face_image():
    """取一张真实注册截图；没有就跳过。"""
    import cv2

    from pathlib import Path

    folder = Path(__file__).resolve().parent.parent / "storage" / "screenshots"
    if not folder.exists():
        pytest.skip("没有可用的注册截图")
    for path in sorted(folder.glob("*.jpg")):
        image = cv2.imread(str(path))
        if image is not None:
            return image
    pytest.skip("注册截图无法读取")


def test_real_detection_produces_468_feature() -> None:
    """用真实人脸跑完整链路：必须得到 (468, 468) 的特征，而不是报错。

    这正是用户遇到的场景 —— 真实检测返回 478 点。
    """
    embedder = LandmarkDistanceEmbedder()
    try:
        image = _real_face_image()
    except Exception:
        pytest.skip("mediapipe 或图片不可用")

    try:
        points = embedder.extract_landmarks(image)
    except FaceQualityError as exc:
        pytest.skip(f"截图中未检出人脸：{exc.message}")
    except Exception as exc:  # pragma: no cover - mediapipe 不可用
        pytest.skip(f"mediapipe 不可用：{exc}")

    assert points.shape == (468, 3), "真实检测路径也必须产出 468 点"

    feature = embedder.extract(image)
    assert feature.shape == (468, 468)
    assert feature.min() >= 0.0 and feature.max() == pytest.approx(1.0)

    embedder.close()


def test_real_same_person_similarity_is_stable() -> None:
    """真实同人（同一批截图）之间的相似度应高度稳定。

    实测三张截图两两相似度 0.9835~0.9893（std≈0.0024）。
    这里放宽到 0.9，既能抓住算法回归，又不会因换数据而误报。
    """
    import cv2

    from pathlib import Path

    from app.vision.face_identity import similarity_from_matrices

    folder = Path(__file__).resolve().parent.parent / "storage" / "screenshots"
    images = []
    for path in sorted(folder.glob("*.jpg")):
        image = cv2.imread(str(path))
        if image is not None:
            images.append(image)
    if len(images) < 2:
        pytest.skip("截图不足 2 张")

    embedder = LandmarkDistanceEmbedder()
    features = []
    try:
        for image in images[:4]:
            try:
                features.append(embedder.extract(image))
            except FaceQualityError:
                continue
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"mediapipe 不可用：{exc}")
    finally:
        embedder.close()

    if len(features) < 2:
        pytest.skip("可用特征不足 2 个")

    for i in range(len(features) - 1):
        score = similarity_from_matrices(features[i], features[i + 1])
        assert score > 0.9, (
            f"第 {i} 与 {i+1} 张截图的相似度仅 {score:.4f}，"
            "特征可能已不稳定（请检查归一化与截断逻辑）"
        )
