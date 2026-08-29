import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import permissions as perms
from app.core.exceptions import NotFoundError
from app.deps.auth import require_project_role
from app.deps.db import get_db
from app.models.automation_resource import AutomationResource
from app.models.user import User
from app.schemas.common import BaseSchema

router = APIRouter(
    prefix="/api/projects/{project_id}/automation-resources",
    tags=["automation-resources"],
)


class ARCreate(BaseSchema):
    name: str
    exists_check: dict = {}
    create_def: dict | None = None
    keep: bool = True
    description: str | None = None


class ARUpdate(BaseSchema):
    name: str | None = None
    exists_check: dict | None = None
    create_def: dict | None = None
    keep: bool | None = None
    description: str | None = None


class ARResponse(BaseSchema):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    exists_check: dict
    create_def: dict | None = None
    keep: bool
    description: str | None = None


def _dump(r: AutomationResource) -> dict:
    return ARResponse.model_validate(r, from_attributes=True).model_dump(by_alias=True)


@router.get("")
async def list_resources(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_READ)),
):
    rows = await session.execute(
        select(AutomationResource).where(AutomationResource.project_id == project_id).order_by(AutomationResource.name)
    )
    return {"data": [_dump(r) for r in rows.scalars().all()]}


@router.get("/precheck")
async def precheck_resources(
    project_id: uuid.UUID,
    env_id: uuid.UUID | None = Query(None, alias="envId"),
    role: str = "ADMIN",
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_READ)),
):
    """跑自动化前预检全局资源存在性。缺失项在 missing 里返回，交用户确认/补建，不自动创建。"""
    from app.services import precheck_service
    report = await precheck_service.check_resources(session, project_id, env_id, role)
    return {"data": report}


@router.post("")
async def create_resource(
    project_id: uuid.UUID,
    body: ARCreate,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_DOC_MANAGE)),
):
    r = AutomationResource(
        project_id=project_id,
        name=body.name,
        exists_check=body.exists_check or {},
        create_def=body.create_def,
        keep=body.keep,
        description=body.description,
    )
    session.add(r)
    await session.commit()
    await session.refresh(r)
    return {"data": _dump(r)}


@router.put("/{resource_id}")
async def update_resource(
    project_id: uuid.UUID,
    resource_id: uuid.UUID,
    body: ARUpdate,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_DOC_MANAGE)),
):
    r = await session.get(AutomationResource, resource_id)
    if not r or r.project_id != project_id:
        raise NotFoundError(code="AUTOMATION_RESOURCE_NOT_FOUND", message="自动化资源不存在")
    if body.name is not None:
        r.name = body.name
    if body.exists_check is not None:
        r.exists_check = body.exists_check
    if body.create_def is not None:
        r.create_def = body.create_def
    if body.keep is not None:
        r.keep = body.keep
    if body.description is not None:
        r.description = body.description
    await session.commit()
    await session.refresh(r)
    return {"data": _dump(r)}


@router.delete("/{resource_id}")
async def delete_resource(
    project_id: uuid.UUID,
    resource_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_DOC_MANAGE)),
):
    r = await session.get(AutomationResource, resource_id)
    if not r or r.project_id != project_id:
        raise NotFoundError(code="AUTOMATION_RESOURCE_NOT_FOUND", message="自动化资源不存在")
    await session.delete(r)
    await session.commit()
    return {"data": {"deleted": True}}
