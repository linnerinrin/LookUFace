"""
app/core/face_identity.py
人脸识别核心模块（MediaPipe）
__new__单例初始化
_intialize 初始化 加载数据库 mediapipe模型
_extract_features 将预测结果展平为向量
_load_db 加载已注册人脸
_save_db 保存已注册人脸
register_from_image_with_screenshot 注册人脸
recognize( 识别人脸

"""


import cv2
import numpy as np
import mediapipe as mp
import pickle
from typing import Optional, Dict, List
from pathlib import Path
from sklearn.metrics.pairwise import cosine_similarity
from app.config import settings
from app.database import SessionLocal, UserFace


class FaceIdentity:
    """人脸识别单例类"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """初始化 MediaPipe 模型"""
        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh = self.mp_face_mesh.FaceMesh(
            static_image_mode=True,
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=settings.DETECTION_CONFIDENCE,
            min_tracking_confidence=settings.TRACKING_CONFIDENCE
        )
        print("人脸识别模块初始化完成（数据库存储）")

    def _get_db(self):
        """获取数据库会话"""
        return SessionLocal()

    def _extract_features(self, landmarks) -> np.ndarray:
        """从468个关键点提取特征向量，中心化后展平"""
        points = np.array([[lm.x, lm.y, lm.z] for lm in landmarks])
        center = points.mean(axis=0)
        points = points - center
        return points.flatten()


    def register_from_image_with_screenshot(
        self,
        image: np.ndarray,
        name: str,
        screenshot_path: str,
        user_id: int = None
    ) -> bool:
        """从图像中注册人脸，绑定截图路径与账户"""
        if image is None or image.size == 0:
            return False
        if user_id is None:
            print("注册失败: 未提供 user_id")
            return False

        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        results = self.face_mesh.process(rgb)

        if not results.multi_face_landmarks:
            print(f"注册失败: 未检测到人脸")
            return False

        features = self._extract_features(results.multi_face_landmarks[0].landmark)
        db = self._get_db()

        try:
            existing = db.query(UserFace).filter_by(
                user_id=user_id, name=name
            ).first()

            if existing:
                # 更新已有记录
                existing.features = pickle.dumps(features)
                existing.screenshot = screenshot_path
                print(f"更新人脸: {name} (user_id={user_id})")
            else:
                # 新建记录
                face_record = UserFace(
                    user_id=user_id,
                    name=name,
                    features=pickle.dumps(features),
                    screenshot=screenshot_path
                )
                db.add(face_record)
                print(f"注册成功: {name} (user_id={user_id})")

            db.commit()
            return True
        except Exception as e:
            db.rollback()
            print(f"注册失败: {e}")
            return False
        finally:
            db.close()


    def recognize(
        self,
        face_roi: np.ndarray,
        threshold: float = settings.RECOGNIZE_THRESHOLD,
        user_id: int = None
    ) -> Dict:
        """识别身份"""
        if face_roi is None or face_roi.size == 0:
            return {"name": "Unknown", "confidence": 0.0}

        rgb = cv2.cvtColor(face_roi, cv2.COLOR_BGR2RGB)
        results = self.face_mesh.process(rgb)

        if not results.multi_face_landmarks:
            return {"name": "Unknown", "confidence": 0.0}

        features = self._extract_features(results.multi_face_landmarks[0].landmark)
        db = self._get_db()

        try:
            query = db.query(UserFace)
            if user_id is not None:
                query = query.filter_by(user_id=user_id)
            face_records = query.all()
        finally:
            db.close()

        if not face_records:
            return {"name": "Unknown", "confidence": 0.0}

        best_match = "Unknown"
        best_score = 0.0

        for record in face_records:
            known_features = pickle.loads(record.features)
            similarity = cosine_similarity([features], [known_features])[0][0]
            if similarity > best_score and similarity > threshold:
                best_score = similarity
                best_match = record.name

        return {"name": best_match, "confidence": float(best_score)}


    def get_all_faces_with_screenshots(self, user_id: int = None) -> List[Dict]:
        """获取当前用户的所有人脸信息（含截图路径）"""
        if user_id is None:
            return []
        db = self._get_db()
        try:
            faces = db.query(UserFace).filter_by(user_id=user_id).all()
            return [{
                "name": f.name,
                "screenshot": f.screenshot,
                "register_time": f.created_at.isoformat()
            } for f in faces]
        finally:
            db.close()

    def get_screenshot_path(self, name: str, user_id: int = None) -> Optional[str]:
        """获取人脸截图路径"""
        if user_id is None:
            return None
        db = self._get_db()
        try:
            face = db.query(UserFace).filter_by(
                user_id=user_id, name=name
            ).first()
            return face.screenshot if face else None
        finally:
            db.close()


    def delete_face(self, name: str, user_id: int = None) -> bool:

        """删除人脸（需验证权限）"""
        if user_id is None:
            return False

        db = self._get_db()
        try:
            face = db.query(UserFace).filter_by(
                user_id=user_id, name=name
            ).first()

            if not face:
                return False
            print(f"准备删除截图: {face.screenshot}")
            print(f"文件是否存在: {Path(face.screenshot).exists()}")
            # 删除截图文件
            if face.screenshot and Path(face.screenshot).exists():
                try:
                    Path(face.screenshot).unlink()
                except Exception:
                    pass

            db.delete(face)
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            print(f"删除失败: {e}")
            return False
        finally:
            db.close()


face_identity = FaceIdentity()