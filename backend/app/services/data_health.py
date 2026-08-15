"""数据不变量体检 —— 静默损坏只能靠巡检发现。

**为什么单独做这个。** 这一轮撞到的最严重的 bug 是驼峰中间件把用户的 HTTP 请求体
也改了：库里 `upstream_id` 取出来变成 `upstreamId`，前端一保存就写回去，场景从此对
被测系统发驼峰、被 422 拒收。而**库里和页面显示的都是驼峰，看不出被改过** ——
只会以为用例本来就写错了。它是在一次成功执行之后才坏的，中间没有任何报错。

这类形状的共同点：**没有一次请求会失败，没有一行日志会出现，只有数据静静地不对。**
单元测试测不到（它测的是函数，数据是运行时攒出来的），执行也测不到（执行只会挂在
下游，指向错误的地方）。唯一能发现的手段是定期把库扫一遍，拿不变量去对。

检测逻辑放这里（纯函数、可测），扫库的入口在 scripts/data_health_check.py。
"""
from __future__ import annotations

import re

# 蛇形 → 驼峰污染的痕迹：小写起头、中间有大写。`upstreamId` / `forwardPath`
_CAMEL_KEY_RE = re.compile(r"^[a-z]+[a-z0-9]*[A-Z]")
# 期望值写成字符串的布尔 —— 必然假红
_BOOL_STRINGS = {"true", "false", "True", "False"}


def flat_keys(obj, out=None) -> list[str]:
    """把嵌套结构里所有 dict 键摊平（含数组里的对象）。"""
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append(str(k))
            flat_keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            flat_keys(v, out)
    return out


def camel_keys_in(obj) -> list[str]:
    """这个请求体里有哪些驼峰键。

    ⚠ 驼峰本身不是错 —— 有的被测接口就用驼峰（实测「API自测项目」的
    `oldPassword`/`newPassword` 是对的）。所以体检只能报"可疑"，
    判断依据是**同一个项目里别的步骤用什么风格**，那个对比放调用方做。
    """
    seen, out = set(), []
    for k in flat_keys(obj):
        if k not in seen and _CAMEL_KEY_RE.match(k):
            seen.add(k)
            out.append(k)
    return out


def dominant_style(bodies: list) -> str | None:
    """一批请求体整体是蛇形还是驼峰。用来判"少数派"是不是被污染了。

    判据只看**同时存在蛇形和驼峰的字段名**没有意义，所以按键计数：
    含 `_` 的算蛇形票，小写起头带大写的算驼峰票。差距不到 2 倍返回 None
    （说不清，不报），避免把一个混用风格的项目全标成污染。
    """
    snake = camel = 0
    for b in bodies:
        for k in flat_keys(b):
            if "_" in k:
                snake += 1
            elif _CAMEL_KEY_RE.match(k):
                camel += 1
    if snake >= camel * 2 and snake > 0:
        return "snake"
    if camel >= snake * 2 and camel > 0:
        return "camel"
    return None


def bool_as_string_assertions(assertions) -> list[dict]:
    """断言里把布尔写成字符串的那几条 —— 必然假红。"""
    out = []
    for a in (assertions or []):
        if not isinstance(a, dict):
            continue
        exp = a.get("expected") if a.get("expected") is not None else a.get("value")
        if isinstance(exp, str) and "${" not in exp and exp in _BOOL_STRINGS:
            out.append({"field": a.get("field") or a.get("type"), "wrote": exp})
    return out


def is_protocol_envelope(body) -> bool:
    """这个请求体是协议信封（JSON-RPC / MCP），驼峰是**规范规定**的，不是污染。

    实测误报：AT-0012 的 MCP initialize body 里 `clientInfo` / `protocolVersion`
    被标成污染，而那条场景 18/18 全绿 —— MCP 走 JSON-RPC，协议字段本来就是驼峰。
    **误报比漏报更致命**：报告里出现一条假的，人就不再逐条看了，
    真的那条也跟着被忽略，等于把这个体检废掉。
    """
    return isinstance(body, dict) and (
        "jsonrpc" in body or ("method" in body and "params" in body and "id" in body)
    )


def check_step(step_body, step_assertions, project_style: str | None) -> list[dict]:
    """一个步骤的体检结论。project_style 是这个项目主流风格（dominant_style 的结果）。"""
    issues = []
    if project_style == "snake" and not is_protocol_envelope(step_body):
        camel = camel_keys_in(step_body)
        if camel:
            issues.append({
                "kind": "body_camel_pollution",
                "severity": "high",
                "keys": camel[:6],
                "why": "这个项目其余请求体都是蛇形，只有这一步是驼峰 —— "
                       "多半是被响应层驼峰化污染过（历史 bug，根因已修）。"
                       "被测系统若拒收未知字段，这一步必然 422。",
            })
    for bad in bool_as_string_assertions(step_assertions):
        issues.append({
            "kind": "assertion_bool_as_string",
            "severity": "high",
            "field": bad["field"],
            "why": f"期望值写成了字符串 \"{bad['wrote']}\"，应为 {bad['wrote'].lower()}。"
                   f"平台故意不做布尔兜底（兜了「期望 true、实际 1」会算相等，那是假绿），"
                   f"所以这条必挂。",
        })
    return issues
