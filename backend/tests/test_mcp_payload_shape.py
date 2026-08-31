"""MCP 出入参形状的封样 —— 都是 2026-08-31 从一次「CC 说连接断了」里挖出来的实账。

三件事各自的来由：

1. **入参被序列化成字符串**。日志里 14:51:00 一条 `dict_type` 校验失败：
   `lum_sync_orchestrated_scenario` 的 `reflections` 收到的是一整段 JSON **字符串**。
   这种失败对调用方是一次纯白烧的往返（几千字符的入参 + 报错），而且 61 个工具里
   凡有 dict/array 参数的都可能中。修在中间件里还原，判据只认 schema ——
   **不能靠"以 `{` 开头"猜**：`lum_create_api_node.body`、
   `lum_upsert_llm_mock_route.response_body` 本来就是 JSON 字符串，猜一次就被吃掉。

2. **`lum_get_sync_spec` 自我重复**。kind='all' 的响应 44,921 字符里有 21,085 是
   playbook 与 sections 一字不差的重复。CC 每轮开场必调这个工具。

3. **点名步骤的逗号契约**。全库 2647 个步骤名里 134 个自带半角逗号，
   "逗号分隔的名字"对它们必然失效，而错误提示让人去抄"逐字一致的名字"——
   抄回来照样失效，无解循环。所以点名首选序号。
"""
import json

import pytest

from app.mcp import mcp
from app.mcp.middleware import _accepts_json_shape, _revive_json_args
from app.mcp.tools.api_tests import _parse_step_picks, _resolve_step_picks
from app.mcp.tools.sync import get_sync_spec


# ── 1. 入参还原 ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_dict_param_revived_from_json_string():
    args = {"case_id": "x", "reflections": '{"verificationPoints": "验了两件事"}'}
    revived = await _revive_json_args("lum_sync_orchestrated_scenario", args)
    assert revived == ["reflections"]
    assert args["reflections"] == {"verificationPoints": "验了两件事"}


@pytest.mark.asyncio
async def test_array_param_revived():
    args = {"branch_id": "x", "changes": '[{"url": "/a", "method": "GET", "kind": "removed"}]'}
    revived = await _revive_json_args("lum_apply_endpoint_diff", args)
    assert revived == ["changes"]
    assert isinstance(args["changes"], list)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,param", [
    ("lum_create_api_node", "body"),                # 接口请求体，本来就是 JSON 字符串
    ("lum_upsert_llm_mock_route", "response_body"),  # mock 响应体，同理
])
async def test_string_params_are_never_touched(tool, param):
    """真正是字符串的参数一个字都不能动 —— 它们的合法值就以 `{` 开头。"""
    raw = '{"choices": [{"message": {"content": "hi"}}]}'
    args = {param: raw}
    assert await _revive_json_args(tool, args) == []
    assert args[param] == raw


@pytest.mark.asyncio
async def test_unparseable_string_left_for_pydantic():
    """解不动就原样往下走，让 pydantic 报它本来的错，别在中间件里另造一种错。"""
    args = {"reflections": "{这不是 JSON"}
    assert await _revive_json_args("lum_sync_orchestrated_scenario", args) == []
    assert args["reflections"] == "{这不是 JSON"


def test_accepts_json_shape_rejects_union_with_string():
    assert _accepts_json_shape({"type": "object"}) is True
    assert _accepts_json_shape({"type": ["array", "null"]}) is True
    assert _accepts_json_shape({"anyOf": [{"type": "object"}, {"type": "string"}]}) is False
    assert _accepts_json_shape({"type": "string"}) is False


@pytest.mark.asyncio
async def test_revival_runs_before_data_scope():
    """还原必须排在数据范围校验**之前** —— 那一层要在入参里翻 project_id/branch_id，
    还是字符串的话它翻不进去，等于对序列化过的入参整层失效。"""
    import inspect

    from app.mcp.middleware import ToolScopeMiddleware

    src = inspect.getsource(ToolScopeMiddleware.on_call_tool)
    assert src.index("_revive_json_args") < src.index("check_data_scope")


# ── 2. get_sync_spec 不再自我重复 ──────────────────────────────
@pytest.mark.asyncio
async def test_sync_spec_sections_are_names_not_bodies():
    r = await get_sync_spec("all")
    assert isinstance(r["sections"], list)
    for name in r["sections"]:
        assert isinstance(name, str) and "\n" not in name and len(name) < 40


@pytest.mark.asyncio
async def test_sync_spec_has_no_duplicated_section_body():
    r = await get_sync_spec("all")
    dumped = json.dumps(r, ensure_ascii=False)
    for name in r["sections"]:
        one = await get_sync_spec(name)
        head = one["playbook"].strip().splitlines()[0]
        assert dumped.count(head) == 1, f"「{name}」的正文在一次响应里出现了两遍"


# ── 3. 点名步骤 ────────────────────────────────────────────────
class _Step:
    def __init__(self, name, sort_order):
        self.name, self.sort_order = name, sort_order


STEPS = [
    _Step("前置: 平台管理员登录", 0),
    _Step("前置: 门禁——开关须为开(关掉则申请直接 active,本条整条落空)", 1),  # 名字自带逗号
    _Step("操作: 驳回申请", 2),
]


def test_pick_by_sort_order():
    names, missing = _resolve_step_picks(STEPS, _parse_step_picks("0,2"))
    assert missing == []
    assert names == [STEPS[0].name, STEPS[2].name]


def test_pick_name_with_comma_needs_json_array():
    target = STEPS[1].name
    # 老契约：逗号分隔 —— 名字被切两半，两半都匹配不上
    names, missing = _resolve_step_picks(STEPS, _parse_step_picks(target))
    assert names == [] and len(missing) == 2
    # 新出口：JSON 数组
    names, missing = _resolve_step_picks(STEPS, _parse_step_picks(json.dumps([target], ensure_ascii=False)))
    assert missing == [] and names == [target]


def test_picks_keep_scenario_order():
    names, _ = _resolve_step_picks(STEPS, _parse_step_picks("2,0"))
    assert names == [STEPS[0].name, STEPS[2].name]


def test_bad_pick_is_reported_not_silently_dropped():
    names, missing = _resolve_step_picks(STEPS, _parse_step_picks("操作: 不存在的一步"))
    assert names == [] and missing == ["操作: 不存在的一步"]


@pytest.mark.asyncio
async def test_get_api_test_tool_description_points_at_sort_order():
    """描述里必须明说"首选序号"——不然 CC 只会按名字点，遇上带逗号的名字就卡死。"""
    tool = await mcp.get_tool("lum_get_api_test")
    assert "序号" in tool.description and "JSON 数组" in tool.description


@pytest.mark.asyncio
async def test_no_tool_param_is_wide_open():
    """没有哪个参数的 schema 是"什么都收"。

    `Any` 标注在这条链上有双重代价：pydantic 不校验（错形状一路混到函数体里才炸，
    错误长得像平台坏了），而上面那套按 schema 判形状的还原也够不到它（连 type 都没有）。
    2026-08-31 有 3 个这样的参数（submit_analysis.evidence、
    upsert_automation_resource.exists_check/create_def），已改标 dict。
    """
    loose = [
        f"{t.name}.{p}"
        for t in await mcp._list_tools()
        for p, spec in ((t.parameters or {}).get("properties") or {}).items()
        if not (set(spec) - {"title", "description", "default"})
    ]
    assert loose == [], f"这些参数的 schema 什么都收，标上真实类型：{loose}"
