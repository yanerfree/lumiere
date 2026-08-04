"""MCP Skill 通道 —— 项目侧 Claude Code 把自己的 skill 推上平台 / 从平台取用。

这是**写入的主通道**：外部项目已经连了 MCP、有 API Key、有 allowed_tools 白名单，
不需要再造一套认证。用法一句话：

    在 A 项目：读本地 .claude/skills/<name>/ → tb_push_skill 推上去
    在 B 项目：tb_list_skills 看有什么 → tb_pull_skill 拿全文 → 写进本地 .claude/skills/

与内置 tb-* 的边界（别混）：内置那批是**平台侧执行**的 prompt（skill_executor 喂
后端 LLM，绑 AI 能力档位），本通道存的是**客户端侧执行**的 skill（跑在你机器的
Claude Code 里，用 Bash/Edit/Playwright）。本通道的东西永不被平台当 prompt 执行。
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.project import Project
from app.models.skill import Skill
from app.services import skill_registry as reg

logger = logging.getLogger(__name__)


def _parse_uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise AppError(code="INVALID_UUID", message=f"{field} 不是合法 UUID：{value}", status_code=400)


async def push_skill(
    session: AsyncSession,
    project_id: str,
    content: str,
    name: str | None = None,
    files: dict | None = None,
    description: str | None = None,
    visibility: str = "public",
    overwrite: bool = True,
) -> dict:
    """把一个本地 skill 推上平台。

    content = SKILL.md 全文（含 frontmatter）；name 不传就取 frontmatter 里的 name。
    files = 附属文件 {相对路径: 文本内容}，如 {"references/api.md": "..."}。
    """
    pid = _parse_uuid(project_id, "project_id")

    exists = await session.execute(select(Project.id).where(Project.id == pid))
    if exists.scalar_one_or_none() is None:
        raise AppError(code="PROJECT_NOT_FOUND", message=f"项目 {project_id} 不存在", status_code=404)

    skill, created = await reg.upsert_skill(
        session,
        project_id=pid,
        name=name or "",
        content=content,
        files=files,
        kind="client",
        visibility=visibility,
        description=description,
        source="mcp",
        overwrite=overwrite,
    )
    await session.flush()

    result = {
        **reg.to_summary(skill),
        "created": created,
        "action": "created" if created else f"updated to v{skill.version}",
    }
    if not created:
        result["hint"] = (
            f"已覆盖，旧版本留档为 v{skill.version - 1}，"
            "可在平台「Skill 管理」页回滚。"
        )
    if result["platformTools"]:
        # 客户端 skill 声明了平台 MCP 工具 —— 合法但值得指出：取用方必须也连了 testBench MCP
        result["note"] = (
            f"这个 skill 声明了平台 MCP 工具 {result['platformTools']}，"
            "取用方项目也必须连上 testBench MCP 才能跑通。"
        )
    return result


async def list_skills(
    session: AsyncSession,
    project_id: str | None = None,
    include_shared: bool = True,
) -> dict:
    """列出可取用的 skill。

    project_id 传了 = 本项目的 + 全平台共享的；不传 = 只看全平台共享的。
    """
    pid = _parse_uuid(project_id, "project_id") if project_id else None
    skills = await reg.list_skills(
        session, project_id=pid, kind="client", include_shared=include_shared
    )
    project_names = await reg.load_project_names(session, {s.project_id for s in skills})

    items = []
    for s in skills:
        items.append({
            "skillId": str(s.id),
            "name": s.name,
            "description": s.description or "",
            "version": s.version,
            "visibility": s.visibility,
            "fileCount": len(s.files or {}),
            "sourceProject": project_names.get(s.project_id, ""),
            "own": pid is not None and s.project_id == pid,
        })
    return {
        "total": len(items),
        "skills": items,
        "howToUse": "用 tb_pull_skill(skill_id=...) 拿全文，写进本地 .claude/skills/<name>/SKILL.md",
    }


async def pull_skill(
    session: AsyncSession,
    skill_id: str | None = None,
    project_id: str | None = None,
    name: str | None = None,
) -> dict:
    """取一个 skill 的全文，用于写进本地 .claude/skills/。

    定位方式二选一：skill_id（推荐，跨项目取用就用它），
    或 project_id + name（取自己项目的）。
    """
    if skill_id:
        sid = _parse_uuid(skill_id, "skill_id")
        result = await session.execute(select(Skill).where(Skill.id == sid))
        skill = result.scalar_one_or_none()
        if skill is None:
            raise AppError(code="SKILL_NOT_FOUND", message=f"skill {skill_id} 不存在", status_code=404)
        # MCP 侧没有「取用方项目」这个上下文，只放行明确共享的
        if skill.visibility != "public":
            raise AppError(
                code="SKILL_NOT_SHARED",
                message=(
                    f"skill '{skill.name}' 的 visibility 是 project，未共享。"
                    "要跨项目取用请让来源项目在平台上改成 public，"
                    "或用 project_id + name 从来源项目自己取。"
                ),
                status_code=403,
            )
    elif project_id and name:
        pid = _parse_uuid(project_id, "project_id")
        skill = await reg.get_skill(session, pid, reg.validate_name(name))
        if skill is None:
            raise AppError(
                code="SKILL_NOT_FOUND",
                message=f"项目 {project_id} 下没有 skill '{name}'",
                status_code=404,
            )
    else:
        raise AppError(
            code="MISSING_PARAM",
            message="需要 skill_id，或者 project_id + name",
            status_code=400,
        )

    files = skill.files or {}
    return {
        "skillId": str(skill.id),
        "name": skill.name,
        "description": skill.description or "",
        "version": skill.version,
        "visibility": skill.visibility,
        # 直接给出落盘路径，取用方不用自己拼
        "writeTo": f".claude/skills/{skill.name}/SKILL.md",
        "content": skill.content,
        "files": files,
        "extraWriteTo": {p: f".claude/skills/{skill.name}/{p}" for p in files},
    }
