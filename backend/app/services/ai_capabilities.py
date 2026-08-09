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
    {"key": "tb-case-generate",        "label": "AI 生成接口用例",      "category": "text",      "where": "用例管理 → 从接口生成"},
    {"key": "tb-quality-review",       "label": "用例质量评审",         "category": "text",      "where": "用例管理"},
    {"key": "tb-diagnose",             "label": "失败诊断",             "category": "text",      "where": "已下线",            "deprecated": True, "deprecatedNote": "归因归外部 Claude Code（tb_submit_analysis），平台只按规则出现象、由人确认结论。前端从来没有调用入口"},
    {"key": "scenario-gen",            "label": "功能场景测试生成",     "category": "text",      "where": "场景生成 Stage1-4"},
    {"key": "api-test-generate",       "label": "接口测试场景生成",     "category": "text",      "where": "接口测试"},
    {"key": "api-test-optimize",       "label": "接口场景 AI 优化",     "category": "text",      "where": "接口测试 → 选中场景 → AI 优化"},
    {"key": "pytest-script",           "label": "pytest 脚本生成",      "category": "text",      "where": "已下线",            "deprecated": True, "deprecatedNote": "入口是已删除的 AIScriptModal"},
    {"key": "doc-generate",            "label": "文档生成",             "category": "text",      "where": "文档管理"},
    {"key": "doc-generate-screenshots","label": "文档带截图生成",       "category": "text",      "where": "文档管理"},
    {"key": "doc-optimize",            "label": "文档优化",             "category": "text",      "where": "暂无入口",          "deprecated": True, "deprecatedNote": "实现还在（POST /documents/{id}/optimize，保留截图只重写文字），但页面上的「重新生成」走的是带截图那条、会重新登录重新截图 —— 这条路没有任何调用方"},
    {"key": "exploratory-charter",     "label": "探索测试 Charter 生成","category": "text",      "where": "探索测试"},
    {"key": "toolbox-regex",           "label": "工具箱-正则生成",      "category": "text",      "where": "已下线",            "deprecated": True, "deprecatedNote": "后端无任何调用方"},
    # ── UI 脚本型(agentic) ──
    {"key": "ui-script",               "label": "AI 生成 UI 自动化脚本","category": "ui_script", "where": "已封存",            "deprecated": True, "deprecatedNote": "改由外部 Claude Code 写好回推，见 docs/cc-platform-loop-spec.md 红线 1"},
    {"key": "ui-script-repair",        "label": "UI 脚本自动修复",      "category": "ui_script", "where": "已封存",            "deprecated": True, "deprecatedNote": "自愈归 CC，平台只出证据"},
]

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
