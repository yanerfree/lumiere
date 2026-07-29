"""AI 服务配置 API — 系统级 + 项目级 CRUD + 测试连接"""
from __future__ import annotations

import logging
import time
import uuid

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, NotFoundError
from app.deps.auth import get_current_user, require_role
from app.deps.db import get_db
from app.models.ai_provider_config import AIProviderConfig, ProjectAIConfig
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai-providers", tags=["ai-config"])
project_router = APIRouter(prefix="/api/projects/{project_id}/ai-config", tags=["ai-config"])


# ── Schemas ──────────────────────────────────────────

class ProviderConfigCreate(BaseModel):
    name: str = Field(..., max_length=100)
    provider: str = Field(default="openai_compatible", max_length=50)
    base_url: str = Field(..., max_length=500)
    api_key: str | None = None
    auth_token: str | None = None
    model: str = Field(..., max_length=100)
    temperature: float = 0.3
    max_tokens: int = 4096
    timeout_seconds: int = 120
    is_system_default: bool = False
    assigned_project_ids: list[str] | None = None


class ProviderConfigUpdate(BaseModel):
    name: str | None = None
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    auth_token: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: int | None = None
    is_system_default: bool | None = None
    is_enabled: bool | None = None
    assigned_project_ids: list[str] | None = None


class TestConnectionRequest(BaseModel):
    provider: str = "openai_compatible"
    base_url: str
    api_key: str | None = None
    auth_token: str | None = None
    model: str | None = None


def _mask_url(url: str) -> str:
    if len(url) > 25:
        return url[:20] + "..." + url[-5:]
    return url


def _mask_secret(s: str | None) -> str:
    if not s:
        return ""
    if len(s) <= 8:
        return "***"
    return s[:4] + "***" + s[-4:]


def _serialize_config(c: AIProviderConfig) -> dict:
    return {
        "id": str(c.id),
        "name": c.name,
        "provider": c.provider,
        "baseUrl": c.base_url,
        "baseUrlMasked": _mask_url(c.base_url),
        "apiKeySet": bool(c.api_key_encrypted),
        "authTokenSet": bool(c.auth_token_encrypted),
        "model": c.model,
        "temperature": c.temperature,
        "maxTokens": c.max_tokens,
        "timeoutSeconds": c.timeout_seconds,
        "isSystemDefault": c.is_system_default,
        "isEnabled": c.is_enabled,
        "assignedProjectIds": c.assigned_project_ids or [],
        "status": c.status,
        "statusMessage": c.status_message,
        "lastTestedAt": c.last_tested_at.isoformat() if c.last_tested_at else None,
        "createdAt": c.created_at.isoformat() if c.created_at else None,
        "updatedAt": c.updated_at.isoformat() if c.updated_at else None,
    }


# ── 系统级 CRUD ──────────────────────────────────────

@router.get("")
async def list_provider_configs(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await session.execute(
        select(AIProviderConfig).order_by(AIProviderConfig.is_system_default.desc(), AIProviderConfig.created_at)
    )
    configs = result.scalars().all()
    return {"data": [_serialize_config(c) for c in configs]}


@router.post("")
async def create_provider_config(
    body: ProviderConfigCreate,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    if body.is_system_default:
        await session.execute(
            update(AIProviderConfig).values(is_system_default=False)
        )

    config = AIProviderConfig(
        name=body.name,
        provider=body.provider,
        base_url=body.base_url,
        api_key_encrypted=body.api_key or None,
        auth_token_encrypted=body.auth_token or None,
        model=body.model,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        timeout_seconds=body.timeout_seconds,
        is_system_default=body.is_system_default,
        assigned_project_ids=body.assigned_project_ids,
        created_by=current_user.id,
    )
    session.add(config)
    await session.commit()
    await session.refresh(config)
    return {"data": _serialize_config(config)}


@router.get("/{config_id}")
async def get_provider_config(
    config_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    config = await session.get(AIProviderConfig, config_id)
    if not config:
        raise NotFoundError(code="CONFIG_NOT_FOUND", message="AI 配置不存在")
    data = _serialize_config(config)
    data["apiKeyMasked"] = _mask_secret(config.api_key_encrypted)
    data["authTokenMasked"] = _mask_secret(config.auth_token_encrypted)
    return {"data": data}


@router.put("/{config_id}")
async def update_provider_config(
    config_id: uuid.UUID,
    body: ProviderConfigUpdate,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    config = await session.get(AIProviderConfig, config_id)
    if not config:
        raise NotFoundError(code="CONFIG_NOT_FOUND", message="AI 配置不存在")

    if body.is_system_default:
        await session.execute(
            update(AIProviderConfig).where(AIProviderConfig.id != config_id).values(is_system_default=False)
        )

    update_fields = body.model_dump(exclude_unset=True)
    if "api_key" in update_fields:
        val = update_fields.pop("api_key")
        if val is not None:
            config.api_key_encrypted = val
    if "auth_token" in update_fields:
        val = update_fields.pop("auth_token")
        if val is not None:
            config.auth_token_encrypted = val

    for k, v in update_fields.items():
        setattr(config, k, v)

    await session.commit()
    await session.refresh(config)
    return {"data": _serialize_config(config)}


@router.delete("/{config_id}")
async def delete_provider_config(
    config_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    config = await session.get(AIProviderConfig, config_id)
    if not config:
        raise NotFoundError(code="CONFIG_NOT_FOUND", message="AI 配置不存在")

    await session.execute(
        update(ProjectAIConfig)
        .where(ProjectAIConfig.provider_config_id == config_id)
        .values(provider_config_id=None, is_active=False)
    )
    await session.delete(config)
    await session.commit()
    return {"data": {"deleted": True}}


# ── 测试连接 ─────────────────────────────────────────

@router.post("/{config_id}/test")
async def test_saved_connection(
    config_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    config = await session.get(AIProviderConfig, config_id)
    if not config:
        raise NotFoundError(code="CONFIG_NOT_FOUND", message="AI 配置不存在")

    result = await _do_test_connection(
        provider=config.provider,
        base_url=config.base_url,
        api_key=config.api_key_encrypted,
        auth_token=config.auth_token_encrypted,
        model=config.model,
    )

    from datetime import datetime, timezone
    config.status = "ok" if result["success"] else "error"
    config.status_message = result.get("message", "")
    config.last_tested_at = datetime.now(timezone.utc)
    await session.commit()

    return {"data": result}


@router.post("/test-connection")
async def test_connection(
    body: TestConnectionRequest,
    current_user: User = Depends(get_current_user),
):
    result = await _do_test_connection(
        provider=body.provider,
        base_url=body.base_url,
        api_key=body.api_key,
        auth_token=body.auth_token,
        model=body.model,
    )
    return {"data": result}


async def _do_test_connection(
    *,
    provider: str,
    base_url: str,
    api_key: str | None,
    auth_token: str | None,
    model: str | None,
) -> dict:
    headers: dict[str, str] = {"User-Agent": "claude-cli/1.0"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    url = base_url.rstrip("/")
    if provider == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
        headers["content-type"] = "application/json"
        if api_key:
            headers["x-api-key"] = api_key
        test_url = f"{url}/messages"
        test_body = {
            "model": model or "claude-haiku-4-5-20251001",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 5,
        }
    else:
        headers["content-type"] = "application/json"
        test_url = f"{url}/chat/completions"
        test_body = {
            "model": model or "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 5,
        }

    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(test_url, json=test_body, headers=headers)
        latency_ms = int((time.time() - start) * 1000)

        if resp.status_code == 200:
            data = resp.json()
            resp_model = data.get("model", model or "unknown")
            return {
                "success": True,
                "message": f"连接成功 · {resp_model}",
                "latencyMs": latency_ms,
                "model": resp_model,
            }
        else:
            error_text = resp.text[:200]
            return {
                "success": False,
                "message": f"HTTP {resp.status_code}: {error_text}",
                "latencyMs": latency_ms,
            }
    except httpx.TimeoutException:
        return {"success": False, "message": "连接超时（30s）", "latencyMs": 30000}
    except Exception as e:
        return {"success": False, "message": f"连接失败: {str(e)[:200]}"}


# ── 项目级配置 ────────────────────────────────────────

@project_router.get("")
async def get_project_ai_config(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await session.execute(
        select(ProjectAIConfig).where(ProjectAIConfig.project_id == project_id)
    )
    configs = result.scalars().all()

    system_result = await session.execute(
        select(AIProviderConfig).where(AIProviderConfig.is_enabled == True).order_by(
            AIProviderConfig.is_system_default.desc(), AIProviderConfig.created_at
        )
    )
    all_system = system_result.scalars().all()
    pid_str = str(project_id)
    system_configs = [
        c for c in all_system
        if c.assigned_project_ids and pid_str in c.assigned_project_ids
    ]

    active = next((c for c in configs if c.is_active), None)

    # 本项目「实际生效」的配置。项目没单独选时会吃管理员的全局兜底 —— 页面必须显示这个,
    # 否则会把正常工作的项目显示成"尚未配置 / 功能不可用"(与管理端总览、真实调用全都矛盾)。
    from app.services.ai_config_resolver import describe_effective
    effective = await describe_effective(project_id, session, mask_url=_mask_url)

    return {
        "data": {
            "effective": effective,
            "systemConfigs": [_serialize_config(c) for c in system_configs],
            "projectConfigs": [
                {
                    "id": str(c.id),
                    "providerConfigId": str(c.provider_config_id) if c.provider_config_id else None,
                    "name": c.name,
                    "provider": c.provider,
                    "model": c.model,
                    "isActive": c.is_active,
                }
                for c in configs
            ],
            "activeConfigId": str(active.id) if active else None,
            "activeProviderConfigId": str(active.provider_config_id) if active and active.provider_config_id else None,
        }
    }


@project_router.post("/select/{provider_config_id}")
async def select_system_config_for_project(
    project_id: uuid.UUID,
    provider_config_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    provider = await session.get(AIProviderConfig, provider_config_id)
    if not provider:
        raise NotFoundError(code="CONFIG_NOT_FOUND", message="AI 配置不存在")

    await session.execute(
        update(ProjectAIConfig).where(ProjectAIConfig.project_id == project_id).values(is_active=False)
    )

    existing = (await session.execute(
        select(ProjectAIConfig).where(
            ProjectAIConfig.project_id == project_id,
            ProjectAIConfig.provider_config_id == provider_config_id,
        )
    )).scalar_one_or_none()

    if existing:
        existing.is_active = True
    else:
        session.add(ProjectAIConfig(
            project_id=project_id,
            provider_config_id=provider_config_id,
            is_active=True,
        ))

    await session.commit()
    return {"data": {"selected": True, "providerConfigId": str(provider_config_id)}}


class ProjectCustomConfigCreate(BaseModel):
    name: str = Field(..., max_length=100)
    provider: str = Field(default="openai_compatible", max_length=50)
    base_url: str = Field(..., max_length=500)
    api_key: str | None = None
    auth_token: str | None = None
    model: str = Field(..., max_length=100)
    temperature: float = 0.3
    max_tokens: int = 4096
    timeout_seconds: int = 120


@project_router.post("/custom")
async def create_project_custom_config(
    project_id: uuid.UUID,
    body: ProjectCustomConfigCreate,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await session.execute(
        update(ProjectAIConfig).where(ProjectAIConfig.project_id == project_id).values(is_active=False)
    )

    config = ProjectAIConfig(
        project_id=project_id,
        name=body.name,
        provider=body.provider,
        base_url=body.base_url,
        api_key_encrypted=body.api_key or None,
        auth_token_encrypted=body.auth_token or None,
        model=body.model,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        timeout_seconds=body.timeout_seconds,
        is_active=True,
    )
    session.add(config)
    await session.commit()
    await session.refresh(config)
    return {
        "data": {
            "id": str(config.id),
            "name": config.name,
            "provider": config.provider,
            "model": config.model,
            "isActive": config.is_active,
        }
    }


@project_router.post("/custom/{config_id}/test")
async def test_project_custom_config(
    project_id: uuid.UUID,
    config_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    config = await session.get(ProjectAIConfig, config_id)
    if not config or config.project_id != project_id:
        raise NotFoundError(code="CONFIG_NOT_FOUND", message="配置不存在")
    if not config.base_url:
        raise AppError(code="NO_URL", message="此配置关联系统配置，请在系统设置中测试", status_code=400)

    result = await _do_test_connection(
        provider=config.provider or "openai_compatible",
        base_url=config.base_url,
        api_key=config.api_key_encrypted,
        auth_token=config.auth_token_encrypted,
        model=config.model,
    )
    return {"data": result}


@project_router.delete("/custom/{config_id}")
async def delete_project_custom_config(
    project_id: uuid.UUID,
    config_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    config = await session.get(ProjectAIConfig, config_id)
    if not config or config.project_id != project_id:
        raise NotFoundError(code="CONFIG_NOT_FOUND", message="配置不存在")
    await session.delete(config)
    await session.commit()
    return {"data": {"deleted": True}}
