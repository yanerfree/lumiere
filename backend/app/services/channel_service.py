"""通知渠道服务"""
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.core.audit import audit_log
from app.models.environment import NotificationChannel


async def list_channels(session: AsyncSession) -> list[NotificationChannel]:
    result = await session.execute(select(NotificationChannel).order_by(NotificationChannel.name))
    return list(result.scalars().all())


@audit_log(action="create", target_type="channel")
async def create_channel(session: AsyncSession, name: str, webhook_url: str) -> NotificationChannel:
    ch = NotificationChannel(name=name, webhook_url=webhook_url)
    session.add(ch)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise ConflictError(code="CHANNEL_NAME_EXISTS", message="渠道名称已存在")
    await session.refresh(ch)
    return ch


@audit_log(action="update", target_type="channel")
async def update_channel(session: AsyncSession, ch_id: uuid.UUID, name: str | None = None, webhook_url: str | None = None) -> NotificationChannel:
    result = await session.execute(select(NotificationChannel).where(NotificationChannel.id == ch_id))
    ch = result.scalar_one_or_none()
    if ch is None:
        raise NotFoundError(code="CHANNEL_NOT_FOUND", message="通知渠道不存在")
    if name is not None:
        ch.name = name
    if webhook_url is not None:
        ch.webhook_url = webhook_url
    await session.flush()
    await session.refresh(ch)
    return ch


@audit_log(action="delete", target_type="channel")
async def get_channel(session: AsyncSession, ch_id: uuid.UUID) -> NotificationChannel:
    """按 id 取渠道，不存在就 404。

    删除接口要先取出来拿 name 写审计日志（删完就查不到了），
    但这个函数一直没写 —— DELETE /api/channels/{id} 因此稳定 500。
    """
    result = await session.execute(select(NotificationChannel).where(NotificationChannel.id == ch_id))
    ch = result.scalar_one_or_none()
    if ch is None:
        raise NotFoundError(code="CHANNEL_NOT_FOUND", message="通知渠道不存在")
    return ch


async def delete_channel(session: AsyncSession, ch_id: uuid.UUID) -> None:
    result = await session.execute(select(NotificationChannel).where(NotificationChannel.id == ch_id))
    ch = result.scalar_one_or_none()
    if ch is None:
        raise NotFoundError(code="CHANNEL_NOT_FOUND", message="通知渠道不存在")
    await session.delete(ch)
    await session.flush()
