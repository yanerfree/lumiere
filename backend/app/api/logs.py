"""审计日志查询端点。"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.auth import get_current_user, require_project_role, require_role
from app.deps.db import get_db
from app.models.user import User
from app.schemas.audit_log import AuditLogListResponse, AuditLogResponse
from app.services import audit_service

router = APIRouter(tags=["logs"])


@router.get("/api/logs")
async def list_global_logs(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
    action: str | None = Query(default=None),
    target_type: str | None = Query(default=None, alias="targetType"),
    actor_type: str | None = Query(default=None, alias="actorType"),
    user_id: uuid.UUID | None = Query(default=None, alias="userId"),
    start_time: datetime | None = Query(default=None, alias="startTime"),
    end_time: datetime | None = Query(default=None, alias="endTime"),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
):
    """全局审计日志（仅 admin）"""
    items, total = await audit_service.list_logs(
        session,
        user_id=user_id,
        action=action,
        target_type=target_type,
        actor_type=actor_type,
        start_time=start_time,
        end_time=end_time,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    return {
        "data": AuditLogListResponse(
            items=[AuditLogResponse(**item) for item in items],
            total=total,
            page=page,
            page_size=page_size,
        ).model_dump(by_alias=True)
    }


@router.get("/api/projects/{project_id}/logs")
async def list_project_logs(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
    action: str | None = Query(default=None),
    target_type: str | None = Query(default=None, alias="targetType"),
    actor_type: str | None = Query(default=None, alias="actorType"),
    user_id: uuid.UUID | None = Query(default=None, alias="userId"),
    start_time: datetime | None = Query(default=None, alias="startTime"),
    end_time: datetime | None = Query(default=None, alias="endTime"),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
):
    """项目级审计日志（项目成员可查看）"""
    items, total = await audit_service.list_logs(
        session,
        project_id=project_id,
        user_id=user_id,
        action=action,
        target_type=target_type,
        actor_type=actor_type,
        start_time=start_time,
        end_time=end_time,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    return {
        "data": AuditLogListResponse(
            items=[AuditLogResponse(**item) for item in items],
            total=total,
            page=page,
            page_size=page_size,
        ).model_dump(by_alias=True)
    }
