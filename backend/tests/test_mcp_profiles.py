"""MCP 工具档位的防漂移封样。

档位是拿 Key 的 allowed_tools 兜底的硬约束，一旦和真实注册表对不上，
后果是安静的：Key 建出来看着有范围，实际少一个工具，用到才发现调不动。
所以这几条要在 CI 里挡住，而不是等人在外部项目里踩。
"""
from app.mcp import TOOL_CATALOG
from app.mcp.profiles import PROFILES, uncovered_tools, unknown_tools

NAMES = {t["name"] for t in TOOL_CATALOG}


def test_档位里的工具名都真的注册过():
    """typo 或工具改名没同步 —— 这是最容易发生也最难发现的一种。"""
    assert unknown_tools(NAMES) == []


def test_每个注册工具至少属于一个档位():
    """没进任何档位 = 只能靠「全量」才用得上，那分档对它就没发生。

    新加工具时这条会红，提醒去想清楚"它是干哪件活的"。
    """
    assert uncovered_tools(NAMES) == []


def test_档位key不重复():
    keys = [p["key"] for p in PROFILES]
    assert len(keys) == len(set(keys))


def test_全量档位是不限制而不是列全部工具():
    """None 才表示不限制。列成全量清单的话，以后新增工具老 Key 就少一个。"""
    allp = next(p for p in PROFILES if p["key"] == "all")
    assert allp["tools"] is None


def test_每个档位都比全量小得多():
    """分档的收益就是这个数。任何一档接近全量就说明它没在分。"""
    for p in PROFILES:
        if p["tools"] is not None:
            assert len(p["tools"]) < len(NAMES) * 0.6, p["key"]


def test_活体验证档位不含凭文档造场景的工具():
    """实测踩过：CC 看到 tb_generate_api_test 觉得省事就走了它，
    正好绕开了"亲手跑一遍"。instructions 是软约束，这里才是墙。"""
    live = next(p for p in PROFILES if p["key"] == "live")
    assert "tb_generate_api_test" not in live["tools"]


def test_归因档位不含任何写用例或脚本的工具():
    """红线 3：CC 的归因不改任何状态。工具范围要把它兜死，不能只靠自觉。"""
    triage = next(p for p in PROFILES if p["key"] == "triage")
    forbidden = {
        "tb_create_case", "tb_sync_ui_script", "tb_sync_orchestrated_scenario",
        "tb_upsert_scenario_variables", "tb_create_api_node", "tb_run_plan",
    }
    assert not (set(triage["tools"]) & forbidden)


def test_每个档位都能定位到项目():
    """连项目都列不出来的档位，进去第一步就卡住。"""
    for p in PROFILES:
        if p["tools"] is not None:
            assert "tb_list_projects" in p["tools"], p["key"]


def test_每个档位都写清了这是干什么活的():
    for p in PROFILES:
        assert p.get("label") and p.get("task") and p.get("hint"), p["key"]


def test_每个分类在前端都有颜色():
    """分类漏登记不会报错，只会静默变成灰色 —— 「失败归因」就这么灰了一整轮。

    盯的是前端 CAT_COLORS 覆盖后端 _section() 实际产出的每一个分类名。
    """
    import re
    from pathlib import Path

    jsx = Path(__file__).resolve().parents[2] / "frontend/src/pages/settings/MCPTools.jsx"
    src = jsx.read_text(encoding="utf-8")
    block = src[src.index("const CAT_COLORS = {"):]
    block = block[:block.index("}")]
    mapped = set(re.findall(r"'([^']+)':\s*'[^']+'", block))

    cats = {t["category"] for t in TOOL_CATALOG}
    missing = cats - mapped
    assert not missing, f"这些分类前端没给颜色，会静默变灰：{sorted(missing)}"
