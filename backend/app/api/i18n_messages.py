import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import permissions as perms
from app.core.exceptions import NotFoundError
from app.deps.auth import require_project_role
from app.deps.db import get_db
from app.models.i18n_message import ProjectI18nMessage
from app.models.user import User
from app.schemas.common import BaseSchema

router = APIRouter(
    prefix="/api/projects/{project_id}/i18n-messages",
    tags=["i18n-messages"],
)


class I18nCreate(BaseSchema):
    key_text: str
    translations: dict = {}
    module: str | None = None
    category: str | None = None
    source: str | None = None
    description: str | None = None


class I18nUpdate(BaseSchema):
    key_text: str | None = None
    translations: dict | None = None
    module: str | None = None
    category: str | None = None
    # 来源也可改。它是履历，但**列表上显示、编辑时改不了**这件事本身更糟 ——
    # 人会以为页面坏了。想纠正错分类的，让它改。
    source: str | None = None
    description: str | None = None


class I18nResponse(BaseSchema):
    id: uuid.UUID
    project_id: uuid.UUID
    key_text: str
    translations: dict
    module: str | None = None
    category: str | None = None
    source: str
    description: str | None = None


def _dump(r: ProjectI18nMessage) -> dict:
    return I18nResponse.model_validate(r, from_attributes=True).model_dump(by_alias=True)


@router.get("")
async def list_messages(
    project_id: uuid.UUID,
    category: str | None = Query(None),
    untranslated: bool = Query(False),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_READ)),
):
    stmt = select(ProjectI18nMessage).where(ProjectI18nMessage.project_id == project_id)
    if category:
        stmt = stmt.where(ProjectI18nMessage.category == category)
    if untranslated:
        # 语种键是 BCP-47（en-US）。只认裸 "en" 的话，从被测系统 locale 导进来的
        # 2400+ 条译文全被当成"待补"，页面上「已翻译」恒为 0。
        en = ProjectI18nMessage.translations["en"].astext
        en_us = ProjectI18nMessage.translations["en-US"].astext
        stmt = stmt.where(or_(en.is_(None), en == "").self_group())
        stmt = stmt.where(or_(en_us.is_(None), en_us == "").self_group())
    stmt = stmt.order_by(ProjectI18nMessage.category, ProjectI18nMessage.key_text)
    rows = await session.execute(stmt)
    return {"data": [_dump(r) for r in rows.scalars().all()]}


@router.post("")
async def create_message(
    project_id: uuid.UUID,
    body: I18nCreate,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_DOC_MANAGE)),
):
    r = ProjectI18nMessage(
        project_id=project_id,
        key_text=body.key_text,
        translations=body.translations or {},
        module=body.module,
        category=body.category,
        description=body.description,
        source=body.source or "manual",
    )
    session.add(r)
    await session.commit()
    await session.refresh(r)
    return {"data": _dump(r)}


@router.put("/{msg_id}")
async def update_message(
    project_id: uuid.UUID,
    msg_id: uuid.UUID,
    body: I18nUpdate,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_DOC_MANAGE)),
):
    r = await session.get(ProjectI18nMessage, msg_id)
    if not r or r.project_id != project_id:
        raise NotFoundError(code="I18N_MESSAGE_NOT_FOUND", message="词条不存在")
    if body.key_text is not None:
        r.key_text = body.key_text
    if body.translations is not None:
        r.translations = body.translations
    if body.module is not None:
        r.module = body.module
    if body.category is not None:
        r.category = body.category
    if body.source is not None:
        r.source = body.source
    if body.description is not None:
        r.description = body.description
    await session.commit()
    await session.refresh(r)
    return {"data": _dump(r)}


@router.delete("/{msg_id}")
async def delete_message(
    project_id: uuid.UUID,
    msg_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_DOC_MANAGE)),
):
    r = await session.get(ProjectI18nMessage, msg_id)
    if not r or r.project_id != project_id:
        raise NotFoundError(code="I18N_MESSAGE_NOT_FOUND", message="词条不存在")
    await session.delete(r)
    await session.commit()
    return {"data": {"deleted": True}}


@router.post("/harvest")
async def harvest_messages(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_DOC_MANAGE)),
):
    """扫该项目所有 UI 脚本，采集含中文的 UI 文案入词典（translations 留空）。"""
    from app.services.i18n_harvest_service import harvest_project
    result = await harvest_project(session, project_id)
    await session.commit()
    return {"data": result}
