"""助手对话编排 —— 把「可见工具集 + 用户消息」变成系统提示词，并从模型输出里解析出提议。

模型不走原生 function-calling（llm_client 不支持），走**提示词协议**：要执行某个动作时，
在回答末尾输出一个 ```json {"tool": "...", "args": {...}} ``` 块；否则纯自然语言回答。
解析只认**目录里存在**的工具，且必须在传入的可见集内 —— 模型编一个名字出来也调不动。
"""
from __future__ import annotations

import json
import re

from app.services.assistant.catalog import AssistantTool

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def build_system_prompt(tools: list[AssistantTool], project_id) -> str:
    """按**已过滤到用户权限**的工具集拼系统提示词。工具集为空时明确告知只能答不能做。"""
    lines = [
        "你是 Lumiere 测试平台的操作助手。你只能在下面列出的「可用操作」范围内帮用户做事——",
        "这些操作已经按当前登录用户的权限过滤过，清单之外的事一律做不了，也不要假装能做。",
        "",
        "回答规则：",
        "1. 能用一句话答清的（查询类结果、解释），就直接自然语言回答。",
        "2. 当用户想执行某个「可用操作」时，先用一句话说明你要做什么，然后在**回答末尾**"
        "另起一段输出一个 JSON 代码块，形如：",
        '   ```json',
        '   {"tool": "操作key", "args": {"参数名": "值"}}',
        "   ```",
        "   一次只提议一个操作。参数缺失就向用户追问，不要瞎填。",
        "3. 清单里没有的需求，直接说明你无权/无法操作，并建议用户去对应页面处理。",
        "",
    ]
    if project_id:
        lines.append(f"当前项目语境 project_id = {project_id}（项目级操作会作用在这个项目上）。")
    else:
        lines.append("当前不在具体项目里，只能做系统级操作（如列项目、建项目）。")
    lines.append("")
    lines.append("可用操作：")
    if not tools:
        lines.append("（无 —— 当前用户在此语境下没有任何可执行操作，只能回答问题。）")
    for t in tools:
        arg_desc = ""
        if t.args:
            parts = [f"{a.name}{'*' if a.required else ''}({a.type})" for a in t.args]
            arg_desc = "，参数：" + "、".join(parts)
        kind = "写" if t.mutates else "读"
        lines.append(f"- {t.key}：{t.description}[{kind}]{arg_desc}")
    lines.append("")
    lines.append("（带 * 的参数必填。写操作会在用户确认后才真正执行。）")
    return "\n".join(lines)


def parse_proposal(text: str, tools: list[AssistantTool]) -> dict | None:
    """从模型输出里解析出工具提议。返回 {"tool","args"} 或 None。

    只接受目录里存在、且在 `tools`（可见集）内的工具；否则视为无提议。
    优先取 ```json``` 代码块里最后一个带 "tool" 的对象；退而求其次扫裸 JSON。
    """
    allowed = {t.key for t in tools}
    candidates: list[str] = _JSON_BLOCK.findall(text or "")
    if not candidates:
        # 没有代码块时，兜底扫一个含 "tool" 的裸对象
        m = re.search(r'\{[^{}]*"tool"\s*:\s*"[^"]+"[^{}]*\}', text or "", re.DOTALL)
        if m:
            candidates = [m.group(0)]
    for raw in reversed(candidates):
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        key = obj.get("tool")
        if key in allowed:
            args = obj.get("args")
            return {"tool": key, "args": args if isinstance(args, dict) else {}}
    return None
