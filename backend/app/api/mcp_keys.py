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

from app.core import permissions as perms
from app.schemas.common import BaseSchema

from app.deps.db import get_db
from app.deps.auth import get_current_user, require_project_role
from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.permissions import canonical_project_role
from app.models.user import User
from app.models.mcp_api_key import McpApiKey
from app.models.project import Branch, Project, ProjectMember

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp-keys", tags=["mcp-keys"])


class CreateKeyRequest(BaseSchema):
    name: str = Field(default="default", max_length=100)
    # Key 归属的项目。它决定**数据范围**（能读写哪个项目），也提供工具范围的天花板
    # （projects.mcp_allowed_tools）。见 CLAUDE.md 硬规则：建 Key 必须绑项目。
    project_id: uuid.UUID | None = None
    # Key 级收窄：不传/null = 跟随项目范围（默认）；列表 = 在项目天花板内只给这几个。
    # ⚠ `[]` 是"一个工具都不给"，不是"不限制"。
    allowed_tools: list[str] | None = None


class UpdateKeyRequest(BaseSchema):
    name: str | None = Field(default=None, max_length=100)
    # 把一把未归属的存量 Key 归到某个项目（归了之后项目范围成为它的天花板）
    project_id: uuid.UUID | None = None
    allowed_tools: list[str] | None = None
    # 显式区分"不改 allowed_tools"和"改成跟随项目"——前者不传该字段，
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


async def _scope_views(session, keys: list[McpApiKey]) -> dict:
    """给每把 Key 算一份「范围视图」：生效了哪些、被项目挡掉了哪些、跟不跟项目。

    页面不能只显示 Key 上勾的那份：生效范围是**交集**，勾了项目天花板外的工具
    会被丢掉，而"我勾了 20 个、实际只生效 12 个"这件事不显示出来就完全看不出来
    —— 人只会以为是 Key 坏了。

    项目范围一次查完。列表页有几把 Key 就查几次的话就是 N+1，
    而这条接口每次进「MCP 工具」页都要打。
    """
    from app.mcp import TOOL_CATALOG
    from app.mcp.middleware import blocked_by_project, pick_scope

    known = {t["name"] for t in TOOL_CATALOG}
    pids = {k.project_id for k in keys if k.project_id}
    proj_scope: dict = {}
    if pids:
        rows = await session.execute(
            select(Project.id, Project.mcp_allowed_tools).where(Project.id.in_(pids)))
        proj_scope = {pid: scope for pid, scope in rows}

    out: dict = {}
    for k in keys:
        pscope = proj_scope.get(k.project_id)
        eff = pick_scope(pscope, k.allowed_tools)
        out[k.id] = {
            "effectiveTools": eff,
            # None = 不限制 → 生效数就是全量。别回 0，页面上「生效 0 / 63」
            # 和"这把 Key 什么都干不了"长得一模一样。
            "effectiveCount": len(known) if eff is None else len(eff),
            "totalTools": len(known),
            # NULL 才叫跟随项目；`[]` 是本 Key 主动收成空的
            "followsProject": k.allowed_tools is None,
            "blockedByProject": blocked_by_project(pscope, k.allowed_tools),
            # 存进去之后工具改名/下线了 —— 名单会静默变窄（那条工具永远不出现），
            # 所以要在 Key 这一行上就说出来，别只在项目那一层提示。
            "staleTools": [n for n in (k.allowed_tools or []) if n not in known],
        }
    return out


# 绑定 Key 到项目 = 给这把 Key 该项目用例/环境的**读写**数据范围（见 CLAUDE.md 硬规则：
# Key 的 project_id 现在同时管工具范围和数据范围）。project_id 走 body 不走 path，
# 所以 require_project_role 那个按 path 取 {project_id} 的依赖用不上——在这里手写同一套判定。
# 允许的角色对齐写口径（不含 guest）：一把能写的 Key 不该由只读成员发出去。
_BIND_ROLES = perms.TIER_WRITE


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
    # 走规范名匹配，新旧名互认（同 require_project_role）。游客不在这里挡 —— 挡它的是
    # deps/auth 的非 GET 闸门（本端点是 POST）；这里只管「项目内档位够不够」。
    allowed = {canonical_project_role(r) for r in _BIND_ROLES}
    if canonical_project_role(member.role) not in allowed:
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

    view = (await _scope_views(session, [api_key]))[api_key.id]
    return {"data": {
        "id": str(api_key.id),
        "name": api_key.name,
        "key": raw_key,
        "prefix": key_prefix,
        "projectId": str(api_key.project_id) if api_key.project_id else None,
        "allowedTools": api_key.allowed_tools,
        "createdAt": api_key.created_at.isoformat(),
        # 建完立刻把「实际生效几个」回给页面。只回 allowedTools 的话，勾了项目
        # 天花板外的工具时页面会照着自己勾的那份显示，而连上去少一批 —— 那种
        # 不一致要等到 CC 抱怨"没有这个工具"才发现。
        "scope": view,
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
        # 归到某个项目后，该项目的范围成为这把 Key 的**天花板**，Key 自己那份
        # 继续作为收窄留着 —— 生效 = 交集，两份同时显示在页面上。
        #
        # 2026-09-03 之前这里会 `key.allowed_tools = None`，理由是"两个来源必须
        # 只剩一个"。那个理由在"只显示项目范围"的页面上成立，但代价是**换个项目
        # 就把人挑好的工具清空**，而且不提示。现在生效范围和被挡掉的工具都回给
        # 页面了（`_scope_views`），一个来源的诉求由呈现解决，不靠删数据。
        key.project_id = body.project_id
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
        "scope": (await _scope_views(session, [key]))[key.id],
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
    keys = list(result.scalars().all())
    views = await _scope_views(session, keys)
    return {"data": [{
        "id": str(k.id),
        "name": k.name,
        "prefix": k.key_prefix,
        "projectId": str(k.project_id) if k.project_id else None,
        "allowedTools": k.allowed_tools,
        "createdAt": k.created_at.isoformat(),
        "lastUsedAt": k.last_used_at.isoformat() if k.last_used_at else None,
        "scope": views[k.id],
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
    _: User = Depends(require_project_role(*perms.TIER_READ)),
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
    _: User = Depends(require_project_role(*perms.TIER_DOC_MANAGE)),
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
