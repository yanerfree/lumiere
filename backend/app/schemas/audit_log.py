"""审计日志 Schema。"""
import uuid
from datetime import datetime

from pydantic import Field

from app.schemas.common import BaseSchema


class AuditLogResponse(BaseSchema):
    """审计日志响应"""
    id: uuid.UUID
    user_id: uuid.UUID | None
    username: str | None
    project_id: uuid.UUID | None
    project_name: str | None = None
    action: str
    target_type: str
    target_id: uuid.UUID | None
    target_name: str | None
    changes: dict | None
    trace_id: str | None
    # 操作来源：None = 存量日志（没记过来源）；"mcp" = 外部 Claude Code 调的，
    # actor_label 是那把 Key 的名字。人在页面上点的照旧只有 username。
    actor_type: str | None = None
    actor_label: str | None = None
    created_at: datetime


class AuditLogListResponse(BaseSchema):
    """审计日志列表响应（含分页）"""
    items: list[AuditLogResponse]
    total: int
    page: int
    page_size: int
