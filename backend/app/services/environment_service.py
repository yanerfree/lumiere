"""环境与环境变量服务"""
import uuid

from sqlalchemy import select, delete, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.audit import audit_log
from app.models.environment import Environment, EnvironmentVariable, GlobalVariable
from app.services.variable_service import RESERVED_VAR_NAMES, _check_reserved


async def list_environments(session: AsyncSession) -> list[Environment]:
    # sort_order 为主（拖拽结果），name 兜底：新建的环境 sort_order 都是 0，
    # 只按 sort_order 排它们之间的顺序就不确定了。
    result = await session.execute(select(Environment).order_by(Environment.sort_order, Environment.name))
    return list(result.scalars().all())


async def reorder_environments(session: AsyncSession, items: list[dict]) -> None:
    for item in items:
        await session.execute(
            update(Environment).where(Environment.id == item["id"]).values(sort_order=item["sort_order"])
        )
    await session.flush()


async def list_environments_with_base_url(session: AsyncSession) -> list[dict]:
    envs = await list_environments(session)
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
async def create_environment(session: AsyncSession, name: str, description: str | None = None) -> Environment:
    env = Environment(name=name, description=description)
    session.add(env)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise ConflictError(code="ENV_NAME_EXISTS", message="环境名称已存在")
    await session.refresh(env)
    return env


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
    """全局变量 + 环境变量合并预览。同名 key 时环境变量覆盖。"""
    # 全局
    global_result = await session.execute(select(GlobalVariable).order_by(GlobalVariable.key))
    global_vars = {g.key: {"key": g.key, "value": g.value, "source": "global"} for g in global_result.scalars().all()}

    # 环境
    env_result = await session.execute(
        select(EnvironmentVariable).where(EnvironmentVariable.environment_id == env_id).order_by(EnvironmentVariable.key)
    )
    for ev in env_result.scalars().all():
        global_vars[ev.key] = {"key": ev.key, "value": ev.value, "source": "environment"}

    return sorted(global_vars.values(), key=lambda x: x["key"])


async def clone_environment(session: AsyncSession, env_id: uuid.UUID, new_name: str) -> Environment:
    """复制环境（含变量）。"""
    source = await get_environment(session, env_id)
    new_env = await create_environment(session, new_name, source.description)

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
