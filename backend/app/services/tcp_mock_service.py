"""TCP Mock 数据库操作 Service"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.protocol_mock import TcpMockHandler, TcpMockLog
from app.schemas.protocol_mock import TcpHandlerCreate, TcpHandlerUpdate


# ───── Handler CRUD ─────

async def list_handlers(session: AsyncSession) -> list[TcpMockHandler]:
    result = await session.execute(select(TcpMockHandler).order_by(TcpMockHandler.sort_order, TcpMockHandler.created_at))
    return list(result.scalars().all())


async def get_handler(session: AsyncSession, handler_id: uuid.UUID) -> TcpMockHandler | None:
    return await session.get(TcpMockHandler, handler_id)


async def create_handler(session: AsyncSession, data: TcpHandlerCreate) -> TcpMockHandler:
    max_order = await session.scalar(select(func.coalesce(func.max(TcpMockHandler.sort_order), -1)))
    handler = TcpMockHandler(**data.model_dump(), sort_order=max_order + 1)
    session.add(handler)
    await session.flush()
    await session.refresh(handler)
    return handler


async def update_handler(session: AsyncSession, handler_id: uuid.UUID, data: TcpHandlerUpdate) -> TcpMockHandler | None:
    handler = await session.get(TcpMockHandler, handler_id)
    if not handler:
        return None
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(handler, k, v)
    await session.flush()
    await session.refresh(handler)
    return handler


async def delete_handler(session: AsyncSession, handler_id: uuid.UUID) -> bool:
    handler = await session.get(TcpMockHandler, handler_id)
    if not handler:
        return False
    await session.delete(handler)
    await session.flush()
    return True


async def toggle_handler(session: AsyncSession, handler_id: uuid.UUID) -> TcpMockHandler | None:
    handler = await session.get(TcpMockHandler, handler_id)
    if not handler:
        return None
    handler.enabled = not handler.enabled
    await session.flush()
    await session.refresh(handler)
    return handler


async def toggle_lock_handler(session: AsyncSession, handler_id: uuid.UUID) -> TcpMockHandler | None:
    handler = await session.get(TcpMockHandler, handler_id)
    if not handler:
        return None
    handler.locked = not handler.locked
    await session.flush()
    await session.refresh(handler)
    return handler


async def reorder_handlers(session: AsyncSession, items: list[dict]) -> None:
    for item in items:
        await session.execute(
            update(TcpMockHandler).where(TcpMockHandler.id == item["id"]).values(sort_order=item["sort_order"])
        )
    await session.flush()


async def increment_hit(session: AsyncSession, handler_id: uuid.UUID) -> None:
    await session.execute(
        update(TcpMockHandler)
        .where(TcpMockHandler.id == handler_id)
        .values(hit_count=TcpMockHandler.hit_count + 1, last_hit_at=func.now())
    )


async def count_handlers(session: AsyncSession) -> int:
    return await session.scalar(select(func.count(TcpMockHandler.id))) or 0


# ───── Connection Logs ─────

async def create_log(session: AsyncSession, data: dict) -> TcpMockLog:
    log = TcpMockLog(**data)
    session.add(log)
    await session.flush()
    return log


async def list_logs(
    session: AsyncSession,
    *,
    handler_id: uuid.UUID | None = None,
    event_type: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[TcpMockLog], int]:
    q = select(TcpMockLog)
    count_q = select(func.count(TcpMockLog.id))

    if handler_id:
        q = q.where(TcpMockLog.handler_id == handler_id)
        count_q = count_q.where(TcpMockLog.handler_id == handler_id)

    if event_type:
        q = q.where(TcpMockLog.event_type == event_type)
        count_q = count_q.where(TcpMockLog.event_type == event_type)

    if search:
        pattern = f"%{search}%"
        q = q.where(TcpMockLog.client_ip.ilike(pattern))
        count_q = count_q.where(TcpMockLog.client_ip.ilike(pattern))

    total = await session.scalar(count_q) or 0
    result = await session.execute(q.order_by(TcpMockLog.timestamp.desc()).offset(offset).limit(limit))
    return list(result.scalars().all()), total


async def get_log(session: AsyncSession, log_id: uuid.UUID) -> TcpMockLog | None:
    return await session.get(TcpMockLog, log_id)


async def clear_logs(session: AsyncSession) -> int:
    result = await session.execute(delete(TcpMockLog))
    await session.flush()
    return result.rowcount


async def count_logs(session: AsyncSession) -> int:
    return await session.scalar(select(func.count(TcpMockLog.id))) or 0


async def trim_logs(session: AsyncSession, max_count: int) -> None:
    total = await count_logs(session)
    if total <= max_count:
        return
    to_delete = total - max_count
    oldest = await session.execute(
        select(TcpMockLog.id).order_by(TcpMockLog.timestamp.asc()).limit(to_delete)
    )
    ids = [row[0] for row in oldest.all()]
    if ids:
        await session.execute(delete(TcpMockLog).where(TcpMockLog.id.in_(ids)))
        await session.flush()
