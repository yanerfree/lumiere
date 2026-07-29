"""AI 能力 → 模型 档位 API(全局层)。

- GET  /api/ai-capabilities            汇总:全局开关 + 档位列表 + 模块注册表 + 类别元信息
- PUT  /api/ai-capabilities/settings   改全局兜底开关
- POST /api/ai-capabilities/bindings   新增自定义档位
- PUT  /api/ai-capabilities/bindings/{id}   改档位(模型/名称/覆盖模块)
- DELETE /api/ai-capabilities/bindings/{id} 删自定义档位(内置不可删)
- GET  /api/ai-capabilities/models     从全局默认连接拉可用模型清单(下拉用)
"""
from __future__ import annotations

import logging
import uuid

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import AppError, NotFoundError
from app.deps.auth import get_current_user, require_role
from app.deps.db import get_db
from app.models.ai_provider_config import AICapabilityBinding, AIGlobalSettings, AIProviderConfig
from app.models.user import User
from app.services.ai_capabilities import CAPABILITY_REGISTRY, CATEGORY_META, BUILTIN_CATEGORIES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai-capabilities", tags=["ai-capabilities"])

# 拉不到网关 /models 时的兜底清单(公司网关实际在供的模型,新→旧)
# 注意:只放裸模型 ID。CLI 侧的长上下文后缀写法(如 claude-opus-5[1m])在接口路径会 404,不要收进来。
_PRESET_MODELS = [
    "claude-opus-5", "claude-sonnet-5", "claude-fable-5",
    "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
    "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
]


# ── helpers ──────────────────────────────────────────

async def _get_or_create_settings(session: AsyncSession) -> AIGlobalSettings:
    row = (await session.execute(select(AIGlobalSettings).limit(1))).scalar_one_or_none()
    if not row:
        row = AIGlobalSettings(fallback_enabled=True)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


def _serialize_binding(b: AICapabilityBinding) -> dict:
    meta = CATEGORY_META.get(b.category, {})
    return {
        "id": str(b.id),
        "key": b.key,
        "label": b.label,
        "category": b.category,
        "model": b.model,
        "isBuiltin": b.is_builtin,
        "moduleKeys": b.module_keys or [],
        "sortOrder": b.sort_order,
        "recommend": meta.get("recommend", ""),
        "icon": meta.get("icon", ""),
    }


async def _global_connection(session: AsyncSession) -> tuple[str, str] | None:
    """全局默认连接(base_url, token) — 系统默认配置优先,否则 .env。用于拉 /models。"""
    sysdef = (await session.execute(
        select(AIProviderConfig).where(
            AIProviderConfig.is_system_default == True,  # noqa: E712
            AIProviderConfig.is_enabled == True,  # noqa: E712
        )
    )).scalar_one_or_none()
    if sysdef and sysdef.base_url:
        token = sysdef.auth_token_encrypted or sysdef.api_key_encrypted or ""
        return sysdef.base_url, token
    if settings.ai_base_url:
        return settings.ai_base_url, (settings.ai_auth_token or settings.ai_api_key or "")
    return None


# ── 汇总 ─────────────────────────────────────────────

@router.get("")
async def get_capabilities(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    st = await _get_or_create_settings(session)
    bindings = (await session.execute(
        select(AICapabilityBinding).order_by(AICapabilityBinding.sort_order, AICapabilityBinding.created_at)
    )).scalars().all()

    return {
        "data": {
            "fallbackEnabled": st.fallback_enabled,
            "bindings": [_serialize_binding(b) for b in bindings],
            "registry": CAPABILITY_REGISTRY,
            "categoryMeta": CATEGORY_META,
            "builtinCategories": BUILTIN_CATEGORIES,
        }
    }


class SettingsUpdate(BaseModel):
    fallback_enabled: bool


@router.put("/settings")
async def update_settings(
    body: SettingsUpdate,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    st = await _get_or_create_settings(session)
    st.fallback_enabled = body.fallback_enabled
    await session.commit()
    return {"data": {"fallbackEnabled": st.fallback_enabled}}


# ── 档位 CRUD ────────────────────────────────────────

class BindingCreate(BaseModel):
    label: str = Field(..., max_length=100)
    category: str = Field(..., max_length=20)  # text | ui_script
    model: str = Field(..., max_length=100)
    module_keys: list[str] | None = None


class BindingUpdate(BaseModel):
    label: str | None = None
    model: str | None = None
    module_keys: list[str] | None = None


@router.post("/bindings")
async def create_binding(
    body: BindingCreate,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    if body.category not in BUILTIN_CATEGORIES:
        raise AppError(code="BAD_CATEGORY", message="类别只能是 text 或 ui_script", status_code=400)

    # 生成唯一 key
    base_key = "custom-" + "".join(c for c in body.label.lower() if c.isalnum())[:20] or "custom"
    key = base_key
    n = 1
    while (await session.execute(select(AICapabilityBinding).where(AICapabilityBinding.key == key))).scalar_one_or_none():
        n += 1
        key = f"{base_key}-{n}"

    max_sort = (await session.execute(
        select(AICapabilityBinding.sort_order).order_by(AICapabilityBinding.sort_order.desc()).limit(1)
    )).scalar_one_or_none() or 0

    binding = AICapabilityBinding(
        key=key,
        label=body.label,
        category=body.category,
        model=body.model,
        is_builtin=False,
        module_keys=body.module_keys or [],
        sort_order=max_sort + 1,
    )
    session.add(binding)
    await session.commit()
    await session.refresh(binding)
    return {"data": _serialize_binding(binding)}


@router.put("/bindings/{binding_id}")
async def update_binding(
    binding_id: uuid.UUID,
    body: BindingUpdate,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    b = await session.get(AICapabilityBinding, binding_id)
    if not b:
        raise NotFoundError(code="BINDING_NOT_FOUND", message="能力档位不存在")

    fields = body.model_dump(exclude_unset=True)
    # 内置档位不允许改 label / module_keys,只能改模型(保证注册表语义稳定)
    if b.is_builtin:
        fields = {k: v for k, v in fields.items() if k == "model"}
    if "module_keys" in fields:
        b.module_keys = fields.pop("module_keys") or []
    for k, v in fields.items():
        setattr(b, k, v)
    await session.commit()
    await session.refresh(b)
    return {"data": _serialize_binding(b)}


@router.delete("/bindings/{binding_id}")
async def delete_binding(
    binding_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    b = await session.get(AICapabilityBinding, binding_id)
    if not b:
        raise NotFoundError(code="BINDING_NOT_FOUND", message="能力档位不存在")
    if b.is_builtin:
        raise AppError(code="BUILTIN_LOCKED", message="内置档位不可删除,只能修改模型", status_code=400)
    await session.delete(b)
    await session.commit()
    return {"data": {"deleted": True}}


# ── 使用总览:兜底链 + 每个项目实际生效的 AI ─────────────

@router.get("/overview")
async def get_overview(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """管理端「谁在用哪个 AI」总览。

    每一格都取自 resolve_ai_config() 的真实返回，**不在这里重写优先级逻辑**——
    否则页面显示会和实际调用漂移，比不显示更糟。只回名称/模型/脱敏 URL，不回密钥。
    """
    from app.api.ai_config import _mask_url
    from app.models.project import Project
    from app.services.ai_config_resolver import describe_effective, resolve_ai_config

    st = await _get_or_create_settings(session)
    bindings = (await session.execute(select(AICapabilityBinding))).scalars().all()

    # ① 兜底链：project_id=None 正好只走全局兜底路径
    resolved_fallback = []
    for cat in BUILTIN_CATEGORIES:
        cfg = await resolve_ai_config(None, session, capability=cat)
        resolved_fallback.append({
            "category": cat,
            "label": CATEGORY_META.get(cat, {}).get("label", cat),
            "model": cfg.model if cfg else None,
        })

    sysdef = (await session.execute(
        select(AIProviderConfig).where(
            AIProviderConfig.is_system_default == True,  # noqa: E712
            AIProviderConfig.is_enabled == True,  # noqa: E712
        )
    )).scalar_one_or_none()

    fallback = {
        "enabled": st.fallback_enabled,
        "connection": {
            "id": str(sysdef.id),
            "name": sysdef.name,
            "provider": sysdef.provider,
            "baseUrlMasked": _mask_url(sysdef.base_url),
            "isEnabled": sysdef.is_enabled,
            "status": sysdef.status,
            "statusMessage": sysdef.status_message,
        } if sysdef else None,
        # 没有系统默认配置时，兜底会落到 .env（且 AI_ENABLED 得为真）
        "usingEnv": sysdef is None and bool(settings.ai_enabled and settings.ai_base_url),
        "envModel": settings.ai_model if sysdef is None else None,
        "envBaseUrlMasked": _mask_url(settings.ai_base_url) if (sysdef is None and settings.ai_base_url) else None,
        "resolved": resolved_fallback,
    }

    # ② 兜底连接下拉的候选项(只列启用的)
    candidates = [
        {"id": str(c.id), "name": c.name, "model": c.model,
         "provider": c.provider, "isSystemDefault": c.is_system_default}
        for c in (await session.execute(
            select(AIProviderConfig).where(AIProviderConfig.is_enabled == True)  # noqa: E712
            .order_by(AIProviderConfig.is_system_default.desc(), AIProviderConfig.created_at)
        )).scalars().all()
    ]

    # ③ 每个项目一行
    projects = (await session.execute(select(Project).order_by(Project.created_at))).scalars().all()
    rows = []
    for p in projects:
        # 与项目 AI 配置页共用 describe_effective,口径必须一致。
        # models 是列表而非 {category: model} 字典:响应会过 CamelCaseResponse 中间件,
        # 字典 key "ui_script" 会被悄悄改写成 "uiScript",前端按 category 取值更稳。
        eff = await describe_effective(p.id, session, mask_url=_mask_url)
        rows.append({"projectId": str(p.id), "projectName": p.name, **eff})

    return {
        "data": {
            "fallback": fallback,
            "candidates": candidates,
            "projects": rows,
            # 自定义档位按 module_keys 覆盖，不在总览两列里展开 → 给个数量提示，
            # 免得用户以为这张表已经涵盖全部映射
            "customBindingCount": sum(1 for b in bindings if not b.is_builtin),
        }
    }


# ── 模型下拉:代理网关 /models ─────────────────────────

@router.get("/models")
async def list_models(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conn = await _global_connection(session)
    if not conn:
        return {"data": {"models": _PRESET_MODELS, "source": "preset", "message": "未配置全局连接,返回预置清单"}}

    base_url, token = conn
    url = base_url.rstrip("/") + "/models"
    # 公司网关是 Anthropic 原生风格:需 x-api-key + anthropic-version + claude-cli UA
    headers = {
        "User-Agent": "claude-cli/1.0",
        "anthropic-version": "2023-06-01",
    }
    if token:
        headers["x-api-key"] = token
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("data", data if isinstance(data, list) else [])
            models = []
            for m in items:
                mid = m.get("id") if isinstance(m, dict) else str(m)
                if not mid:
                    continue
                models.append({"id": mid, "displayName": m.get("display_name", mid) if isinstance(m, dict) else mid})
            if models:
                return {"data": {"models": models, "source": "gateway"}}
        logger.warning("拉取 /models 失败 HTTP %s: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.warning("拉取 /models 异常: %s", e)

    return {"data": {"models": [{"id": m, "displayName": m} for m in _PRESET_MODELS], "source": "preset",
                     "message": "网关未返回模型清单,已回退预置清单"}}
