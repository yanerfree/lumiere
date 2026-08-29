"""全局变量 + 环境 + 通知渠道 API"""
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.status import HTTP_201_CREATED

from app.core import permissions as perms
from app.core.audit import write_audit_log
from app.deps.auth import get_current_user, require_project_role, require_role
from app.deps.db import get_db
from app.models.user import User
from app.schemas.common import MessageResponse
from app.schemas.variable import (
    ChannelResponse,
    CloneEnvRequest,
    CreateChannelRequest,
    CreateEnvRequest,
    CreateVarRequest,
    EnvReorderRequest,
    EnvResponse,
    EnvVarItem,
    EnvVarResponse,
    UpdateChannelRequest,
    UpdateEnvRequest,
    UpdateVarRequest,
    VarResponse,
)
from app.services import channel_service, environment_service, variable_service
from app.services import token_service

router = APIRouter(tags=["variables"])

# 环境改成项目级之后单独一个 router：路径里带 {project_id}，才能挂
# require_project_role（"你是不是这个项目的成员"）和 verify_path_scope
# （"路径里的 env_id 真属于这个项目"）。两道都需要 —— 只验成员身份的话，
# A 项目的人把 env_id 换成 B 项目的照样能读到别人的凭证，
# 这正是 app/deps/scope.py 开头记的那类越权。
# 只有通知渠道**仍留在上面那个 router 上** —— 它是平台设施，不是项目资产。
env_router = APIRouter(prefix="/api/projects/{project_id}/environments",
                       tags=["environments"])
# 全局变量同理（迁移 zzp0gvarproj）。「全局」= 项目内跨环境，不是跨项目。
gvar_router = APIRouter(prefix="/api/projects/{project_id}/global-variables",
                        tags=["global-variables"])

# 环境和全局变量共用同一套角色：两者都是"这个项目的测试怎么跑"的配置，
# 权限口径不该不一样（分开定义迟早会漂）。
_VAR_READ = perms.TIER_READ
_VAR_WRITE = perms.TIER_WRITE


# ---- 全局变量 API（项目级）----
#
# 2026-08-21 从 /api/global-variables 挪到 /api/projects/{project_id}/global-variables。
# 「全局」指的是**项目内跨环境**，不是跨项目 —— 见 models/environment.py
# 的 GlobalVariable 说明和迁移 zzp0gvarproj。

@gvar_router.get("")
async def list_global_variables(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*_VAR_READ)),
):
    variables = await variable_service.list_variables(session, project_id)
    return {"data": [VarResponse.model_validate(v, from_attributes=True).model_dump(by_alias=True) for v in variables]}

@gvar_router.post("", status_code=HTTP_201_CREATED)
async def create_global_variable(
    project_id: uuid.UUID,
    body: CreateVarRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*_VAR_WRITE)),
):
    var = await variable_service.create_variable(session, project_id, body.key, body.value, body.description)
    await write_audit_log(session, action="create", target_type="global_variable", target_id=var.id, target_name=var.key)
    return {"data": VarResponse.model_validate(var, from_attributes=True).model_dump(by_alias=True)}

@gvar_router.put("")
async def put_global_variables(
    project_id: uuid.UUID,
    body: list[CreateVarRequest],
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*_VAR_WRITE)),
):
    variables = await variable_service.put_variables(session, project_id, [v.model_dump() for v in body])
    await write_audit_log(session, action="batch_update", target_type="global_variable", changes={"count": len(body)})
    return {"data": [VarResponse.model_validate(v, from_attributes=True).model_dump(by_alias=True) for v in variables]}

@gvar_router.put("/{var_id}")
async def update_global_variable(
    project_id: uuid.UUID,
    var_id: uuid.UUID,
    body: UpdateVarRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*_VAR_WRITE)),
):
    var = await variable_service.update_variable(session, var_id, body.value, body.description)
    await write_audit_log(session, action="update", target_type="global_variable", target_id=var.id, target_name=var.key)
    return {"data": VarResponse.model_validate(var, from_attributes=True).model_dump(by_alias=True)}

@gvar_router.delete("/{var_id}")
async def delete_global_variable(
    project_id: uuid.UUID,
    var_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*_VAR_WRITE)),
):
    var = await variable_service.get_variable(session, var_id)
    await variable_service.delete_variable(session, var_id)
    await write_audit_log(session, action="delete", target_type="global_variable", target_id=var_id, target_name=var.key)
    return MessageResponse(message="删除成功").model_dump()


# ---- 环境 API（项目级）----
#
# 2026-08-21 从 /api/environments 挪到 /api/projects/{project_id}/environments。
# 挪路径不是为了好看：环境里有 BASE_URL、账号、token，原来那组路径里没有项目，
# 所以既没法验"你是这个项目的人"，也没法验"这个 env 是这个项目的"。
# 见 docs/data-scoping-and-isolation.md §4。

@env_router.get("")
async def list_environments(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*_VAR_READ)),
):
    envs = await environment_service.list_environments_with_base_url(session, project_id)
    return {"data": [EnvResponse(**e).model_dump(by_alias=True) for e in envs]}

@env_router.post("", status_code=HTTP_201_CREATED)
async def create_environment(
    project_id: uuid.UUID,
    body: CreateEnvRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*_VAR_WRITE)),
):
    env = await environment_service.create_environment(session, project_id, body.name, body.description)
    await write_audit_log(session, action="create", target_type="environment", target_id=env.id, target_name=env.name)
    return {"data": EnvResponse.model_validate(env, from_attributes=True).model_dump(by_alias=True)}

@env_router.put("/reorder")
async def reorder_environments(
    project_id: uuid.UUID,
    body: EnvReorderRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*_VAR_WRITE)),
):
    # 必须声明在 /{env_id} 之前，否则 "reorder" 会被当作 env_id 解析
    await environment_service.reorder_environments(session, project_id, [i.model_dump() for i in body.items])
    await write_audit_log(session, action="reorder", target_type="environment", changes={"count": len(body.items)})
    return MessageResponse(message="排序已保存").model_dump()

@env_router.delete("/{env_id}")
async def delete_environment(
    project_id: uuid.UUID,
    env_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*_VAR_WRITE)),
):
    env = await environment_service.get_environment(session, env_id)
    await environment_service.delete_environment(session, env_id)
    await write_audit_log(session, action="delete", target_type="environment", target_id=env_id, target_name=env.name)
    return MessageResponse(message="删除成功").model_dump()

@env_router.put("/{env_id}")
async def update_environment(
    project_id: uuid.UUID,
    env_id: uuid.UUID,
    body: UpdateEnvRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*_VAR_WRITE)),
):
    updates = body.model_dump(exclude_unset=True)
    env = await environment_service.update_environment(session, env_id, **updates)
    await write_audit_log(session, action="update", target_type="environment", target_id=env_id, target_name=env.name)
    return {"data": EnvResponse.model_validate(env, from_attributes=True).model_dump(by_alias=True)}

@env_router.get("/{env_id}/variables")
async def list_env_variables(
    project_id: uuid.UUID,
    env_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*_VAR_READ)),
):
    variables = await environment_service.list_env_variables(session, env_id)
    return {"data": [EnvVarResponse.model_validate(v, from_attributes=True).model_dump(by_alias=True) for v in variables]}

@env_router.put("/{env_id}/variables")
async def put_env_variables(
    project_id: uuid.UUID,
    env_id: uuid.UUID,
    body: list[EnvVarItem],
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*_VAR_WRITE)),
):
    variables = await environment_service.put_env_variables(session, env_id, [v.model_dump() for v in body])
    env = await environment_service.get_environment(session, env_id)
    await write_audit_log(session, action="update_variables", target_type="environment", target_id=env_id, target_name=env.name, changes={"count": len(body)})
    return {"data": [EnvVarResponse.model_validate(v, from_attributes=True).model_dump(by_alias=True) for v in variables]}

@env_router.get("/{env_id}/merged-variables")
async def get_merged_variables(
    project_id: uuid.UUID,
    env_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*_VAR_READ)),
):
    merged = await environment_service.get_merged_variables(session, env_id)
    return {"data": merged}


@env_router.post("/{env_id}/token")
async def acquire_env_token(
    project_id: uuid.UUID,
    env_id: uuid.UUID,
    role: str = "ADMIN",
    refresh: bool = False,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*_VAR_WRITE)),
):
    """登录目标系统取 token（回填 Redis 缓存）。用于验证登录配方 + 供自动化注入。
    role: 角色前缀(ADMIN/TENANT...); refresh=true 强制重登。密码级不回传，仅返回脱敏预览。"""
    token = await token_service.get_target_token(session, env_id, role, force_refresh=refresh)
    if token:
        preview = token[:6] + "…" + token[-4:] if len(token) > 12 else "••••"
        return {"data": {"ok": True, "role": role.upper(), "tokenPreview": preview, "cached": not refresh}}
    # 失败时回传具体原因（重新登录一次拿 error，不缓存）
    from app.models.environment import EnvironmentVariable
    from sqlalchemy import select
    rows = await session.execute(
        select(EnvironmentVariable).where(EnvironmentVariable.environment_id == env_id)
    )
    env_vars = {v.key: v.value for v in rows.scalars().all()}
    _t, err = await token_service.fetch_token(env_vars, role)
    return {"data": {"ok": False, "role": role.upper(), "error": err or "取 token 失败"}}

@env_router.post("/{env_id}/clone", status_code=HTTP_201_CREATED)
async def clone_environment(
    project_id: uuid.UUID,
    env_id: uuid.UUID,
    body: CloneEnvRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*_VAR_WRITE)),
):
    env = await environment_service.clone_environment(session, env_id, body.name)
    await write_audit_log(session, action="clone", target_type="environment", target_id=env.id, target_name=env.name)
    return {"data": EnvResponse.model_validate(env, from_attributes=True).model_dump(by_alias=True)}


# ---- 通知渠道 API ----
# 渠道是全局平台设施（不属于任何项目，见文件头注释）。读保持登录可见，
# 写（增改删）收到系统 admin —— 一个渠道被所有项目共用，普通用户不该能改别人在用的 webhook。

@router.get("/api/channels")
async def list_channels(session: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    channels = await channel_service.list_channels(session)
    return {"data": [ChannelResponse.model_validate(c, from_attributes=True).model_dump(by_alias=True) for c in channels]}

@router.post("/api/channels", status_code=HTTP_201_CREATED)
async def create_channel(body: CreateChannelRequest, session: AsyncSession = Depends(get_db), _: User = Depends(require_role("admin"))):
    ch = await channel_service.create_channel(session, body.name, body.webhook_url)
    await write_audit_log(session, action="create", target_type="channel", target_id=ch.id, target_name=ch.name)
    return {"data": ChannelResponse.model_validate(ch, from_attributes=True).model_dump(by_alias=True)}

@router.put("/api/channels/{ch_id}")
async def update_channel(ch_id: uuid.UUID, body: UpdateChannelRequest, session: AsyncSession = Depends(get_db), _: User = Depends(require_role("admin"))):
    ch = await channel_service.update_channel(session, ch_id, body.name, body.webhook_url)
    await write_audit_log(session, action="update", target_type="channel", target_id=ch.id, target_name=ch.name)
    return {"data": ChannelResponse.model_validate(ch, from_attributes=True).model_dump(by_alias=True)}

@router.delete("/api/channels/{ch_id}")
async def delete_channel(ch_id: uuid.UUID, session: AsyncSession = Depends(get_db), _: User = Depends(require_role("admin"))):
    ch = await channel_service.get_channel(session, ch_id)
    await channel_service.delete_channel(session, ch_id)
    await write_audit_log(session, action="delete", target_type="channel", target_id=ch_id, target_name=ch.name)
    return MessageResponse(message="删除成功").model_dump()
