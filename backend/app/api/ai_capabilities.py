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
from app.services.ai_capabilities import (
    CAPABILITY_REGISTRY, CATEGORY_META, BUILTIN_CATEGORIES, active_categories,
)

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


async def _probe_cli_channel() -> dict:
    """探 429 降级通道（claude-proxy）的死活。探不到不抛错，只如实报。"""
    base = (settings.ai_proxy_base_url or settings.ai_ui_base_url or "").rstrip("/")
    if not base:
        return {"configured": False, "alive": False,
                "hint": "没配 AI_PROXY_BASE_URL：网关一限流就只能靠重试，重试耗尽即失败"}
    url = base[: -len("/v1")] if base.endswith("/v1") else base
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{url}/health")
        alive = r.status_code == 200
        return {
            "configured": True, "alive": alive, "endpoint": url,
            "hint": None if alive else f"探活返回 {r.status_code}，限流时降级会失败",
        }
    except Exception as e:  # noqa: BLE001
        return {"configured": True, "alive": False, "endpoint": url,
                "hint": f"连不上（{type(e).__name__}）：限流时降级会失败，用 deploy/start-ai-services.sh 起一下"}


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
    # 只报还有活着模块的档位；全下线的档位继续报模型会误导（见 active_categories 注释）
    for cat in active_categories():
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
            # 连接自己的默认模型。**页面必须能拿到它** —— 连接名是人随便起的
            # （「公司网关-Opus」），档位又可以覆盖成别的模型，于是首屏出现过
            # 「claude-sonnet-5 经 公司网关-Opus」这种自相矛盾的写法。
            # 名字骗人的时候，唯一的解释办法是把"名字/连接默认/档位覆盖"三件事摆开说。
            "model": sysdef.model,
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

    # ①-b 429 降级通道（claude-proxy）的死活。
    #
    # 这条通道在顶栏「服务 N/17」里已经被探活，但**在 AI 配置页改模型的人不会去看顶栏**。
    # 而 CLAUDE.md 写得很清楚：文本生成仍依赖它 —— 网关一限流，主路重试耗尽后就靠它兜。
    # 它挂了的话，人在这里换完模型、回头跑生成时会撞上莫名其妙的 429 失败，
    # 而这一页当时什么都不说。所以把状态摆到「平台当前在用」那张卡片上。
    fallback["cliChannel"] = await _probe_cli_channel()

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
            # 免得用户以为这张表已经涵盖全部映射。
            # **单入口覆盖（key 前缀 cap-）不算"自定义档位"** —— 那是"这一行换个模型"，
            # 混进档位数里，人会去找一张并不存在的档位卡片。
            "customBindingCount": sum(1 for b in bindings
                                      if not b.is_builtin and not (b.key or "").startswith("cap-")),
            # 哪几处入口单独指定了模型。首屏那句"一个模型负责全部 N 项"要减掉它们，
            # 否则页面又开始说一句不成立的话。
            "perCapabilityOverrides": [
                {"key": (b.module_keys or [None])[0], "label": b.label, "model": b.model}
                for b in bindings
                if not b.is_builtin and (b.key or "").startswith("cap-")
            ],
        }
    }


# ── AI 到底用在哪儿:能力 → 入口 → 模型 → 真实用量 ─────

@router.get("/usage")
async def get_capability_usage(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """每个 AI 入口:走哪个档位、解析出的模型是什么、**最近真的被调过没有**。

    ## 为什么要有"真实用量"这一列

    这一页原来只回答"配了什么",回答不了"用了什么"。用户自己的结论是
    「系统里用到 AI 的好像只有 AI 审核」—— 而库里 `scenario-*` 有 111 条调用记录。
    页面说不清,人就只能猜,猜完照着猜的结论砍功能。

    `metered=false` 的行**不能读成"没用过"**,只能读成"这条链路以前不记账"
    （见 ai_capabilities.METERED_SINCE）。两者在界面上必须分开写。
    """
    from sqlalchemy import func as sa_func

    from app.models.case_file import AIUsageLog
    from app.services.ai_capabilities import METERED_SINCE, normalize_usage_key
    from app.services.ai_config_resolver import resolve_ai_config

    rows = (await session.execute(
        select(AIUsageLog.skill_name, sa_func.count(), sa_func.max(AIUsageLog.created_at),
               sa_func.sum(AIUsageLog.total_tokens))
        .group_by(AIUsageLog.skill_name)
    )).all()

    agg: dict[str, dict] = {}
    for skill, calls, last, tokens in rows:
        key = normalize_usage_key(skill)
        a = agg.setdefault(key, {"calls": 0, "last": None, "tokens": 0, "rawNames": []})
        a["calls"] += calls
        a["tokens"] += int(tokens or 0)
        a["rawNames"].append(skill)
        if last and (a["last"] is None or last > a["last"]):
            a["last"] = last

    # 档位解析走 resolve_ai_config,和真实调用同一条路 —— 页面不自己重算优先级。
    # **按 key 解析而不是按档位缓存**:单个入口可以有自己的专用档(见 PUT
    # /capability-model),按 category 缓存会把它显示成档位的模型,页面又开始骗人。
    bindings = (await session.execute(select(AICapabilityBinding))).scalars().all()
    own_of = {k: b for b in bindings if not b.is_builtin
              for k in [list(b.module_keys or [])[0]] if list(b.module_keys or []) == [k]}
    items = []
    for cap in CAPABILITY_REGISTRY:
        if cap.get("deprecated"):
            continue
        cat = cap["category"]
        cfg = await resolve_ai_config(None, session, capability=cap["key"])
        model_cache = {cat: cfg.model if cfg else None}
        u = agg.get(cap["key"], {})
        items.append({
            "key": cap["key"],
            "label": cap["label"],
            "category": cat,
            "where": cap.get("where"),
            "model": model_cache[cat],
            # 这一行的模型是「跟着档位」还是「这个入口单独指定的」
            "ownModel": own_of[cap["key"]].model if cap["key"] in own_of else None,
            "calls": u.get("calls", 0),
            "lastUsedAt": u["last"].isoformat() if u.get("last") else None,
            "tokens": u.get("tokens", 0),
            # 这条链路从什么时候开始记账。None = 一直有记账,0 次就是真的 0 次
            "meteredSince": METERED_SINCE.get(cap["key"]),
        })

    # 记了账但对不上任何能力 key 的（历史 skill 名、已下线的能力）也要露出来，
    # 否则"平台上跑过的 AI 调用"这本账和这张表永远差着数，而差多少没人说得出来
    known = {i["key"] for i in items}
    orphans = [{"key": k, "calls": v["calls"], "lastUsedAt": v["last"].isoformat() if v["last"] else None,
                "tokens": v["tokens"]}
               for k, v in agg.items() if k not in known]

    return {"data": {"items": items, "orphans": sorted(orphans, key=lambda x: -x["calls"])}}


class CapabilityModelUpdate(BaseModel):
    key: str
    # None / 空 = 取消单独指定,回到跟着档位走
    model: str | None = None


@router.put("/capability-model")
async def set_capability_model(
    body: CapabilityModelUpdate,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """给**单个 AI 入口**指定模型(或取消,回到跟着档位走)。

    为什么要有这个:原来只能按"档位"配(文本生成 / UI 脚本生成两档),想让文档生成
    用便宜模型、评审用强模型,得自己去「新增自定义档位」里建一个档、再勾模块 ——
    三步操作、两个新概念,而用户要的只是"这一行换个模型"。

    实现上仍然是自定义档位(一个入口一档,`module_keys=[key]`),只是把三步压成一步:
    页面上每一行一个下拉。**不新造第二套优先级** —— 解析路径还是
    ai_config_resolver 那一条,否则页面显示和实际调用早晚漂开。
    """
    cap = next((c for c in CAPABILITY_REGISTRY if c["key"] == body.key), None)
    if cap is None:
        raise NotFoundError(code="CAPABILITY_NOT_FOUND", message=f"没有这个 AI 入口:{body.key}")

    bindings = (await session.execute(select(AICapabilityBinding))).scalars().all()
    # 已有的"专用档"：非内置、且刚好只圈了这一个 key
    own = next((b for b in bindings
                if not b.is_builtin and list(b.module_keys or []) == [body.key]), None)

    model = (body.model or "").strip()
    if not model:
        if own is not None:
            await session.delete(own)
            await session.commit()
        return {"data": {"key": body.key, "model": None, "followsCategory": True}}

    if own is None:
        own = AICapabilityBinding(
            key=f"cap-{body.key}",
            label=f"{cap['label']}·专用",
            category=cap["category"],
            model=model,
            is_builtin=False,
            module_keys=[body.key],
            sort_order=100,
        )
        session.add(own)
    else:
        own.model = model
    await session.commit()
    return {"data": {"key": body.key, "model": model, "followsCategory": False}}


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


# ── CC 归因质量（B6）──────────────────────────────────────
# 放在 AI 能力这组下面：它量的是"AI 的判断准不准"，和这一组的其它指标同类。
# 平台此前只有生成通过率，没有任何东西量 AI 判断的质量。

@router.get("/analysis-agreement")
async def analysis_agreement(
    project_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """CC 归因 vs 人确认 的一致率（按 cause 分桶）。"""
    from app.services.analysis_service import agreement_stats
    return {"data": await agreement_stats(session, project_id)}
