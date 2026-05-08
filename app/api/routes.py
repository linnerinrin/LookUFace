"""
主要 API 路由
- 人脸检测（上传图片/Base64/异步）
- WebSocket 实时摄像头流
- 人脸注册/删除/查询（带用户绑定）
"""

import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks, Form, WebSocket, WebSocketDisconnect, Request
import cv2
import numpy as np

from app.api.schemas import DetectResponse, HealthResponse, TaskStatus
from app.auth.auth import decode_token
from app.core.face_identity import face_identity
from app.services.task_service import task_service
from app.services.face_service import face_service
from app.config import settings
from app.utils.image_utils import decode_base64_to_image

router = APIRouter()
executor = ThreadPoolExecutor(max_workers=settings.MAX_WORKERS)


async def run_sync_analysis(image: np.ndarray, detect_gender_age: bool) -> DetectResponse:
    """在线程池中执行同步分析，避免阻塞事件循环"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor,
        face_service.analyze,
        image,
        detect_gender_age,
        True
    )


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """健康检查接口"""
    return HealthResponse(
        status="healthy",
        version=settings.APP_VERSION,
        models_loaded=True,
        registered_faces=face_identity.get_face_count()
    )


@router.post("/detect/upload", response_model=DetectResponse)
async def detect_from_upload(
    file: UploadFile = File(...),
    detect_gender_age: bool = True
):
    """上传图片文件进行人脸分析"""
    if not file.content_type.startswith("image/"):
        raise HTTPException(400, "只支持图片文件")

    contents = await file.read()
    if len(contents) > settings.MAX_FILE_SIZE:
        raise HTTPException(400, f"文件过大，最大 {settings.MAX_FILE_SIZE // 1024 // 1024}MB")

    nparr = np.frombuffer(contents, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    if image is None:
        raise HTTPException(400, "无法解码图片")

    return await run_sync_analysis(image, detect_gender_age)


@router.post("/detect/base64", response_model=DetectResponse)
async def detect_from_base64(
    request: dict,
    detect_gender_age: bool = True
):
    """Base64 编码图片进行人脸分析"""
    image_base64 = request.get("image_base64")
    if not image_base64:
        raise HTTPException(400, "缺少 image_base64 字段")

    image = decode_base64_to_image(image_base64)
    if image is None:
        raise HTTPException(400, "Base64 解码失败")

    return await run_sync_analysis(image, detect_gender_age)


@router.post("/detect/async", response_model=TaskStatus)
async def detect_async(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    detect_gender_age: bool = True
):
    """异步处理：后台任务 + 轮询结果"""
    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    if image is None:
        raise HTTPException(400, "无法解码图片")

    task = task_service.create_task()
    background_tasks.add_task(
        task_service.process_task,
        task.task_id,
        image,
        detect_gender_age
    )
    return task


@router.get("/task/{task_id}", response_model=TaskStatus)
async def get_task_status(task_id: str):
    """查询异步任务状态"""
    task = task_service.get_task(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    return task


@router.websocket("/ws/camera")
async def websocket_camera(websocket: WebSocket):
    """WebSocket 实时摄像头分析"""
    await websocket.accept()
    print("WebSocket 客户端已连接")

    try:
        while True:
            message = await websocket.receive()
            if "bytes" in message:
                nparr = np.frombuffer(message["bytes"], np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

                if frame is None:
                    continue

                result = face_service.analyze(
                    frame,
                    detect_gender_age=True,
                    detect_identity=True
                )

                faces_data = []
                for f in result.faces:
                    faces_data.append({
                        "bbox": f.bbox,
                        "gender": f.gender,
                        "age": f.age,
                        "confidence": f.confidence,
                        "identity": f.identity,
                        "identity_confidence": f.identity_confidence,
                    })

                await websocket.send_json({
                    "success": True,
                    "face_count": result.face_count,
                    "faces": faces_data,
                    "processing_ms": result.processing_ms
                })
    except WebSocketDisconnect:
        print("WebSocket 客户端已断开")


@router.post("/face/register")
async def register_face(
    file: UploadFile = File(...),
    name: str = Form(...),
    token: str = Form(None)
):
    """注册人脸并绑定到当前登录用户"""
    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    if image is None:
        return {"success": False, "message": "无法解码图片"}

    # 从 token 获取用户 ID
    user_id = None
    if token:
        payload = decode_token(token)
        if payload:
            user_id = payload.get("user_id")
            print(f"注册人脸，用户ID: {user_id}")

    # 保存截图
    screenshot_dir = Path("data/screenshots")
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"{timestamp}.jpg"
    screenshot_path = screenshot_dir / filename
    cv2.imwrite(str(screenshot_path), image)

    relative_path = f"data/screenshots/{filename}"

    # 注册人脸并绑定用户
    success = face_identity.register_from_image_with_screenshot(
        image, name, relative_path, user_id
    )

    return {
        "success": success,
        "name": name,
        "screenshot": relative_path,
        "message": f"注册成功！欢迎 {name}" if success else "注册失败"
    }


@router.get("/face/list_with_screenshots", response_model=None)
async def list_faces_with_screenshots(request: Request):
    """列出当前用户的所有人脸（带截图路径）"""
    token = request.headers.get("Authorization")
    user_id = None
    if token and token.startswith("Bearer "):
        token = token[7:]
        payload = decode_token(token)
        if payload:
            user_id = payload.get("user_id")

    if user_id is None:
        return {"faces": []}

    return {"faces": face_identity.get_all_faces_with_screenshots(user_id)}


@router.get("/face/list")
async def list_faces(request: Request):
    """列出当前用户的所有人脸名称"""
    token = request.headers.get("Authorization")
    user_id = None
    if token and token.startswith("Bearer "):
        token = token[7:]
        payload = decode_token(token)
        if payload:
            user_id = payload.get("user_id")
    return {"faces": face_identity.get_all_faces(user_id), "count": face_identity.get_face_count(user_id)}


@router.delete("/face/{name}")
async def delete_face(name: str, request: Request):
    """删除人脸（必须登录）"""
    token = request.headers.get("Authorization")
    user_id = None
    if token and token.startswith("Bearer "):
        token = token[7:]
        payload = decode_token(token)
        if payload:
            user_id = payload.get("user_id")

    if user_id is None:
        raise HTTPException(status_code=401, detail="请先登录")

    success = face_identity.delete_face(name, user_id)
    return {"success": success, "name": name}


@router.get("/face/screenshot/{name}")
async def get_face_screenshot(name: str, request: Request):
    """获取人脸截图（必须登录）"""
    from fastapi.responses import FileResponse
    from urllib.parse import unquote

    token = request.headers.get("Authorization")
    user_id = None
    if token and token.startswith("Bearer "):
        token = token[7:]
        payload = decode_token(token)
        if payload:
            user_id = payload.get("user_id")

    if user_id is None:
        raise HTTPException(status_code=401, detail="请先登录")

    decoded_name = unquote(name)
    screenshot_path = face_identity.get_screenshot_path(decoded_name, user_id)

    if screenshot_path is None:
        raise HTTPException(404, "截图不存在")

    base_dir = Path(__file__).parent.parent.parent
    screenshot_path = Path(screenshot_path)
    if not screenshot_path.is_absolute():
        screenshot_path = base_dir / screenshot_path

    if not screenshot_path.exists():
        raise HTTPException(404, "截图文件不存在")

    return FileResponse(str(screenshot_path))