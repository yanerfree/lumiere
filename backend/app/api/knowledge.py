"""知识库 API — CRUD + 查询"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.common import BaseSchema
from app.deps.auth import require_project_role
from app.deps.db import get_db
from app.models.user import User
from app.models.knowledge import KnowledgeEntry

router = APIRouter(prefix="/api/projects/{project_id}/knowledge", tags=["knowledge"])

# 知识库带 {project_id}，此前只校验登录 → 任意登录用户可越权读写他人项目知识库
# （会喂 AI，污染别人项目的上下文）。补项目级成员+角色校验，口径同环境/全局变量。
_KB_READ = ("project_admin", "developer", "tester", "guest")
_KB_WRITE = ("project_admin", "developer", "tester")


class CreateKnowledgeRequest(BaseSchema):
    category: str = Field(..., pattern="^(review_feedback|bug_pattern|api_note|custom)$")
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1)
    reference_id: str | None = None


@router.get("")
async def list_knowledge(
    project_id: uuid.UUID,
    category: str | None = None,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*_KB_READ)),
):
    q = select(KnowledgeEntry).where(KnowledgeEntry.project_id == project_id)
    if category:
        q = q.where(KnowledgeEntry.category == category)
    q = q.order_by(KnowledgeEntry.created_at.desc())
    result = await session.execute(q)
    entries = result.scalars().all()
    return {"data": [
        {
            "id": str(e.id),
            "category": e.category,
            "title": e.title,
            "content": e.content,
            "source": e.source,
            "referenceId": e.reference_id,
            "createdAt": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entries
    ]}


@router.post("")
async def create_knowledge(
    project_id: uuid.UUID,
    body: CreateKnowledgeRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*_KB_WRITE)),
):
    entry = KnowledgeEntry(
        project_id=project_id,
        category=body.category,
        title=body.title,
        content=body.content,
        source="manual",
        reference_id=body.reference_id,
    )
    session.add(entry)
    await session.flush()
    return {"data": {"id": str(entry.id), "title": entry.title}}


@router.delete("/{entry_id}")
async def delete_knowledge(
    project_id: uuid.UUID,
    entry_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*_KB_WRITE)),
):
    entry = await session.get(KnowledgeEntry, entry_id)
    if entry and entry.project_id == project_id:
        await session.delete(entry)
        await session.commit()
    return {"data": {"deleted": True}}


async def add_knowledge_from_review(session: AsyncSession, project_id: uuid.UUID, report: dict):
    """评审完成后自动写入知识条目"""
    suggestions = report.get("suggestions", [])
    issues = report.get("issues", [])

    for s in suggestions[:5]:
        session.add(KnowledgeEntry(
            project_id=project_id,
            category="review_feedback",
            title=s[:100] if isinstance(s, str) else str(s)[:100],
            content=s if isinstance(s, str) else str(s),
            source="ai_review",
        ))

    for issue in issues[:5]:
        if isinstance(issue, dict):
            session.add(KnowledgeEntry(
                project_id=project_id,
                category="review_feedback",
                title=f"[{issue.get('dimension','')}] {issue.get('description','')[:80]}",
                content=f"用例: {issue.get('case','')}\n问题: {issue.get('description','')}\n严重度: {issue.get('severity','')}",
                source="ai_review",
            ))

    await session.flush()
