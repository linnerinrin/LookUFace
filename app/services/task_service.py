"""
异步任务服务
- 管理后台人脸分析任务
- 支持任务状态查询
"""

import uuid
import asyncio
from datetime import datetime
from typing import Dict, Optional
from concurrent.futures import ThreadPoolExecutor

from app.config import settings
from app.api.schemas import DetectResponse, TaskStatus
from app.services.face_service import face_service


class TaskService:
    """异步任务管理器"""

    def __init__(self):
        self.tasks: Dict[str, TaskStatus] = {}
        self.executor = ThreadPoolExecutor(max_workers=settings.MAX_WORKERS)

    def create_task(self) -> TaskStatus:
        """创建新任务"""
        task_id = str(uuid.uuid4())[:8]
        task = TaskStatus(
            task_id=task_id,
            status="pending",
            progress=0,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        self.tasks[task_id] = task
        return task

    async def process_task(self, task_id: str, image, detect_gender_age: bool):
        """后台处理任务"""
        task = self.tasks.get(task_id)
        if not task:
            return

        try:
            task.status = "processing"
            task.progress = 30
            task.updated_at = datetime.now()

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self.executor,
                face_service.analyze,
                image,
                detect_gender_age,
                True
            )

            task.status = "completed"
            task.progress = 100
            task.result = result
            task.updated_at = datetime.now()

        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            task.progress = -1
            task.updated_at = datetime.now()

    def get_task(self, task_id: str) -> Optional[TaskStatus]:
        """获取任务状态"""
        return self.tasks.get(task_id)


task_service = TaskService()