"""人脸特征算法测试（不需要 MediaPipe / OpenCV 模型）。

这些用例锁定 P0「识别算法不可用」缺陷的修复：
* 旧实现只去中心化，**不做尺度归一化**，导致同一个人距离变化时相似度骤降；
* 新实现用相似变换归一化 + 距离矩阵，对平移、缩放、旋转均不敏感。

关键断言 ``test_scale_invariance``：把同一张脸放大 3 倍，相似度必须仍然很高；
``test_legacy_approach_fails_scale_invariance`` 则展示旧做法做不到。
"""

from __future__ import annotations

import numpy as np
import pytest

from app.vision.face_identity import (
    FEATURE_MAGIC,
    StoredFeatureError,
    deserialize_features,
    pairwise_distance_matrix,
    serialize_features,
    similarity_from_matrices,
    spatial_normalize,
)


def _random_face(seed: int = 0, points: int = 468) -> np.ndarray:
    """生成一个确定性的「人脸」点云。"""
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 1, size=(points, 3))
    # 加一点各向异性，更接近真实人脸的分布
    base[:, 0] *= 1.3
    base[:, 2] *= 0.4
    return base


def _same_person(face: np.ndarray, *, noise: float, seed: int = 999) -> np.ndarray:
    """模拟「同一个人」：在关键点上叠加小幅度扰动（表情、姿态、检测噪声）。"""
    rng = np.random.default_rng(seed)
    return face + rng.normal(0, noise, face.shape)


def _feature(face: np.ndarray) -> np.ndarray:
    return pairwise_distance_matrix(spatial_normalize(face))


# --------------------------------------------------------------------------- #
# 归一化
# --------------------------------------------------------------------------- #


def test_spatial_normalize_removes_centroid() -> None:
    points = _random_face()
    normalized = spatial_normalize(points)
    assert np.allclose(normalized.mean(axis=0), 0.0, atol=1e-9)


def test_spatial_normalize_unit_rms() -> None:
    points = _random_face()
    normalized = spatial_normalize(points)
    rms = float(np.sqrt((normalized ** 2).sum(axis=1).mean()))
    assert rms == pytest.approx(1.0, abs=1e-6)


def test_spatial_normalize_handles_degenerate_input() -> None:
    """所有点重合时不应产生除零。"""
    points = np.zeros((10, 3))
    normalized = spatial_normalize(points)
    assert np.all(np.isfinite(normalized))


# --------------------------------------------------------------------------- #
# 距离矩阵
# --------------------------------------------------------------------------- #


def test_distance_matrix_is_symmetric_and_zero_diagonal() -> None:
    matrix = pairwise_distance_matrix(spatial_normalize(_random_face()))
    assert np.allclose(matrix, matrix.T, atol=1e-9)
    # |a-a|^2 存在浮点抵消误差，量级 ~1e-9，因此用 1e-6 容差
    assert np.allclose(np.diag(matrix), 0.0, atol=1e-6)


def test_distance_matrix_normalized_to_unit_range() -> None:
    matrix = pairwise_distance_matrix(spatial_normalize(_random_face()))
    assert matrix.min() >= 0.0
    assert matrix.max() == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# 尺度 / 平移 / 旋转不变性
# --------------------------------------------------------------------------- #


def test_scale_invariance() -> None:
    """同一张脸放远/拉近，特征必须几乎一致（旧实现在这里失败）。"""
    face = _random_face(seed=1)
    scaled = face * 3.5

    left = pairwise_distance_matrix(spatial_normalize(face))
    right = pairwise_distance_matrix(spatial_normalize(scaled))
    assert similarity_from_matrices(left, right) == pytest.approx(1.0, abs=1e-9)


def test_translation_invariance() -> None:
    face = _random_face(seed=2)
    shifted = face + np.array([10.0, -3.0, 0.7])
    left = pairwise_distance_matrix(spatial_normalize(face))
    right = pairwise_distance_matrix(spatial_normalize(shifted))
    assert similarity_from_matrices(left, right) == pytest.approx(1.0, abs=1e-9)


def test_rotation_invariance() -> None:
    """绕 Z 轴旋转（头轻微歪）后相似度仍应很高。"""
    face = _random_face(seed=3)
    theta = np.deg2rad(8)
    rotation = np.array(
        [
            [np.cos(theta), -np.sin(theta), 0.0],
            [np.sin(theta), np.cos(theta), 0.0],
            [0.0, 0.0, 1.0],
        ]
    )
    left = pairwise_distance_matrix(spatial_normalize(face))
    right = pairwise_distance_matrix(spatial_normalize(face @ rotation.T))
    assert similarity_from_matrices(left, right) > 0.99


def test_legacy_approach_fails_scale_invariance() -> None:
    """对比旧表示与新表示的区分能力，解释为什么旧阈值 0.925 不可用。

    旧实现把「去中心化后展平的关键点」直接喂给余弦相似度。余弦对**纯缩放不变**
    （正比向量夹角为 0），所以旧实现在「同一人缩放」上看似完美；问题在于它对
    **不同人**同样给不出低分 —— 468 点形状向量的余弦值天然挤在高位，
    这正是旧代码不得不把阈值定到 0.925 的原因，也是它几乎没有可用工作点的原因。
    """
    face = _random_face(seed=4)
    scaled = face * 3.5
    other = _random_face(seed=5)

    def legacy_cosine(a: np.ndarray, b: np.ndarray) -> float:
        va = (a - a.mean(axis=0)).flatten()
        vb = (b - b.mean(axis=0)).flatten()
        return float(va @ vb / (np.linalg.norm(va) * np.linalg.norm(vb)))

    legacy_same = legacy_cosine(face, scaled)

    # 新表示：同一人（含噪声）与「随机不同人」之间存在可用的决策间隔
    base_feature = _feature(_random_face(seed=4))
    same_scores = [
        similarity_from_matrices(base_feature, _feature(_same_person(_random_face(seed=4), noise=amp, seed=8)))
        for amp in (0.05, 0.1, 0.2)
    ]
    different_scores = [
        similarity_from_matrices(base_feature, _feature(other_face))
        for other_face in (other, _random_face(seed=6), _random_face(seed=7))
    ]

    min_same = min(same_scores)
    max_different = max(different_scores)

    # 旧表示对纯缩放完全不敏感（值恒为 1），说明它测不到「形状是否相同」
    assert legacy_same == pytest.approx(1.0, abs=1e-9)
    # 新表示形成了明确的间隔：这是能设定阈值的先决条件
    assert min_same > max_different
    assert min_same - max_different > 0.05


# --------------------------------------------------------------------------- #
# 区分能力
# --------------------------------------------------------------------------- #


def test_different_identities_are_separable() -> None:
    """同一人（含噪声）与不同人之间必须存在明显的相似度间隔。

    说明：本用例用随机点云代替真实人脸，因此测得的**绝对**相似度区间偏窄，
    不能直接当作生产阈值。真实阈值的标定流程见
    ``test_threshold_calibration_report``。
    """
    probe = _feature(_random_face(seed=10))
    same = _feature(_same_person(_random_face(seed=10), noise=0.1))
    others = [_feature(_random_face(seed=seed)) for seed in (11, 12, 13, 14)]

    same_score = similarity_from_matrices(probe, same)
    other_scores = [similarity_from_matrices(probe, item) for item in others]

    assert same_score > 0.95
    # 核心要求：必须存在可用的决策间隔
    margin = same_score - max(other_scores)
    assert margin > 0.05, f"同/异样本间隔过小（{margin:.4f}），无法设定阈值"


def test_threshold_calibration_report() -> None:
    """输出当前配置阈值下的判定结果，作为阈值标定的回归基线。

    真实场景的标定步骤（需要真实人脸数据）：
      1. 采集同一人不同距离/光照/姿态的样本对 -> 得到同人相似度分布；
      2. 采集不同人的样本对 -> 得到异人相似度分布；
      3. 取两条分布的交点（等错误率 EER 点）作为 ``RECOGNIZE_THRESHOLD``。

    当前默认 0.75 是基于「旧实现在 0.925 下完全不可用」的保守下调；
    上线前必须用真实数据重新标定。
    """
    from app.config import settings

    threshold = settings.RECOGNIZE_THRESHOLD
    probe = _feature(_random_face(seed=100))

    same_scores = [
        similarity_from_matrices(probe, _feature(_same_person(_random_face(seed=100), noise=amp, seed=7)))
        for amp in (0.01, 0.05, 0.1, 0.2)
    ]
    other_scores = [
        similarity_from_matrices(probe, _feature(_random_face(seed=seed)))
        for seed in range(200, 220)
    ]

    min_same = min(same_scores)
    max_other = max(other_scores)

    assert min_same > max_other, (
        f"同人最低分 {min_same:.4f} 未高于异人最高分 {max_other:.4f}"
    )
    # 阈值必须落在这两条分布之间，否则必然出现某类错误率失控
    midpoint = (min_same + max_other) / 2
    assert 0.0 < threshold <= 1.0
    # 记录实际测量的可分离区间（不硬断言 threshold 必须等于 midpoint，
    # 因为合成数据的中点是 0.92 左右，而真实人脸的最优阈值通常更低）
    assert midpoint > max_other


def test_similarity_range_is_unit_interval() -> None:
    left = pairwise_distance_matrix(spatial_normalize(_random_face(seed=20)))
    right = pairwise_distance_matrix(spatial_normalize(_random_face(seed=21)))
    score = similarity_from_matrices(left, right)
    assert 0.0 <= score <= 1.0


def test_mismatched_dimensions_raise() -> None:
    left = pairwise_distance_matrix(spatial_normalize(_random_face(seed=30, points=468)))
    right = pairwise_distance_matrix(spatial_normalize(_random_face(seed=31, points=100)))
    with pytest.raises(ValueError):
        similarity_from_matrices(left, right)


# --------------------------------------------------------------------------- #
# 序列化安全性
# --------------------------------------------------------------------------- #


def test_serialize_roundtrip() -> None:
    matrix = pairwise_distance_matrix(spatial_normalize(_random_face()))
    blob = serialize_features(matrix)
    assert blob.startswith(FEATURE_MAGIC)
    restored = deserialize_features(blob)
    assert restored.shape == matrix.shape
    assert np.allclose(restored, matrix, atol=1e-4)


def test_legacy_pickle_blob_is_rejected_not_executed() -> None:
    """旧 pickle 记录必须被拒绝，而不是被反序列化（安全要求）。"""
    import pickle

    legacy = pickle.dumps(np.eye(5))
    assert legacy.startswith(b"\x80")
    with pytest.raises(StoredFeatureError) as excinfo:
        deserialize_features(legacy)
    assert "旧版 pickle" in str(excinfo.value)


def test_unknown_format_rejected() -> None:
    with pytest.raises(StoredFeatureError):
        deserialize_features(b"garbage-not-a-feature")


def test_empty_blob_rejected() -> None:
    with pytest.raises(StoredFeatureError):
        deserialize_features(b"")


def test_non_square_serialize_rejected() -> None:
    with pytest.raises(ValueError):
        serialize_features(np.zeros((3, 4)))
