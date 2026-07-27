"""UDP Mock 数据库操作 Service"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.protocol_mock import UdpMockHandler, UdpMockLog
from app.schemas.protocol_mock import UdpHandlerCreate, UdpHandlerUpdate


# ───── Handler CRUD ─────

async def list_handlers(session: AsyncSession) -> list[UdpMockHandler]:
    result = await session.execute(select(UdpMockHandler).order_by(UdpMockHandler.sort_order, UdpMockHandler.created_at))
    return list(result.scalars().all())


async def get_handler(session: AsyncSession, handler_id: uuid.UUID) -> UdpMockHandler | None:
    return await session.get(UdpMockHandler, handler_id)


async def create_handler(session: AsyncSession, data: UdpHandlerCreate) -> UdpMockHandler:
    max_order = await session.scalar(select(func.coalesce(func.max(UdpMockHandler.sort_order), -1)))
    handler = UdpMockHandler(**data.model_dump(), sort_order=max_order + 1)
    session.add(handler)
    await session.flush()
    await session.refresh(handler)
    return handler


async def update_handler(session: AsyncSession, handler_id: uuid.UUID, data: UdpHandlerUpdate) -> UdpMockHandler | None:
    handler = await session.get(UdpMockHandler, handler_id)
    if not handler:
        return None
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(handler, k, v)
    await session.flush()
    await session.refresh(handler)
    return handler


async def delete_handler(session: AsyncSession, handler_id: uuid.UUID) -> bool:
    handler = await session.get(UdpMockHandler, handler_id)
    if not handler:
        return False
    await session.delete(handler)
    await session.flush()
    return True


async def toggle_handler(session: AsyncSession, handler_id: uuid.UUID) -> UdpMockHandler | None:
    handler = await session.get(UdpMockHandler, handler_id)
    if not handler:
        return None
    handler.enabled = not handler.enabled
    await session.flush()
    await session.refresh(handler)
    return handler


async def toggle_lock_handler(session: AsyncSession, handler_id: uuid.UUID) -> UdpMockHandler | None:
    handler = await session.get(UdpMockHandler, handler_id)
    if not handler:
        return None
    handler.locked = not handler.locked
    await session.flush()
    await session.refresh(handler)
    return handler


async def reorder_handlers(session: AsyncSession, items: list[dict]) -> None:
    for item in items:
        await session.execute(
            update(UdpMockHandler).where(UdpMockHandler.id == item["id"]).values(sort_order=item["sort_order"])
        )
    await session.flush()


async def increment_hit(session: AsyncSession, handler_id: uuid.UUID) -> None:
    await session.execute(
        update(UdpMockHandler)
        .where(UdpMockHandler.id == handler_id)
        .values(hit_count=UdpMockHandler.hit_count + 1, last_hit_at=func.now())
    )


async def count_handlers(session: AsyncSession) -> int:
    return await session.scalar(select(func.count(UdpMockHandler.id))) or 0


# ───── Datagram Logs ─────

async def create_log(session: AsyncSession, data: dict) -> UdpMockLog:
    log = UdpMockLog(**data)
    session.add(log)
    await session.flush()
    return log


async def list_logs(
    session: AsyncSession,
    *,
    handler_id: uuid.UUID | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[UdpMockLog], int]:
    q = select(UdpMockLog)
    count_q = select(func.count(UdpMockLog.id))

    if handler_id:
        q = q.where(UdpMockLog.handler_id == handler_id)
        count_q = count_q.where(UdpMockLog.handler_id == handler_id)

    if search:
        pattern = f"%{search}%"
        q = q.where(UdpMockLog.client_ip.ilike(pattern))
        count_q = count_q.where(UdpMockLog.client_ip.ilike(pattern))

    total = await session.scalar(count_q) or 0
    result = await session.execute(q.order_by(UdpMockLog.timestamp.desc()).offset(offset).limit(limit))
    return list(result.scalars().all()), total


async def get_log(session: AsyncSession, log_id: uuid.UUID) -> UdpMockLog | None:
    return await session.get(UdpMockLog, log_id)


async def clear_logs(session: AsyncSession) -> int:
    result = await session.execute(delete(UdpMockLog))
    await session.flush()
    return result.rowcount


async def count_logs(session: AsyncSession) -> int:
    return await session.scalar(select(func.count(UdpMockLog.id))) or 0


async def trim_logs(session: AsyncSession, max_count: int) -> None:
    total = await count_logs(session)
    if total <= max_count:
        return
    to_delete = total - max_count
    oldest = await session.execute(
        select(UdpMockLog.id).order_by(UdpMockLog.timestamp.asc()).limit(to_delete)
    )
    ids = [row[0] for row in oldest.all()]
    if ids:
        await session.execute(delete(UdpMockLog).where(UdpMockLog.id.in_(ids)))
        await session.flush()
