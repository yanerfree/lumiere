"""接口场景 API — 场景/步骤 CRUD + 整链执行 + 编排生成。

**只服务「用例 → 接口」页签**。2026-08-15 下线了独立的「接口测试」模块
（凭接口文档 AI 造单接口场景那条路），随之删掉的是它的专属端点：
质量统计 / 批量操作 / 文件夹增删改 / 复制 / 新版本 / 拆分 / AI 优化 / 单步执行。

删的理由不是没人用，是**它的产物结构上跑不起来**：`scenario_variables.case_id`
是 NOT NULL，场景变量只能挂在用例上；没有 source_case_id 就拿不到凭据，
实跑必挂在「变量未解析」。生成一律归外部 Claude Code，平台只做呈现和回推通道。

所以这里剩下的每个端点都有用例侧的调用方，别再按"这是接口测试模块的"删。
"""

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
        "folderId": str(s.folder_id) if s.folder_id else None,
        "sourceCaseId": str(s.source_case_id) if s.source_case_id else None,
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


class CreateScenarioRequest(BaseSchema):
    title: str = Field(..., min_length=1, max_length=200)
    priority: str = Field(default="P1")
    folder_id: str | None = None
    description: str | None = None
    # **必填**。唯一调用方是用例详情「接口测试」页签的「新建场景」。
    # 库里 source_case_id 是 NOT NULL，声明成必填让它变成一条说得清的 422，
    # 而不是插进去才撞约束抛 500。
    source_case_id: str


@router.post("")
async def create_scenario(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: CreateScenarioRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    # 编号 = 用例编号。原来这里发 AT-#### max+1，那是已下线的「接口测试」模块的号段；
    # 而用例详情看到 AT- 开头会标「未绑定用例」，于是从这个按钮建出来的场景
    # 明明绑着用例却被标成孤儿。一个用例 = 一条场景，它不需要第二个名字。
    from app.models.case import Case

    scid = uuid.UUID(body.source_case_id)
    src_case = await session.get(Case, scid)
    if src_case is None:
        raise NotFoundError(code="NOT_FOUND", message=f"用例不存在：{body.source_case_id}")

    scenario = ApiTestScenario(
        project_id=project_id,
        branch_id=branch_id,
        code=src_case.case_code,
        title=body.title,
        priority=body.priority,
        source="manual",
        status="draft",
        folder_id=uuid.UUID(body.folder_id) if body.folder_id else None,
        description=body.description,
        source_case_id=scid,
        created_by=current_user.id,
    )
    session.add(scenario)
    await session.commit()
    await write_audit_log(session, action="create", target_type="api_test_scenario",
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


class GenerateRequest(BaseSchema):
    api_info: str = Field(default="", max_length=10000)
    env_variables: dict | None = None
    env_id: str | None = None
    folder_id: str | None = None
    # 必填。唯一调用方是用例详情的「编排为接口测试」，产物必须落到那条用例上；
    # 库里 source_case_id 已是 NOT NULL，不传只会撞约束抛 500。
    # （原来可空是为了服务已下线的「接口测试」模块那个生成弹窗。）
    case_id: str
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
                env_variables=env_vars or None,
                folder_id=uuid.UUID(body.folder_id) if body.folder_id else None,
                case_id=uuid.UUID(body.case_id),
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
