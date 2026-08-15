"""接口测试 API — 场景 CRUD + AI 生成"""

import json
import logging
import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.schemas.common import BaseSchema
from app.core.exceptions import NotFoundError
from app.deps.auth import get_current_user, require_project_role
from app.deps.db import get_db
from app.models.user import User
from app.models.api_test import ApiTestScenario, ApiTestStep
from app.models.api_test_folder import ApiTestFolder

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/projects/{project_id}/branches/{branch_id}/api-tests",
    tags=["api-test"],
)


def _scenario_to_dict(s: ApiTestScenario, steps: list[ApiTestStep] | None = None) -> dict:
    d = {
        "id": str(s.id),
        "code": s.code,
        "title": s.title,
        "priority": s.priority,
        "description": s.description,
        "status": s.status,
        "source": s.source,
        "preSteps": s.pre_steps,
        "folderId": str(s.folder_id) if s.folder_id else None,
        "sourceCaseId": str(s.source_case_id) if s.source_case_id else None,
        "sourceApiIds": s.source_api_ids,
        "envVariables": s.env_variables,
        "createdAt": s.created_at.isoformat() if s.created_at else None,
        "updatedAt": s.updated_at.isoformat() if s.updated_at else None,
    }
    if steps is not None:
        d["steps"] = [_step_to_dict(st) for st in steps]
    return d


def _step_to_dict(st: ApiTestStep) -> dict:
    return {
        "id": str(st.id),
        "sortOrder": st.sort_order,
        "groupName": st.group_name,
        "name": st.name,
        "method": st.method,
        "url": st.url,
        "headers": st.headers,
        "body": st.body,
        "assertions": st.assertions,
        "variablesExtract": st.variables_extract,
        "waitMs": st.wait_ms,
        "retryTimeoutMs": st.retry_timeout_ms,
        "retryIntervalMs": st.retry_interval_ms,
        "enabled": st.enabled,
        "preScript": st.pre_script,
        "postScript": st.post_script,
        "lastStatus": st.last_status,
        "lastResponse": st.last_response,
    }


@router.get("")
async def list_scenarios(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    status: str | None = Query(None),
    folder_id: str | None = Query(None),
    source_case_id: str | None = Query(None),
    # 场景其实有两类混在同一张表里：
    #   single       单接口测试（source_api_ids，无 source_case_id）—— 接口测试模块的本职
    #   orchestrated 用例编排的多步链（source_case_id）—— 归属某条用例，在用例详情里看
    # 不拆表是因为编排链要复用同一套执行引擎（整链跑 + 出测试报告），
    # 但列表要能分开，否则模块会被编排场景淹没。
    kind: str | None = Query(None, description="single | orchestrated | all"),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = select(ApiTestScenario).where(
        ApiTestScenario.project_id == project_id,
        ApiTestScenario.branch_id == branch_id,
    )
    if status and status != "all":
        q = q.where(ApiTestScenario.status == status)
    if folder_id:
        q = q.where(ApiTestScenario.folder_id == uuid.UUID(folder_id))
    if source_case_id:
        q = q.where(ApiTestScenario.source_case_id == uuid.UUID(source_case_id))
    if kind == "single":
        q = q.where(ApiTestScenario.source_case_id.is_(None))
    elif kind == "orchestrated":
        q = q.where(ApiTestScenario.source_case_id.is_not(None))
    if search:
        kw = f"%{search}%"
        q = q.where(
            ApiTestScenario.title.ilike(kw) | ApiTestScenario.code.ilike(kw)
        )
    q = q.order_by(ApiTestScenario.created_at.desc())

    if size > 0:
        from sqlalchemy import func as sa_func
        count_result = await session.execute(select(sa_func.count()).select_from(q.subquery()))
        total = count_result.scalar() or 0
        q = q.offset((page - 1) * size).limit(size)
        result = await session.execute(q)
        scenarios = result.scalars().all()
        return {"data": {"items": await _with_step_counts(session, scenarios), "total": total, "page": page, "size": size}}

    result = await session.execute(q)
    scenarios = result.scalars().all()
    return {"data": await _with_step_counts(session, scenarios)}


async def _with_step_counts(session: AsyncSession, scenarios: list[ApiTestScenario]) -> list[dict]:
    """列表项补 stepCount。用例详情里的编排场景需要它——只显示标题看不出
    这条场景有多少步，"11 步"这种信息得跳到接口测试模块才看得到。"""
    items = [_scenario_to_dict(s) for s in scenarios]
    if not items:
        return items
    from sqlalchemy import func as sa_func

    rows = (await session.execute(
        select(ApiTestStep.scenario_id, sa_func.count())
        .where(ApiTestStep.scenario_id.in_([s.id for s in scenarios]))
        .group_by(ApiTestStep.scenario_id)
    )).all()
    counts = {str(sid): n for sid, n in rows}
    for it in items:
        it["stepCount"] = counts.get(it["id"], 0)
    return items


@router.get("/stats/quality")
async def generation_quality_stats(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """生成质量度量 — AI 生成直接发布率等统计。"""
    from sqlalchemy import func as sa_func

    base = select(ApiTestScenario).where(
        ApiTestScenario.project_id == project_id,
        ApiTestScenario.branch_id == branch_id,
        ApiTestScenario.source == "ai",
    )
    total_result = await session.execute(select(sa_func.count()).select_from(base.subquery()))
    total_ai = total_result.scalar() or 0

    published_unedited = await session.execute(
        select(sa_func.count()).select_from(
            base.where(ApiTestScenario.status == "published", ApiTestScenario.edited_after_generate == False).subquery()
        )
    )
    direct_publish = published_unedited.scalar() or 0

    published_edited = await session.execute(
        select(sa_func.count()).select_from(
            base.where(ApiTestScenario.status == "published", ApiTestScenario.edited_after_generate == True).subquery()
        )
    )
    edited_publish = published_edited.scalar() or 0

    total_published = direct_publish + edited_publish
    direct_rate = round(direct_publish / total_published * 100, 1) if total_published > 0 else 0

    return {"data": {
        "totalAi": total_ai,
        "totalPublished": total_published,
        "directPublish": direct_publish,
        "editedPublish": edited_publish,
        "directPublishRate": direct_rate,
    }}


class BatchOperationRequest(BaseSchema):
    ids: list[str]
    action: str  # publish | deprecate | delete | move
    folder_id: str | None = None


@router.put("/batch")
async def batch_operation(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: BatchOperationRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    from app.core.exceptions import AppError

    scenario_ids = [uuid.UUID(sid) for sid in body.ids]
    result = await session.execute(
        select(ApiTestScenario)
        .where(ApiTestScenario.id.in_(scenario_ids), ApiTestScenario.project_id == project_id, ApiTestScenario.branch_id == branch_id)
    )
    scenarios = result.scalars().all()

    if body.action == "publish":
        for s in scenarios:
            if s.status == "draft":
                s.status = "published"
    elif body.action == "deprecate":
        for s in scenarios:
            if s.status == "published":
                s.status = "deprecated"
    elif body.action == "delete":
        for s in scenarios:
            await session.delete(s)
    elif body.action == "move":
        if not body.folder_id:
            raise AppError(code="MISSING_FOLDER", message="请指定目标文件夹", status_code=400)
        fid = uuid.UUID(body.folder_id)
        for s in scenarios:
            s.folder_id = fid
    else:
        raise AppError(code="INVALID_ACTION", message=f"不支持的操作: {body.action}", status_code=400)

    await session.commit()
    return {"data": {"affected": len(scenarios)}}


class RunBatchRequest(BaseSchema):
    scenario_ids: list[str]
    env_id: str | None = None
    # 页面上勾选的步骤（运行时子集，不落库）。不传 = 全跑。
    # 跟步骤自己的 `enabled` 是两件事：enabled 是持久禁用，这个只管这一次。
    step_ids: list[str] | None = None


@router.post("/run")
async def run_batch_scenarios(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: RunBatchRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    from app.services.api_test_runner import run_batch

    scenario_uuids = [uuid.UUID(sid) for sid in body.scenario_ids]

    # 选择环境时合并 全局变量+环境变量 作为基础 env（优先级低于场景自身 env_variables）
    base_env: dict = {}
    env_name: str | None = None
    if body.env_id:
        from app.services import environment_service
        try:
            merged = await environment_service.get_merged_variables(session, uuid.UUID(body.env_id))
            base_env = {item["key"]: item["value"] for item in merged}
        except Exception:
            logger.warning("加载环境变量失败 env_id=%s", body.env_id)
        # 环境名带下去，运行详情里要能说清"这个值是哪个环境给的"
        try:
            from app.models.environment import Environment
            env_obj = await session.get(Environment, uuid.UUID(body.env_id))
            env_name = env_obj.name if env_obj else None
        except Exception:
            env_name = None

    async def event_stream():
        try:
            async for event in run_batch(scenario_uuids, session, user_id=current_user.id, project_id=project_id, base_env=base_env, branch_id=branch_id, env_name=env_name,
                                        step_ids=set(body.step_ids) if body.step_ids is not None else None):
                yield f"data: {json.dumps({'type': event.type, **event.data}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("run_batch failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:200]}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# ── 文件夹管理（必须在 /{scenario_id} 之前） ──

@router.get("/folders")
async def list_api_test_folders(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    # 必须跟场景列表用同一个 kind 口径。否则目录树数的是全部、列表只显示一部分，
    # 出现"树上写着 5 条、点进去暂无数据"这种自相矛盾。
    kind: str | None = Query(None, description="single | orchestrated | all"),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await session.execute(
        select(ApiTestFolder).where(ApiTestFolder.branch_id == branch_id).order_by(ApiTestFolder.sort_order)
    )
    all_folders = result.scalars().all()

    cnt_q = select(ApiTestScenario.folder_id).where(ApiTestScenario.branch_id == branch_id)
    if kind == "single":
        cnt_q = cnt_q.where(ApiTestScenario.source_case_id.is_(None))
    elif kind == "orchestrated":
        cnt_q = cnt_q.where(ApiTestScenario.source_case_id.is_not(None))
    sc_all = await session.execute(cnt_q)
    scenario_counts = {}
    for (fid,) in sc_all:
        if fid:
            scenario_counts[fid] = scenario_counts.get(fid, 0) + 1

    def build_tree(parent_id=None):
        children = [f for f in all_folders if f.parent_id == parent_id]
        result = []
        for f in children:
            child_nodes = build_tree(f.id)
            direct_count = scenario_counts.get(f.id, 0)
            total_count = direct_count + sum(c.get("scenarioCount", 0) for c in child_nodes)
            child_folder_ids = [f.id]
            for c in child_nodes:
                child_folder_ids.extend(c.get("descendantFolderIds", []))
            result.append({
                "id": str(f.id),
                "name": f.name,
                "parentId": str(f.parent_id) if f.parent_id else None,
                "scenarioCount": total_count,
                "children": child_nodes,
                "descendantFolderIds": [str(fid) for fid in child_folder_ids],
            })
        return result

    return {"data": build_tree()}


@router.post("/folders")
async def create_api_test_folder(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    parent_id: str | None = None,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    folder = ApiTestFolder(
        branch_id=branch_id,
        name=name,
        parent_id=uuid.UUID(parent_id) if parent_id else None,
    )
    session.add(folder)
    await session.commit()
    return {"data": {"id": str(folder.id), "name": folder.name}}


@router.put("/folders/{folder_id}")
async def rename_api_test_folder(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    folder_id: uuid.UUID,
    name: str,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    folder = await session.get(ApiTestFolder, folder_id)
    if not folder or folder.branch_id != branch_id:
        raise NotFoundError(code="NOT_FOUND", message="文件夹不存在")
    name = name.strip()
    if not name:
        from app.core.exceptions import AppError
        raise AppError(code="INVALID_NAME", message="文件夹名不能为空", status_code=400)
    folder.name = name
    await session.commit()
    return {"data": {"id": str(folder.id), "name": folder.name}}


@router.delete("/folders/{folder_id}")
async def delete_api_test_folder(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    folder_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    folder = await session.get(ApiTestFolder, folder_id)
    if not folder or folder.branch_id != branch_id:
        raise NotFoundError(code="NOT_FOUND", message="文件夹不存在")
    children = await session.execute(select(ApiTestFolder).where(ApiTestFolder.parent_id == folder_id))
    if children.scalars().first():
        from app.core.exceptions import AppError
        raise AppError(code="HAS_CHILDREN", message="该文件夹下有子文件夹，请先删除", status_code=400)
    sc = await session.execute(select(ApiTestScenario).where(ApiTestScenario.folder_id == folder_id).limit(1))
    if sc.scalars().first():
        from app.core.exceptions import AppError
        raise AppError(code="HAS_SCENARIOS", message="该文件夹下有测试场景，请先移动或删除", status_code=400)
    await session.delete(folder)
    await session.commit()
    return {"data": {"deleted": True}}


@router.get("/{scenario_id}")
async def get_scenario(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    scenario_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scenario = await session.get(ApiTestScenario, scenario_id)
    if not scenario or scenario.project_id != project_id:
        raise NotFoundError(code="NOT_FOUND", message="场景不存在")

    steps_result = await session.execute(
        select(ApiTestStep)
        .where(ApiTestStep.scenario_id == scenario_id)
        .order_by(ApiTestStep.sort_order)
    )
    steps = steps_result.scalars().all()
    return {"data": _scenario_to_dict(scenario, steps)}


@router.delete("/{scenario_id}")
async def delete_scenario(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    scenario_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    scenario = await session.get(ApiTestScenario, scenario_id)
    if not scenario or scenario.project_id != project_id:
        raise NotFoundError(code="NOT_FOUND", message="场景不存在")
    title = scenario.title
    await session.delete(scenario)
    await session.commit()
    await write_audit_log(session, action="delete", target_type="api_test_scenario",
                          target_id=scenario_id, target_name=title,
                          user_id=current_user.id, project_id=project_id)
    return {"data": {"deleted": True}}


VALID_STATUS_TRANSITIONS = {
    "draft": ["published"],
    "published": ["deprecated"],
    "deprecated": [],
}


class CreateScenarioRequest(BaseSchema):
    title: str = Field(..., min_length=1, max_length=200)
    priority: str = Field(default="P1")
    folder_id: str | None = None
    description: str | None = None
    # 从用例详情里手动新建时带上，这样它和 CC 同步过来的场景是同一份数据、
    # 出现在同一个列表里（否则用例里建的又变成游离场景）
    source_case_id: str | None = None


@router.post("")
async def create_scenario(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: CreateScenarioRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    from sqlalchemy import func as sa_func
    max_result = await session.execute(
        select(sa_func.max(ApiTestScenario.code))
        .where(ApiTestScenario.branch_id == branch_id)
    )
    max_code = max_result.scalar()
    next_num = 1
    if max_code:
        try:
            next_num = int(max_code.split("-")[1]) + 1
        except (IndexError, ValueError):
            pass
    code = f"AT-{next_num:04d}"

    scenario = ApiTestScenario(
        project_id=project_id,
        branch_id=branch_id,
        code=code,
        title=body.title,
        priority=body.priority,
        source="manual",
        status="draft",
        folder_id=uuid.UUID(body.folder_id) if body.folder_id else None,
        description=body.description,
        source_case_id=uuid.UUID(body.source_case_id) if body.source_case_id else None,
        created_by=current_user.id,
    )
    session.add(scenario)
    await session.commit()
    await write_audit_log(session, action="create", target_type="api_test_scenario",
                          target_id=scenario.id, target_name=scenario.title,
                          user_id=current_user.id, project_id=project_id)
    return {"data": _scenario_to_dict(scenario)}


class UpdateScenarioRequest(BaseSchema):
    title: str | None = None
    status: str | None = None
    priority: str | None = None
    description: str | None = None
    pre_steps: dict | None = None
    folder_id: str | None = None


@router.put("/{scenario_id}")
async def update_scenario(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    scenario_id: uuid.UUID,
    body: UpdateScenarioRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    from app.core.exceptions import AppError

    scenario = await session.get(ApiTestScenario, scenario_id)
    if not scenario or scenario.project_id != project_id:
        raise NotFoundError(code="NOT_FOUND", message="场景不存在")

    if body.status is not None and body.status != scenario.status:
        allowed = VALID_STATUS_TRANSITIONS.get(scenario.status, [])
        if body.status not in allowed:
            raise AppError(
                code="INVALID_STATUS_TRANSITION",
                message=f"不允许从 {scenario.status} 变更为 {body.status}",
                status_code=400,
            )
        scenario.status = body.status

    if scenario.status == "published" or scenario.status == "deprecated":
        has_content_change = any([body.title, body.priority, body.description, body.pre_steps])
        if has_content_change:
            raise AppError(code="NOT_EDITABLE", message="已发布/已废弃的场景不可编辑", status_code=400)

    if body.title is not None: scenario.title = body.title
    if body.priority is not None: scenario.priority = body.priority
    if body.description is not None: scenario.description = body.description
    if body.pre_steps is not None: scenario.pre_steps = body.pre_steps
    if body.folder_id is not None: scenario.folder_id = uuid.UUID(body.folder_id) if body.folder_id else None

    if scenario.source == "ai" and not scenario.edited_after_generate:
        has_edit = any([body.title, body.priority, body.description, body.pre_steps])
        if has_edit:
            scenario.edited_after_generate = True

    await session.commit()
    await session.refresh(scenario)
    if body.status is not None:
        await write_audit_log(session, action=f"status_{body.status}", target_type="api_test_scenario",
                              target_id=scenario.id, target_name=scenario.title,
                              user_id=current_user.id, project_id=project_id)
    return {"data": _scenario_to_dict(scenario)}


class UpdateStepRequest(BaseSchema):
    name: str | None = None
    method: str | None = None
    url: str | None = None
    headers: dict | None = None
    # 请求体不一定是对象：有的接口就要裸数组（如 PUT /environments/{id}/variables
    # 收的是 [{key,value}]），也有 raw 文本。列只是 JSONB，之前限死 dict 导致
    # MCP 能写进去、编辑器一保存就 422「body: Input should be a valid dictionary」，
    # 连带整条场景存不了、"添加步骤"也跟着失败。
    body: dict | list | str | None = None
    assertions: list | None = None
    variables_extract: dict | None = None
    # 异步下发导致的抢跑假红靠这三个解决，见 api_test_runner.run_step
    wait_ms: int | None = None
    retry_timeout_ms: int | None = None
    retry_interval_ms: int | None = None
    enabled: bool | None = None
    group_name: str | None = None
    pre_script: dict | None = None
    post_script: dict | None = None


class ReorderStepsRequest(BaseSchema):
    step_ids: list[str]


@router.put("/{scenario_id}/steps/reorder")
async def reorder_steps(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    scenario_id: uuid.UUID,
    body: ReorderStepsRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    from app.core.exceptions import AppError

    scenario = await session.get(ApiTestScenario, scenario_id)
    if not scenario or scenario.project_id != project_id:
        raise NotFoundError(code="NOT_FOUND", message="场景不存在")
    if scenario.status != "draft":
        raise AppError(code="NOT_EDITABLE", message="已发布/已废弃的场景不可排序步骤", status_code=400)

    for i, sid in enumerate(body.step_ids):
        step = await session.get(ApiTestStep, uuid.UUID(sid))
        if step and step.scenario_id == scenario_id:
            step.sort_order = i
    await session.commit()
    return {"data": {"reordered": len(body.step_ids)}}


@router.put("/{scenario_id}/steps/{step_id}")
async def update_step(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    scenario_id: uuid.UUID,
    step_id: uuid.UUID,
    payload: UpdateStepRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    from app.core.exceptions import AppError

    scenario = await session.get(ApiTestScenario, scenario_id)
    if not scenario or scenario.project_id != project_id:
        raise NotFoundError(code="NOT_FOUND", message="场景不存在")
    if scenario.status != "draft":
        raise AppError(code="NOT_EDITABLE", message="已发布/已废弃的场景不可编辑步骤", status_code=400)

    step = await session.get(ApiTestStep, step_id)
    if not step or step.scenario_id != scenario_id:
        raise NotFoundError(code="NOT_FOUND", message="步骤不存在")
    for field in ['name', 'method', 'url', 'headers', 'body', 'assertions', 'variables_extract', 'enabled', 'group_name', 'pre_script', 'post_script',
                  'wait_ms', 'retry_timeout_ms', 'retry_interval_ms']:
        val = getattr(payload, field, None)
        if val is not None:
            setattr(step, field, val)

    if scenario.source == "ai" and not scenario.edited_after_generate:
        scenario.edited_after_generate = True

    await session.commit()
    return {"data": _step_to_dict(step)}


class CreateStepRequest(BaseSchema):
    name: str = Field(..., min_length=1)
    method: str = Field(default="GET")
    url: str = Field(default="")


@router.post("/{scenario_id}/steps")
async def create_step(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    scenario_id: uuid.UUID,
    body: CreateStepRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    from app.core.exceptions import AppError

    scenario = await session.get(ApiTestScenario, scenario_id)
    if not scenario or scenario.project_id != project_id:
        raise NotFoundError(code="NOT_FOUND", message="场景不存在")
    if scenario.status != "draft":
        raise AppError(code="NOT_EDITABLE", message="已发布/已废弃的场景不可添加步骤", status_code=400)
    from sqlalchemy import func as sa_func
    max_result = await session.execute(
        select(sa_func.max(ApiTestStep.sort_order)).where(ApiTestStep.scenario_id == scenario_id)
    )
    next_order = (max_result.scalar() or 0) + 1

    step = ApiTestStep(
        scenario_id=scenario_id,
        sort_order=next_order,
        name=body.name,
        method=body.method,
        url=body.url,
    )
    session.add(step)
    await session.commit()
    return {"data": _step_to_dict(step)}


@router.delete("/{scenario_id}/steps/{step_id}")
async def delete_step(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    scenario_id: uuid.UUID,
    step_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    from app.core.exceptions import AppError

    scenario = await session.get(ApiTestScenario, scenario_id)
    if not scenario or scenario.project_id != project_id:
        raise NotFoundError(code="NOT_FOUND", message="场景不存在")
    if scenario.status != "draft":
        raise AppError(code="NOT_EDITABLE", message="已发布/已废弃的场景不可删除步骤", status_code=400)

    step = await session.get(ApiTestStep, step_id)
    if not step or step.scenario_id != scenario_id:
        raise NotFoundError(code="NOT_FOUND", message="步骤不存在")
    await session.delete(step)
    await session.commit()
    return {"data": {"deleted": True}}


@router.post("/{scenario_id}/copy")
async def copy_scenario(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    scenario_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """复制场景为新草稿"""
    scenario = await session.get(ApiTestScenario, scenario_id)
    if not scenario or scenario.project_id != project_id:
        raise NotFoundError(code="NOT_FOUND", message="场景不存在")

    from sqlalchemy import func as sa_func
    max_result = await session.execute(
        select(sa_func.max(ApiTestScenario.code)).where(ApiTestScenario.branch_id == branch_id)
    )
    max_code = max_result.scalar()
    next_num = 1
    if max_code:
        try:
            next_num = int(max_code.split("-")[1]) + 1
        except (IndexError, ValueError):
            pass

    new_scenario = ApiTestScenario(
        project_id=project_id,
        branch_id=branch_id,
        code=f"AT-{next_num:04d}",
        title=f"{scenario.title}(副本)",
        priority=scenario.priority,
        description=scenario.description,
        status="draft",
        source=scenario.source,
        folder_id=scenario.folder_id,
        pre_steps=scenario.pre_steps,
        source_api_ids=scenario.source_api_ids,
        env_variables=scenario.env_variables,
        created_by=current_user.id,
    )
    session.add(new_scenario)
    await session.flush()

    steps_result = await session.execute(
        select(ApiTestStep).where(ApiTestStep.scenario_id == scenario_id).order_by(ApiTestStep.sort_order)
    )
    for st in steps_result.scalars().all():
        session.add(ApiTestStep(
            scenario_id=new_scenario.id,
            sort_order=st.sort_order,
            group_name=st.group_name,
            name=st.name,
            method=st.method,
            url=st.url,
            headers=st.headers,
            body=st.body,
            assertions=st.assertions,
            variables_extract=st.variables_extract,
            enabled=st.enabled,
            pre_script=st.pre_script,
            post_script=st.post_script,
        ))

    await session.commit()
    await write_audit_log(session, action="copy", target_type="api_test_scenario",
                          target_id=new_scenario.id, target_name=new_scenario.title,
                          user_id=current_user.id, project_id=project_id)
    return {"data": _scenario_to_dict(new_scenario)}


@router.post("/{scenario_id}/new-version")
async def new_version(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    scenario_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """已发布场景 → 复制为新草稿（v2），原版本自动废弃"""
    scenario = await session.get(ApiTestScenario, scenario_id)
    if not scenario or scenario.project_id != project_id:
        raise NotFoundError(code="NOT_FOUND", message="场景不存在")
    if scenario.status != "published":
        from app.core.exceptions import AppError
        raise AppError(code="NOT_PUBLISHED", message="仅已发布的场景可更新版本", status_code=400)

    from sqlalchemy import func as sa_func
    max_result = await session.execute(
        select(sa_func.max(ApiTestScenario.code)).where(ApiTestScenario.branch_id == branch_id)
    )
    max_code = max_result.scalar()
    next_num = 1
    if max_code:
        try:
            next_num = int(max_code.split("-")[1]) + 1
        except (IndexError, ValueError):
            pass

    import re
    base_title = re.sub(r'\(v\d+\)$', '', scenario.title).strip()
    version_match = re.search(r'\(v(\d+)\)$', scenario.title)
    next_ver = int(version_match.group(1)) + 1 if version_match else 2

    new_scenario = ApiTestScenario(
        project_id=project_id,
        branch_id=branch_id,
        code=f"AT-{next_num:04d}",
        title=f"{base_title}(v{next_ver})",
        priority=scenario.priority,
        description=scenario.description,
        status="draft",
        source=scenario.source,
        folder_id=scenario.folder_id,
        pre_steps=scenario.pre_steps,
        source_api_ids=scenario.source_api_ids,
        env_variables=scenario.env_variables,
        edited_after_generate=False,
        created_by=current_user.id,
    )
    session.add(new_scenario)
    await session.flush()

    steps_result = await session.execute(
        select(ApiTestStep).where(ApiTestStep.scenario_id == scenario_id).order_by(ApiTestStep.sort_order)
    )
    for st in steps_result.scalars().all():
        session.add(ApiTestStep(
            scenario_id=new_scenario.id,
            sort_order=st.sort_order,
            group_name=st.group_name,
            name=st.name,
            method=st.method,
            url=st.url,
            headers=st.headers,
            body=st.body,
            assertions=st.assertions,
            variables_extract=st.variables_extract,
            enabled=st.enabled,
            pre_script=st.pre_script,
            post_script=st.post_script,
        ))

    scenario.status = "deprecated"

    await session.commit()
    await write_audit_log(session, action="new_version", target_type="api_test_scenario",
                          target_id=new_scenario.id, target_name=new_scenario.title,
                          user_id=current_user.id, project_id=project_id)
    return {"data": _scenario_to_dict(new_scenario)}


class SplitRequest(BaseSchema):
    step_ids: list[str]
    title: str | None = None


@router.post("/{scenario_id}/split")
async def split_scenario(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    scenario_id: uuid.UUID,
    body: SplitRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """将选中的步骤拆分为新场景"""
    scenario = await session.get(ApiTestScenario, scenario_id)
    if not scenario or scenario.project_id != project_id:
        raise NotFoundError(code="NOT_FOUND", message="场景不存在")

    step_uuids = [uuid.UUID(sid) for sid in body.step_ids]
    steps_result = await session.execute(
        select(ApiTestStep).where(
            ApiTestStep.id.in_(step_uuids),
            ApiTestStep.scenario_id == scenario_id,
        ).order_by(ApiTestStep.sort_order)
    )
    steps_to_split = steps_result.scalars().all()
    if not steps_to_split:
        from app.core.exceptions import AppError
        raise AppError(code="NO_STEPS", message="未选择步骤", status_code=400)

    from sqlalchemy import func as sa_func
    max_result = await session.execute(
        select(sa_func.max(ApiTestScenario.code)).where(ApiTestScenario.branch_id == branch_id)
    )
    max_code = max_result.scalar()
    next_num = 1
    if max_code:
        try:
            next_num = int(max_code.split("-")[1]) + 1
        except (IndexError, ValueError):
            pass

    new_scenario = ApiTestScenario(
        project_id=project_id,
        branch_id=branch_id,
        code=f"AT-{next_num:04d}",
        title=body.title or f"{scenario.title}(拆分)",
        priority=scenario.priority,
        description=scenario.description,
        status="draft",
        source="manual",
        folder_id=scenario.folder_id,
        pre_steps=scenario.pre_steps,
        env_variables=scenario.env_variables,
        created_by=current_user.id,
    )
    session.add(new_scenario)
    await session.flush()

    split_step_ids = {st.id for st in steps_to_split}
    for i, st in enumerate(steps_to_split):
        session.add(ApiTestStep(
            scenario_id=new_scenario.id,
            sort_order=i,
            group_name=st.group_name,
            name=st.name, method=st.method, url=st.url,
            headers=st.headers, body=st.body,
            assertions=st.assertions, variables_extract=st.variables_extract,
            enabled=st.enabled,
            pre_script=st.pre_script, post_script=st.post_script,
        ))

    for st in steps_to_split:
        await session.delete(st)

    remaining_result = await session.execute(
        select(ApiTestStep).where(ApiTestStep.scenario_id == scenario_id).order_by(ApiTestStep.sort_order)
    )
    for i, st in enumerate(remaining_result.scalars().all()):
        st.sort_order = i

    await session.commit()
    await write_audit_log(session, action="split", target_type="api_test_scenario",
                          target_id=new_scenario.id, target_name=new_scenario.title,
                          user_id=current_user.id, project_id=project_id)
    return {"data": _scenario_to_dict(new_scenario)}


class GenerateRequest(BaseSchema):
    api_info: str = Field(default="", max_length=10000)
    api_ids: list[str] | None = None
    env_variables: dict | None = None
    env_id: str | None = None
    folder_id: str | None = None
    case_id: str | None = None
    # 该用例已有接口场景时怎么办：append 接到后面 / replace 换掉步骤。
    # 不传 = 已存在就报错，逼调用方明确表态（此前是静默新建第二条，
    # 而用例页面只显示步骤最多的那一条，另一条就此隐身）。
    on_existing: str | None = Field(default=None, pattern="^(append|replace)$")


@router.post("/generate")
async def generate_api_tests(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: GenerateRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    from app.services.ai_config_resolver import resolve_ai_config
    from app.core.exceptions import AppError

    ai_config = await resolve_ai_config(project_id, session, capability="api-test-generate")
    if not ai_config:
        raise AppError(code="AI_NOT_CONFIGURED", message="AI 服务未配置", status_code=503)

    # 合并环境变量：选择环境 + 手动传入
    env_vars = {}
    if body.env_id:
        from app.services import environment_service
        try:
            merged = await environment_service.get_merged_variables(session, uuid.UUID(body.env_id))
            env_vars = {item["key"]: item["value"] for item in merged}
        except Exception:
            logger.warning("生成-加载环境变量失败 env_id=%s", body.env_id)
    if body.env_variables:
        env_vars.update(body.env_variables)

    from app.services.ai.api_scenario_gen_service import generate_api_test

    async def event_stream():
        try:
            async for event in generate_api_test(
                project_id=project_id,
                branch_id=branch_id,
                api_info=body.api_info,
                api_ids=body.api_ids,
                env_variables=env_vars or None,
                folder_id=uuid.UUID(body.folder_id) if body.folder_id else None,
                case_id=uuid.UUID(body.case_id) if body.case_id else None,
                on_existing=body.on_existing,
                ai_config=ai_config,
                session=session,
                user_id=current_user.id,
            ):
                yield f"data: {json.dumps({'type': event.type, **event.data}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("generate_api_test failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:200]}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


class OptimizeRequest(BaseSchema):
    suggestion: str = Field(..., min_length=1, max_length=2000)


class ApplyOptimizeRequest(BaseSchema):
    changes: list[dict]


@router.post("/{scenario_id}/ai-optimize")
async def ai_optimize(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    scenario_id: uuid.UUID,
    body: OptimizeRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    from app.services.ai_config_resolver import resolve_ai_config
    from app.services.ai.api_test_optimizer import analyze_optimization
    from app.core.exceptions import AppError

    ai_config = await resolve_ai_config(project_id, session, capability="api-test-optimize")
    if not ai_config:
        raise AppError(code="AI_NOT_CONFIGURED", message="AI 服务未配置", status_code=503)

    result = await analyze_optimization(scenario_id, body.suggestion, ai_config, session)
    return {"data": result}


@router.post("/{scenario_id}/ai-optimize/apply")
async def ai_optimize_apply(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    scenario_id: uuid.UUID,
    body: ApplyOptimizeRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    from app.services.ai.api_test_optimizer import apply_optimization

    result = await apply_optimization(scenario_id, body.changes, session)
    return {"data": result}


class RunStepRequest(BaseSchema):
    env_id: str | None = None


@router.post("/{scenario_id}/run-step/{step_id}")
async def run_step(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    scenario_id: uuid.UUID,
    step_id: uuid.UUID,
    body: RunStepRequest | None = None,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """执行单个测试步骤 — 复用执行引擎（变量解析/TokenCache/断言/request 持久化）"""
    import httpx
    from app.services.api_test_runner import (
        TokenCache, _extract_value, _inject_runtime_variables, run_single_step,
    )

    scenario = await session.get(ApiTestScenario, scenario_id)
    if not scenario or scenario.project_id != project_id:
        raise NotFoundError(code="NOT_FOUND", message="场景不存在")

    step = await session.get(ApiTestStep, step_id)
    if not step or step.scenario_id != scenario_id:
        raise NotFoundError(code="NOT_FOUND", message="步骤不存在")

    # 变量优先级：步骤提取 > 运行时 > 用户选择的环境 > 场景 env_variables
    env: dict = {}
    env.update(scenario.env_variables or {})
    if body and body.env_id:
        from app.services import environment_service
        try:
            merged = await environment_service.get_merged_variables(session, uuid.UUID(body.env_id))
            env.update({item["key"]: item["value"] for item in merged})
        except Exception:
            logger.warning("加载环境变量失败 env_id=%s", body.env_id)
    _inject_runtime_variables(env)

    # 从同场景已执行步骤中收集提取的变量
    steps_result = await session.execute(
        select(ApiTestStep)
        .where(ApiTestStep.scenario_id == scenario_id, ApiTestStep.last_response != None)
        .order_by(ApiTestStep.sort_order)
    )
    for prev in steps_result.scalars().all():
        if prev.variables_extract and prev.last_response:
            resp_body = prev.last_response.get("body", {})
            for var_name, path in prev.variables_extract.items():
                val = _extract_value(resp_body, path)
                if val is not None:
                    env[var_name] = str(val)

    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        result = await run_single_step(step, env, client, TokenCache(env))

    step.last_status = result.status
    step.last_response = {
        "statusCode": result.status_code,
        "duration": result.duration,
        "body": result.response_body,
        "assertions": result.assertions,
        "request": result.request_data,
    } if not result.error else {"error": result.error, "request": result.request_data}
    await session.commit()

    return {"data": step.last_response}
