"""
人脸分析服务
- 整合人脸检测、年龄性别识别、身份识别
- 返回结构化检测结果
"""

import cv2
import numpy as np
import time
import uuid
from datetime import datetime

from app.core.detector import FaceDetector
from app.core.models import GenderAgeModel
from app.core.face_identity import face_identity
from app.api.schemas import FaceInfo, DetectResponse


class FaceAnalysisService:
    """人脸分析服务"""

    def __init__(self):
        self.face_detector = FaceDetector()
        self.gender_age = GenderAgeModel()

    def analyze(
        self,
        image: np.ndarray,
        detect_gender_age: bool = True,
        detect_identity: bool = True
    ) -> DetectResponse:
        """分析单张图片，返回检测结果"""
        start_time = time.time()
        request_id = str(uuid.uuid4())[:8]

        face_boxes = self.face_detector.detect(image)
        faces = []

        for (x1, y1, x2, y2) in face_boxes:
            face_roi = image[y1:y2, x1:x2]

            gender = "Unknown"
            age = "Unknown"
            confidence = 0.0
            identity = "Unknown"
            identity_conf = 0.0

            if detect_gender_age and face_roi.size > 0:
                g, a, conf = self.gender_age.predict(face_roi)
                gender = g if g in ["Male", "Female"] else "Unknown"
                age = a
                confidence = conf

            if detect_identity:
                try:
                    identity_result = face_identity.recognize(face_roi)
                    identity = identity_result.get("name", "Unknown")
                    identity_conf = identity_result.get("confidence", 0.0)
                except Exception as e:
                    print(f"身份识别失败: {e}")

            faces.append(FaceInfo(
                bbox=[x1, y1, x2, y2],
                gender=gender,
                age=age,
                confidence=confidence,
                identity=identity,
                identity_confidence=identity_conf
            ))

        processing_ms = (time.time() - start_time) * 1000

        return DetectResponse(
            request_id=request_id,
            faces=faces,
            face_count=len(faces),
            processing_ms=round(processing_ms, 2),
            timestamp=datetime.now()
        )


face_service = FaceAnalysisService()