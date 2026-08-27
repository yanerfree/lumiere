"""当前用户的自省接口 —— 「我能干什么」。

前端菜单/按钮收口、AI 助手能力面都读这一个接口，后端按 core/permissions 解析。
一处判定、三处消费，不再前后端各拍一套权限规则。
"""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import permissions as perms
from app.deps.auth import get_current_user
from app.deps.db import get_db
from app.deps.permissions import resolve_for_request
from app.models.user import User

router = APIRouter(prefix="/api/me", tags=["me"])


@router.get("/permissions")
async def my_permissions(
    project_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """当前用户的权限点集合。

    - 不带 project_id：只返回系统级权限点（能建项目、看不看得到平台设施等）。
    - 带 project_id：叠加该项目里的项目角色权限点；admin 恒为全集。

    返回还带上 system_role / project_role，方便前端直接展示「你在本项目是 X」。
    """
    project_role = None
    if current_user.role != "admin" and project_id is not None:
        from app.deps.permissions import load_project_role
        project_role = await load_project_role(session, current_user, project_id)

    granted = await resolve_for_request(session, current_user, project_id)
    return {
        "data": {
            "system_role": current_user.role,
            "project_role": project_role,
            "is_super_admin": current_user.role == "admin",
            "permissions": sorted(granted),
            "all_permissions": sorted(perms.ALL_PERMISSIONS),
        }
    }
