"""项目和分支查询工具 — Claude Code 用来定位目标项目和分支"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project, Branch


async def list_projects(session: AsyncSession) -> list[dict]:
    """列出本 Key 能操作的项目。

    **这个工具必须自己过滤。** `ToolScopeMiddleware` 的数据范围校验是按入参里的
    id 反查归属的，而它一个入参都没有 —— 反查那套管不到它。不过滤的后果不是
    "多看见几行"：它的描述写着「用于确定要操作的目标项目」，等于把全部项目摆到
    CC 面前请它自己挑，挑错就往别人项目里写。
    """
    from app.mcp.middleware import current_caller_project_id

    stmt = select(Project)
    mine = await current_caller_project_id()
    if mine:
        stmt = stmt.where(Project.id == uuid.UUID(mine))
    result = await session.execute(stmt.order_by(Project.created_at.desc()))
    return [{"id": str(p.id), "name": p.name, "description": p.description} for p in result.scalars().all()]


async def list_branches(session: AsyncSession, project_id: str) -> list[dict]:
    """列出项目下所有分支。Claude Code 用于确定目标分支。"""
    result = await session.execute(
        select(Branch)
        .where(Branch.project_id == uuid.UUID(project_id), Branch.status == "active")
        .order_by(Branch.created_at)
    )
    return [{"id": str(b.id), "name": b.name, "branch": b.branch, "description": b.description} for b in result.scalars().all()]
