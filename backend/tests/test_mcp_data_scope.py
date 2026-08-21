"""MCP 数据范围的封样 —— 一把 Key 只能碰它归属的那个项目。

这一层此前**整层不存在**：Key 的 project_id 只用来选工具范围，工具入参里的
project_id / branch_id / case_id 是调用方随便填的，直接拿去查库。实测（2026-08-21）
不带任何凭据就能 initialize，再 tb_list_projects 列出全部 6 个项目、
tb_list_branches 往下读到任意项目 —— 匿名和越权两件事叠在一起。

背景和方案：docs/data-scoping-and-isolation.md
"""
import inspect
import re

import pytest

from app.mcp import TOOL_CATALOG
from app.mcp.middleware import (
    _OWNER_EXEMPT,
    _OWNER_SQL,
    check_data_scope,
    scope_targets,
)

PA = "11111111-1111-1111-1111-111111111111"
PB = "22222222-2222-2222-2222-222222222222"
X = "33333333-3333-3333-3333-333333333333"


def _id_params() -> set[str]:
    out = set()
    for t in TOOL_CATALOG:
        for p in (x.strip() for x in t["params"].split(",")):
            if p.endswith("_id") or p.endswith("_ids"):
                out.add(p)
    return out


# ── ① 覆盖不全就红 ────────────────────────────────────────────────

def test_每个id入参都必须明确归类():
    """新加一个带 *_id 入参的工具、忘了决定它要不要校验 —— 这条会红。

    这是整层的地基：漏一个参数名，那个参数就成了绕过校验的通道
    （工具照常执行，只是没人问过那个 id 属于谁）。
    """
    missing = sorted(_id_params() - set(_OWNER_SQL) - set(_OWNER_EXEMPT))
    assert not missing, (
        "以下 id 入参既没有反查规则、也没写豁免理由，等于静默放过：\n"
        + "\n".join("  " + m for m in missing)
        + "\n要么加进 _OWNER_SQL，要么加进 _OWNER_EXEMPT 并写清为什么。"
    )


def test_确实扫到了足够多的入参():
    """防的是选择器坏掉 —— 一个都没匹配到时，上面那条会安静地全绿。"""
    assert len(_id_params()) >= 15, len(_id_params())


def test_豁免必须写理由():
    """不写理由的豁免，下一个人只会当成"漏了"然后补上去 —— 而补上去会出事（见下一条）。"""
    for k, why in _OWNER_EXEMPT.items():
        assert why and len(why) > 20, f"{k} 的豁免理由太短或为空"


def test_skill_id必须豁免():
    """★ tb_pull_skill 的 skill_id 是**跨项目取用的正规通道**（要求 skill 是 public）。

    把它"补全"进 _OWNER_SQL 会把 skill 共享整个打死，而且症状是
    "别的项目的 skill 突然拉不下来了"，很难联想到这里。
    """
    assert "skill_id" in _OWNER_EXEMPT
    assert "skill_id" not in _OWNER_SQL


@pytest.mark.parametrize("param", ["env_id", "environment_id"])
def test_环境id受校验(param):
    """环境 2026-08-21 起是项目级的（迁移 zzo0envproj），所以它不再豁免。

    两个名字都得在：tb_create_plan 用的是 environment_id，其余工具用 env_id，
    指的是同一张表 —— 只挪一个等于留半个口子。
    环境里存着 BASE_URL、账号、密码，漏掉它比漏掉用例更贵。
    """
    assert param in _OWNER_SQL, f"{param} 必须受校验"
    assert param not in _OWNER_EXEMPT


# ── ② 反查 SQL 指向的表/列真的存在 ────────────────────────────────

def test_反查SQL里的表都真的存在():
    """SQL 是字符串，表名写错不会有任何编译期提示 —— 而 check_data_scope 是 fail open 的，
    写错的后果是**这一层静默失效**（每次查库抛异常、每次都放行）。
    """
    import app.models  # noqa: F401  确保全部模型都注册进 metadata
    from app.models.user import Base

    known = set(Base.metadata.tables)
    used = set()
    for sqls in _OWNER_SQL.values():
        for sql in sqls:
            used |= set(re.findall(r"(?:from|join)\s+(\w+)", sql, re.I))
    unknown = sorted(used - known)
    assert not unknown, f"反查 SQL 引用了不存在的表: {unknown}"


# ── ③ 入参解析（纯函数） ──────────────────────────────────────────

@pytest.mark.parametrize("args,expect,why", [
    ({"branch_id": X}, [("branch_id", X)], "单个 id"),
    ({"case_ids": [X, PA]}, [("case_ids", X), ("case_ids", PA)], "list 形态"),
    ({"scenario_ids": f"{X},{PA}"}, [("scenario_ids", X), ("scenario_ids", PA)],
     "★逗号分隔的字符串 —— tb_run_api_test 的 scenario_ids 就是这个形态"),
    ({"title": X, "keyword": "abc"}, [], "不在表里的参数一概不看"),
    ({"branch_id": None}, [], "None 跳过（可选参数没传）"),
    ({"branch_id": "not-a-uuid"}, [],
     "★不是 UUID 就丢掉 —— 那是调用方参数写错，该由工具自己报错，不该伪装成权限问题"),
    ({"case_ids": f"{X}, ,{PA}"}, [("case_ids", X), ("case_ids", PA)], "空段落跳过"),
    ({}, [], "空入参"),
    (None, [], "arguments 是 None"),
])
def test_挑得出该校验的id(args, expect, why):
    assert scope_targets(args) == expect, why


# ── ④ 判定本身 ────────────────────────────────────────────────────

class _FakeSession:
    """按 (sql 片段 -> 归属项目) 应答。None 表示查不到。"""

    def __init__(self, answers: list):
        self.answers = list(answers)
        self.calls = 0

    async def execute(self, stmt, params=None):
        self.calls += 1
        got = self.answers.pop(0) if self.answers else None

        class _R:
            @staticmethod
            def scalar_one_or_none():
                return got
        return _R()


def _patch_session(monkeypatch, session):
    from contextlib import asynccontextmanager

    import app.mcp.deps as deps

    @asynccontextmanager
    async def fake():
        yield session

    monkeypatch.setattr(deps, "get_mcp_session", fake)


@pytest.mark.asyncio
async def test_归属对得上就放行(monkeypatch):
    _patch_session(monkeypatch, _FakeSession([PA]))
    assert await check_data_scope(PA, {"branch_id": X}) is None


@pytest.mark.asyncio
async def test_归属别的项目就拦(monkeypatch):
    _patch_session(monkeypatch, _FakeSession([PB]))
    assert await check_data_scope(PA, {"branch_id": X}) == ("branch_id", X)


@pytest.mark.asyncio
async def test_查不到归属也算不合规(monkeypatch):
    """★ 放行"查不到"等于留后门：随便编一个 UUID 就能绕过校验、让工具自己去查库。

    而且该拒的两种情况在这里长得一样 —— id 不存在，或者它属于别的项目。
    """
    _patch_session(monkeypatch, _FakeSession([None]))
    assert await check_data_scope(PA, {"branch_id": X}) == ("branch_id", X)


@pytest.mark.asyncio
async def test_folder_id两张表任一命中就算(monkeypatch):
    """★ 同一个参数名在两个工具里指不同的表：tb_list_cases 的 folder_id 是
    case_folders，tb_list_api_tests 的是 api_test_folders。

    只查第一张表的话，tb_list_api_tests 会被整个打死（它的 folder_id
    在 case_folders 里永远查不到 → 被当成"不是本项目的"）。
    """
    assert len(_OWNER_SQL["folder_id"]) == 2, "folder_id 必须有两条候选"
    # 第一张表查不到、第二张查到 → 放行
    _patch_session(monkeypatch, _FakeSession([None, PA]))
    assert await check_data_scope(PA, {"folder_id": X}) is None


@pytest.mark.asyncio
async def test_没有可校验的id就不查库(monkeypatch):
    """大量工具的入参里没有任何 id（tb_llm_mock_status 等），别为它们白打一次库。"""
    sess = _FakeSession([])
    _patch_session(monkeypatch, sess)
    assert await check_data_scope(PA, {"path": "/v1/x", "limit": 10}) is None
    assert sess.calls == 0


@pytest.mark.asyncio
async def test_多个id里有一个不合规就拦(monkeypatch):
    """tb_sync_orchestrated_scenario 这类同时带 branch_id 和 source_case_id 的，
    任一条不属于本项目都得拦。"""
    _patch_session(monkeypatch, _FakeSession([PA, PB]))
    bad = await check_data_scope(PA, {"branch_id": X, "source_case_id": PB})
    assert bad is not None


# ── ⑤ 两处只能靠结构钉住的 ────────────────────────────────────────

def test_没有匿名通道():
    """★ MCPAuthMiddleware 以前有一条「没带 bearer 且 MCP_API_KEY 未设 → 放行」。

    MCP_API_KEY 从来没设过，于是那个口子一直全开。这条钉住它不许回来 ——
    尤其不许再写成"靠环境变量兜底"，.env 一丢就静默恢复成全开。
    """
    from app.main import MCPAuthMiddleware

    src = inspect.getsource(MCPAuthMiddleware.__call__)
    body = src[src.index("if not bearer_token:"):]
    head = body.split("if self.env_key")[0]
    assert "_deny" in head, "没带 bearer 必须直接拒"
    assert "await self.app(" not in head, "没带 bearer 的分支里不许有放行"


def test_列项目的工具自己过滤():
    """它一个入参都没有 —— 中间件那套按 id 反查的校验管不到它，只能它自己问。

    不过滤的后果不是"多看见几行"：它的描述写着「用于确定要操作的目标项目」，
    等于把全部项目摆到 CC 面前请它自己挑，挑错就往别人项目里写。
    """
    from app.mcp.tools import projects

    src = inspect.getsource(projects.list_projects)
    assert "current_caller_project_id" in src
    assert "Project.id ==" in src


def test_调用工具时真的会走数据范围校验():
    """中间件里把校验漏掉、或者只在 on_list_tools 里做，都会让这层形同虚设。"""
    from app.mcp.middleware import ToolScopeMiddleware

    src = inspect.getsource(ToolScopeMiddleware.on_call_tool)
    assert "check_data_scope" in src
    assert "key_project" in src
