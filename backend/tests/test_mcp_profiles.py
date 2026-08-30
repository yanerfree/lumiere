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


def test_单件活的档位都比全量挡掉更多():
    """分档的收益就是这个数。一档接近全量就说明它没在分。

    `fullloop` 例外，单独由下面那条测 —— 它是把四段拼成一整条链，
    本来就大；拿"小"当唯一标准会逼着把它拆回去，而拆回去正是它要解决的问题
    （想干整条链的人只能选「全量」，分档对他等于没发生）。

    **按"排除了几个"判，不按比例判** —— 跟下面那条同一个道理。
    这里原来写的是 `len(tools) < len(NAMES) * 0.6`，2026-08-27 下线
    `lum_get_doc_spec` 之后它红了：`live` 从 35/59 变成 35/58，档位一个工具
    没多放，分母小了一个百分比就自己越线。**那是假红** —— 守卫盯错了东西。
    分母会随着下线一直缩，比例这个判据只要不改就会周期性地假红一次，
    而每次都只能靠调阈值糊过去，调着调着这条守卫就什么都不守了。

    阈值 15：当下最小的单件活档是 `live`（排除 23 个），留出往下再删几个工具
    的余量；同时任何"其实等于全量"的档（只象征性排除三五个）都会被挡下。
    """
    for p in PROFILES:
        if p["tools"] is not None and p["key"] != "fullloop":
            excluded = set(NAMES) - set(p["tools"])
            assert len(excluded) >= 15, (
                f"{p['key']} 只排除了 {len(excluded)} 个工具，跟「全量」没差多少 —— "
                "这一档到底在分什么？")


def test_全链路档大_但仍然挡住那几条岔路():
    """它挡的不是"工具多"，是**会把人带偏的岔路**。这几条一条都不能漏进去。"""
    fl = next(p for p in PROFILES if p["key"] == "fullloop")
    tools = set(fl["tools"])
    for forbidden, why in [
        ("lum_generate_api_test", "凭文档造场景，绕开亲手验证"),
        ("lum_create_scenario_task", "需求文档流水线是另一条路，不碰被测系统"),
        ("lum_confirm_and_generate", "同上"),
        ("lum_push_skill", "Skill 存取跟这条链无关"),
    ]:
        assert forbidden not in tools, f"{forbidden} 不该进全链路档：{why}"
    # 也不能大到跟全量没区别。**按"排除了几个"判，不按比例判** ——
    # 比例的分母是全部工具数，砍掉 5 个 docgen 之后分母变小，比例自己就涨了，
    # 而全链路档一个岔路都没多放。守卫该盯的是"还挡着几条岔路"，不是百分比。
    #
    # 这个数 2026-08-27 从 5 降到 4：`lum_get_doc_spec` 随「文档管理」整个下线，
    # 从注册表里消失了 —— **不是全链路档把它放进来了**。它那一条改由
    # `test_平台不再提供文档生成工具` 用 "not in NAMES" 盯着，比"不在这一档里"更硬
    # （工具都没了，"不在某档里"是恒真的，留在这儿等于少一条守卫）。
    excluded = set(NAMES) - tools
    assert len(excluded) >= 4, (
        f"全链路档只排除了 {len(excluded)} 个工具（{sorted(excluded)}）——"
        "接近全量了，重新想想它到底排除了什么")


def test_全链路档覆盖整条链的每一步():
    """少任何一步，人就得退回去选「全量」—— 那这一档就白加了。"""
    fl = set(next(p for p in PROFILES if p["key"] == "fullloop")["tools"])
    for step, tool in [
        ("写用例", "lum_create_case"),
        ("回填接口场景", "lum_sync_orchestrated_scenario"),
        ("回填 UI 脚本", "lum_sync_ui_script"),
        ("组计划", "lum_create_plan"),
        ("跑一轮", "lum_run_plan"),
        ("读报告", "lum_get_report_summary"),
        ("看失败证据", "lum_get_ui_script_result"),
        ("提归因", "lum_submit_analysis"),
    ]:
        assert tool in fl, f"全链路缺了「{step}」这一步（{tool}）"


def test_平台不再提供文档生成工具():
    """lum_get_doc_spec 2026-08-27 随「文档管理」模块下线。

    原来这条是在「全链路档不含它」的岔路清单里 —— 工具删掉之后那种写法恒真，
    留着等于没测（跟下面那条 lum_generate_api_test 撞过同一个坑）。
    改成测"它根本没被注册回来"。

    下线的不只是这个工具：它和平台侧生成共用 lum-doc-generate/SKILL.md 切片当模板，
    那份文件已删 —— 单留工具就是发一份不存在的模板。
    """
    assert "lum_get_doc_spec" not in NAMES


def test_平台不再提供凭文档造场景的工具():
    """原来这条测的是"live 档不含 lum_generate_api_test" —— 靠档位把它挡在外面。

    2026-08-15 那个工具连同「接口测试」模块整个下线了，于是这条要测的东西变了：
    不是"某一档挡住它"，而是**它根本不该再被注册回来**。
    档位断言在工具不存在时是恒真的，留着等于没测（假通过）。

    下线的理由不是没人用，是它的产物结构上跑不了：场景变量只能挂在用例上
    （scenario_variables.case_id NOT NULL），不绑用例就拿不到凭据，实跑必挂在
    「变量未解析」。生成归外部 Claude Code，平台只做呈现和回推通道。
    """
    assert "lum_generate_api_test" not in NAMES


def test_归因档位不含任何写用例或脚本的工具():
    """红线 3：CC 的归因不改任何状态。工具范围要把它兜死，不能只靠自觉。"""
    triage = next(p for p in PROFILES if p["key"] == "triage")
    forbidden = {
        "lum_create_case", "lum_sync_ui_script", "lum_sync_orchestrated_scenario",
        "lum_upsert_scenario_variables", "lum_create_api_node", "lum_run_plan",
    }
    assert not (set(triage["tools"]) & forbidden)


def test_每个档位都能定位到项目():
    """连项目都列不出来的档位，进去第一步就卡住。"""
    for p in PROFILES:
        if p["tools"] is not None:
            assert "lum_list_projects" in p["tools"], p["key"]


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


def test_接入指令留着模块占位符():
    """唯一一个要人填的空。**不能省** —— "干哪个模块"是平台唯一不知道的事，
    删了它指令就变成一句没有对象的空话，CC 只能自己挑一个模块开工。

    （原来这条还断言指令里有 lum_list_cases / 清单 / 证据 三句纪律。纪律搬走了，
    见 test_接入指令短_只带上下文不复述纪律 和 test_这些纪律在instructions里也有一份。）
    """
    from app.mcp.profiles import MODULE_SLOT, render_prompt

    for p in PROFILES:
        if p["tools"] is None:
            continue
        got = render_prompt(p["key"], mcp_url="http://h/mcp/")
        assert MODULE_SLOT in got, f"{p['key']} 没留模块占位符，用户不知道要填什么"


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
    dead = {"lum_create_scenario_task", "lum_confirm_and_generate", "lum_get_scenario_task",
            "lum_query_coverage_matrix", "lum_get_generation_stats"}
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

def test_接入指令短_只带上下文不复述纪律():
    """纪律从指令里删了（三组八条、1075 字）。**这条钉的是"别再抄回来"。**

    抄回来的代价不是长，是它变成第四个孤岛：instructions 改了、工具描述改了，
    没人会想起来还有一份躺在 profiles 里。而纪律真正的家有三个，都比它强 ——
    instructions 一连上就生效（不用人粘贴）、工具描述随工具一起发、
    工具返回值在人需要它的那一刻才出现（lum_next_duty 直接说下一步调谁）。

    指令只干工具干不了的那件事：把地址/项目/分支填好，给个开头。
    """
    from app.mcp.profiles import render_prompt

    for p in PROFILES:
        if p["tools"] is None:
            continue
        got = render_prompt(p["key"], mcp_url="http://h/mcp/",
                            project_name="网关管理系统", branch_name="default")
        assert len(got) < 400, f"{p['key']} 的指令 {len(got)} 字，纪律又被抄回来了"
        assert len(got.splitlines()) <= 6, f"{p['key']} 的指令有 {len(got.splitlines())} 行"
        # 分节标题是"抄回来了"最好认的痕迹
        for mark in ("【怎么挑场景】", "【怎么写】", "【动手前后】"):
            assert mark not in got, f"{p['key']} 又有 {mark} 这一节了"


def test_接入指令不点名本档范围外的工具():
    """删纪律顺带修掉的那个 bug，钉在这儿别复发。

    纪律是无条件拼给每一档的，里面点名的工具却按档发：「先调 lum_list_cases」
    曾同时发给归因 / Mock / 接口库 / Skill 四档，而这四档的 Key 里没有这个工具 ——
    `tools/list` 看不见、硬调也会被 `on_call_tool` 拒掉。CC 照着指令做只能撞空，
    而撞空之后它会自己找替代路子，那就是分档要挡的岔路。
    """
    import re

    from app.mcp.profiles import render_prompt

    for p in PROFILES:
        if p["tools"] is None:
            continue
        got = render_prompt(p["key"], mcp_url="http://h/mcp/")
        outside = sorted(set(re.findall(r"lum_[a-z_]+", got)) - set(p["tools"]))
        assert not outside, f"{p['key']} 的指令点名了本档范围外的工具：{outside}"


def test_这些纪律在instructions里也有一份():
    """**指令里删掉的那些，这里是它们现在唯一的家。**

    原来两处各有一份，删掉指令那份的前提是这份真的全 —— 所以这条断言从
    5 个关键词扩到 12 个，把原先只有指令才钉住的（判重、报清单四列、
    回推带证据、标题反例）一并接过来。instructions 是 CC 一连上就读的，
    不需要人记得粘贴，本来就该是主份。
    """
    from app.mcp import mcp

    ins = mcp.instructions
    for key in (
        "页面上用户能做的事", "碎片", "完整流程",          # 怎么挑
        "一挂全挂", "互不依赖", "切换之后", "访问不通",     # 合还是拆 / 状态
        "对象 + 做了什么 + 预期结果", "异常场景",           # 标题
        "lum_list_cases",                                   # 动手前判重
        # 报清单四列。断言整行而不是光找"用户在哪儿看得到"五个字 ——
        # 只判单词的话，把列去掉、解释留着，守卫照样绿
        "场景名称 | 这条验什么（一句话） | 用户在哪儿看得到 | 库里已有吗",
        "没跑过",                                          # 回推带证据
    ):
        assert key in ins, f"instructions 里缺「{key}」"


def test_能读项目须知的档位也必须能写回去():
    """读得到写不回 = 这一轮撞出来的坑跟着会话一起没了，下一轮从零再踩。

    原来 UI 脚本档和归因档就是这样：`lum_list_project_notes` 发了，
    `lum_add_project_note` 没发。这张表当初要解决的就是"知识只活在某次会话里"，
    只发读的那一半等于把它又还回去了。

    钉成"读⟹写"而不是点名那两档：以后新增档位照样受这条管。
    """
    for p in PROFILES:
        tools = p["tools"]
        if tools and "lum_list_project_notes" in tools:
            assert "lum_add_project_note" in tools, p["key"]
