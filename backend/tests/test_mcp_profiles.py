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


def test_全链路的说明逐字引用四个子档的名字():
    """手写一句顺口的描述，和子档各自的说法必然对不上 —— 人就得自己猜
    "这一档到底包不包含那一件"。实测被问到了：「第一个是包含后面的 4 个吗？」

    所以说明是拼出来的，不是写出来的。这条钉住：改了任一子档的名字，
    父档说明会跟着变；要是哪天有人改回手写，这里会红。
    """
    fl = next(p for p in PROFILES if p["key"] == "fullloop")
    for key in ("live", "uiscript", "regression", "triage"):
        label = next(p for p in PROFILES if p["key"] == key)["label"]
        assert label in fl["task"], f"全链路的说明里没逐字出现「{label}」"


def test_全链路的说明只提它真包含的活():
    """反过来也得成立：说明里提到的每一件，工具都必须真被包含，否则是页面说假话。"""
    fl = next(p for p in PROFILES if p["key"] == "fullloop")
    tools = set(fl["tools"])
    for p in PROFILES:
        if p["key"] != "fullloop" and p["tools"] and p["label"] in fl["task"]:
            assert set(p["tools"]) <= tools, f"说明提了「{p['label']}」但工具没全包含"


def test_四段的排列顺序和说明一致():
    """说明里写 ③跑回归 ④失败归因、卡片上却是失败归因排在前面 —— 一眼就看出对不上，
    人又得回头猜哪个才算数。页面是按 PROFILES 顺序渲染的，所以这里钉住。"""
    from app.mcp.profiles import _CHAIN

    order = [p["key"] for p in PROFILES]
    got = [k for k in order if k in _CHAIN]
    assert got == _CHAIN, f"卡片顺序 {got} 和说明里的顺序 {_CHAIN} 对不上"


# ── 接入指令：把档位渲染成能直接粘给 CC 的一段话 ──────────────────

def test_接入指令正文是档位字段拼出来的_不是另写一份():
    """**这一条是这个功能的验收线。**

    模板一旦允许写独立文案，就成了第四个孤岛：改了档位说明忘了改模板，
    页面上写的和复制出去的成了两回事，而没人会把两处对着看。
    所以正文必须逐字含 task，措辞要改就去改 PROFILES。
    """
    from app.mcp.profiles import render_prompt

    for p in PROFILES:
        got = render_prompt(p["key"], mcp_url="http://h/mcp/")
        if p["tools"] is None:
            assert got is None, f"{p['key']} 是「全量」档，没有具体要干的活，不该渲染出指令"
            continue
        assert got, f"{p['key']} 渲染不出指令"
        assert p["task"] in got, f"{p['key']} 的指令没逐字带上 task —— 另写了一份文案"
        assert p["hint"] in got, f"{p['key']} 的 hint 没送到 CC 手上"


def test_接入指令带齐三句纪律():
    """先查 / 报清单 / 带证据。三句和干哪种活无关，是纪律，每档都得有。

    「先查」那句尤其关键：平台只硬拒同模块下**标题完全相同**的用例，
    换个说法就绕过去了 —— 真正防重复的是 CC 动手之前那一眼。
    """
    from app.mcp.profiles import MODULE_SLOT, render_prompt

    for p in PROFILES:
        if p["tools"] is None:
            continue
        got = render_prompt(p["key"], mcp_url="http://h/mcp/")
        assert "tb_list_cases" in got, f"{p['key']} 没让 CC 先查已有场景"
        assert MODULE_SLOT in got, f"{p['key']} 没留模块占位符，用户不知道要填什么"
        assert "清单" in got, f"{p['key']} 没要求动库前先报清单"
        assert "证据" in got, f"{p['key']} 没要求回推带证据"


def test_接入指令带上下文而不是让用户自己填():
    """项目/分支平台自己知道，填死；只留一个占位符。

    留两个以上用户就会漏填，而漏掉的占位符会被 CC 当字面量执行。
    """
    from app.mcp.profiles import render_prompt

    got = render_prompt("fullloop", mcp_url="http://h:18800/mcp/",
                        project_name="网关管理系统", branch_name="default")
    assert "http://h:18800/mcp/" in got, "地址没带上，用户得复制两次"
    assert "网关管理系统" in got and "default" in got
    # 占位符**只能有一个**
    import re
    assert len(set(re.findall(r"\{[^}]+\}", got))) == 1, "留了不止一个占位符，用户会漏填"


# ── 需求→用例流水线已下线 ──────────────────────────────────────────

def test_下线的流水线工具没有偷偷回来():
    """入口下线之后工具还留着的话，CC 走「全量」档仍能开出一个任务，
    而平台上已经没有页面能看它跑到哪了 —— 比不下线更糟。
    """
    dead = {"tb_create_scenario_task", "tb_confirm_and_generate", "tb_get_scenario_task",
            "tb_query_coverage_matrix", "tb_get_generation_stats"}
    back = dead & NAMES
    assert not back, f"这些工具又被注册回来了：{sorted(back)}"
    assert not any(p["key"] == "docgen" for p in PROFILES), "docgen 档位又回来了"


def test_下线的只是入口_实现和数据没动():
    """删表会伤到 49 条还挂着 generation_task_id 的老用例。
    所以服务层和模型必须还在 —— 这条钉住"下线 ≠ 删库"。
    """
    import importlib

    assert importlib.import_module("app.models.scenario_gen").GenerationTask is not None
    from app.models.case import Case

    assert hasattr(Case, "generation_task_id"), "用例上的批次外键被删了，老数据会失联"


# ── 选题纪律：CC 第一版按接口字段切碎片，被用户当场打回 ────────────

def test_接入指令教了怎么挑场景():
    """实测打回原话：「不够场景化，也不够核心，边缘化，随便挑几个」。

    根因不在模型：它拿到的输入全是接口维度（接口树、字段定义），
    平台没有任何东西告诉它"用户在页面上看得见什么"，按字段排列组合是必然的。
    所以这几条必须在指令里，而不是靠人每次口头纠正。
    """
    from app.mcp.profiles import render_prompt

    got = render_prompt("live", mcp_url="http://h/mcp/")
    assert "页面上用户能做的事" in got, "没让它先盘功能，它只会从接口列表出发"
    assert "碎片" in got, "没点破「按接口字段切出来的是碎片」这个具体错法"
    assert "完整流程" in got, "没说清一条用例的单位是什么"


def test_接入指令给了合还是拆的判据():
    """「可以合并的合并，复杂的或前置很麻烦的不建议合并」——
    光说这句 CC 判不了，得给判据。

    判据：**合并的唯一代价是「一挂全挂」**。所以只在"前面挂了后面本来也测不了"
    的天然链条上合，那时不丢信息；互不依赖的两个功能合成一条只是互相绑架。
    """
    from app.mcp.profiles import render_prompt

    got = render_prompt("live", mcp_url="http://h/mcp/")
    assert "一挂全挂" in got, "没给合并的代价，CC 只能凭感觉合"
    assert "互不依赖" in got, "没说清什么时候不该合"
    assert "前置很重" in got and "拆开" in got, "没给拆开的触发条件"


def test_接入指令要求覆盖状态切换之后():
    """只写「创建成功」是漏了大头 —— 状态类功能的价值全在切换之后：
    切过去能不能用、切回来对不对、切到不可用状态后是不是真的访问不通。
    """
    from app.mcp.profiles import render_prompt

    got = render_prompt("live", mcp_url="http://h/mcp/")
    assert "切换之后" in got
    assert "访问不通" in got, "没要求验「下线之后真的调不通」这类反向断言"


def test_接入指令管了标题怎么写():
    """标题是列表页唯一露出来的东西。写成「异常场景」，
    几百条之后所有人都得点进详情才知道在测什么。
    """
    from app.mcp.profiles import render_prompt

    got = render_prompt("live", mcp_url="http://h/mcp/")
    assert "对象 + 做了什么 + 预期结果" in got, "没给标题的格式"
    assert "异常场景" in got, "没给反例，光说「要清晰」没用"


def test_清单有用户可见落点那一列():
    """这一列是筛子：说不出"用户在哪儿看得到"的，基本就是接口碎片。
    放在报清单之前，CC 自己就能划掉一批，不用等人看完全部步骤才发现。
    """
    from app.mcp.profiles import render_prompt

    got = render_prompt("live", mcp_url="http://h/mcp/")
    # 断言整行四列，不是光找这五个字 —— 下面还有一句解释这一列是干嘛的，
    # 只判 `in got` 的话，把列去掉、解释留着，守卫照样绿（本轮第六次踩这个坑）。
    assert "场景名称 | 这条验什么 | 用户在哪儿看得到 | 库里已有吗" in got


def test_这些纪律在instructions里也有一份():
    """接入指令要用户手动复制粘贴；instructions 是 CC 一连上就读的。
    只写在指令里的话，没粘贴的那些会话完全不受约束。
    """
    from app.mcp import mcp

    ins = mcp.instructions
    for key in ("页面上用户能做的事", "一挂全挂", "切换之后",
                "对象 + 做了什么 + 预期结果", "用户在哪儿看得到"):
        assert key in ins, f"instructions 里缺「{key}」，没粘指令的会话就管不住"
