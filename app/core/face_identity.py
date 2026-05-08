"""
人脸识别核心模块（MediaPipe）
- 人脸特征提取（468个关键点）
- 人脸注册/识别（支持用户绑定）
- 截图路径管理
"""

import cv2
import numpy as np
from typing import Optional, Dict, List
from pathlib import Path
import json
from sklearn.metrics.pairwise import cosine_similarity
import mediapipe as mp
from datetime import datetime


class FaceIdentity:
    """人脸识别单例类"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """初始化：加载数据库 + MediaPipe 模型"""
        self.known_faces: Dict[str, Dict] = {}  # name -> {features, screenshot, register_time, user_id}
        self.db_path = Path("data/face_features.json")
        self._load_db()

        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh = self.mp_face_mesh.FaceMesh(
            static_image_mode=True,
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5
        )
        print("✓ 人脸识别模块初始化完成")

    def _extract_features(self, landmarks) -> np.ndarray:
        """从468个关键点提取特征向量"""
        points = np.array([[lm.x, lm.y, lm.z] for lm in landmarks])
        center = points.mean(axis=0)
        points = points - center
        return points.flatten()

    def _load_db(self):
        """从 JSON 文件加载已注册人脸"""
        if self.db_path.exists():
            try:
                with open(self.db_path, 'r') as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        for key, value in loaded.items():
                            if isinstance(value, list):
                                self.known_faces[key] = {
                                    "features": value,
                                    "screenshot": None,
                                    "register_time": None,
                                    "user_id": None
                                }
                            else:
                                self.known_faces[key] = value
            except:
                self.known_faces = {}

    def _save_db(self):
        """保存人脸数据到 JSON 文件"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.db_path, 'w') as f:
            json.dump(self.known_faces, f, indent=2, default=str)

    def register_from_image_with_screenshot(self, image: np.ndarray, name: str,
                                             screenshot_path: str, user_id: int = None) -> bool:
        """注册人脸并绑定用户"""
        if image is None or image.size == 0:
            return False

        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        results = self.face_mesh.process(rgb)

        if results.multi_face_landmarks:
            features = self._extract_features(results.multi_face_landmarks[0].landmark)
            self.known_faces[name] = {
                "features": [features.tolist()],
                "screenshot": screenshot_path,
                "register_time": datetime.now().isoformat(),
                "user_id": user_id
            }
            self._save_db()
            print(f"✅ 注册成功: {name}, 绑定用户: {user_id}")
            return True
        return False

    def recognize(self, face_roi: np.ndarray, threshold: float = 0.6, user_id: int = None) -> Dict:
        """识别身份（筛选指定用户的人脸）"""
        if face_roi is None or face_roi.size == 0 or len(self.known_faces) == 0:
            return {"name": "Unknown", "confidence": 0.0}

        rgb = cv2.cvtColor(face_roi, cv2.COLOR_BGR2RGB)
        results = self.face_mesh.process(rgb)

        if not results.multi_face_landmarks:
            return {"name": "Unknown", "confidence": 0.0}

        features = self._extract_features(results.multi_face_landmarks[0].landmark)

        best_match = "Unknown"
        best_score = 0.0

        for name, data in self.known_faces.items():
            if user_id is not None and data.get("user_id") != user_id:
                continue
            for known_feature in data.get("features", []):
                similarity = cosine_similarity([features], [known_feature])[0][0]
                if similarity > best_score and similarity > threshold:
                    best_score = similarity
                    best_match = name

        return {"name": best_match, "confidence": float(best_score)}

    def get_all_faces_with_screenshots(self, user_id: int = None) -> List[Dict]:
        """获取当前用户的所有人脸信息"""
        result = []
        for name, data in self.known_faces.items():
            if user_id is not None and data.get("user_id") != user_id:
                continue
            result.append({
                "name": name,
                "screenshot": data.get("screenshot"),
                "register_time": data.get("register_time")
            })
        return result

    def get_all_faces(self, user_id: int = None) -> List[str]:
        """获取当前用户的所有人脸名称"""
        if user_id is not None:
            return [name for name, data in self.known_faces.items() if data.get("user_id") == user_id]
        return list(self.known_faces.keys())

    def get_screenshot_path(self, name: str, user_id: int = None) -> Optional[str]:
        """获取人脸截图路径"""
        data = self.known_faces.get(name)
        if not data:
            return None
        if user_id is not None and data.get("user_id") != user_id:
            return None
        return str(data.get("screenshot"))

    def delete_face(self, name: str, user_id: int = None) -> bool:
        """删除人脸（需验证权限）"""
        if name not in self.known_faces:
            return False
        if user_id is not None and self.known_faces[name].get("user_id") != user_id:
            return False

        screenshot = self.known_faces[name].get("screenshot")
        if screenshot and Path(screenshot).exists():
            try:
                Path(screenshot).unlink()
            except:
                pass

        del self.known_faces[name]
        self._save_db()
        return True

    def get_face_count(self, user_id: int = None) -> int:
        """获取当前用户的人脸数量"""
        if user_id is not None:
            return sum(1 for data in self.known_faces.values() if data.get("user_id") == user_id)
        return len(self.known_faces)


face_identity = FaceIdentity()