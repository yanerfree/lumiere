"""MCP 中间件 —— 按 API Key 限定该连接能看到什么工具、能碰哪个项目的数据。

两层，各自独立：
  · **工具范围**（哪些 lum_* 露出来）—— 由 Key 归属项目的 `mcp_allowed_tools` 决定。
  · **数据范围**（能读写哪个项目）—— 由 Key 的 `project_id` 决定，见 `_OWNER_SQL`。
    这一层此前**整层不存在**：Key 上的 project_id 只用来查工具范围，
    工具入参里的 project_id/branch_id/case_id 是调用方随便填的，直接拿去查库。
    实测拿一把 A 项目的 Key 能列出全部项目、能改 B 项目的用例。
    HTTP 侧同样的坑早堵过（见 app/deps/scope.py 开头那段实测记录），MCP 侧漏了。

为什么需要：平台注册了 30+ 个工具，外部 Claude Code 面对全量列表容易挑错
（当年的典型：该做"活体验证后回推"的场景，却去调 lum_generate_api_test 凭文档造 ——
那个工具已下线，但挑错这件事本身不会随它消失）。
instructions 里的引导是**软约束**，模型不一定听；这里做成**硬约束**——
范围外的工具在 tools/list 里根本不出现，直接 tools/call 也会被拒。

身份获取方式：不走 contextvar。FastMCP 的 streamable-http 用 session manager，
工具执行不一定在 HTTP 请求那个 task 里，contextvar 未必能传到。改用
`get_http_headers()` 读当前请求头——注意它**默认会剥掉 authorization**，
必须显式 include。
"""
from __future__ import annotations

import hashlib
import time
import uuid

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware

# key_hash -> (allowed_tools|None, user_id|None, key_name|None, project_id|None, 写入时间)。
# allowed=None 表示不限制工具；project_id=None 表示不限制数据范围。
# tools/list 每次连接都会调，加个短 TTL 缓存避免频繁打库。
#
# user_id 一起缓存：Key 上本来就有它，此前只取 allowed_tools 就把整行扔了，
# 于是所有人的回推 created_by 全记成同一个 admin —— 多人一起用时，
# 操作日志失去意义，「CC归因 vs 人确认」也没法按人分桶。**这段历史数据事后补不回来。**
#
# key_name 也一起缓存：建 Key 的接口写死 `user_id=current_user.id`（只能给自己建），
# 所以所有 CC 的 Key 归属人都是同一个（admin），光靠 user_id 分不出是哪台 CC。
# Key 名是人写的（"uag-cc使用"、"小李的开发机"），它才是那条连接的身份。
#
# project_id 也一起缓存：数据范围校验挂在每一次 tools/call 上，是最热的那条路，
# 不能为它再打一次库。
_CACHE: dict[str, tuple[list[str] | None, str | None, str | None, str | None, float]] = {}
_TTL_SECONDS = 30


def pick_scope(
    project_id, project_scope: list | None, legacy_scope: list | None
) -> list | None:
    """一把 Key 到底按哪份范围跑。返回 None = 不限制。

    判据是**有没有归属项目**，不是"项目范围真不真"。
    写成 `project_scope or legacy_scope` 是这里最自然也最错的写法：项目明确
    设成不限制（NULL）时，那个写法会掉回 Key 上那份旧范围 —— 等于把人刚放开的
    权限又悄悄收回去，而页面上完全看不出为什么。

    抽成纯函数是为了能直接测这条判据，不用去正则匹配源码。
    """
    raw = project_scope if project_id else legacy_scope
    return [str(t) for t in raw] if raw else None


def invalidate_scope_cache(key_hash: str | None = None) -> None:
    """Key 的工具范围被改动后调用，让缓存立刻失效（不传则全清）。"""
    if key_hash is None:
        _CACHE.clear()
    else:
        _CACHE.pop(key_hash, None)


async def _lookup_key() -> tuple[list[str] | None, str | None, str | None, str | None]:
    """返回 (工具白名单, 调用方 user_id, Key 名, 归属 project_id)。None = 该维度不限制。

    没有 bearer（环境变量 key 走的是 MCPAuthMiddleware 那条）→ 全 None：
    那条路子不是"某个 Key"，没有项目可归，与放行口径保持一致。
    **注意匿名已经进不来了**（MCPAuthMiddleware 现在一律 401），
    所以这里的全 None 实际只剩 env key 一种情形。
    """
    headers = get_http_headers(include={"authorization"})
    auth = headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None, None, None, None
    token = auth[7:].strip()
    if not token:
        return None, None, None, None

    key_hash = hashlib.sha256(token.encode()).hexdigest()
    hit = _CACHE.get(key_hash)
    # TTL 取 **最后一位**，不写死下标：这个元组已经加过两次字段（Key 名、归属项目），
    # 每次都要把 TTL 的下标跟着挪，而写错不会报错 —— `monotonic() - "uag-cc使用"`
    # 抛的 TypeError 被外层 except 吞掉，症状是"缓存静默失效、每次都查库"。
    if hit and (time.monotonic() - hit[-1]) < _TTL_SECONDS:
        return hit[0], hit[1], hit[2], hit[3]

    allowed: list[str] | None = None
    user_id: str | None = None
    key_name: str | None = None
    key_project: str | None = None
    try:
        from sqlalchemy import select

        from app.deps.db import async_session_factory
        from app.models.mcp_api_key import McpApiKey
        from app.models.project import Project

        async with async_session_factory() as session:
            # LEFT JOIN：Key 归属项目 → 用项目的范围；没归属（存量 Key）→ 用 Key 自己那份。
            # 一次查询取完，别拆成两次 —— 这条在连接热路径上。
            result = await session.execute(
                select(
                    Project.mcp_allowed_tools,
                    McpApiKey.allowed_tools,
                    McpApiKey.user_id,
                    McpApiKey.project_id,
                    McpApiKey.name,
                )
                .select_from(McpApiKey)
                .join(Project, Project.id == McpApiKey.project_id, isouter=True)
                .where(
                    McpApiKey.key_hash == key_hash,
                    McpApiKey.is_active == True,  # noqa: E712
                )
            )
            row = result.first()
            # 查不到（环境变量 key 等）→ 不限制；查到但范围为 NULL → 不限制
            if row:
                project_scope, legacy_scope, uid, project_id, name = row
                allowed = pick_scope(project_id, project_scope, legacy_scope)
                if uid:
                    user_id = str(uid)
                key_name = name or None
                key_project = str(project_id) if project_id else None
    except Exception:
        # 查库失败不能把 MCP 打死，退化为不限制
        return None, None, None, None

    _CACHE[key_hash] = (allowed, user_id, key_name, key_project, time.monotonic())
    return allowed, user_id, key_name, key_project


async def _lookup_allowed_tools() -> list[str] | None:
    return (await _lookup_key())[0]


async def current_caller_user_id() -> str | None:
    """当前 MCP 调用方的用户 id（由其 API Key 决定）。拿不到返回 None。

    工具落库时用它填 created_by / executed_by —— 记成别人比不记还糟。
    """
    try:
        return (await _lookup_key())[1]
    except Exception:  # noqa: BLE001
        return None


async def current_caller_key_name() -> str | None:
    """当前 MCP 调用方那把 Key 的名字。拿不到返回 None。

    审计日志的「操作来源」用它：user_id 说不出是哪台 CC（Key 都是一个人建的），
    Key 名说得出。日志里存的是**名字快照**，Key 删了也还认得出。
    """
    try:
        return (await _lookup_key())[2]
    except Exception:  # noqa: BLE001
        return None


# ── 数据范围：入参里的 id 到底归哪个项目 ──────────────────────────
#
# 参数名 → 反查它归属哪个项目的候选 SQL。**一个名字可以有多条候选**：
# `folder_id` 在 lum_list_cases 里是用例目录（case_folders），在 lum_list_api_tests 里
# 是接口场景目录（api_test_folders）—— 同名不同表。按 (工具名, 参数名) 硬编码
# 会在新增工具时漏掉，所以改成**任一候选查到就算它**，全部候选都查不到才当"不是本项目的"。
#
# 用 text() 而不是 ORM：这几条都是两三段的 join 链，写成 ORM 要 import 一堆模型，
# 而这里在每一次 tools/call 的热路径上。
_OWNER_SQL: dict[str, tuple[str, ...]] = {
    "project_id": ("select id from projects where id = :v",),
    "branch_id": ("select project_id from branches where id = :v",),
    "case_id": (
        "select b.project_id from cases c join branches b on b.id = c.branch_id where c.id = :v",
    ),
    "case_ids": (
        "select b.project_id from cases c join branches b on b.id = c.branch_id where c.id = :v",
    ),
    "source_case_id": (
        "select b.project_id from cases c join branches b on b.id = c.branch_id where c.id = :v",
    ),
    "folder_id": (
        "select b.project_id from case_folders f join branches b on b.id = f.branch_id where f.id = :v",
        "select b.project_id from api_test_folders f join branches b on b.id = f.branch_id where f.id = :v",
    ),
    "plan_id": ("select project_id from plans where id = :v",),
    "report_id": ("select project_id from test_reports where id = :v",),
    "scenario_id": (
        "select b.project_id from api_test_scenarios s join branches b on b.id = s.branch_id where s.id = :v",
    ),
    "scenario_ids": (
        "select b.project_id from api_test_scenarios s join branches b on b.id = s.branch_id where s.id = :v",
    ),
    "node_id": ("select project_id from api_nodes where id = :v",),
    "parent_id": ("select project_id from api_nodes where id = :v",),
    # 环境 2026-08-21 起是项目级的（迁移 zzo0envproj）。挪进来之前它在 _OWNER_EXEMPT 里，
    # 理由是"反查不到项目" —— 现在查得到了，豁免也就没理由继续留着。
    "env_id": ("select project_id from environments where id = :v",),
    "environment_id": ("select project_id from environments where id = :v",),
    "run_id": (
        "select b.project_id from script_runs r join cases c on c.id = r.case_id "
        "join branches b on b.id = c.branch_id where r.id = :v",
    ),
    # 审核批次自己带 project_id。不校的话 A 项目的 Key 能拿 B 项目的 batchId
    # 读出逐条结论和用例编号 —— 和当初"随便填 branch_id 就能改别人用例"同一个洞。
    "batch_id": ("select project_id from review_batches where id = :v",),
}

# 故意**不**校验的 id 参数。每一条都得写清为什么，否则下一个人只会以为是漏了。
_OWNER_EXEMPT: dict[str, str] = {
    "skill_id": (
        "跨项目取用 skill 的正规通道。lum_pull_skill 的描述里明写「skill_id(推荐,跨项目取用用它,"
        "要求该 skill 是 public)」—— 校了等于把 skill 共享整个打死。"
        "同一个工具的 project_id 入参照常受校验（那条是「取自己项目的」）。"
    ),
}


def scope_targets(arguments: dict | None) -> list[tuple[str, str]]:
    """从一次 tools/call 的入参里挑出所有"能定位到项目"的 id，返回 [(参数名, id 字符串)]。

    抽成纯函数是为了能直接测，不用起 MCP、不用连库。三件事在这里做完：
      · 只认 `_OWNER_SQL` 里的参数名，其余（title/steps/keyword…）一概不看；
      · 复数形态既可能是 list 也可能是逗号分隔的字符串
        （lum_run_api_test 的 scenario_ids 描述就是「逗号分隔的场景UUID列表」）；
      · **不是合法 UUID 的值直接丢掉**，不当成"查不到"去拒 ——
        那种是调用方参数写错，该由工具自己报错，不该被伪装成权限问题。
    """
    out: list[tuple[str, str]] = []
    for name, raw in (arguments or {}).items():
        if name not in _OWNER_SQL or raw is None:
            continue
        values = raw if isinstance(raw, (list, tuple, set)) else str(raw).split(",")
        for v in values:
            v = str(v).strip()
            if not v:
                continue
            try:
                out.append((name, str(uuid.UUID(v))))
            except (ValueError, AttributeError, TypeError):
                continue
    return out


async def _owner_project(session, param: str, value: str) -> str | None:
    """这个 id 归哪个项目。查不到 / 归属为空 → None。"""
    from sqlalchemy import text

    for sql in _OWNER_SQL[param]:
        got = (await session.execute(text(sql), {"v": value})).scalar_one_or_none()
        if got is not None:
            return str(got)
    return None


async def check_data_scope(key_project: str, arguments: dict | None) -> tuple[str, str] | None:
    """入参里有 id 不属于 key_project 就返回那一条 (参数名, id)；全都合规返回 None。

    **查不到归属也算不合规。** 理由：本来就该拒的两种情况在这里长得一样 ——
    id 根本不存在，或者它属于别的项目。放行"查不到"的那种等于留个后门：
    随便编一个 UUID 就能绕过校验、让工具自己去查库。
    """
    targets = scope_targets(arguments)
    if not targets:
        return None
    from app.mcp.deps import get_mcp_session

    async with get_mcp_session() as session:
        for param, value in targets:
            if await _owner_project(session, param, value) != key_project:
                return param, value
    return None


async def current_caller_project_id() -> str | None:
    """当前 MCP 调用方那把 Key 归属的项目 id。None = 不限制数据范围。

    `lum_list_projects` 用它把列表收窄到本项目 —— 那个工具没有任何 id 入参，
    `_OWNER_SQL` 那套反查管不到它，只能它自己问。
    """
    try:
        return (await _lookup_key())[3]
    except Exception:  # noqa: BLE001
        return None


class ToolScopeMiddleware(Middleware):
    """按 Key 过滤工具列表 + 拦截越权调用。"""

    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        allowed = await _lookup_allowed_tools()
        if allowed is None:
            return tools
        allowed_set = set(allowed)
        return [t for t in tools if t.name in allowed_set]

    async def on_call_tool(self, context, call_next):
        # 审计上下文只在 HTTP 认证依赖（deps/auth.py）里设过，**MCP 这条路整条漏了** ——
        # 于是 CC 改的每一条用例在「操作日志」里操作人和所属项目都是「-」。
        # 一份说不出是谁干的日志不叫审计日志：现在写库的主力是 CC，
        # 分不出哪些是它改的、哪些是人改的，出问题就只能靠猜。
        #
        # 身份本来就有（Key 决定，script_runs 的 executed_by 一直在用它），
        # 只是审计那条路从没问过它。挂在 on_call_tool 上 = 所有 lum_* 工具一次性覆盖。
        #
        # 一次 _lookup_key 同时供审计和两层范围校验用（本来就带 30s 缓存，但没必要查三遍）。
        allowed, uid, key_name, key_project = await _lookup_key()
        try:
            from app.core.audit import set_audit_context
            set_audit_context(user_id=uuid.UUID(uid) if uid else None,
                              trace_id=f"mcp:{context.message.name}",
                              # 来源固定 mcp；label 是那把 Key 的名字 ——
                              # 页面上「admin · via uag-cc使用」才分得出哪台 CC。
                              # user_id 分不出这件事：Key 只能给自己建，全是同一个人。
                              actor_type="mcp",
                              actor_label=key_name)
        except Exception:  # noqa: BLE001
            pass  # 记账绝不能把 MCP 调用打死

        # 必须单独拦一道：从 tools/list 里藏起来 ≠ 不能直接调
        if allowed is not None and context.message.name not in set(allowed):
            raise ToolError(
                f"工具 {context.message.name} 不在本项目的 MCP 工具范围内。"
                "如需使用，请在 Lumiere「MCP 工具中心 → 工具范围」调整 —— "
                "范围是项目级的，改一次本项目所有 Key 都生效，不用重新建 Key。"
            )

        # 数据范围：入参里的 id 必须属于这把 Key 归属的项目。
        # 归属为 NULL 的存量 Key 不限制 —— 跟 pick_scope 那条判据同一个口径
        # （"判据是有没有归属项目"）。上线前把在用的 Key 都归好项目，
        # 否则这层对它们等于没开。
        if key_project:
            try:
                bad = await check_data_scope(key_project, context.message.arguments)
            except Exception:  # noqa: BLE001
                # 校验本身出错（库抖了、表名写错了）不能把 MCP 打死。
                # 这里**故意 fail open**：这一层防的是 CC 挑错项目，
                # 不是防攻击者 —— 为它把整条通道弄挂，代价比漏一次大。
                bad = None
            if bad:
                param, value = bad
                raise ToolError(
                    f"{param}={value} 不属于本 Key 绑定的项目（或不存在）。"
                    "一把 Key 只能操作它归属的那一个项目 —— 要动别的项目，"
                    "用那个项目自己的 Key，不要改入参硬试。"
                    "本项目有哪些分支/用例，用 lum_list_branches、lum_list_cases 查。"
                )
        return await call_next(context)
