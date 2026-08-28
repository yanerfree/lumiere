"""助手工具目录 —— 声明式：每个工具挂一个权限点 + 一个落到守卫服务的 handler。

**加工具的规矩**：permission 必须是 core/permissions 里的某个 P_* 常量（或 None=任意登录用户），
handler 必须调用已有的 *_service（别在这里直接写 DB）。这样「助手能做什么」和「页面/端点能做什么」
永远是同一批守卫、同一份权限映射，不会漂。
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import permissions as perms
from app.core.exceptions import ForbiddenError, ValidationError
from app.models.user import User
from app.services import (
    branch_service,
    case_service,
    environment_service,
    execution_service,
    plan_service,
    project_service,
    variable_service,
)


# ── 执行上下文 ──────────────────────────────────────────────────
@dataclass
class ToolContext:
    session: AsyncSession
    user: User
    project_id: uuid.UUID | None
    args: dict
    background_tasks: object | None = None  # FastAPI BackgroundTasks，run_plan 自动化派发用


@dataclass(frozen=True)
class ArgSpec:
    name: str
    type: str  # "string" | "integer" | "uuid" | "boolean" | "list"
    required: bool = False
    description: str = ""


@dataclass(frozen=True)
class AssistantTool:
    key: str
    label: str
    description: str
    scope: str  # "system" | "project"
    mutates: bool
    permission: str | None  # 需要的权限点；None = 任意登录用户
    handler: Callable[[ToolContext], Awaitable[dict]]
    args: tuple[ArgSpec, ...] = field(default_factory=tuple)


# ── 内部工具函数 ─────────────────────────────────────────────────
async def _default_branch_id(session: AsyncSession, project_id: uuid.UUID) -> uuid.UUID:
    """取项目的默认分支（用例/计划挂在分支上）。优先 name=='default'，否则第一个。"""
    branches = await branch_service.list_branches(session, project_id)
    for b in branches:
        if b.name == "default":
            return b.id
    if branches:
        return branches[0].id
    raise ValidationError(code="NO_BRANCH", message="项目下没有分支")


def coerce_args(tool: AssistantTool, raw: dict) -> dict:
    """按 tool.args 校验并规整入参。缺必填 / 类型不对 → ValidationError。未声明的键丢弃。"""
    raw = raw or {}
    out: dict = {}
    for spec in tool.args:
        if spec.name not in raw or raw[spec.name] is None:
            if spec.required:
                raise ValidationError(code="ARG_REQUIRED", message=f"缺少参数「{spec.name}」")
            continue
        val = raw[spec.name]
        try:
            if spec.type == "uuid":
                val = val if isinstance(val, uuid.UUID) else uuid.UUID(str(val))
            elif spec.type == "integer":
                val = int(val)
            elif spec.type == "boolean":
                val = bool(val)
            elif spec.type == "list":
                if not isinstance(val, list):
                    raise ValueError
            else:  # string
                val = str(val)
        except (ValueError, TypeError):
            raise ValidationError(code="ARG_TYPE", message=f"参数「{spec.name}」类型应为 {spec.type}")
        out[spec.name] = val
    return out


# ── handlers ─────────────────────────────────────────────────────
async def _h_list_projects(ctx: ToolContext) -> dict:
    rows = await project_service.list_projects(ctx.session, ctx.user)
    return {"projects": [{"id": str(p.id), "name": p.name, "description": p.description} for p in rows]}


async def _h_create_project(ctx: ToolContext) -> dict:
    from app.schemas.project import CreateProjectRequest

    req = CreateProjectRequest(name=ctx.args["name"], description=ctx.args.get("description"))
    p = await project_service.create_project(ctx.session, req, ctx.user)
    return {"id": str(p.id), "name": p.name}


async def _h_list_cases(ctx: ToolContext) -> dict:
    bid = await _default_branch_id(ctx.session, ctx.project_id)
    limit = ctx.args.get("limit") or 20
    cases, total = await case_service.list_cases(ctx.session, bid, page=1, page_size=min(limit, 50))
    return {
        "total": total,
        "cases": [
            {"case_code": c.case_code, "title": c.title, "type": c.type, "priority": c.priority}
            for c in cases
        ],
    }


async def _h_create_case(ctx: ToolContext) -> dict:
    from app.schemas.case import CreateCaseRequest

    bid = await _default_branch_id(ctx.session, ctx.project_id)
    steps = ctx.args.get("steps")
    if not steps:
        # 用例至少要一步才能存在；助手建的是草稿，占位一步并在结果里说明
        steps = [{"step": "（助手创建，待补充步骤）", "expected": ""}]
    req = CreateCaseRequest(
        title=ctx.args["title"],
        type=ctx.args["type"],
        module=ctx.args["module"],
        priority=ctx.args.get("priority") or "P2",
        steps=steps,
    )
    c = await case_service.create_case(ctx.session, bid, req, source="assistant")
    return {"id": str(c.id), "case_code": c.case_code, "title": c.title}


async def _h_list_environments(ctx: ToolContext) -> dict:
    envs = await environment_service.list_environments(ctx.session, ctx.project_id)
    return {"environments": [{"id": str(e.id), "name": e.name, "description": e.description} for e in envs]}


async def _h_create_environment(ctx: ToolContext) -> dict:
    e = await environment_service.create_environment(
        ctx.session, ctx.project_id, ctx.args["name"], ctx.args.get("description")
    )
    return {"id": str(e.id), "name": e.name}


async def _h_list_global_variables(ctx: ToolContext) -> dict:
    # 只回 key/描述/有没有值 —— 全局变量可能存密码类，助手把明文抄进对话是额外暴露面，
    # 与「页面能看到值」不是一回事。要看值到环境页去看。
    rows = await variable_service.list_variables(ctx.session, ctx.project_id)
    return {"variables": [{"key": v.key, "description": v.description, "has_value": bool(v.value)} for v in rows]}


async def _h_set_global_variable(ctx: ToolContext) -> dict:
    key = ctx.args["key"]
    value = ctx.args["value"]
    desc = ctx.args.get("description")
    existing = await variable_service.list_variables(ctx.session, ctx.project_id)
    match = next((v for v in existing if v.key == key), None)
    if match is not None:
        await variable_service.update_variable(ctx.session, match.id, value, desc)
        return {"key": key, "action": "updated"}
    await variable_service.create_variable(ctx.session, ctx.project_id, key, value, desc)
    return {"key": key, "action": "created"}


async def _h_list_plans(ctx: ToolContext) -> dict:
    items, total = await plan_service.list_plans(ctx.session, ctx.project_id)
    return {
        "total": total,
        "plans": [
            {"id": str(i["plan"].id), "name": i["plan"].name, "status": i["plan"].status, "case_count": i["case_count"]}
            for i in items
        ],
    }


async def _h_run_plan(ctx: ToolContext) -> dict:
    plan_id = ctx.args["plan_id"]
    plan = await plan_service.get_plan(ctx.session, plan_id)  # 不存在 → NotFound
    if plan.project_id != ctx.project_id:
        raise ForbiddenError(code="PLAN_NOT_IN_PROJECT", message="计划不属于当前项目")
    report = await execution_service.start_execution(ctx.session, plan_id, ctx.user.id)
    # 自动化计划：与 plans.execute 端点同款派发（先 commit 让后台任务看得到报告）
    if plan.plan_type == "automated" and ctx.background_tasks is not None:
        await ctx.session.commit()
        from app.engine.task_status import set_task_status
        from app.engine.tasks.execution import run_automated_execution

        task_id = uuid.uuid4().hex
        await set_task_status(task_id, "pending", message="自动化执行任务已提交...")
        ctx.background_tasks.add_task(
            run_automated_execution, task_id, str(plan_id), str(report.id), str(ctx.user.id)
        )
        return {"report_id": str(report.id), "plan_status": "executing", "task_id": task_id}
    return {"report_id": str(report.id), "plan_status": "executing"}


# ── 目录 ─────────────────────────────────────────────────────────
TOOLS: tuple[AssistantTool, ...] = (
    # 系统级
    AssistantTool(
        key="list_projects", label="列出我的项目", scope="system", mutates=False,
        permission=None, handler=_h_list_projects,
        description="列出当前用户可见的项目（管理员看全部，普通用户看已加入的）。",
    ),
    AssistantTool(
        key="create_project", label="新建项目", scope="system", mutates=True,
        permission=perms.P_PROJECT_CREATE, handler=_h_create_project,
        description="新建一个项目（会自动铺默认分支/环境/全局变量）。",
        args=(
            ArgSpec("name", "string", True, "项目名称"),
            ArgSpec("description", "string", False, "项目描述"),
        ),
    ),
    # 项目级 —— 读
    AssistantTool(
        key="list_cases", label="列出用例", scope="project", mutates=False,
        permission=perms.P_PROJECT_READ, handler=_h_list_cases,
        description="列出当前项目默认分支下的测试用例。",
        args=(ArgSpec("limit", "integer", False, "返回条数，默认 20，上限 50"),),
    ),
    AssistantTool(
        key="create_case", label="新建用例", scope="project", mutates=True,
        permission=perms.P_CASE_WRITE, handler=_h_create_case,
        description="在当前项目默认分支下新建一条测试用例。",
        args=(
            ArgSpec("title", "string", True, "用例标题"),
            ArgSpec("type", "string", True, "用例类型：api 或 e2e"),
            ArgSpec("module", "string", True, "所属模块"),
            ArgSpec("priority", "string", False, "优先级 P0/P1/P2/P3，默认 P2"),
            ArgSpec("steps", "list", False, "步骤列表，形如 [{\"step\":\"...\",\"expected\":\"...\"}]"),
        ),
    ),
    AssistantTool(
        key="list_environments", label="列出环境", scope="project", mutates=False,
        permission=perms.P_PROJECT_READ, handler=_h_list_environments,
        description="列出当前项目的环境。",
    ),
    AssistantTool(
        key="create_environment", label="新建环境", scope="project", mutates=True,
        permission=perms.P_ENV_WRITE, handler=_h_create_environment,
        description="在当前项目下新建一个环境。",
        args=(
            ArgSpec("name", "string", True, "环境名称，如 staging"),
            ArgSpec("description", "string", False, "环境描述"),
        ),
    ),
    AssistantTool(
        key="list_global_variables", label="列出全局变量", scope="project", mutates=False,
        permission=perms.P_PROJECT_READ, handler=_h_list_global_variables,
        description="列出当前项目的全局变量（只回 key 与描述，不回值）。",
    ),
    AssistantTool(
        key="set_global_variable", label="设置全局变量", scope="project", mutates=True,
        permission=perms.P_ENV_WRITE, handler=_h_set_global_variable,
        description="新增或更新当前项目的一个全局变量。",
        args=(
            ArgSpec("key", "string", True, "变量名"),
            ArgSpec("value", "string", True, "变量值"),
            ArgSpec("description", "string", False, "说明"),
        ),
    ),
    AssistantTool(
        key="list_plans", label="列出测试计划", scope="project", mutates=False,
        permission=perms.P_PROJECT_READ, handler=_h_list_plans,
        description="列出当前项目的测试计划及其用例数。",
    ),
    AssistantTool(
        key="run_plan", label="执行测试计划", scope="project", mutates=True,
        permission=perms.P_PLAN_RUN, handler=_h_run_plan,
        description="执行一个测试计划（生成报告；自动化计划会后台跑）。",
        args=(ArgSpec("plan_id", "uuid", True, "要执行的计划 id"),),
    ),
)

_BY_KEY: dict[str, AssistantTool] = {t.key: t for t in TOOLS}


def get_tool(key: str) -> AssistantTool | None:
    return _BY_KEY.get(key)


def tool_allowed(tool: AssistantTool, held: frozenset[str] | set[str]) -> bool:
    """工具是否在持有权限内。permission=None 的工具（任意登录用户）恒放行。"""
    return tool.permission is None or tool.permission in held


def visible_tools(held: frozenset[str] | set[str]) -> list[AssistantTool]:
    """按持有权限点过滤出可见工具 —— 这就是「能力面 = 页面动作 ∩ 用户权限」的实现。"""
    return [t for t in TOOLS if tool_allowed(t, held)]
