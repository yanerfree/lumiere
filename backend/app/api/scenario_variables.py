import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import permissions as perms
from app.core.exceptions import NotFoundError
from app.deps.auth import require_project_role
from app.deps.db import get_db
from app.models.scenario_variable import ScenarioVariable
from app.models.user import User
from app.schemas.common import BaseSchema

router = APIRouter(
    prefix="/api/projects/{project_id}/branches/{branch_id}/cases/{case_id}/scenario-variables",
    tags=["scenario-variables"],
)

_KINDS = ("literal", "random", "global_ref", "template")


class SVCreate(BaseSchema):
    name: str
    kind: str = "literal"          # literal | random | global_ref | template
    value_template: str = ""
    var_type: str = "string"
    description: str | None = None


class SVUpdate(BaseSchema):
    name: str | None = None
    kind: str | None = None
    value_template: str | None = None
    var_type: str | None = None
    description: str | None = None


class SVResponse(BaseSchema):
    id: uuid.UUID
    case_id: uuid.UUID
    name: str
    kind: str
    value_template: str
    var_type: str
    description: str | None = None


def _dump(v: ScenarioVariable) -> dict:
    return SVResponse.model_validate(v, from_attributes=True).model_dump(by_alias=True)


class PreviewBody(BaseSchema):
    template: str = ""


@router.get("/generators")
async def list_data_generators(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    _: User = Depends(require_project_role(*perms.TIER_READ)),
):
    """apifox 式「快速插入」数据生成器目录（分类 → 生成器 token）。"""
    from app.services.data_generators import GENERATORS
    return {"data": GENERATORS}


@router.post("/preview")
async def preview_template(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    body: PreviewBody,
    _: User = Depends(require_project_role(*perms.TIER_READ)),
):
    """把模板展开一次做样例预览（如 svc-{{$string:6}}-{{$city}} → svc-a1b2c3-上海）。"""
    from app.services.data_generators import expand_template
    return {"data": {"template": body.template, "sample": expand_template(body.template or "")}}


@router.get("")
async def list_scenario_variables(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_READ)),
):
    rows = await session.execute(
        select(ScenarioVariable).where(ScenarioVariable.case_id == case_id).order_by(ScenarioVariable.name)
    )
    return {"data": [_dump(v) for v in rows.scalars().all()]}


@router.post("")
async def create_scenario_variable(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    body: SVCreate,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_WRITE)),
):
    v = ScenarioVariable(
        case_id=case_id,
        name=body.name,
        kind=body.kind if body.kind in _KINDS else "literal",
        value_template=body.value_template or "",
        var_type=body.var_type or "string",
        description=body.description,
    )
    session.add(v)
    await session.commit()
    await session.refresh(v)
    return {"data": _dump(v)}


@router.put("/{var_id}")
async def update_scenario_variable(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    var_id: uuid.UUID,
    body: SVUpdate,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_WRITE)),
):
    v = await session.get(ScenarioVariable, var_id)
    if not v or v.case_id != case_id:
        raise NotFoundError(code="SCENARIO_VAR_NOT_FOUND", message="场景变量不存在")
    if body.name is not None:
        v.name = body.name
    if body.kind is not None and body.kind in _KINDS:
        v.kind = body.kind
    if body.value_template is not None:
        v.value_template = body.value_template
    if body.var_type is not None:
        v.var_type = body.var_type
    if body.description is not None:
        v.description = body.description
    await session.commit()
    await session.refresh(v)
    return {"data": _dump(v)}


@router.delete("/{var_id}")
async def delete_scenario_variable(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    var_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_WRITE)),
):
    v = await session.get(ScenarioVariable, var_id)
    if not v or v.case_id != case_id:
        raise NotFoundError(code="SCENARIO_VAR_NOT_FOUND", message="场景变量不存在")
    await session.delete(v)
    await session.commit()
    return {"data": {"deleted": True}}
