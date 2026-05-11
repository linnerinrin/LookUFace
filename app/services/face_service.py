"""
app/services/face_service.py
人脸分析
analyze 总分析
_analyze_single_face 单脸分析
_predict_gender_age 年龄性别
_recognize_identity 身份
"""

import time
import uuid
from datetime import datetime

from app.core.detector import  face_detector
from app.core.models import  gender_age_model
from app.core.face_identity import face_identity
from app.api.schemas import FaceInfo, DetectResponse
from app.logger import logger


class FaceAnalysisService:
    """人脸分析服务"""

    def __init__(self):
        self.face_detector = face_detector
        self.gender_age = gender_age_model

    def analyze(self, image, detect_gender_age=True, detect_identity=True):
        start_time = time.time()
        request_id = str(uuid.uuid4())[:8]
        face_boxes = self.face_detector.detect(image)

        faces = []
        for bbox in face_boxes:
            face_info = self._analyze_single_face(
                image, bbox, detect_gender_age, detect_identity
            )
            faces.append(face_info)

        processing_ms = (time.time() - start_time) * 1000
        return DetectResponse(
            request_id=request_id,
            faces=faces,
            face_count=len(faces),
            processing_ms=round(processing_ms, 2),
            timestamp=datetime.now()
        )

    def _analyze_single_face(self, image, bbox, detect_gender_age, detect_identity):
        """分析单张人脸，返回 FaceInfo"""
        x1, y1, x2, y2 = bbox
        face_roi = image[y1:y2, x1:x2]

        gender, age, confidence = self._predict_gender_age(face_roi) if detect_gender_age else (
        "Unknown", "Unknown", 0.0)
        identity, identity_conf = self._recognize_identity(face_roi) if detect_identity else ("Unknown", 0.0)

        return FaceInfo(
            bbox=[x1, y1, x2, y2],
            gender=gender,
            age=age,
            confidence=confidence,
            identity=identity,
            identity_confidence=identity_conf
        )

    def _predict_gender_age(self, face_roi):
        if face_roi.size == 0:
            return ("Unknown", "Unknown", 0.0)
        g, a, conf = self.gender_age.predict(face_roi)
        return (g if g in ["Male", "Female"] else "Unknown", a, conf)

    def _recognize_identity(self, face_roi):
        try:
            result = face_identity.recognize(face_roi)
            return result.get("name", "Unknown"), result.get("confidence", 0.0)
        except Exception as e:
            logger.warning("识别失败",exc_info=True)
            return ("Unknown", 0.0)


face_service = FaceAnalysisService()