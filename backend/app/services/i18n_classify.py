"""按键路径给文案分类 —— 让人一眼看出这是按钮、还是错误提示、还是状态值。

**为什么需要它。** 从被测系统 locale 导入时，我给 2400 多条一律写了
`category="text"` —— 那是个写死的常量，等于没分类。页面上「分类」一列全是 text，
筛不了、也看不出哪些是断言最常用的错误提示语。

采集器那条路能从定位方式推出分类（`get_by_role("button")` → button），
locale 文件没有定位上下文，但**键本身带着控件类型**：
`apps.btn.disable`、`services.list.searchPlaceholder`、`common.yaml.validation.nameRequired`。

分类是**存下来、可编辑**的（和 module 一样）—— 这里只负责导入时给个准确的初值，
之后人和 CC 改了以库里的为准。判不出来才落 text。
"""
from __future__ import annotations

import re

# 顺序有意义：**先判语义强的（校验/提示），再判控件形态（按钮/占位符）**。
# 「confirmDisable」既像按钮又像确认语，按钮那条要放在后面才不会抢先。
_RULES: list[tuple[re.Pattern, str]] = [
    # 校验错误 —— 测试断言最常用的一类，必须单独一档，不能混在 message 里：
    # 「必填」「格式不对」是**可预期的**输入校验，而 message 里还有成功提示。
    (re.compile(r"\b(validation|invalid|required|pattern|tooLong|tooShort|mismatch)\b", re.I), "validation"),
    # 提示 / Toast / 结果消息
    (re.compile(r"\b(msg|message|toast|notice|error|failed|failure|success|succeeded|warn|warning|hint|tip)\b", re.I), "message"),
    # 状态值（active / draft / pending…）—— 断言状态标签时用
    (re.compile(r"\b(status|state|phase|stats)\b", re.I), "status"),
    (re.compile(r"\b(placeholder|searchPlaceholder|search)\b", re.I), "placeholder"),
    (re.compile(r"\b(btn|button|action|actions|submit|cancel|confirm|save|delete|create|edit)\b", re.I), "button"),
    (re.compile(r"\b(title|heading|header|modalTitle)\b", re.I), "title"),
    (re.compile(r"\b(tab|tabs)\b", re.I), "tab"),
    (re.compile(r"\b(menu|nav|sidebar)\b", re.I), "menu"),
    (re.compile(r"\b(link|href)\b", re.I), "link"),
    (re.compile(r"\b(option|options|select|dropdown)\b", re.I), "option"),
    (re.compile(r"\b(label|field|column|col|name|desc|description)\b", re.I), "label"),
]


def classify(key: str) -> str:
    """键 → 分类。判不出来落 text（不猜）。

    只看键的**后面几段**：命名空间（services / apps）不参与判断，
    否则 `services.xxx` 里的 "service" 之类的词会到处干扰。
    """
    if not key:
        return "text"
    segs = key.split(".")
    body = ".".join(segs[1:]) if len(segs) > 1 else key
    # 驼峰拆成词，让 \b 边界能匹配到（searchPlaceholder → search Placeholder）
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", body)
    for pat, cat in _RULES:
        if pat.search(spaced):
            return cat
    return "text"
