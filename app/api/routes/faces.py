"""人脸库路由。"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse

from app.api.deps import CurrentUser, DbSession
from app.api.schemas import (
    DetectResponse,
    FaceDeleteRequest,
    FaceDeleteResponse,
    FaceInfo,
    FaceModifyResponse,
    FaceRegisterResponse,
    FaceRenameRequest,
    FaceScreenshotRequest,
    FacesListResponse,
    FaceItem,
)
from app.core.errors import AppError, FaceQualityError, NotFoundError, ValidationError
from app.db.repository import delete_face, get_face, list_faces, rename_face, upsert_face
from app.logger import logger
from app.services.analysis import face_analysis_service
from app.services.storage import decode_image, delete_file, resolve_stored_path, save_screenshot
from app.vision.face_identity import StoredFeatureError, deserialize_features

router = APIRouter(prefix="/faces", tags=["人脸库"])


def _is_feature_current(blob: bytes) -> bool:
    """判断存储的特征是否为当前算法版本（旧 pickle 视为需要重新注册）。"""
    try:
        deserialize_features(blob)
    except StoredFeatureError:
        return False
    return True


@router.get("", response_model=FacesListResponse)
async def list_faces_endpoint(current_user: CurrentUser, db: DbSession) -> FacesListResponse:
    """列出当前登录用户的全部人脸记录。"""
    records = list(list_faces(db, current_user.id))
    items = [
        FaceItem(
            name=record.name,
            screenshot=record.screenshot,
            register_time=record.created_at,
            online_time=int(record.online_time or 0),
            is_online=bool(record.is_online),
            is_away=bool(record.is_away),
            checkin_time=record.checkin_time,
            features_current=_is_feature_current(record.features),
        )
        for record in records
    ]
    return FacesListResponse(faces=items)


@router.post("/register", response_model=FaceRegisterResponse)
async def register_face(
    current_user: CurrentUser,
    db: DbSession,
    file: UploadFile = File(..., description="人脸图片（JPG/PNG）"),
    name: str = Form(..., min_length=1, max_length=100),
) -> FaceRegisterResponse:
    """注册或更新人脸。"""
    clean_name = name.strip()
    if not clean_name:
        raise ValidationError("姓名不能为空")

    contents = await file.read()
    image = decode_image(contents)

    # 先提特征：质量不合格就没有必要落盘截图
    try:
        features = face_analysis_service.extract_features(image)
    except FaceQualityError as exc:
        logger.warning("人脸注册被拒 name=%s reason=%s", clean_name, exc.message)
        return FaceRegisterResponse(
            success=False,
            name=clean_name,
            screenshot=None,
            message=f"注册失败：{exc.message}。请确保光线充足、正脸对准镜头、画面中只有一张脸。",
        )

    existing = get_face(db, current_user.id, clean_name)
    old_screenshot = existing.screenshot if existing else None

    screenshot_path = save_screenshot(image, prefix=clean_name)
    upsert_face(
        db,
        user_id=current_user.id,
        name=clean_name,
        features=features,
        screenshot=screenshot_path,
    )

    # 更新成功后清理旧截图，避免文件堆积
    if old_screenshot and old_screenshot != screenshot_path:
        delete_file(old_screenshot)

    face_analysis_service.clear_cache()
    logger.info("人脸注册成功 name=%s user=%s", clean_name, current_user.email)
    return FaceRegisterResponse(
        success=True,
        name=clean_name,
        screenshot=screenshot_path,
        message=f"注册成功！欢迎 {clean_name}",
    )


@router.post("/screenshot")
async def get_face_screenshot(
    request: FaceScreenshotRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> FileResponse:
    """获取人脸注册截图。"""
    name = request.name.strip()
    face = get_face(db, current_user.id, name)
    if face is None or not face.screenshot:
        raise NotFoundError("截图不存在")

    path = resolve_stored_path(face.screenshot)
    if path is None or not path.exists():
        raise NotFoundError("截图文件不存在或已被清理")

    return FileResponse(
        str(path),
        media_type="image/jpeg",
        filename=path.name,
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.put("/{name}", response_model=FaceModifyResponse)
async def rename_face_endpoint(
    name: str,
    request: FaceRenameRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> FaceModifyResponse:
    """重命名人脸（可选同时修正注册时间）。"""
    old_name = request.old_name.strip()
    new_name = request.new_name.strip()
    ok = rename_face(
        db,
        user_id=current_user.id,
        old_name=old_name,
        new_name=new_name,
        register_time=request.register_time,
    )
    if not ok:
        raise ValidationError("修改失败：目标名称已存在或原记录不存在")

    face_analysis_service.clear_cache()
    logger.info("人脸重命名 %s -> %s user=%s", old_name, new_name, current_user.email)
    return FaceModifyResponse(success=True, message="修改成功")


@router.delete("/{name}", response_model=FaceDeleteResponse)
async def delete_face_endpoint(
    name: str,
    current_user: CurrentUser,
    db: DbSession,
) -> FaceDeleteResponse:
    """删除人脸记录及其截图。"""
    clean_name = name.strip()
    face = delete_face(db, user_id=current_user.id, name=clean_name)
    if face is None:
        raise NotFoundError(f"人脸不存在：{clean_name}")

    delete_file(face.screenshot)
    face_analysis_service.clear_cache()
    logger.info("删除人脸 name=%s user=%s", clean_name, current_user.email)
    return FaceDeleteResponse(success=True, name=clean_name, message="删除成功")


@router.post("/analyze", response_model=DetectResponse)
async def analyze_upload(
    current_user: CurrentUser,
    file: UploadFile = File(..., description="待分析图片"),
    detect_gender_age: bool = Form(default=True),
    detect_identity: bool = Form(default=True),
) -> DetectResponse:
    """一次性上传图片做完整分析（调试与第三方集成用）。"""
    contents = await file.read()
    image = decode_image(contents)

    try:
        result = face_analysis_service.analyze_image(
            image,
            detect_gender_age=detect_gender_age,
            detect_identity=detect_identity,
            user_id=current_user.id if detect_identity else None,
        )
    except AppError:
        raise

    return DetectResponse(
        request_id=uuid.uuid4().hex[:8],
        faces=[
            FaceInfo(
                bbox=face.bbox,
                gender=face.gender,
                age=face.age,
                confidence=face.confidence,
                identity=face.identity,
                identity_confidence=face.identity_confidence,
                detection_confidence=face.detection_confidence,
            )
            for face in result.faces
        ],
        face_count=result.face_count,
        processing_ms=result.processing_ms,
        timestamp=datetime.now(),
    )


__all__ = ["router"]
