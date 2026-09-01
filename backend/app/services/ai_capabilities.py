"""AI 能力注册表 — 全站用到 AI 的功能清单 + 按模型类别分档。

分类依据:功能是否需要模型"边看页面边写代码 + 自愈"(agentic)。
- text:      给 prompt 出文本,Haiku 即可胜任。
- ui_script: 真实 claude CLI 驱动 Playwright MCP 的 agentic 生成,必须强模型。

这份注册表是"展示 + 解析"的唯一事实来源:
- 前端「AI 能力 → 模型」区块据此展示每个模型档位覆盖哪些模块。
- resolver 据此把调用方传入的 capability(模块 key)映射到档位类别。
"""
from __future__ import annotations

# 内置档位(不可删,只能改模型)。key 同时作为 category。
BUILTIN_CATEGORIES = ["text", "ui_script"]

CATEGORY_META = {
    "text": {
        "label": "文本生成",
        "icon": "📝",
        "defaultModel": "claude-sonnet-5",
        "recommend": "默认 Sonnet-5(近 Opus 质量、Sonnet 价位),用例/场景生成的质量问题优先靠它兜;"
                     "纯搬运类高频任务想省钱可切 Haiku-4-5。",
    },
    "ui_script": {
        "label": "UI 脚本生成",
        "icon": "🎭",
        "defaultModel": "claude-sonnet-5",
        "recommend": "默认 Sonnet-5:同一用例实测 4/4 通过、均 178s(138-209s 很稳);"
                     "Opus-5 也 4/4 但均 246s、区间 148-414s 抖动大且贵 67%,只在 Sonnet-5 反复失败的硬用例上升级;"
                     "Sonnet-4-6 实测 1/2,别再用;Haiku 会导致工具调用循环失败,不要选。",
    },
}

# 填模型时的红线:接口路径只认裸 ID。CLI 的长上下文后缀写法(claude-opus-5[1m])
# 在 /v1/messages 会 404(实测),Opus-5 本身 1M 上下文就是默认值,不需要任何后缀。

# 全站 AI 功能清单。category 决定归属哪个内置档位;capability key 由各调用点传给 resolver。
#
# deprecated=True 的条目：入口已下线或能力已封存。**不要删 key** —— 删了之后
# category_of() 会走 .get(cap,"text") 兜底，把原本 ui_script 档的调用静默降档到
# text 档模型；而且 BUILTIN_CATEGORIES 里的档位删空会让「AI 能力→模型」页
# 冒出一个绑不上模型的空档位。前端只渲染非 deprecated。
CAPABILITY_REGISTRY = [
    # ── 文本生成型 ──
    # where 原来写「AI 侧栏」—— AISidebar.jsx 早就没有任何页面引用了（已随本次删除）。
    # 真实入口是用例管理工具栏的「从接口生成」→ TestForgeModal → POST /ai/generate-cases。
    # 入口 2026-08-19 下线（CaseManagement.jsx 里那段注释就是当时的裁定）：它建的是
    # testforge task JSON，真正生成用例的是 CC 侧 /tf-forge。**页面上已经没有这个按钮**，
    # 而这一行还写着"用例管理 → 从接口生成"—— 用户照着它去找，找不到。
    # 端点和 skill 保留（tf-forge 还按老 task 文件跑），所以只下线入口、不删 key。
    {"key": "lum-case-generate",        "label": "AI 生成接口用例",      "category": "text",      "where": "已下线", "deprecated": True, "deprecatedNote": "页面入口 2026-08-19 下线：改由外部 Claude Code 的 /tf-forge 生成后回推（它自己就能读接口树）。/testforge/* 端点保留"},
    # 标签必须**和用户点的那个按钮同名**。页面上按钮叫「AI 审核」，这里原来只写
    # 「用例质量评审（单条·六维）」—— 用户想改 AI 审核用的模型，在这一页里找不到它
    # （原话：「可是我要改的是 AI 审核啊，没看到这个在哪改」）。术语对不上就等于没写。
    {"key": "lum-quality-review",       "label": "AI 审核（用例质量评审·六维）", "category": "text",  "where": "用例管理「AI 审核」按钮 / 用例详情「审核」页 / MCP lum_review_case"},
    {"key": "lum-diagnose",             "label": "失败诊断",             "category": "text",      "where": "已下线",            "deprecated": True, "deprecatedNote": "归因归外部 Claude Code（lum_submit_analysis），平台只按规则出现象、由人确认结论。前端从来没有调用入口"},
    # 入口 2026-08-15 双下线：MCP 工具摘了（见 mcp/__init__.py 那段说明：8 个批次
    # 3 个卡在半路、2 个 failed、一个月无人问津），页面也**没有任何路由和菜单**指向
    # pages/scenario-gen —— 库里 111 条调用记录全是 8-09 之前的。
    # 实现、7 张表、/api/scenario-gen/* 全部保留（49 条老用例还挂着 task id）。
    {"key": "scenario-gen",            "label": "功能场景测试生成",     "category": "text",      "where": "已下线", "deprecated": True, "deprecatedNote": "2026-08-15 入口 + MCP 工具双下线（仪式太重，用户用脚投票）。实现和 7 张表保留，只是没有任何页面/工具能发起"},
    # 「接口测试」模块 2026-08-15 下线，但这条 key 还活着 —— 用例详情里
    # 入口名字校正（2026-08-27）：页签实际叫「本次流量」，registry 里原来写的
    # 「探索流量」在页面上根本搜不到；「探索测试」下线之后这个词还会被读成
    # "指向那个已删模块"。它跟探索测试无关 —— 是用例运行验证时抓到的真实请求。
    {"key": "api-test-generate",       "label": "接口场景编排生成",     "category": "text",      "where": "用例详情 →「本次流量」页签 →「编排为接口测试」"},
    {"key": "pytest-script",           "label": "pytest 脚本生成",      "category": "text",      "where": "已下线",            "deprecated": True, "deprecatedNote": "入口是已删除的 AIScriptModal"},
    # 「文档管理」模块 2026-08-27 整体下线（docs/cc-platform-loop-spec.md §14）：
    # 页面 / 路由 / api/documents.py / services/doc_generator.py / lum-doc-generate
    # 那份 SKILL.md / MCP 的 lum_get_doc_spec 全删了，documents 表和迁移留着不动。
    # **三条 key 都不删** —— 见上面那段：删了 category_of() 会走 .get(cap,"text") 兜底，
    # 而且页面上"曾经有过这个能力、为什么没了"这件事本身要留在清单里。
    {"key": "doc-generate",            "label": "文档生成",             "category": "text",      "where": "已下线", "deprecated": True, "deprecatedNote": "2026-08-27 随「文档管理」下线：平台侧驱动浏览器截图 + AI 看图编文字，写得对不对没有对照物。要文档在 Claude Code 里自己实操系统写"},
    {"key": "doc-generate-screenshots","label": "文档带截图生成",       "category": "text",      "where": "已下线", "deprecated": True, "deprecatedNote": "同上，截图那条支路一并下线"},
    {"key": "doc-optimize",            "label": "文档优化",             "category": "text",      "where": "已下线", "deprecated": True, "deprecatedNote": "同上。它是「保留截图只重写文字」那条路，随模块一起没了"},
    {"key": "exploratory-charter",     "label": "探索测试 Charter 生成","category": "text",      "where": "已下线", "deprecated": True, "deprecatedNote": "2026-08-27 随「探索测试」下线：它唯一的输入是接口库（全库 7 个 endpoint 的 url 一个都没填），实际拿到的上下文就是模块名四个字，出来的章程换个系统照样成立。探索这件事归外部 Claude Code：真在页面上点一遍，把探到的可操作项喂 lum_module_checkup(observed_actions=…) 跟现有用例对账"},
    # 2026-08-09 实测：这条功能一直是通的（页面上「正则测试 → AI 生成」真能出结果），
    # 之前标成"已下线"是因为端点直接 complete() 没带 capability —— 档位绑了模型也不生效。
    # 现在走 resolve_ai_config，标签和现实对上了。
    {"key": "toolbox-regex",           "label": "工具箱-正则生成",      "category": "text",      "where": "工具箱 → 正则测试 → AI 生成"},
    # 顶栏 AI 助手（操作助手）：把用户意图变成受权限约束的操作提议。走 text 档，不新增独立档位
    # （新档位没绑模型就会在「AI 能力→模型」页冒一个绑不上的空档，见本文件顶部约定）。
    {"key": "assistant",               "label": "AI 助手（操作助手）",   "category": "text",      "where": "顶栏「AI 助手」抽屉"},
    # CC 反馈分诊。走 text 档，不新增独立档位（新档位没绑模型就会在这一页冒一个空档）。
    # 它**只出建议不改状态** —— 理由见 models/cc_feedback.py 的 ai_analysis 那段注释。
    {"key": "cc-feedback-triage",      "label": "CC 反馈分诊",          "category": "text",      "where": "系统管理 →「CC 反馈」→ 某条详情 →「AI 分析」"},
    # ── UI 脚本型(agentic) ──
    {"key": "ui-script",               "label": "AI 生成 UI 自动化脚本","category": "ui_script", "where": "已封存",            "deprecated": True, "deprecatedNote": "改由外部 Claude Code 写好回推，见 docs/cc-platform-loop-spec.md 红线 1"},
    {"key": "ui-script-repair",        "label": "UI 脚本自动修复",      "category": "ui_script", "where": "已封存",            "deprecated": True, "deprecatedNote": "自愈归 CC，平台只出证据"},
]

# 用量记账的 skill_name 和注册表 key 不是一一对应的：场景生成是四个阶段各记一条
# （extract/model/expand/health-check/…），它们同属「功能场景测试生成」这一个入口。
# 不归并的话页面会冒出四个不在能力清单里的名字，而清单里那一项显示"从没被调用"。
USAGE_ALIASES = {
    "scenario-extract": "scenario-gen",
    "scenario-model": "scenario-gen",
    "scenario-expand": "scenario-gen",
    "scenario-reflection": "scenario-gen",
    "scenario-health-check": "scenario-gen",
    "scenario-self-review": "scenario-gen",
}

# 这四条链路 2026-08-24 才补上记账（此前压根没写过 AIUsageLog）。
# **必须在页面上标出来** —— 「没被数」和「没被用」在界面上长得一模一样，
# 而用户已经照着旧页面得出过"其他 AI 都没用到"的结论，然后据此考虑砍功能。
METERED_SINCE = {
    "doc-generate": "2026-08-24",
    "doc-generate-screenshots": "2026-08-24",
    "doc-optimize": "2026-08-24",
    "exploratory-charter": "2026-08-24",
    "toolbox-regex": "2026-08-24",
    "api-test-generate": "2026-08-24",
}


def normalize_usage_key(skill_name: str) -> str:
    return USAGE_ALIASES.get(skill_name, skill_name)


_KEY_TO_CATEGORY = {c["key"]: c["category"] for c in CAPABILITY_REGISTRY}


def category_of(capability: str | None) -> str:
    """把调用方传入的 capability(模块 key,或直接是 'text'/'ui_script')映射到档位类别。"""
    if not capability:
        return "text"
    if capability in BUILTIN_CATEGORIES:
        return capability
    return _KEY_TO_CATEGORY.get(capability, "text")


def modules_for_category(category: str, include_deprecated: bool = False) -> list[dict]:
    return [
        c for c in CAPABILITY_REGISTRY
        if c["category"] == category and (include_deprecated or not c.get("deprecated"))
    ]


def active_categories() -> list[str]:
    """还有活着的模块的内置档位。

    一个档位里的模块全下线了，就不该继续在「实际生效」「使用总览」里报它的模型——
    那会让用户以为平台还在为一个已经没有调用方的能力付费/选型。
    """
    return [c for c in BUILTIN_CATEGORIES if modules_for_category(c)]
