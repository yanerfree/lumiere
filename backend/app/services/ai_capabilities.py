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
        "defaultModel": "claude-haiku-4-5-20251001",
        "recommend": "文本类任务用 Haiku 性价比最高;质量评审、场景建模等复杂任务可切 Sonnet-4-6 / Sonnet-5。",
    },
    "ui_script": {
        "label": "UI 脚本生成",
        "icon": "🎭",
        "defaultModel": "claude-sonnet-4-6",
        "recommend": "Agentic 浏览器探索 + 自愈,必须用强模型(推荐 Sonnet-4-6 / Sonnet-5 / Opus);"
                     "切成 Haiku 会导致工具调用循环失败,不要选。",
    },
}

# 全站 AI 功能清单。category 决定归属哪个内置档位;capability key 由各调用点传给 resolver。
CAPABILITY_REGISTRY = [
    # ── 文本生成型 ──
    {"key": "tb-case-generate",        "label": "AI 生成接口用例",      "category": "text",      "where": "用例管理 / AI 侧栏"},
    {"key": "tb-quality-review",       "label": "用例质量评审",         "category": "text",      "where": "用例管理"},
    {"key": "tb-diagnose",             "label": "失败诊断",             "category": "text",      "where": "测试报告详情"},
    {"key": "scenario-gen",            "label": "功能场景测试生成",     "category": "text",      "where": "场景生成 Stage1-4"},
    {"key": "api-test-generate",       "label": "接口测试场景生成",     "category": "text",      "where": "接口测试"},
    {"key": "api-test-optimize",       "label": "接口场景 AI 优化",     "category": "text",      "where": "接口测试步骤"},
    {"key": "pytest-script",           "label": "pytest 脚本生成",      "category": "text",      "where": "AI 脚本弹窗"},
    {"key": "doc-generate",            "label": "文档生成",             "category": "text",      "where": "文档管理"},
    {"key": "doc-generate-screenshots","label": "文档带截图生成",       "category": "text",      "where": "文档管理"},
    {"key": "doc-optimize",            "label": "文档优化",             "category": "text",      "where": "文档管理"},
    {"key": "exploratory-charter",     "label": "探索测试 Charter 生成","category": "text",      "where": "探索测试"},
    {"key": "toolbox-regex",           "label": "工具箱-正则生成",      "category": "text",      "where": "工具箱"},
    # ── UI 脚本型(agentic) ──
    {"key": "ui-script",               "label": "AI 生成 UI 自动化脚本","category": "ui_script", "where": "AI 脚本弹窗 / 用例详情"},
    {"key": "ui-script-repair",        "label": "UI 脚本自动修复",      "category": "ui_script", "where": "AI 脚本弹窗"},
]

_KEY_TO_CATEGORY = {c["key"]: c["category"] for c in CAPABILITY_REGISTRY}


def category_of(capability: str | None) -> str:
    """把调用方传入的 capability(模块 key,或直接是 'text'/'ui_script')映射到档位类别。"""
    if not capability:
        return "text"
    if capability in BUILTIN_CATEGORIES:
        return capability
    return _KEY_TO_CATEGORY.get(capability, "text")


def modules_for_category(category: str) -> list[dict]:
    return [c for c in CAPABILITY_REGISTRY if c["category"] == category]
