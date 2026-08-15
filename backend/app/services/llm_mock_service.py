"""LLM Mock 数据库操作 Service"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_mock import MockRequestLog, MockRoute
from app.schemas.llm_mock import MockRouteCreate, MockRouteUpdate


# ───── Route CRUD ─────

async def list_routes(session: AsyncSession) -> list[MockRoute]:
    result = await session.execute(select(MockRoute).order_by(MockRoute.sort_order, MockRoute.created_at))
    return list(result.scalars().all())


async def get_route(session: AsyncSession, route_id: uuid.UUID) -> MockRoute | None:
    return await session.get(MockRoute, route_id)


async def create_route(session: AsyncSession, data: MockRouteCreate) -> MockRoute:
    max_order = await session.scalar(select(func.coalesce(func.max(MockRoute.sort_order), -1)))
    payload = data.model_dump()
    route = MockRoute(**payload, sort_order=max_order + 1)
    session.add(route)
    await session.flush()
    await session.refresh(route)
    return route


async def update_route(session: AsyncSession, route_id: uuid.UUID, data: MockRouteUpdate) -> MockRoute | None:
    route = await session.get(MockRoute, route_id)
    if not route:
        return None
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(route, k, v)
    await session.flush()
    await session.refresh(route)
    return route


async def delete_route(session: AsyncSession, route_id: uuid.UUID) -> bool:
    route = await session.get(MockRoute, route_id)
    if not route:
        return False
    await session.delete(route)
    await session.flush()
    return True


async def toggle_route(session: AsyncSession, route_id: uuid.UUID) -> MockRoute | None:
    route = await session.get(MockRoute, route_id)
    if not route:
        return None
    route.enabled = not route.enabled
    await session.flush()
    await session.refresh(route)
    return route


async def toggle_lock(session: AsyncSession, route_id: uuid.UUID) -> MockRoute | None:
    route = await session.get(MockRoute, route_id)
    if not route:
        return None
    route.locked = not route.locked
    await session.flush()
    await session.refresh(route)
    return route


async def reorder_routes(session: AsyncSession, items: list[dict]) -> None:
    for item in items:
        await session.execute(
            update(MockRoute).where(MockRoute.id == item["id"]).values(sort_order=item["sort_order"])
        )
    await session.flush()


async def increment_hit(session: AsyncSession, route_id: uuid.UUID) -> None:
    await session.execute(
        update(MockRoute)
        .where(MockRoute.id == route_id)
        .values(hit_count=MockRoute.hit_count + 1, last_hit_at=func.now())
    )


async def count_routes(session: AsyncSession) -> int:
    return await session.scalar(select(func.count(MockRoute.id))) or 0


# ───── Request Logs ─────

async def create_log(session: AsyncSession, data: dict) -> MockRequestLog:
    log = MockRequestLog(**data)
    session.add(log)
    await session.flush()
    return log


async def list_logs(
    session: AsyncSession,
    *,
    status: str | None = None,
    route_id: uuid.UUID | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[MockRequestLog], int]:
    q = select(MockRequestLog)
    count_q = select(func.count(MockRequestLog.id))

    if status == "ok":
        q = q.where(MockRequestLog.status_code < 400)
        count_q = count_q.where(MockRequestLog.status_code < 400)
    elif status == "error":
        q = q.where(MockRequestLog.status_code >= 400)
        count_q = count_q.where(MockRequestLog.status_code >= 400)

    if route_id:
        q = q.where(MockRequestLog.route_id == route_id)
        count_q = count_q.where(MockRequestLog.route_id == route_id)

    if search:
        pattern = f"%{search}%"
        q = q.where(
            MockRequestLog.path.ilike(pattern) | MockRequestLog.request_model.ilike(pattern)
        )
        count_q = count_q.where(
            MockRequestLog.path.ilike(pattern) | MockRequestLog.request_model.ilike(pattern)
        )

    total = await session.scalar(count_q) or 0
    result = await session.execute(q.order_by(MockRequestLog.timestamp.desc()).offset(offset).limit(limit))
    return list(result.scalars().all()), total


async def get_log(session: AsyncSession, log_id: uuid.UUID) -> MockRequestLog | None:
    return await session.get(MockRequestLog, log_id)


async def clear_logs(session: AsyncSession) -> int:
    result = await session.execute(delete(MockRequestLog))
    await session.flush()
    return result.rowcount


async def count_logs(session: AsyncSession) -> int:
    return await session.scalar(select(func.count(MockRequestLog.id))) or 0


async def trim_logs(session: AsyncSession, max_count: int) -> None:
    total = await count_logs(session)
    if total <= max_count:
        return
    to_delete = total - max_count
    oldest = await session.execute(
        select(MockRequestLog.id).order_by(MockRequestLog.timestamp.asc()).limit(to_delete)
    )
    ids = [row[0] for row in oldest.all()]
    if ids:
        await session.execute(delete(MockRequestLog).where(MockRequestLog.id.in_(ids)))
        await session.flush()
