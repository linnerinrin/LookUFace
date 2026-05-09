"""
/app/api/routes.py
主要API路由
run_sync_analysis() 异步处理face_service的analysis.py
health_check() 健康检测
websocket_camera() 开启websocket摄像流
register_face() 注册人脸 并从token中绑定账户
list_faces_with_screenshots() 从token中获取账户 并获得截图列表
delete_face() 获取账户 删除人脸
get_face_screenshot() 获取账户 获得人脸截图
"""

import asyncio
import cv2
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, UploadFile, File, HTTPException, Form, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer

from app.api.schemas import DetectResponse, HealthResponse, DeleteFaceResponse, FaceRegisterResponse, FacesListResponse,DeleteFaceRequest, GetScreenshotRequest
from app.auth.routes import get_current_user
from app.core.face_identity import face_identity
from app.services.face_service import face_service
from app.database import User
from app.config import settings

#创建路由器 后续接口都在这里注册
router = APIRouter()

#cpu密集型任务放进线程池
executor = ThreadPoolExecutor(max_workers=settings.MAX_WORKERS)

#读取token
security = HTTPBearer()

async def run_sync_analysis(image: np.ndarray, detect_gender_age: bool) -> DetectResponse:
    """在线程池中执行同步分析，避免阻塞事件循环"""
    loop = asyncio.get_event_loop()

    #loop.run_in_executor(executor,func,*args)
    #在线程executor中异步执行func函数 可以被await
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
    )


@router.websocket("/ws/camera")
async def websocket_camera(websocket: WebSocket):
    """WebSocket 实时摄像头分析"""
    await websocket.accept()
    print("WebSocket 客户端已连接")

    try:
        while True:
            try:
                message = await websocket.receive()
            except RuntimeError as e:
                print(f"WebSocket 接收异常: {e}")
                break
            except WebSocketDisconnect:
                print("WebSocket 客户端已断开")
                break

            if "bytes" in message:
                nparr = np.frombuffer(message["bytes"], np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

                if frame is None:
                    continue

            #数据拿去分析
                result = await run_sync_analysis(frame, detect_gender_age=True)
                faces_data = [f.dict() for f in result.faces]


            #返回分析结果
                await websocket.send_json({
                    "success": True,
                    "face_count": result.face_count,
                    "faces": faces_data,
                    "processing_ms": result.processing_ms
                })
    except WebSocketDisconnect:
        print("WebSocket 客户端已断开")


@router.post("/face/register",response_model=FaceRegisterResponse)
async def register_face(
    file: UploadFile = File(...),
    name: str = Form(...),
    current_user: User = Depends(get_current_user)
):
    """注册人脸并绑定到当前登录用户"""
    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    if image is None:
        return FaceRegisterResponse(
            success=False,
            name=name,
            screenshot="",
            message="无法解码图片"
        )

    user_id = current_user.id

    # 保存截图
    screenshot_dir = settings.screenshot_dir
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"{timestamp}.jpg"
    screenshot_path = screenshot_dir / filename
    cv2.imwrite(str(screenshot_path), image)

    relative_path = f"storage/screenshots/{filename}"

    success = face_identity.register_from_image_with_screenshot(
        image, name, relative_path, user_id
    )

    return FaceRegisterResponse(
        success=success,
        name=name,
        screenshot=relative_path,
        message=f"注册成功！欢迎 {name}" if success else "注册失败"
    )


@router.post("/face/list_with_screenshots",response_model=FacesListResponse)
async def list_faces_with_screenshots(current_user:User=Depends(get_current_user)):
    """列出当前用户的所有人脸（带截图路径）"""
    user_id = current_user.id
    return FacesListResponse(faces=face_identity.get_all_faces_with_screenshots(user_id))



@router.delete("/face",response_model=DeleteFaceResponse)
async def delete_face(request:DeleteFaceRequest, current_user:User=Depends(get_current_user)):
    """删除人脸（必须登录）"""
    user_id = current_user.id
    name=request.name
    success = face_identity.delete_face(name, user_id)
    return DeleteFaceResponse(
        success=success,
        name=name
    )


@router.post("/face/screenshot",response_class=FileResponse)
async def get_face_screenshot(request:GetScreenshotRequest, current_user:User=Depends(get_current_user)):
    """获取人脸截图（必须登录）"""
    user_id = current_user.id
    name=request.name
    #对前端编码后的中文字符进行解码
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



#@router.post("")