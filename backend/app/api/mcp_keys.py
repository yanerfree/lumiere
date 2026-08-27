"""MCP API Key 管理 — 生成/列表/吊销"""
from __future__ import annotations

import hashlib
import logging
import secrets
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.common import BaseSchema

from app.deps.db import get_db
from app.deps.auth import get_current_user, require_project_role
from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.user import User
from app.models.mcp_api_key import McpApiKey
from app.models.project import Branch, Project, ProjectMember

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp-keys", tags=["mcp-keys"])


class CreateKeyRequest(BaseSchema):
    name: str = Field(default="default", max_length=100)
    # Key 归属的项目。它的工具范围由这个项目决定（projects.mcp_allowed_tools）。
    # 不传 = 不归属任何项目，走下面 allowed_tools 那条遗留路径。
    project_id: uuid.UUID | None = None
    # 【遗留】Key 级范围。范围已挪到项目级，页面不再传这个字段。
    allowed_tools: list[str] | None = None


class UpdateKeyRequest(BaseSchema):
    name: str | None = Field(default=None, max_length=100)
    # 把一把未归属的存量 Key 归到某个项目（归了之后它就跟着项目范围走）
    project_id: uuid.UUID | None = None
    allowed_tools: list[str] | None = None
    # 显式区分"不改 allowed_tools"和"改成不限制"——前者不传该字段，
    # 后者传 reset_tools=true（JSON 里 null 无法表达这个区别）
    reset_tools: bool = False


class ProjectScopeRequest(BaseSchema):
    """项目级工具范围。三态和 Key 级那套完全一致，别自己再发明一套。"""
    allowed_tools: list[str] | None = None
    reset_tools: bool = False


def _validate_tools(names: list[str] | None) -> list[str] | None:
    """过滤掉不存在的工具名，避免存进一堆拼错的名字导致 Key 形同虚设。"""
    if names is None:
        return None
    from app.mcp import TOOL_CATALOG

    known = {t["name"] for t in TOOL_CATALOG}
    return [n for n in names if n in known]


# 绑定 Key 到项目 = 给这把 Key 该项目用例/环境的**读写**数据范围（见 CLAUDE.md 硬规则：
# Key 的 project_id 现在同时管工具范围和数据范围）。project_id 走 body 不走 path，
# 所以 require_project_role 那个按 path 取 {project_id} 的依赖用不上——在这里手写同一套判定。
# 允许的角色对齐写口径（不含 guest）：一把能写的 Key 不该由只读成员发出去。
_BIND_ROLES = ("project_admin", "developer", "tester")


async def _assert_can_bind_project(
    session: AsyncSession, current_user: User, project_id: uuid.UUID | None
) -> None:
    if project_id is None:
        return
    if current_user.role == "admin":  # 系统 admin 绕过，口径同 require_project_role
        return
    member = (await session.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == current_user.id,
        )
    )).scalar_one_or_none()
    if member is None:
        raise ForbiddenError(code="NOT_PROJECT_MEMBER", message="未绑定到该项目，不能把 Key 归到此项目")
    if member.role not in _BIND_ROLES:
        raise ForbiddenError(code="PROJECT_ROLE_DENIED", message="当前项目角色无权把 Key 归到此项目")


@router.get("/tools")
async def list_available_tools(_: User = Depends(get_current_user)):
    """MCP 工具目录（供工具中心展示 + Key 工具范围勾选）。

    直接来自 mcp 注册时登记的 TOOL_CATALOG，不再前端硬编码——
    此前前端写死 20 条而后端实际 32 条，回推工具全都没露出来。
    """
    from app.mcp import TOOL_CATALOG

    return {"data": TOOL_CATALOG}


@router.get("/profiles")
async def list_tool_profiles(
    project_id: uuid.UUID | None = Query(default=None, alias="projectId"),
    branch_id: uuid.UUID | None = Query(default=None, alias="branchId"),
    mcp_url: str | None = Query(default=None, alias="mcpUrl"),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """按「活」分的工具档位，外加每档可直接粘给 CC 的接入指令。

    档位定义放后端，和 TOOL_CATALOG 同一个进程 —— 之前工具列表就是因为
    前端硬编码而漂移过（写死 20 条、后端实际 32 条）。这里顺带把校验结果
    一起回给前端：档位里写了但没注册的工具名，不该等到 Key 建出来才发现。

    `prompt` 同理必须在后端渲染：它由 task/hint 拼出来，前端自己拼一份的话，
    改了 task 忘了改模板，页面上写的和复制出去的就成了两回事。
    传 projectId/branchId 就把项目分支名填进去，不传则只留占位符。
    """
    from app.mcp import TOOL_CATALOG
    from app.mcp.profiles import PROFILES, render_prompt, uncovered_tools, unknown_tools

    project_name = branch_name = None
    if project_id:
        project_name = (await session.execute(
            select(Project.name).where(Project.id == project_id))).scalar_one_or_none()
    if branch_id:
        branch_name = (await session.execute(
            select(Branch.name).where(Branch.id == branch_id))).scalar_one_or_none()

    names = {t["name"] for t in TOOL_CATALOG}
    url = mcp_url or "（见页面顶部的 MCP 服务地址）"
    return {"data": {
        "profiles": [
            {**p, "prompt": render_prompt(p["key"], mcp_url=url,
                                          project_name=project_name, branch_name=branch_name)}
            for p in PROFILES
        ],
        "totalTools": len(names),
        "uncovered": uncovered_tools(names),
        "unknown": [{"profile": k, "tool": n} for k, n in unknown_tools(names)],
    }}


@router.post("")
async def create_api_key(
    body: CreateKeyRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 归属项目必须是本人有写权限的项目，否则等于凭空给自己开一把能读写他人项目的 Key
    await _assert_can_bind_project(session, current_user, body.project_id)

    raw_key = f"lum_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:8]

    api_key = McpApiKey(
        user_id=current_user.id,
        name=body.name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        project_id=body.project_id,
        allowed_tools=_validate_tools(body.allowed_tools),
    )
    session.add(api_key)
    await session.commit()

    return {"data": {
        "id": str(api_key.id),
        "name": api_key.name,
        "key": raw_key,
        "prefix": key_prefix,
        "projectId": str(api_key.project_id) if api_key.project_id else None,
        "allowedTools": api_key.allowed_tools,
        "createdAt": api_key.created_at.isoformat(),
    }}


@router.patch("/{key_id}")
async def update_api_key(
    key_id: uuid.UUID,
    body: UpdateKeyRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """改名 / 调整工具范围。"""
    key = await session.get(McpApiKey, key_id)
    if not key or key.user_id != current_user.id:
        return {"error": "Key not found"}

    # 改归属同样要过项目写权限校验——否则 PATCH 就成了绕过 create 校验的后门
    await _assert_can_bind_project(session, current_user, body.project_id)

    if body.name is not None:
        key.name = body.name
    if body.project_id is not None:
        # 归到某个项目后，它的范围立刻改由该项目决定。Key 上那份遗留范围一并清掉，
        # 留着只会让"页面显示项目范围、实际生效的是别的"—— 两个来源必须只剩一个。
        key.project_id = body.project_id
        key.allowed_tools = None
    if body.reset_tools:
        key.allowed_tools = None
    elif body.allowed_tools is not None:
        key.allowed_tools = _validate_tools(body.allowed_tools)
    await session.commit()

    # 中间件按 key_hash 缓存了范围，改完要立刻失效，否则最长 30s 还是旧范围
    from app.mcp.middleware import invalidate_scope_cache

    invalidate_scope_cache(key.key_hash)

    return {"data": {
        "id": str(key.id),
        "name": key.name,
        "projectId": str(key.project_id) if key.project_id else None,
        "allowedTools": key.allowed_tools,
    }}


@router.get("")
async def list_api_keys(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await session.execute(
        select(McpApiKey)
        .where(McpApiKey.user_id == current_user.id, McpApiKey.is_active == True)
        .order_by(McpApiKey.created_at.desc())
    )
    keys = result.scalars().all()
    return {"data": [{
        "id": str(k.id),
        "name": k.name,
        "prefix": k.key_prefix,
        "projectId": str(k.project_id) if k.project_id else None,
        "allowedTools": k.allowed_tools,
        "createdAt": k.created_at.isoformat(),
        "lastUsedAt": k.last_used_at.isoformat() if k.last_used_at else None,
    } for k in keys]}


@router.delete("/{key_id}")
async def revoke_api_key(
    key_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    key = await session.get(McpApiKey, key_id)
    if not key or key.user_id != current_user.id:
        return {"error": "Key not found"}
    key.is_active = False
    await session.commit()

    from app.mcp.middleware import invalidate_scope_cache

    invalidate_scope_cache(key.key_hash)
    return {"data": {"revoked": True}}


# ── 项目级工具范围 ────────────────────────────────────────────────
# 范围的心智是「**这个项目**允许 CC 干哪些活」，不是「这一把钥匙允许干哪些活」。
# 同一个项目发五把 Key 给五个人，范围本来就该是同一个 —— 所以设置落在项目上，
# 该项目下的所有 Key 一起生效，不用一把一把改、更不用为了换范围重新建 Key。
project_scope_router = APIRouter(
    prefix="/api/projects/{project_id}/mcp-scope", tags=["mcp-keys"]
)


def _match_profile(allowed: list[str] | None) -> str:
    """这份范围对应哪个档位。对不上任何一档就是 custom。

    只用来在页面上把「当前生效」标出来 —— 落库的永远是展开后的显式工具名列表，
    不存档位名。存档位名的话，日后改了档位定义，已有项目的范围会**悄悄变**。
    """
    from app.mcp.profiles import PROFILES

    if allowed is None:
        return "all"
    cur = set(allowed)
    for p in PROFILES:
        if p["tools"] and set(p["tools"]) == cur:
            return p["key"]
    return "custom"


@project_scope_router.get("")
async def get_project_scope(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    from app.mcp import TOOL_CATALOG

    project = await session.get(Project, project_id)
    if not project:
        raise NotFoundError(code="PROJECT_NOT_FOUND", message="项目不存在")

    n_keys = (await session.execute(
        select(func.count()).select_from(McpApiKey).where(
            McpApiKey.project_id == project_id,
            McpApiKey.is_active == True,  # noqa: E712
        )
    )).scalar_one()

    allowed = project.mcp_allowed_tools
    return {"data": {
        "allowedTools": allowed,
        "profileKey": _match_profile(allowed),
        "totalTools": len(TOOL_CATALOG),
        # 页面要说清"这一改会影响几把 Key" —— 改的是别人正在用的连接，
        # 不写出来的话人不知道自己动了多大的面。
        "keyCount": n_keys,
    }}


@project_scope_router.put("")
async def set_project_scope(
    project_id: uuid.UUID,
    body: ProjectScopeRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer")),
):
    project = await session.get(Project, project_id)
    if not project:
        raise NotFoundError(code="PROJECT_NOT_FOUND", message="项目不存在")

    if body.reset_tools:
        project.mcp_allowed_tools = None
    elif body.allowed_tools is not None:
        project.mcp_allowed_tools = _validate_tools(body.allowed_tools)
    await session.commit()

    # 缓存是按 key_hash 存的，这里改的是项目 —— 拿不到该项目所有 Key 的 hash 就
    # 全清。缓存本来就只有 30s TTL、条目数是 Key 数量级，全清的代价是下一次
    # tools/list 多打一次库；而漏清的代价是人在页面上改完、CC 那边还是旧范围，
    # 最难查的那类"改了没生效"。
    from app.mcp.middleware import invalidate_scope_cache

    invalidate_scope_cache()

    return {"data": {
        "allowedTools": project.mcp_allowed_tools,
        "profileKey": _match_profile(project.mcp_allowed_tools),
    }}
