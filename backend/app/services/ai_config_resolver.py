"""AI 配置解析 — 从 DB 读取项目/系统级配置，替代 .env

优先级(连接):项目选择 > 全局默认(系统默认配置 / .env) > 无
模型:按 capability(模块 key)命中能力档位覆盖连接自带模型。
全局开关关闭时,未单独配置的项目直接返回 None(去掉 .env 静默兜底)。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.ai_provider_config import (
    AICapabilityBinding,
    AIGlobalSettings,
    AIProviderConfig,
    ProjectAIConfig,
)
from app.services.ai_capabilities import category_of


@dataclass
class ResolvedAIConfig:
    provider: str
    base_url: str
    api_key: str | None
    auth_token: str | None
    model: str
    temperature: float
    max_tokens: int
    timeout_seconds: int
    source: str  # "project" | "system" | "env"
    # 「这份配置是谁」——给管理端总览做展示用，避免页面自己重写一遍优先级导致与实际调用漂移。
    # config_kind: project_selected(项目选了系统配置) | project_custom(项目自建)
    #            | system_default(全局兜底) | env(.env 兜底)
    config_id: str | None = None
    config_name: str | None = None
    config_kind: str | None = None


async def _resolve_model(
    session: AsyncSession,
    capability: str,
    connection_model: str,
    source: str,
) -> str:
    """按能力档位决定实际模型。

    - 自定义档位若用 module_keys 圈中了该 capability → 用它的模型(任何路径都生效)。
    - 否则按类别取内置档位:ui_script 任何路径都覆盖(项目无 UI 模型概念);
      text 仅在全局兜底路径(system/env)覆盖,项目显式选定的模型予以尊重。
    """
    category = category_of(capability)
    bindings = (await session.execute(select(AICapabilityBinding))).scalars().all()

    # 1. 自定义档位精确圈中
    for b in bindings:
        if not b.is_builtin and b.module_keys and capability in b.module_keys:
            return b.model or connection_model

    # 2. 内置档位按类别
    builtin = next((b for b in bindings if b.is_builtin and b.category == category), None)
    if builtin and builtin.model:
        if category == "ui_script" or source in ("system", "env"):
            return builtin.model

    return connection_model


async def resolve_ai_config(
    project_id: uuid.UUID | None,
    session: AsyncSession,
    capability: str = "text",
) -> ResolvedAIConfig | None:
    """解析 AI 配置。capability 为调用方模块 key(见 ai_capabilities 注册表),默认文本类。"""

    # 1. 项目级：查项目激活的配置(显式配置,不受全局开关影响)
    if project_id:
        result = await session.execute(
            select(ProjectAIConfig).where(
                ProjectAIConfig.project_id == project_id,
                ProjectAIConfig.is_active == True,  # noqa: E712
            )
        )
        project_cfg = result.scalar_one_or_none()

        if project_cfg and project_cfg.provider_config_id:
            system_cfg = await session.get(AIProviderConfig, project_cfg.provider_config_id)
            if system_cfg and system_cfg.is_enabled:
                model = await _resolve_model(session, capability, system_cfg.model, "project")
                return ResolvedAIConfig(
                    provider=system_cfg.provider,
                    base_url=system_cfg.base_url,
                    api_key=system_cfg.api_key_encrypted,
                    auth_token=system_cfg.auth_token_encrypted,
                    model=model,
                    temperature=system_cfg.temperature,
                    max_tokens=system_cfg.max_tokens,
                    timeout_seconds=system_cfg.timeout_seconds,
                    source="project",
                    config_id=str(system_cfg.id),
                    config_name=system_cfg.name,
                    config_kind="project_selected",
                )

        if project_cfg and project_cfg.base_url:
            model = await _resolve_model(session, capability, project_cfg.model or "gpt-4o", "project")
            return ResolvedAIConfig(
                provider=project_cfg.provider or "openai_compatible",
                base_url=project_cfg.base_url,
                api_key=project_cfg.api_key_encrypted,
                auth_token=project_cfg.auth_token_encrypted,
                model=model,
                temperature=project_cfg.temperature or 0.3,
                max_tokens=project_cfg.max_tokens or 4096,
                timeout_seconds=project_cfg.timeout_seconds or 120,
                source="project",
                config_id=str(project_cfg.id),
                config_name=project_cfg.name or "项目自建",
                config_kind="project_custom",
            )

    # ── 以下为「全局默认」路径:受全局开关控制 ──
    st = (await session.execute(select(AIGlobalSettings).limit(1))).scalar_one_or_none()
    fallback_enabled = st.fallback_enabled if st else True
    if not fallback_enabled:
        return None  # 开关关闭 → 未单独配置的项目彻底禁用 AI

    # 2. 系统默认(全局兜底,不再受 assigned_project_ids 限制)
    result = await session.execute(
        select(AIProviderConfig).where(
            AIProviderConfig.is_system_default == True,  # noqa: E712
            AIProviderConfig.is_enabled == True,  # noqa: E712
        )
    )
    system_default = result.scalar_one_or_none()
    if system_default:
        model = await _resolve_model(session, capability, system_default.model, "system")
        return ResolvedAIConfig(
            provider=system_default.provider,
            base_url=system_default.base_url,
            api_key=system_default.api_key_encrypted,
            auth_token=system_default.auth_token_encrypted,
            model=model,
            temperature=system_default.temperature,
            max_tokens=system_default.max_tokens,
            timeout_seconds=system_default.timeout_seconds,
            source="system",
            config_id=str(system_default.id),
            config_name=system_default.name,
            config_kind="system_default",
        )

    # 3. .env fallback（向后兼容,同样受全局开关控制）
    if settings.ai_enabled and settings.ai_base_url:
        model = await _resolve_model(session, capability, settings.ai_model, "env")
        return ResolvedAIConfig(
            provider=settings.ai_provider,
            base_url=settings.ai_base_url,
            api_key=settings.ai_api_key or None,
            auth_token=settings.ai_auth_token or None,
            model=model,
            temperature=settings.ai_temperature,
            max_tokens=settings.ai_max_tokens,
            timeout_seconds=settings.ai_timeout_seconds,
            source="env",
            config_name=".env",
            config_kind="env",
        )

    return None
