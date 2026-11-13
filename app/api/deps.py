# """模块：FastAPI依赖"""
# """架构作用：为路由注入数据库会话"""
"""FastAPI依赖

系统没有任何身份校验 —— 见README §安全边界，
部署时请把 ``HOST`` 保持为 ``127.0.0.1`` 或置于可信内网
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.models import get_db

# FastAPI通过get_db提供和释放每个请求的会话
DbSession = Annotated[Session, Depends(get_db)]

__all__ = ["DbSession"]
