"""MCP 工具按「活」分档 —— 一次连接只暴露干这件事要用的那些。

## 为什么要分档

42 个工具平铺给模型，它挑不准。实测过的具体后果：想回推活体验证成果的 CC
看到 `tb_generate_api_test`（凭接口文档造场景），觉得更省事就走了它 ——
正好是要避开的那条路。instructions 里写了"默认先活体验证"也拦不住，
**软约束对模型是建议，工具范围才是墙**。

所以档位不是 UI 便利，是拿 Key 上的 `allowed_tools` 兜底：范围外的工具
在 `tools/list` 里看不到，直接 `tools/call` 也会被 `on_call_tool` 拒掉。

## 三条纪律

1. **不改工具名**。外部项目的 CC 已经在用这些名字，改名等于把别人的用法弄坏。
   收敛的是"一次给它看多少个"，不是重命名重组。
2. **档位只是勾选的快捷方式**。Key 上落库的永远是展开后的显式工具名列表，
   不存档位名 —— 这样语义可审计，日后改了档位定义也不会让已有 Key 的范围悄悄变。
3. **每个注册工具至少属于一个档位**。没进任何档位的工具，等于只能靠"全量"才用得上，
   那这次收敛对它就没发生。`tests/test_mcp_profiles.py` 把这条钉死了。

## 档位不是越小越好

`live` 有 19 个，超过当初"15 以内"的设想。逐个看下来都在这条链上真会用到
（定位 → 看接口怎么调 → 查环境变量 → 建用例 → 写变量 → 回推 → 执行验证），
硬砍到 15 只会逼人去选全量。**收敛的收益来自 42 → 19，不来自 19 → 15。**
"""
from __future__ import annotations

# 每条链都要先定位到项目/分支，单列出来避免各档位重复抄
_LOCATE = ["tb_list_projects", "tb_list_branches"]

# 档位名单列出来：全链路那一档的说明要**逐字引用**这四个名字拼出来。
# 手写一句"写用例 → 回填接口场景和 UI 脚本 → 组计划跑一轮 → 读报告 → 提归因"
# 看着顺，但和子档各自的说法对不上 —— 人得自己猜"这一档到底包不包含那一件"。
# 实测被问到了：「第一个是包含后面的 4 个吗？」拼出来就不会漂，也不用猜。
_LABELS = {
    "live": "用例：步骤 + 接口场景",
    "uiscript": "UI 脚本：本地写好回推",
    "regression": "跑回归、看结果",
    "triage": "失败归因：看证据、提判断",
}
# 顺序 = 实际干活的先后。**PROFILES 里这四档的排列必须和它一致** ——
# 说明里写 ③跑回归 ④失败归因、卡片上却是失败归因排在前面，人一眼就看出对不上，
# 又得回头猜哪个才算数。test_四段的排列顺序和说明一致 钉住了这条。
_CHAIN = ["live", "uiscript", "regression", "triage"]

_LIVE = _LOCATE + [
    "tb_list_cases", "tb_get_case", "tb_get_folder_tree", "tb_create_case",
    "tb_list_api_tree", "tb_get_api_node",
    "tb_list_environments", "tb_get_merged_variables",
    "tb_get_sync_spec", "tb_list_global_data",
    "tb_upsert_scenario_variables", "tb_list_scenario_variables",
    "tb_upsert_automation_resource",
    "tb_sync_orchestrated_scenario",
    "tb_list_api_tests", "tb_get_api_test", "tb_run_api_test",
]

_UISCRIPT = _LOCATE + [
    "tb_list_cases", "tb_get_case",
    "tb_get_sync_spec", "tb_list_global_data",
    "tb_list_scenario_variables", "tb_upsert_scenario_variables",
    "tb_list_environments", "tb_get_merged_variables",
    "tb_sync_ui_script", "tb_run_ui_script", "tb_get_ui_script_result",
]

_TRIAGE = _LOCATE + [
    "tb_list_plans", "tb_list_reports", "tb_get_report_summary",
    "tb_get_failed_scenarios", "tb_get_ui_script_result", "tb_get_case",
    "tb_submit_analysis", "tb_list_pending_confirm",
]

_REGRESSION = _LOCATE + [
    "tb_list_cases", "tb_list_environments",
    "tb_create_plan", "tb_run_plan", "tb_list_plans",
    "tb_list_reports", "tb_get_report_summary", "tb_get_failed_scenarios",
    "tb_run_ui_scripts_batch", "tb_list_api_tests", "tb_run_api_test",
]

# 主线那条完整的链。前面四档是它切开的段，单独用得上，但**最常见的用法是从头干到尾** ——
# 原来没有这一档，想干整条链的人只能去选「全量」，等于分档对他没发生（和当初 18 个
# 工具不属于任何档位是同一个毛病）。
#
# 它比别的档大得多（三十多个），这是有意的：这一档挡的不是"工具多"，而是那几条
# **会把人带偏的岔路** —— tb_generate_api_test（凭文档造，绕开亲手验证）、
# Skill 存取、文档规范。（需求文档流水线那条岔路已整体下线，不用再挡了）
_FULLLOOP = sorted(set(_LIVE + _UISCRIPT + _REGRESSION + _TRIAGE))
_FULLLOOP_TASK = "下面这四件活连起来干完：" + " → ".join(
    f"{n}{_LABELS[k]}" for n, k in zip("①②③④", _CHAIN)
)

PROFILES: list[dict] = [
    {
        "key": "fullloop",
        "label": "全链路：从写用例到读报告",
        "task": _FULLLOOP_TASK,
        "hint": "最常见的用法就是这一条。不含 tb_generate_api_test —— 那个凭接口文档造，和你亲手跑过不是一回事",
        "tools": _FULLLOOP,
    },
    {
        "key": "live",
        "label": _LABELS["live"],
        "task": "在被测系统里真跑一遍，把测试步骤和接口链回写成用例",
        "hint": "刻意排除 tb_generate_api_test —— 那个凭文档造，和「亲手验证过」是两回事",
        "tools": _LIVE,
    },
    {
        "key": "uiscript",
        "label": _LABELS["uiscript"],
        "task": "在本地把 Playwright 脚本写通，回推到用例的「UI 测试」页签，再在目标环境上真跑一遍确认",
        "hint": "平台侧 AI 生成 UI 脚本已封存，这是现在唯一的 UI 脚本入库路径",
        "tools": _UISCRIPT,
    },
    {
        "key": "regression",
        "label": _LABELS["regression"],
        "task": "组计划、在平台执行器上跑一轮，看通过率和失败分布",
        "hint": "只按按钮和读结果 —— 执行结果由平台执行器写，你改不了通过状态",
        "tools": _REGRESSION,
    },
    {
        "key": "triage",
        "label": _LABELS["triage"],
        "task": "拿失败用例的证据包（截图/流量/现象）判断为什么挂，把归因提交到待确认队列",
        "hint": "刻意不含任何写用例/脚本的工具 —— CC 的归因不改任何状态，人拍板才算数",
        "tools": _TRIAGE,
    },
    # 「需求文档批量生成用例」档已删 —— 那条流水线的入口整体下线了，
    # 原因见 app/mcp/__init__.py 里「需求→用例流水线：已下线」那段注释。
    {
        "key": "apidoc",
        "label": "只有接口文档，连不上系统",
        "task": "拿不到可访问环境时，按接口定义造一组正向/参数/边界/安全场景",
        "hint": "退而求其次的路。能连上被测系统就该选「用例：步骤 + 接口场景」，亲手跑通比凭文档猜靠谱",
        "tools": _LOCATE + [
            "tb_list_api_tree", "tb_get_api_node", "tb_create_api_node",
            "tb_generate_api_test", "tb_list_api_tests", "tb_get_api_test",
        ],
    },
    {
        "key": "doc",
        "label": "写操作/演示/验收文档",
        "task": "拿平台的文档规范，在本地实操被测系统、截图，产出带图文档",
        "hint": "平台只给模板和规范，实操和截图都在你本地 —— 它不需要写库权限",
        "tools": _LOCATE + ["tb_get_doc_spec", "tb_list_cases", "tb_get_case"],
    },
    {
        "key": "skill",
        "label": "Skill 取用与共享",
        "task": "把本项目的 skill 推上平台，或取用别的项目共享出来的",
        "hint": "存的是客户端侧执行的 skill（跑在你机器的 Claude Code 里），平台只做存取",
        "tools": ["tb_list_projects", "tb_list_skills", "tb_pull_skill", "tb_push_skill"],
    },
    {
        "key": "all",
        "label": "全量（不限制）",
        "task": "开放所有工具",
        "hint": "调试用。日常别用 —— 工具越多模型越容易挑错，这正是分档要解决的问题",
        "tools": None,
    },
]


MODULE_SLOT = "{模块名}"

# 三句硬约束，每个档位的接入指令都带。它们同时解决三件事：
#   先查    → 同一个场景已经有了就别再建一条（去重）
#   报清单  → 用户在动库之前拦住跑偏（确认）
#   带证据  → 回推的是真跑过的，不是想象出来的（红线②）
# 写在这儿而不是各档位各写一份：这三句和具体干哪种活无关，是纪律。
_DISCIPLINE = [
    f"动手前先调 tb_list_cases 带 module={MODULE_SLOT} 看这个模块已经有哪些场景。"
    "已经存在的场景不要再建一条 —— 该补的是它欠的那一维（返回里 owes 会说）。",
    "开建之前先把清单列给我看：场景名称 | 这条验什么（一句话）| 库里已有吗。"
    "我说 OK 你再动手；我也可能只让你去掉其中几条。",
    "每条回推都要带你亲手跑过的证据（接口的真实请求响应、UI 的本地跑通结果）。"
    "没跑过就别推。",
]


def render_prompt(key: str, *, mcp_url: str, project_name: str | None = None,
                  branch_name: str | None = None) -> str | None:
    """把一个档位渲染成可以直接粘给 Claude Code 的接入指令。

    **模板必须放后端、和 task/hint 同源。** 前端硬编码一份的话，改了 task 而
    忘了改模板，页面上说的和复制出去的就是两回事，而这种漂移没人会发现 ——
    用户不会把两处对着看。

    这里**不允许出现独立文案**：正文只由 task / hint / tools 拼出来。想改措辞
    就去改上面 PROFILES 的字段，页面和指令一起变。唯一的例外是 _DISCIPLINE
    那三句，它们和档位无关、对所有档位一字不差。
    """
    p = next((x for x in PROFILES if x["key"] == key), None)
    if p is None or p["tools"] is None:
        return None  # 「全量」档没有"去干什么活"，渲染不出指令

    lines = [f"连上 testBench：{mcp_url}", ""]
    where = " / ".join(x for x in (project_name, branch_name) if x)
    if where:
        lines.append(f"项目 {where}")
    lines.append(f"这次干「{MODULE_SLOT}」这块。{p['task']}。")
    lines.append("")
    for i, d in enumerate(_DISCIPLINE, 1):
        lines.append(f"{i}. {d}")
    if p.get("hint"):
        lines += ["", f"注意：{p['hint']}。"]
    return "\n".join(lines)


def uncovered_tools(catalog_names: set[str]) -> list[str]:
    """返回没被任何具体档位覆盖的工具名。

    没进任何档位 = 只有选「全量」才用得上，那这次收敛对它就没发生。
    """
    covered: set[str] = set()
    for p in PROFILES:
        if p["tools"]:
            covered |= set(p["tools"])
    return sorted(catalog_names - covered)


def unknown_tools(catalog_names: set[str]) -> list[tuple[str, str]]:
    """返回档位里写了但根本没注册的工具名（typo 或工具被删了没同步）。

    这种错最阴：Key 建出来看着有范围，实际少一个工具，用的时候才发现调不到。
    """
    bad = []
    for p in PROFILES:
        for name in p["tools"] or []:
            if name not in catalog_names:
                bad.append((p["key"], name))
    return bad
