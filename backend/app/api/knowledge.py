"""知识库 API — CRUD + 查询"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import permissions as perms
from app.core.exceptions import AppError
from app.schemas.common import BaseSchema
from app.deps.auth import require_project_role
from app.deps.db import get_db
from app.models.user import User
from app.models.knowledge import MAX_CONTENT, KnowledgeEntry

router = APIRouter(prefix="/api/projects/{project_id}/knowledge", tags=["knowledge"])

# 知识库带 {project_id}，此前只校验登录 → 任意登录用户可越权读写他人项目知识库
# （会喂 AI，污染别人项目的上下文）。补项目级成员+角色校验，口径同环境/全局变量。
_KB_READ = perms.TIER_READ
_KB_WRITE = perms.TIER_WRITE


class CreateKnowledgeRequest(BaseSchema):
    category: str = Field(..., pattern="^(review_feedback|bug_pattern|api_note|custom)$")
    title: str = Field(..., min_length=1, max_length=200)
    # content 上**故意不挂 max_length**：pydantic 的 422 只会说「至多 200 个字符」，
    # 而这条上限最要紧的不是数字，是「超了该往哪儿去」（拆成两条 / 换去 Skill）。
    # 只报数字，写的人只会把后半句删掉 —— 那正是这次要修的那个坑。见 _reject_if_too_long。
    content: str = Field(..., min_length=1)
    reference_id: str | None = None


def _reject_if_too_long(content: str) -> None:
    """正文超限 → 400，并且**把出路一起说出来**。

    页面那边早就 `maxLength={200}` 拦住了，所以走到这儿的一定不是页面：
    curl、脚本、别的 agent 拿 token 直接打。此前这条路**一个字都不校验** ——
    同一条内容 CC 写被拒、打接口就进去了，「200 字上限」只有一半是真的。

    message 要短（前端只把 error.message 弹成 toast，detail 不显示），
    真正的理由塞 detail，给直接打接口的人看。
    """
    if len(content) <= MAX_CONTENT:
        return
    raise AppError(
        code="NOTE_TOO_LONG",
        message=f"正文 {len(content)} 字，超过 {MAX_CONTENT} 字上限。"
                "一条只说一件事，说不完拆成两条 —— 拆，不是把后半句删掉；"
                "整份规范/流程别塞这儿，它的家是「项目 Skill」（不限长度）。",
        detail="这些条目每次生成都会整个喂给下一轮 CC，长了直接挤占它的 context；"
               "平台侧原有的消费代码本来就只取前 100 字，写长了也没人看。"
               f"长文走 POST /api/projects/{{project_id}}/skills，须知这边只留一条指路的事实。",
        status_code=400,
    )


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
    # 评审自动写的那批（add_knowledge_from_review）不经过这条路由，所以不受这条限制 ——
    # 那是有意的：它写的是既有事实的搬运，砍掉后半句只会让结论变得看不懂。
    _reject_if_too_long(body.content)
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
