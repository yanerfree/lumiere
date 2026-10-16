"""个人记录 API —— 本人增删改查自己的；系统管理员能读所有人的（只读）。

不带 {project_id}，所以不走项目守卫：可见范围只由「是不是本人 / 是不是 admin」决定。
游客的写操作由 deps/auth.py 的非 GET 闸门统一挡掉（只读封顶），这里不另开口子。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError
from app.deps.auth import get_current_user
from app.deps.db import get_db
from app.models.user import User
from app.models.user_note import MAX_TITLE, UserNote
from app.schemas.common import BaseSchema

router = APIRouter(prefix="/api/notes", tags=["notes"])


class NoteRequest(BaseSchema):
    title: str = Field(..., min_length=1, max_length=MAX_TITLE)
    content: str = ""

    @field_validator("title", mode="before")
    @classmethod
    def _strip_title(cls, v):
        # 先去空格再校验长度：否则「三个空格」能过 min_length，存进去是个空标题
        return v.strip() if isinstance(v, str) else v


def _is_admin(user: User) -> bool:
    return user.role == "admin"


def _dump(note: UserNote, owner_name: str | None, me: User) -> dict:
    return {
        "id": str(note.id),
        "title": note.title,
        "content": note.content,
        "userId": str(note.user_id),
        "username": owner_name,
        "mine": note.user_id == me.id,
        "createdAt": note.created_at.isoformat() if note.created_at else None,
        "updatedAt": note.updated_at.isoformat() if note.updated_at else None,
    }


async def _load_visible(session: AsyncSession, note_id: uuid.UUID, me: User) -> tuple[UserNote, str | None]:
    row = (await session.execute(
        select(UserNote, User.username).join(User, User.id == UserNote.user_id).where(UserNote.id == note_id)
    )).first()
    # 别人的记录对非 admin 一律报「不存在」，不报 403 —— 403 等于告诉对方这个 id 是真的
    if row is None or (row[0].user_id != me.id and not _is_admin(me)):
        raise NotFoundError(message="记录不存在")
    return row[0], row[1]


async def _load_own(session: AsyncSession, note_id: uuid.UUID, me: User) -> tuple[UserNote, str | None]:
    note, owner = await _load_visible(session, note_id, me)
    if note.user_id != me.id:
        raise ForbiddenError(message="只能修改或删除自己的记录")
    return note, owner


@router.get("")
async def list_notes(
    mine_only: bool = False,
    keyword: str | None = None,
    session: AsyncSession = Depends(get_db),
    me: User = Depends(get_current_user),
):
    q = select(UserNote, User.username).join(User, User.id == UserNote.user_id)
    if not _is_admin(me) or mine_only:
        q = q.where(UserNote.user_id == me.id)
    if keyword:
        like = f"%{keyword}%"
        q = q.where(UserNote.title.ilike(like) | UserNote.content.ilike(like))
    q = q.order_by(UserNote.updated_at.desc())
    rows = (await session.execute(q)).all()
    return {"data": [_dump(n, name, me) for n, name in rows]}


@router.get("/{note_id}")
async def get_note(note_id: uuid.UUID, session: AsyncSession = Depends(get_db), me: User = Depends(get_current_user)):
    note, owner = await _load_visible(session, note_id, me)
    return {"data": _dump(note, owner, me)}


@router.post("", status_code=201)
async def create_note(body: NoteRequest, session: AsyncSession = Depends(get_db), me: User = Depends(get_current_user)):
    note = UserNote(user_id=me.id, title=body.title, content=body.content)
    session.add(note)
    await session.commit()
    await session.refresh(note)
    return {"data": _dump(note, me.username, me)}


@router.put("/{note_id}")
async def update_note(
    note_id: uuid.UUID, body: NoteRequest,
    session: AsyncSession = Depends(get_db), me: User = Depends(get_current_user),
):
    note, owner = await _load_own(session, note_id, me)
    note.title = body.title
    note.content = body.content
    await session.commit()
    await session.refresh(note)
    return {"data": _dump(note, owner, me)}


@router.delete("/{note_id}")
async def delete_note(note_id: uuid.UUID, session: AsyncSession = Depends(get_db), me: User = Depends(get_current_user)):
    note, _ = await _load_own(session, note_id, me)
    await session.delete(note)
    await session.commit()
    return {"data": {"id": str(note_id)}}
