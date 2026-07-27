"""WebSocket Mock 数据库操作 Service"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.protocol_mock import WsMockEndpoint, WsMockLog
from app.schemas.protocol_mock import WsEndpointCreate, WsEndpointUpdate


# ───── Endpoint CRUD ─────

async def list_endpoints(session: AsyncSession) -> list[WsMockEndpoint]:
    result = await session.execute(select(WsMockEndpoint).order_by(WsMockEndpoint.sort_order, WsMockEndpoint.created_at))
    return list(result.scalars().all())


async def get_endpoint(session: AsyncSession, endpoint_id: uuid.UUID) -> WsMockEndpoint | None:
    return await session.get(WsMockEndpoint, endpoint_id)


async def create_endpoint(session: AsyncSession, data: WsEndpointCreate) -> WsMockEndpoint:
    max_order = await session.scalar(select(func.coalesce(func.max(WsMockEndpoint.sort_order), -1)))
    endpoint = WsMockEndpoint(**data.model_dump(), sort_order=max_order + 1)
    session.add(endpoint)
    await session.flush()
    await session.refresh(endpoint)
    return endpoint


async def update_endpoint(session: AsyncSession, endpoint_id: uuid.UUID, data: WsEndpointUpdate) -> WsMockEndpoint | None:
    endpoint = await session.get(WsMockEndpoint, endpoint_id)
    if not endpoint:
        return None
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(endpoint, k, v)
    await session.flush()
    await session.refresh(endpoint)
    return endpoint


async def delete_endpoint(session: AsyncSession, endpoint_id: uuid.UUID) -> bool:
    endpoint = await session.get(WsMockEndpoint, endpoint_id)
    if not endpoint:
        return False
    await session.delete(endpoint)
    await session.flush()
    return True


async def toggle_endpoint(session: AsyncSession, endpoint_id: uuid.UUID) -> WsMockEndpoint | None:
    endpoint = await session.get(WsMockEndpoint, endpoint_id)
    if not endpoint:
        return None
    endpoint.enabled = not endpoint.enabled
    await session.flush()
    await session.refresh(endpoint)
    return endpoint


async def toggle_lock_endpoint(session: AsyncSession, endpoint_id: uuid.UUID) -> WsMockEndpoint | None:
    endpoint = await session.get(WsMockEndpoint, endpoint_id)
    if not endpoint:
        return None
    endpoint.locked = not endpoint.locked
    await session.flush()
    await session.refresh(endpoint)
    return endpoint


async def reorder_endpoints(session: AsyncSession, items: list[dict]) -> None:
    for item in items:
        await session.execute(
            update(WsMockEndpoint).where(WsMockEndpoint.id == item["id"]).values(sort_order=item["sort_order"])
        )
    await session.flush()


async def increment_hit(session: AsyncSession, endpoint_id: uuid.UUID) -> None:
    await session.execute(
        update(WsMockEndpoint)
        .where(WsMockEndpoint.id == endpoint_id)
        .values(hit_count=WsMockEndpoint.hit_count + 1, last_hit_at=func.now())
    )


async def count_endpoints(session: AsyncSession) -> int:
    return await session.scalar(select(func.count(WsMockEndpoint.id))) or 0


# ───── Connection Logs ─────

async def create_log(session: AsyncSession, data: dict) -> WsMockLog:
    log = WsMockLog(**data)
    session.add(log)
    await session.flush()
    return log


async def list_logs(
    session: AsyncSession,
    *,
    endpoint_id: uuid.UUID | None = None,
    event_type: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[WsMockLog], int]:
    q = select(WsMockLog)
    count_q = select(func.count(WsMockLog.id))

    if endpoint_id:
        q = q.where(WsMockLog.endpoint_id == endpoint_id)
        count_q = count_q.where(WsMockLog.endpoint_id == endpoint_id)

    if event_type:
        q = q.where(WsMockLog.event_type == event_type)
        count_q = count_q.where(WsMockLog.event_type == event_type)

    if search:
        pattern = f"%{search}%"
        q = q.where(WsMockLog.path.ilike(pattern))
        count_q = count_q.where(WsMockLog.path.ilike(pattern))

    total = await session.scalar(count_q) or 0
    result = await session.execute(q.order_by(WsMockLog.timestamp.desc()).offset(offset).limit(limit))
    return list(result.scalars().all()), total


async def get_log(session: AsyncSession, log_id: uuid.UUID) -> WsMockLog | None:
    return await session.get(WsMockLog, log_id)


async def clear_logs(session: AsyncSession) -> int:
    result = await session.execute(delete(WsMockLog))
    await session.flush()
    return result.rowcount


async def count_logs(session: AsyncSession) -> int:
    return await session.scalar(select(func.count(WsMockLog.id))) or 0


async def trim_logs(session: AsyncSession, max_count: int) -> None:
    total = await count_logs(session)
    if total <= max_count:
        return
    to_delete = total - max_count
    oldest = await session.execute(
        select(WsMockLog.id).order_by(WsMockLog.timestamp.asc()).limit(to_delete)
    )
    ids = [row[0] for row in oldest.all()]
    if ids:
        await session.execute(delete(WsMockLog).where(WsMockLog.id.in_(ids)))
        await session.flush()
