"""封样：AI 助手工具目录 + 对话编排的不变量。

核心保证是那句话本身 —— **能力面 = 页面动作 ∩ 当前用户权限**。落到代码上有三条红线：
1. 每个工具挂的权限点必须是 core/permissions 真实存在的点（不能挂一个谁都拿不到的孤儿点，
   那样 admin 都看不见它，等于静默失效）；
2. 写操作必须挂权限点（permission=None 只留给纯读的 list_projects），
   且**项目级写操作对项目 viewer 一律不可见**（viewer 能看却不能改，才是漏洞）；
3. visible_tools 必须随角色单调递增，admin 恒为全集 —— 与 permissions_seal 同源。

外加 parse_proposal 只认可见集里的工具（模型编个名字出来也调不动），纯断言、不起服务。
"""
import uuid

import pytest

from app.core import permissions as perms
from app.core.exceptions import ValidationError
from app.services.assistant import catalog, runner


# ── 目录形状的不变量 ─────────────────────────────────────────────
def test_every_tool_permission_is_real_or_none():
    """工具挂的权限点要么是 None，要么真实存在于全集里 —— 挂个孤儿点连 admin 都看不见。"""
    for t in catalog.TOOLS:
        assert t.permission is None or t.permission in perms.ALL_PERMISSIONS, t.key


def test_write_tools_always_gated():
    """写操作（mutates）必须挂权限点；permission=None 只允许给纯读工具。"""
    for t in catalog.TOOLS:
        if t.mutates:
            assert t.permission is not None, f"{t.key} 是写操作却没挂权限点"


def test_tool_shape_is_wellformed():
    keys = [t.key for t in catalog.TOOLS]
    assert len(keys) == len(set(keys)), "工具 key 有重复"
    for t in catalog.TOOLS:
        assert t.scope in ("system", "project"), t.key
        assert isinstance(t.mutates, bool)
        assert t.handler is not None and callable(t.handler)
        assert t.label and t.description


def test_get_tool_roundtrips_and_unknown_is_none():
    for t in catalog.TOOLS:
        assert catalog.get_tool(t.key) is t
    assert catalog.get_tool("delete_everything") is None


# ── 权限门控 ─────────────────────────────────────────────────────
def test_tool_allowed_semantics():
    none_perm = next(t for t in catalog.TOOLS if t.permission is None)
    gated = next(t for t in catalog.TOOLS if t.permission is not None)
    assert catalog.tool_allowed(none_perm, frozenset()) is True   # 任意登录用户
    assert catalog.tool_allowed(gated, frozenset()) is False
    assert catalog.tool_allowed(gated, frozenset({gated.permission})) is True


def test_visible_tools_is_monotonic_by_role():
    """能力面随角色单调递增：viewer ⊆ tester ⊆ member ⊆ manager ⊆ admin(全集)。"""
    def keys(role):
        return {t.key for t in catalog.visible_tools(perms.resolve_permissions("user", role))}

    viewer, tester, member, manager = keys("viewer"), keys("tester"), keys("member"), keys("manager")
    admin = {t.key for t in catalog.visible_tools(perms.ALL_PERMISSIONS)}
    assert viewer <= tester <= member <= manager <= admin


def test_admin_sees_every_tool():
    """系统 admin（全集）能看见目录里的每一个工具。"""
    visible = {t.key for t in catalog.visible_tools(perms.ALL_PERMISSIONS)}
    assert visible == {t.key for t in catalog.TOOLS}


def test_project_viewer_cannot_mutate_project_data():
    """项目 viewer 看得见读操作，但**任何项目级写操作都不可见** —— 看得见却改不了才是漏洞。"""
    held = perms.resolve_permissions("user", "viewer")
    for t in catalog.visible_tools(held):
        if t.scope == "project":
            assert not t.mutates, f"viewer 不该看见项目级写操作 {t.key}"


def test_tester_gains_case_write_over_viewer():
    """具体回归点：viewer 看不到 create_case，tester 能 —— 权限差异确实反映到能力面。"""
    viewer = {t.key for t in catalog.visible_tools(perms.resolve_permissions("user", "viewer"))}
    tester = {t.key for t in catalog.visible_tools(perms.resolve_permissions("user", "tester"))}
    assert "create_case" not in viewer
    assert "create_case" in tester
    assert "run_plan" not in viewer
    assert "run_plan" in tester


# ── 入参校验 ─────────────────────────────────────────────────────
def test_coerce_args_required_and_types():
    create_env = catalog.get_tool("create_environment")
    with pytest.raises(ValidationError):
        catalog.coerce_args(create_env, {})  # 缺必填 name
    out = catalog.coerce_args(create_env, {"name": "staging", "extra": "dropped"})
    assert out == {"name": "staging"}  # 未声明的键丢弃

    run_plan = catalog.get_tool("run_plan")
    pid = uuid.uuid4()
    coerced = catalog.coerce_args(run_plan, {"plan_id": str(pid)})
    assert coerced["plan_id"] == pid  # 字符串 → UUID

    list_cases = catalog.get_tool("list_cases")
    assert catalog.coerce_args(list_cases, {"limit": "5"})["limit"] == 5  # 字符串 → int
    with pytest.raises(ValidationError):
        catalog.coerce_args(list_cases, {"limit": "abc"})


# ── 提议解析 ─────────────────────────────────────────────────────
def test_parse_proposal_accepts_visible_tool():
    tools = list(catalog.TOOLS)
    text = '我来建。\n\n```json\n{"tool":"create_environment","args":{"name":"staging"}}\n```'
    p = runner.parse_proposal(text, tools)
    assert p == {"tool": "create_environment", "args": {"name": "staging"}}


def test_parse_proposal_rejects_tool_outside_visible_set():
    """模型编的工具 / 不在可见集里的工具一律不认 —— 越权提议在解析层就死掉。"""
    only_reads = [t for t in catalog.TOOLS if not t.mutates]
    text = '```json\n{"tool":"create_environment","args":{"name":"x"}}\n```'
    assert runner.parse_proposal(text, only_reads) is None  # create_environment 不在只读集
    bogus = '```json\n{"tool":"drop_database","args":{}}\n```'
    assert runner.parse_proposal(bogus, list(catalog.TOOLS)) is None


def test_parse_proposal_no_block_and_last_wins():
    assert runner.parse_proposal("就是一句普通回答，没有代码块。", list(catalog.TOOLS)) is None
    two = (
        '```json\n{"tool":"list_cases","args":{}}\n```\n'
        '再想想，其实是：\n```json\n{"tool":"list_plans","args":{}}\n```'
    )
    assert runner.parse_proposal(two, list(catalog.TOOLS))["tool"] == "list_plans"


def test_parse_proposal_bare_json_fallback():
    # 兜底只保证「无嵌套的扁平对象」（模型漏了 ``` 围栏时的安全网）；
    # 带 args 嵌套的靠围栏路径，见 test_parse_proposal_accepts_visible_tool。
    text = '好的 {"tool": "list_projects"} 就这样'
    got = runner.parse_proposal(text, list(catalog.TOOLS))
    assert got["tool"] == "list_projects"
    assert got["args"] == {}


def test_parse_proposal_garbage_is_safe():
    for junk in ("", "```json\n{not valid}\n```", '```json\n[1,2,3]\n```', '```json\n"string"\n```'):
        assert runner.parse_proposal(junk, list(catalog.TOOLS)) is None


# ── 系统提示词 ───────────────────────────────────────────────────
def test_system_prompt_lists_only_given_tools():
    viewer_tools = catalog.visible_tools(perms.resolve_permissions("user", "viewer"))
    sp = runner.build_system_prompt(viewer_tools, None)
    assert "run_plan" not in sp        # viewer 无 run_plan
    assert "create_case" not in sp
    assert "list_cases" in sp
    for t in viewer_tools:
        assert t.key in sp


def test_system_prompt_empty_toolset_is_explicit():
    sp = runner.build_system_prompt([], None)
    assert "无" in sp  # 明确告知「没有可执行操作，只能回答」
