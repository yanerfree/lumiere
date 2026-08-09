"""全局变量 + 环境 + 通知渠道 API"""
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.status import HTTP_201_CREATED

from app.core.audit import write_audit_log
from app.deps.auth import get_current_user
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


# ---- 全局变量 API ----

@router.get("/api/global-variables")
async def list_global_variables(session: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    variables = await variable_service.list_variables(session)
    return {"data": [VarResponse.model_validate(v, from_attributes=True).model_dump(by_alias=True) for v in variables]}

@router.post("/api/global-variables", status_code=HTTP_201_CREATED)
async def create_global_variable(body: CreateVarRequest, session: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    var = await variable_service.create_variable(session, body.key, body.value, body.description)
    await write_audit_log(session, action="create", target_type="global_variable", target_id=var.id, target_name=var.key)
    return {"data": VarResponse.model_validate(var, from_attributes=True).model_dump(by_alias=True)}

@router.put("/api/global-variables")
async def put_global_variables(body: list[CreateVarRequest], session: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    variables = await variable_service.put_variables(session, [v.model_dump() for v in body])
    await write_audit_log(session, action="batch_update", target_type="global_variable", changes={"count": len(body)})
    return {"data": [VarResponse.model_validate(v, from_attributes=True).model_dump(by_alias=True) for v in variables]}

@router.put("/api/global-variables/{var_id}")
async def update_global_variable(var_id: uuid.UUID, body: UpdateVarRequest, session: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    var = await variable_service.update_variable(session, var_id, body.value, body.description)
    await write_audit_log(session, action="update", target_type="global_variable", target_id=var.id, target_name=var.key)
    return {"data": VarResponse.model_validate(var, from_attributes=True).model_dump(by_alias=True)}

@router.delete("/api/global-variables/{var_id}")
async def delete_global_variable(var_id: uuid.UUID, session: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    var = await variable_service.get_variable(session, var_id)
    await variable_service.delete_variable(session, var_id)
    await write_audit_log(session, action="delete", target_type="global_variable", target_id=var_id, target_name=var.key)
    return MessageResponse(message="删除成功").model_dump()


# ---- 环境 API ----

@router.get("/api/environments")
async def list_environments(session: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    envs = await environment_service.list_environments_with_base_url(session)
    return {"data": [EnvResponse(**e).model_dump(by_alias=True) for e in envs]}

@router.post("/api/environments", status_code=HTTP_201_CREATED)
async def create_environment(body: CreateEnvRequest, session: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    env = await environment_service.create_environment(session, body.name, body.description)
    await write_audit_log(session, action="create", target_type="environment", target_id=env.id, target_name=env.name)
    return {"data": EnvResponse.model_validate(env, from_attributes=True).model_dump(by_alias=True)}

@router.put("/api/environments/reorder")
async def reorder_environments(body: EnvReorderRequest, session: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    # 必须声明在 /api/environments/{env_id} 之前，否则 "reorder" 会被当作 env_id 解析
    await environment_service.reorder_environments(session, [i.model_dump() for i in body.items])
    await write_audit_log(session, action="reorder", target_type="environment", changes={"count": len(body.items)})
    return MessageResponse(message="排序已保存").model_dump()

@router.delete("/api/environments/{env_id}")
async def delete_environment(env_id: uuid.UUID, session: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    env = await environment_service.get_environment(session, env_id)
    await environment_service.delete_environment(session, env_id)
    await write_audit_log(session, action="delete", target_type="environment", target_id=env_id, target_name=env.name)
    return MessageResponse(message="删除成功").model_dump()

@router.put("/api/environments/{env_id}")
async def update_environment(env_id: uuid.UUID, body: UpdateEnvRequest, session: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    updates = body.model_dump(exclude_unset=True)
    env = await environment_service.update_environment(session, env_id, **updates)
    await write_audit_log(session, action="update", target_type="environment", target_id=env_id, target_name=env.name)
    return {"data": EnvResponse.model_validate(env, from_attributes=True).model_dump(by_alias=True)}

@router.get("/api/environments/{env_id}/variables")
async def list_env_variables(env_id: uuid.UUID, session: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    variables = await environment_service.list_env_variables(session, env_id)
    return {"data": [EnvVarResponse.model_validate(v, from_attributes=True).model_dump(by_alias=True) for v in variables]}

@router.put("/api/environments/{env_id}/variables")
async def put_env_variables(env_id: uuid.UUID, body: list[EnvVarItem], session: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    variables = await environment_service.put_env_variables(session, env_id, [v.model_dump() for v in body])
    env = await environment_service.get_environment(session, env_id)
    await write_audit_log(session, action="update_variables", target_type="environment", target_id=env_id, target_name=env.name, changes={"count": len(body)})
    return {"data": [EnvVarResponse.model_validate(v, from_attributes=True).model_dump(by_alias=True) for v in variables]}

@router.get("/api/environments/{env_id}/merged-variables")
async def get_merged_variables(env_id: uuid.UUID, session: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    merged = await environment_service.get_merged_variables(session, env_id)
    return {"data": merged}


@router.post("/api/environments/{env_id}/token")
async def acquire_env_token(
    env_id: uuid.UUID,
    role: str = "ADMIN",
    refresh: bool = False,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
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

@router.post("/api/environments/{env_id}/clone", status_code=HTTP_201_CREATED)
async def clone_environment(env_id: uuid.UUID, body: CloneEnvRequest, session: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    env = await environment_service.clone_environment(session, env_id, body.name)
    await write_audit_log(session, action="clone", target_type="environment", target_id=env.id, target_name=env.name)
    return {"data": EnvResponse.model_validate(env, from_attributes=True).model_dump(by_alias=True)}


# ---- 通知渠道 API ----

@router.get("/api/channels")
async def list_channels(session: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    channels = await channel_service.list_channels(session)
    return {"data": [ChannelResponse.model_validate(c, from_attributes=True).model_dump(by_alias=True) for c in channels]}

@router.post("/api/channels", status_code=HTTP_201_CREATED)
async def create_channel(body: CreateChannelRequest, session: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    ch = await channel_service.create_channel(session, body.name, body.webhook_url)
    await write_audit_log(session, action="create", target_type="channel", target_id=ch.id, target_name=ch.name)
    return {"data": ChannelResponse.model_validate(ch, from_attributes=True).model_dump(by_alias=True)}

@router.put("/api/channels/{ch_id}")
async def update_channel(ch_id: uuid.UUID, body: UpdateChannelRequest, session: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    ch = await channel_service.update_channel(session, ch_id, body.name, body.webhook_url)
    await write_audit_log(session, action="update", target_type="channel", target_id=ch.id, target_name=ch.name)
    return {"data": ChannelResponse.model_validate(ch, from_attributes=True).model_dump(by_alias=True)}

@router.delete("/api/channels/{ch_id}")
async def delete_channel(ch_id: uuid.UUID, session: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    ch = await channel_service.get_channel(session, ch_id)
    await channel_service.delete_channel(session, ch_id)
    await write_audit_log(session, action="delete", target_type="channel", target_id=ch_id, target_name=ch.name)
    return MessageResponse(message="删除成功").model_dump()
