"""API Mock 数据库操作 Service"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_mock import ApiMockLog, ApiMockRoute
from app.schemas.api_mock import ApiMockRouteCreate, ApiMockRouteUpdate


# ───── Route CRUD ─────

async def list_routes(session: AsyncSession) -> list[ApiMockRoute]:
    result = await session.execute(select(ApiMockRoute).order_by(ApiMockRoute.sort_order, ApiMockRoute.created_at))
    return list(result.scalars().all())


async def get_route(session: AsyncSession, route_id: uuid.UUID) -> ApiMockRoute | None:
    return await session.get(ApiMockRoute, route_id)


async def create_route(session: AsyncSession, data: ApiMockRouteCreate) -> ApiMockRoute:
    max_order = await session.scalar(select(func.coalesce(func.max(ApiMockRoute.sort_order), -1)))
    route = ApiMockRoute(**data.model_dump(), sort_order=max_order + 1)
    session.add(route)
    await session.flush()
    await session.refresh(route)
    return route


async def update_route(session: AsyncSession, route_id: uuid.UUID, data: ApiMockRouteUpdate) -> ApiMockRoute | None:
    route = await session.get(ApiMockRoute, route_id)
    if not route:
        return None
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(route, k, v)
    await session.flush()
    await session.refresh(route)
    return route


async def delete_route(session: AsyncSession, route_id: uuid.UUID) -> bool:
    route = await session.get(ApiMockRoute, route_id)
    if not route:
        return False
    await session.delete(route)
    await session.flush()
    return True


async def toggle_route(session: AsyncSession, route_id: uuid.UUID) -> ApiMockRoute | None:
    route = await session.get(ApiMockRoute, route_id)
    if not route:
        return None
    route.enabled = not route.enabled
    await session.flush()
    await session.refresh(route)
    return route


async def toggle_lock(session: AsyncSession, route_id: uuid.UUID) -> ApiMockRoute | None:
    route = await session.get(ApiMockRoute, route_id)
    if not route:
        return None
    route.locked = not route.locked
    await session.flush()
    await session.refresh(route)
    return route


async def reorder_routes(session: AsyncSession, items: list[dict]) -> None:
    for item in items:
        await session.execute(
            update(ApiMockRoute).where(ApiMockRoute.id == item["id"]).values(sort_order=item["sort_order"])
        )
    await session.flush()


async def increment_hit(session: AsyncSession, route_id: uuid.UUID) -> None:
    await session.execute(
        update(ApiMockRoute)
        .where(ApiMockRoute.id == route_id)
        .values(hit_count=ApiMockRoute.hit_count + 1, last_hit_at=func.now())
    )


async def count_routes(session: AsyncSession) -> int:
    return await session.scalar(select(func.count(ApiMockRoute.id))) or 0


# ───── Request Logs ─────

async def create_log(session: AsyncSession, data: dict) -> ApiMockLog:
    log = ApiMockLog(**data)
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
) -> tuple[list[ApiMockLog], int]:
    q = select(ApiMockLog)
    count_q = select(func.count(ApiMockLog.id))

    if status == "ok":
        q = q.where(ApiMockLog.status_code < 400)
        count_q = count_q.where(ApiMockLog.status_code < 400)
    elif status == "error":
        q = q.where(ApiMockLog.status_code >= 400)
        count_q = count_q.where(ApiMockLog.status_code >= 400)

    if route_id:
        q = q.where(ApiMockLog.route_id == route_id)
        count_q = count_q.where(ApiMockLog.route_id == route_id)

    if search:
        pattern = f"%{search}%"
        q = q.where(ApiMockLog.path.ilike(pattern))
        count_q = count_q.where(ApiMockLog.path.ilike(pattern))

    total = await session.scalar(count_q) or 0
    result = await session.execute(q.order_by(ApiMockLog.timestamp.desc()).offset(offset).limit(limit))
    return list(result.scalars().all()), total


async def get_log(session: AsyncSession, log_id: uuid.UUID) -> ApiMockLog | None:
    return await session.get(ApiMockLog, log_id)


async def clear_logs(session: AsyncSession, route_id: uuid.UUID | None = None) -> int:
    stmt = delete(ApiMockLog)
    if route_id:
        stmt = stmt.where(ApiMockLog.route_id == route_id)
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount


async def count_logs(session: AsyncSession) -> int:
    return await session.scalar(select(func.count(ApiMockLog.id))) or 0


async def trim_logs(session: AsyncSession, max_count: int) -> None:
    total = await count_logs(session)
    if total <= max_count:
        return
    to_delete = total - max_count
    oldest = await session.execute(
        select(ApiMockLog.id).order_by(ApiMockLog.timestamp.asc()).limit(to_delete)
    )
    ids = [row[0] for row in oldest.all()]
    if ids:
        await session.execute(delete(ApiMockLog).where(ApiMockLog.id.in_(ids)))
        await session.flush()
