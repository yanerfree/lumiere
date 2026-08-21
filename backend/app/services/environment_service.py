"""环境与环境变量服务"""
import uuid

from sqlalchemy import select, delete, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.audit import audit_log
from app.models.environment import Environment, EnvironmentVariable, GlobalVariable
from app.services.variable_service import RESERVED_VAR_NAMES, _check_reserved


async def list_environments(session: AsyncSession, project_id: uuid.UUID) -> list[Environment]:
    """列出某个项目的环境。

    `project_id` **是必填的**，故意不给默认值：环境 2026-08-21 从全局改成项目级
    （见 docs/data-scoping-and-isolation.md §4），给个 `None=全部` 的默认值
    等于留一条静默返回全库的路 —— 漏改的调用点会安静地跑通，正是最难发现的那种。
    """
    # sort_order 为主（拖拽结果），name 兜底：新建的环境 sort_order 都是 0，
    # 只按 sort_order 排它们之间的顺序就不确定了。
    result = await session.execute(
        select(Environment)
        .where(Environment.project_id == project_id)
        .order_by(Environment.sort_order, Environment.name)
    )
    return list(result.scalars().all())


async def reorder_environments(session: AsyncSession, project_id: uuid.UUID,
                               items: list[dict]) -> None:
    """拖拽排序。id 来自请求体，所以这里必须再按 project_id 兜一道 ——
    路径上的项目校验管不到 body 里的 id。"""
    for item in items:
        await session.execute(
            update(Environment)
            .where(Environment.id == item["id"], Environment.project_id == project_id)
            .values(sort_order=item["sort_order"])
        )
    await session.flush()


async def list_environments_with_base_url(session: AsyncSession,
                                          project_id: uuid.UUID) -> list[dict]:
    envs = await list_environments(session, project_id)
    if not envs:
        return []
    env_ids = [e.id for e in envs]
    vars_result = await session.execute(
        select(EnvironmentVariable)
        .where(EnvironmentVariable.environment_id.in_(env_ids))
        .where(EnvironmentVariable.key == "BASE_URL")
    )
    base_url_map = {v.environment_id: v.value for v in vars_result.scalars().all()}
    return [
        {
            "id": e.id,
            "name": e.name,
            "description": e.description,
            "base_url": base_url_map.get(e.id),
        }
        for e in envs
    ]


@audit_log(action="create", target_type="environment")
async def create_environment(session: AsyncSession, project_id: uuid.UUID, name: str,
                             description: str | None = None) -> Environment:
    env = Environment(project_id=project_id, name=name, description=description)
    session.add(env)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        # 唯一约束现在是 (project_id, name)：别的项目有同名环境不算冲突
        raise ConflictError(code="ENV_NAME_EXISTS", message="本项目下已有同名环境")
    await session.refresh(env)
    return env


async def assert_env_in_project(session: AsyncSession, env_id, project_id) -> None:
    """请求体里带过来的 env_id 必须属于路径里那个项目。

    路由上的 `require_project_role` 只回答"你是不是这个项目的成员"，
    `verify_path_scope` 只管**路径里**的 id —— 两道都管不到 body。
    环境项目化之后，body 里塞一个别的项目的 env_id 就等于把别人的 BASE_URL、
    账号、密码注进本次执行（`plans.environment_id` 和「跑接口场景」都走 body）。

    和 deps/scope.py 同一个口径：不是你的东西，对你来说就该是"不存在"，不返 403。
    env_id 为空是合法的（不指定环境就跑），直接放过。
    """
    if not env_id:
        return
    if not isinstance(env_id, uuid.UUID):
        try:
            env_id = uuid.UUID(str(env_id))
        except (ValueError, AttributeError, TypeError):
            raise NotFoundError(code="ENV_NOT_FOUND", message="环境不存在")
    owner = (await session.execute(
        select(Environment.project_id).where(Environment.id == env_id)
    )).scalar_one_or_none()
    if owner is None or str(owner) != str(project_id):
        raise NotFoundError(code="ENV_NOT_FOUND", message="环境不存在")


async def get_environment(session: AsyncSession, env_id: uuid.UUID) -> Environment:
    result = await session.execute(select(Environment).where(Environment.id == env_id))
    env = result.scalar_one_or_none()
    if env is None:
        raise NotFoundError(code="ENV_NOT_FOUND", message="环境不存在")
    return env


@audit_log(action="update", target_type="environment")
async def update_environment(session: AsyncSession, env_id: uuid.UUID, **kwargs) -> Environment:
    env = await get_environment(session, env_id)
    if 'name' in kwargs and kwargs['name'] is not None:
        env.name = kwargs['name']
    if 'description' in kwargs:
        env.description = kwargs['description']
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise ConflictError(code="ENV_NAME_EXISTS", message="环境名称已存在")
    await session.refresh(env)
    return env


@audit_log(action="delete", target_type="environment")
async def delete_environment(session: AsyncSession, env_id: uuid.UUID) -> None:
    env = await get_environment(session, env_id)
    await session.delete(env)
    await session.flush()


async def list_env_variables(session: AsyncSession, env_id: uuid.UUID) -> list[EnvironmentVariable]:
    result = await session.execute(
        select(EnvironmentVariable)
        .where(EnvironmentVariable.environment_id == env_id)
        .order_by(EnvironmentVariable.sort_order, EnvironmentVariable.key)
    )
    return list(result.scalars().all())


async def put_env_variables(session: AsyncSession, env_id: uuid.UUID, variables: list[dict]) -> list[EnvironmentVariable]:
    """全量替换环境变量。"""
    await get_environment(session, env_id)  # 确认存在

    # 校验保留名
    for v in variables:
        _check_reserved(v["key"])

    # 删旧
    await session.execute(
        delete(EnvironmentVariable).where(EnvironmentVariable.environment_id == env_id)
    )

    # 写新
    new_vars = []
    for i, v in enumerate(variables):
        ev = EnvironmentVariable(
            environment_id=env_id,
            key=v["key"],
            value=v["value"],
            description=v.get("description"),
            sort_order=i,
        )
        session.add(ev)
        new_vars.append(ev)

    await session.flush()
    for v in new_vars:
        await session.refresh(v)
    return new_vars


async def get_merged_variables(session: AsyncSession, env_id: uuid.UUID) -> list[dict]:
    """全局变量 + 环境变量合并预览。同名 key 时环境变量覆盖。

    「全局」= **本项目**跨环境，不是跨项目（迁移 zzp0gvarproj）。所以要先从 env
    反查项目 —— 不反查的话这份"执行时实际会注入什么"的预览会把别的项目的
    TEST_LANGUAGE / API_TIMEOUT 也算进来，而它是排查「变量未解析」的第一入口，
    错在这里会把人带偏。
    """
    # 全局（本项目的）
    proj = (await session.execute(
        select(Environment.project_id).where(Environment.id == env_id)
    )).scalar_one_or_none()
    global_vars: dict[str, dict] = {}
    if proj is not None:
        global_result = await session.execute(
            select(GlobalVariable)
            .where(GlobalVariable.project_id == proj)
            .order_by(GlobalVariable.key)
        )
        global_vars = {g.key: {"key": g.key, "value": g.value, "source": "global"}
                       for g in global_result.scalars().all()}

    # 环境
    env_result = await session.execute(
        select(EnvironmentVariable).where(EnvironmentVariable.environment_id == env_id).order_by(EnvironmentVariable.key)
    )
    for ev in env_result.scalars().all():
        global_vars[ev.key] = {"key": ev.key, "value": ev.value, "source": "environment"}

    return sorted(global_vars.values(), key=lambda x: x["key"])


async def clone_environment(session: AsyncSession, env_id: uuid.UUID, new_name: str) -> Environment:
    """复制环境（含变量）。副本**留在源环境所属的项目里** ——
    跨项目复制不走这条路（那等于把别的项目的凭证搬过来，得人明确操作）。"""
    source = await get_environment(session, env_id)
    new_env = await create_environment(session, source.project_id, new_name, source.description)

    # 复制变量
    vars_result = await session.execute(
        select(EnvironmentVariable).where(EnvironmentVariable.environment_id == env_id)
    )
    for v in vars_result.scalars().all():
        session.add(EnvironmentVariable(
            environment_id=new_env.id, key=v.key, value=v.value,
            description=v.description, sort_order=v.sort_order,
        ))
    await session.flush()
    return new_env
