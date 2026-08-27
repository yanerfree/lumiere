"""MCP 工具按「活」分档 —— 一次连接只暴露干这件事要用的那些。

## 为什么要分档

42 个工具平铺给模型，它挑不准。实测过的具体后果：想回推活体验证成果的 CC
看到 `lum_generate_api_test`（凭接口文档造场景），觉得更省事就走了它 ——
正好是要避开的那条路。instructions 里写了"默认先活体验证"也拦不住，
**软约束对模型是建议，工具范围才是墙**。

（那个工具后来直接下线了 —— 拦不如没有。但这条教训对别的岔路照样成立，
分档因此保留。）

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
_LOCATE = ["lum_list_projects", "lum_list_branches"]
# 项目须知：动手前该读的那些「被测系统就是这样」。读放进 _LOCATE 之外
# 单列，是因为归因档也要读（判断"这是缺陷还是系统本来如此"全靠它），
# 但归因档刻意不给任何写库工具。
_NOTES_READ = ["lum_list_project_notes"]

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

_LIVE = _LOCATE + _NOTES_READ + ["lum_add_project_note"] + [
    "lum_list_cases", "lum_get_case", "lum_get_folder_tree", "lum_create_case", "lum_update_case",
    "lum_list_api_tree", "lum_get_api_node",
    "lum_list_environments", "lum_get_merged_variables",
    "lum_get_sync_spec", "lum_list_global_data",
    "lum_upsert_scenario_variables", "lum_list_scenario_variables",
    "lum_upsert_automation_resource",
    "lum_sync_orchestrated_scenario",
    # 断言里的错误提示语走 ${T:中文}，用的是同一份词典，所以这一档也要能登记
    "lum_upsert_i18n_terms",
    # 选择器登记表跟词典是同一件事的另一半（外部取值不许写死在正文里）
    "lum_upsert_selectors", "lum_list_selectors",
    "lum_list_api_tests", "lum_get_api_test", "lum_run_api_test",
    # 跑绿之后还得回答"这些断言有没有用" —— 跳掉动作步再跑一遍，该红的必须红
    "lum_check_assertion_bite",
    # 自己造的垃圾会反过来毁掉自己的断言（列表堆满之后 data[0] 指向别人）
    "lum_check_env_hygiene",
    # 产出完自己先跑一遍交付门禁，别再自己宣布"这条可以交付了"
    "lum_check_deliverable", "lum_check_branch",
    # 六维评审，回推完自己先过一遍（blocker 一条都不许留着交上去）。
    # **lum_review_case 和 lum_review_check 必须同档**：前者超时中止时唯一正确的
    # 下一步就是调后者查（评审是跑完就落库）—— 只发前者不发后者的话，超时之后
    # CC 手上没有任何只读查询手段，只能重调 lum_review_case，而那正是要防的重复真跑。
    "lum_review_case", "lum_review_check",
    # 推一批就送一批进队列 —— 别自己 for 循环调上面那个，那样并发真跑打同一个
    # 环境，同环境串行和熔断两道保护一条都吃不到（假打回就是这么来的）
    "lum_review_batch", "lum_review_batch_status",
    # 写完一批自己问一句「这个模块还缺什么」，拿到清单接着补，不用人催
    "lum_module_checkup",
    # 版本升级对账：新分支复制完，拿本机 git diff 跟平台的端点表求交集，
    # 把这批用例分成照抄/要改/该废/待补四堆。**跟主线同一档** ——
    # 单独开一档的话，干版本升级的人得同时选两档才能干完一件事。
    "lum_list_branch_endpoints", "lum_apply_endpoint_diff", "lum_request_deprecate",
]

# Mock 上游 / 抓真实请求。**单独一档而不是塞进 live**：
# ①不是每个项目都测 AI 网关，塞进去会让所有人的 live 档白白变大
# ②单独一张卡片，这个能力才**被看得见** —— 平台自己的工具没人知道怎么用，
#   多半不是因为难用，是因为它只存在于某个菜单深处
_MOCKS = [
    "lum_llm_mock_status", "lum_upsert_llm_mock_route",
    "lum_llm_mock_requests", "lum_llm_mock_reset", "lum_proxy_capture",
]

_UISCRIPT = _LOCATE + _NOTES_READ + [
    "lum_list_cases", "lum_get_case",
    "lum_get_sync_spec", "lum_list_global_data",
    "lum_list_scenario_variables", "lum_upsert_scenario_variables",
    "lum_list_environments", "lum_get_merged_variables",
    "lum_sync_ui_script", "lum_run_ui_script", "lum_get_ui_script_result",
    # 本地跑之前先渲染一份（文案占位在平台执行前才替换，本地跑要先换掉）
    "lum_render_ui_script",
    # 文案纪律要求走 t()，那就得有地方登记词条 —— 缺这个通道，纪律就只能靠人工转抄
    "lum_upsert_i18n_terms",
    # 选择器同理，而且这一档最需要它：写 UI 脚本第一件事就是查登记表，别现编
    "lum_upsert_selectors", "lum_list_selectors",
    "lum_check_deliverable", "lum_check_branch",
    "lum_review_case", "lum_review_check",
    "lum_review_batch", "lum_review_batch_status", "lum_module_checkup",
]

_TRIAGE = _LOCATE + _NOTES_READ + [
    "lum_list_plans", "lum_list_reports", "lum_get_report_summary",
    "lum_get_failed_scenarios", "lum_get_ui_script_result", "lum_get_case",
    "lum_submit_analysis", "lum_list_pending_confirm",
    # 每轮上来先问"该干什么" —— 四个队列一次给全，不用自己拼
    "lum_next_duty",
]

_REGRESSION = _LOCATE + [
    "lum_list_cases", "lum_list_environments",
    "lum_create_plan", "lum_run_plan", "lum_list_plans",
    "lum_list_reports", "lum_get_report_summary", "lum_get_failed_scenarios",
    "lum_check_deliverable", "lum_check_branch",
    "lum_run_ui_scripts_batch", "lum_list_api_tests", "lum_run_api_test",
    "lum_next_duty",
]

# 主线那条完整的链。前面四档是它切开的段，单独用得上，但**最常见的用法是从头干到尾** ——
# 原来没有这一档，想干整条链的人只能去选「全量」，等于分档对他没发生（和当初 18 个
# 工具不属于任何档位是同一个毛病）。
#
# 它比别的档大得多（三十多个），这是有意的：这一档挡的不是"工具多"，而是那几条
# **会把人带偏的岔路** —— Skill 存取、文档规范、接口库维护。
# （需求文档流水线和 lum_generate_api_test 那两条岔路已整体下线，不用再挡了）
_FULLLOOP = sorted(set(_LIVE + _UISCRIPT + _REGRESSION + _TRIAGE + _MOCKS))
_FULLLOOP_TASK = "下面这四件活连起来干完：" + " → ".join(
    f"{n}{_LABELS[k]}" for n, k in zip("①②③④", _CHAIN)
)

PROFILES: list[dict] = [
    {
        "key": "fullloop",
        "label": "全链路：从写用例到读报告",
        "task": _FULLLOOP_TASK,
        "hint": "最常见的用法就是这一条，另把 Mock 上游那一档也带上了"
                "（同屏那张卡片自己标着「已包含」，别以为漏了）。"
                "不含接口库维护和 Skill 存取 —— 那些是另一件活",
        "tools": _FULLLOOP,
    },
    {
        "key": "live",
        "label": _LABELS["live"],
        "task": "在被测系统里真跑一遍，把测试步骤和接口链回写成用例",
        "hint": "接口场景一律亲手跑通再回推 —— 平台不提供「凭文档造」那条路",
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
    {
        "key": "mocks",
        "label": "Mock 上游 / 抓真实请求",
        "task": "把被测系统的上游换成可控的 Mock（造 429、超时、截断、自定义 token 用量），"
                "并断言它到底往上游发了什么；或者用代理抓真实请求当写用例的素材",
        "hint": "测 AI 网关绕不开这一档 —— 用真上游又慢又费钱又不确定，"
                "而且「网关往上游发了什么」只有 Mock 看得见（客户端只看得到最终响应）",
        "tools": _LOCATE + _MOCKS,
    },
    # 「需求文档批量生成用例」档已删 —— 那条流水线的入口整体下线了，
    # 原因见 app/mcp/__init__.py 里「需求→用例流水线：已下线」那段注释。
    # 原来这一档叫「只有接口文档，连不上系统」，配的是 lum_generate_api_test（凭文档造场景）。
    # 那个工具 2026-08-15 随「接口测试」模块一起下线 —— 它造出来的场景不绑用例，
    # 而场景变量只能挂在用例上，所以结构上就跑不了。这一档因此收敛回它真正干的事：
    # **维护接口库**（记系统有哪些接口、怎么调），供后面编排场景时查阅引用。
    # 连不上环境时的正解不是凭文档编场景，是只回推 spec 用例、把接口那一维明明白白欠着。
    {
        "key": "apidoc",
        "label": "维护接口库",
        "task": "把系统有哪些接口、怎么调记进接口库，供后续写用例和编排场景时查阅",
        "hint": "接口库只是文档，没有断言、不能执行。要可执行的接口场景，选「用例：步骤 + 接口场景」亲手跑通再回推",
        "tools": _LOCATE + [
            "lum_list_api_tree", "lum_get_api_node", "lum_create_api_node",
            "lum_list_api_tests", "lum_get_api_test",
        ],
    },
    # 原来这里有一档「写操作/演示/验收文档」，配 lum_get_doc_spec。
    # 那个工具 2026-08-27 随「文档管理」模块一起下线（见 mcp/__init__.py 那段说明），
    # 档位跟着删 —— 留一个配着不存在的工具的档位，选中它等于什么都没有。
    {
        "key": "skill",
        "label": "Skill 取用与共享",
        "task": "把本项目的 skill 推上平台，或取用别的项目共享出来的",
        "hint": "存的是客户端侧执行的 skill（跑在你机器的 Claude Code 里），平台只做存取",
        "tools": ["lum_list_projects", "lum_list_skills", "lum_pull_skill", "lum_push_skill"],
    },
    {
        "key": "qareview",
        "label": "QA 仓：取域评审结论",
        "task": "把平台对某个域的评审结论拿回本地，照 evidence 里的锚点 grep 定位，逐条改脚本",
        "hint": "**平台对 QA 仓永远只读**，这一档也只有「拿」没有「写」—— "
                "结论是建议不是门禁，改不改由仓库主人定。刻意不含任何写平台库的工具：读结论的人"
                "跟写用例的人不是同一拨，给他全链路那一档等于把别人的用例库也一并交出去",
        "tools": ["lum_list_projects", "lum_get_qa_review"],
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

# 纪律**不在这儿**。原来这里有三组八条（怎么挑场景 / 怎么写 / 动手前后），
# 每档一字不差地拼进接入指令，1075 字。删掉了，理由是它们本来就有更好的家：
#
#   · 一连上就生效的那份 —— `app/mcp/__init__.py` 的 `instructions`（10.7k 字，
#     内容比这里全得多，还带「预期不能照抄实测」这类只有它有的判据）。
#   · 工具自己的描述 —— 判重看 lum_list_cases 的 owes/pending_only，
#     模块缺口看 lum_module_checkup 的 coverageGaps。
#   · **工具的返回值** —— 这是最强的一层：lum_next_duty 每条带「下一步该调哪个工具」、
#     lum_check_deliverable 直接说卡在哪、lum_sync_orchestrated_scenario 推完当场把
#     四问摊出来。规范在人需要它的那一刻才出现，不用先背下来。
#
# 抄进指令的代价不只是长：指令是无条件拼给每一档的，纪律里点名的工具却按档发 ——
# 「先调 lum_list_cases」曾同时发给归因/Mock/接口库/Skill 四档，而这四档的 Key
# 里根本没有这个工具，CC 照着做只能撞空。范围外的工具不许在指令里出现，
# `test_接入指令不点名本档范围外的工具` 钉住了这条。
#
# 所以指令只干一件工具干不了的事：**把上下文填好，给个开头。**


def render_prompt(key: str, *, mcp_url: str, project_name: str | None = None,
                  branch_name: str | None = None) -> str | None:
    """把一个档位渲染成可以直接粘给 Claude Code 的接入指令。

    **模板必须放后端、和 task/hint 同源。** 前端硬编码一份的话，改了 task 而
    忘了改模板，页面上说的和复制出去的就是两回事，而这种漂移没人会发现 ——
    用户不会把两处对着看。

    这里**不允许出现独立文案**：正文只由 task / hint 拼出来。想改措辞就去改上面
    PROFILES 的字段，页面和指令一起变 —— 例外一个都没有（原来抄在这儿的三组
    纪律已经删了，理由见 MODULE_SLOT 上面那段）。
    """
    p = next((x for x in PROFILES if x["key"] == key), None)
    if p is None or p["tools"] is None:
        return None  # 「全量」档没有"去干什么活"，渲染不出指令

    lines = [f"连上 Lumiere：{mcp_url}", ""]
    where = " / ".join(x for x in (project_name, branch_name) if x)
    if where:
        lines.append(f"项目 {where}")
    lines.append(f"这次干「{MODULE_SLOT}」这块。{p['task']}。")
    if p.get("hint"):
        lines.append(f"注意：{p['hint']}。")
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
