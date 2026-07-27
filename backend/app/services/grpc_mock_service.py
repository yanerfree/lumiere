"""gRPC Mock 数据库操作 Service"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.protocol_mock import GrpcMockLog, GrpcMockService
from app.schemas.protocol_mock import GrpcServiceCreate, GrpcServiceUpdate


# ───── Service CRUD ─────

async def list_services(session: AsyncSession) -> list[GrpcMockService]:
    result = await session.execute(select(GrpcMockService).order_by(GrpcMockService.sort_order, GrpcMockService.created_at))
    return list(result.scalars().all())


async def get_service(session: AsyncSession, service_id: uuid.UUID) -> GrpcMockService | None:
    return await session.get(GrpcMockService, service_id)


async def create_service(session: AsyncSession, data: GrpcServiceCreate) -> GrpcMockService:
    max_order = await session.scalar(select(func.coalesce(func.max(GrpcMockService.sort_order), -1)))
    service = GrpcMockService(**data.model_dump(), sort_order=max_order + 1)
    session.add(service)
    await session.flush()
    await session.refresh(service)
    return service


async def update_service(session: AsyncSession, service_id: uuid.UUID, data: GrpcServiceUpdate) -> GrpcMockService | None:
    service = await session.get(GrpcMockService, service_id)
    if not service:
        return None
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(service, k, v)
    await session.flush()
    await session.refresh(service)
    return service


async def delete_service(session: AsyncSession, service_id: uuid.UUID) -> bool:
    service = await session.get(GrpcMockService, service_id)
    if not service:
        return False
    await session.delete(service)
    await session.flush()
    return True


async def toggle_service(session: AsyncSession, service_id: uuid.UUID) -> GrpcMockService | None:
    service = await session.get(GrpcMockService, service_id)
    if not service:
        return None
    service.enabled = not service.enabled
    await session.flush()
    await session.refresh(service)
    return service


async def toggle_lock_service(session: AsyncSession, service_id: uuid.UUID) -> GrpcMockService | None:
    service = await session.get(GrpcMockService, service_id)
    if not service:
        return None
    service.locked = not service.locked
    await session.flush()
    await session.refresh(service)
    return service


async def reorder_services(session: AsyncSession, items: list[dict]) -> None:
    for item in items:
        await session.execute(
            update(GrpcMockService).where(GrpcMockService.id == item["id"]).values(sort_order=item["sort_order"])
        )
    await session.flush()


async def increment_hit(session: AsyncSession, service_id: uuid.UUID) -> None:
    await session.execute(
        update(GrpcMockService)
        .where(GrpcMockService.id == service_id)
        .values(hit_count=GrpcMockService.hit_count + 1, last_hit_at=func.now())
    )


async def count_services(session: AsyncSession) -> int:
    return await session.scalar(select(func.count(GrpcMockService.id))) or 0


# ───── Call Logs ─────

async def create_log(session: AsyncSession, data: dict) -> GrpcMockLog:
    log = GrpcMockLog(**data)
    session.add(log)
    await session.flush()
    return log


async def list_logs(
    session: AsyncSession,
    *,
    service_id: uuid.UUID | None = None,
    service_name: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[GrpcMockLog], int]:
    q = select(GrpcMockLog)
    count_q = select(func.count(GrpcMockLog.id))

    if service_id:
        q = q.where(GrpcMockLog.service_id == service_id)
        count_q = count_q.where(GrpcMockLog.service_id == service_id)

    if service_name:
        q = q.where(GrpcMockLog.service_name == service_name)
        count_q = count_q.where(GrpcMockLog.service_name == service_name)

    if search:
        pattern = f"%{search}%"
        q = q.where(GrpcMockLog.method_name.ilike(pattern))
        count_q = count_q.where(GrpcMockLog.method_name.ilike(pattern))

    total = await session.scalar(count_q) or 0
    result = await session.execute(q.order_by(GrpcMockLog.timestamp.desc()).offset(offset).limit(limit))
    return list(result.scalars().all()), total


async def get_log(session: AsyncSession, log_id: uuid.UUID) -> GrpcMockLog | None:
    return await session.get(GrpcMockLog, log_id)


async def clear_logs(session: AsyncSession) -> int:
    result = await session.execute(delete(GrpcMockLog))
    await session.flush()
    return result.rowcount


async def count_logs(session: AsyncSession) -> int:
    return await session.scalar(select(func.count(GrpcMockLog.id))) or 0


async def trim_logs(session: AsyncSession, max_count: int) -> None:
    total = await count_logs(session)
    if total <= max_count:
        return
    to_delete = total - max_count
    oldest = await session.execute(
        select(GrpcMockLog.id).order_by(GrpcMockLog.timestamp.asc()).limit(to_delete)
    )
    ids = [row[0] for row in oldest.all()]
    if ids:
        await session.execute(delete(GrpcMockLog).where(GrpcMockLog.id.in_(ids)))
        await session.flush()
