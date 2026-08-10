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


def test_单件活的档位都比全量小得多():
    """分档的收益就是这个数。一档接近全量就说明它没在分。

    `fullloop` 例外，单独由下面那条测 —— 它是把四段拼成一整条链，
    本来就大；拿"小"当唯一标准会逼着把它拆回去，而拆回去正是它要解决的问题
    （想干整条链的人只能选「全量」，分档对他等于没发生）。
    """
    for p in PROFILES:
        if p["tools"] is not None and p["key"] != "fullloop":
            assert len(p["tools"]) < len(NAMES) * 0.6, p["key"]


def test_全链路档大_但仍然挡住那几条岔路():
    """它挡的不是"工具多"，是**会把人带偏的岔路**。这几条一条都不能漏进去。"""
    fl = next(p for p in PROFILES if p["key"] == "fullloop")
    tools = set(fl["tools"])
    for forbidden, why in [
        ("tb_generate_api_test", "凭文档造场景，绕开亲手验证"),
        ("tb_create_scenario_task", "需求文档流水线是另一条路，不碰被测系统"),
        ("tb_confirm_and_generate", "同上"),
        ("tb_push_skill", "Skill 存取跟这条链无关"),
        ("tb_get_doc_spec", "写文档是另一件活"),
    ]:
        assert forbidden not in tools, f"{forbidden} 不该进全链路档：{why}"
    # 也不能大到跟全量没区别，那样等于没分
    assert len(tools) < len(NAMES) * 0.85, "全链路档已经接近全量，重新想想它排除了什么"


def test_全链路档覆盖整条链的每一步():
    """少任何一步，人就得退回去选「全量」—— 那这一档就白加了。"""
    fl = set(next(p for p in PROFILES if p["key"] == "fullloop")["tools"])
    for step, tool in [
        ("写用例", "tb_create_case"),
        ("回填接口场景", "tb_sync_orchestrated_scenario"),
        ("回填 UI 脚本", "tb_sync_ui_script"),
        ("组计划", "tb_create_plan"),
        ("跑一轮", "tb_run_plan"),
        ("读报告", "tb_get_report_summary"),
        ("看失败证据", "tb_get_ui_script_result"),
        ("提归因", "tb_submit_analysis"),
    ]:
        assert tool in fl, f"全链路缺了「{step}」这一步（{tool}）"


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
