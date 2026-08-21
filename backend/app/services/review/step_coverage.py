"""手工步骤 ↔ 脚本：**每个操作有没有真做、每个预期有没有真查**（review-spec §3 第 2、3 条）。

这两条是这一轮新提的硬性要求。原来靠模型判、算 major ——
而模型读脚本时会被"长得像"的代码骗过去：步骤说「点击弃用服务」，
脚本里有个 `.click()` 点的是「确认」，模型看见有 click 就说落实了。

## 判据：锚点比对，不是数数

`traffic_diff` 里原来那条是**数数**（手工 N 步、脚本 M 个动作，M < N/2 就报）。
数数的毛病两头都犯：一步「进入详情页并展开更多下拉」在脚本里是两个动作（少报），
而两步「点确认」「点提交」可能被一句链式调用覆盖（误报）。

改成对**锚点**：中文用例的步骤里几乎必然带引号标签 ——
`点击「弃用服务」`、`头部状态徽标变为「已弃用」`。这些标签在脚本里
要么以文本选择器出现（`name="弃用服务"`、`text=已弃用`），要么以断言期望值出现。
**一个都找不到 = 这一步脚本里没有**，这是确定判断，不需要理解业务。

## 反例（什么时候它会冤枉人）—— 每条都做了出口

1. **脚本全用 i18n key / data-testid，不写中文字面量。**
   这时候锚点必然对不上，但脚本是对的。出口：脚本里**一个中文字符都没有**时
   整条判据不生效（`_script_uses_literals`）。半中半英的脚本照判 ——
   它既然写得出一部分中文，就不是 key 驱动的写法。
2. **前置/清理步骤**不要求页面动作（数据可以走 API 铺）。出口：`_ROLE_SKIP` 跳过。
3. **预期是隐式验证**（比如"页面不报错"）。出口：`expected` 里没有引号锚点、
   也没有数字/状态码时，不参与「预期有没有查」的判定 —— 只数总量。
"""
from __future__ import annotations

import re

# 「」『』"" 里的东西就是锚点：按钮名、状态文案、字段名
_ANCHOR = re.compile(r"[「『“]([^」』”]{1,24})[」』”]")
# 预期里的硬信号：状态码、数字断言
_NUMERIC = re.compile(r"\b([1-5]\d{2})\b|\b(\d+)\s*(条|个|次|项)")
# 前置/清理不要求页面动作
_ROLE_SKIP = re.compile(r"^\s*(前置|准备|制备|清理|收尾|环境|数据准备)\s*[:：]?")
_CJK = re.compile(r"[一-鿿]")

_ACTION_CALL = re.compile(
    r"\.(click|dblclick|fill|type|check|uncheck|select_option|selectOption|press|"
    r"set_input_files|setInputFiles|hover|tap|drag_to|dragTo|goto|focus|clear)\s*\(")
_ASSERT_CALL = re.compile(
    r"\bexpect\s*\(|\bassert\b|\.to_(have|be|contain)_|\.toHave|\.toBe|\.toContain|"
    r"assert_that|assertEqual|assertTrue|should_")


def _script_uses_literals(script: str) -> bool:
    """脚本里有中文字面量吗。没有 = 它是 key/testid 驱动的，锚点判据不适用。"""
    return bool(_CJK.search(script or ""))


def _anchors(text: str) -> list[str]:
    return [a.strip() for a in _ANCHOR.findall(text or "") if a.strip()]


def _hit(script: str, anchors: list[str]) -> bool:
    return any(a in script for a in anchors)


def analyze(manual_steps: list, script_content: str | None,
            scenario_steps: list | None = None) -> list[dict]:
    """产出 findings。脚本为空时不下结论（该由「承诺要做 UI 却没脚本」那条判据管）。"""
    out: list[dict] = []

    # ── ③ 接口场景里「验证:」角色的步骤必须带断言 ─────────────────
    # **放在最前面，且不依赖 UI 脚本**：这条判的是接口场景自己。
    # 原来它写在函数末尾、共用上面那两个 early return，于是"只有接口场景、
    # 没有 UI 脚本"的用例（spec_api 档，占多数）一次都没判到。
    for st in (scenario_steps or []):
        if not isinstance(st, dict):
            continue
        name = str(st.get("name") or "")
        if re.match(r"^\s*(验证|校验|断言)\s*[:：]", name) and not (st.get("assertions") or []):
            out.append({
                "kind": "verify_step_without_assertion", "severity": "blocker", "where": "api",
                "detail": f"接口场景步骤「{name[:40]}」的角色是**验证**，却一条断言都没有 —— "
                          f"这一步只是发了个请求，什么都没验。",
            })
            break               # 一条就够说明问题，不刷屏

    script = str(script_content or "")
    if not script.strip():
        return out

    steps = [s for s in (manual_steps or [])
             if isinstance(s, dict) and not _ROLE_SKIP.match(str(s.get("action") or ""))]
    if not steps:
        return out

    literals = _script_uses_literals(script)

    # ── ① 每个操作步骤，脚本里有没有真做 ─────────────────────────
    if literals:
        missing_act = []
        for s in steps:
            anchors = _anchors(s.get("action") or "")
            if not anchors:
                continue                # 没锚点的步骤这条判据管不了，跳过（不猜）
            if not _hit(script, anchors):
                missing_act.append((s.get("seq"), (s.get("action") or "")[:60], anchors[:3]))
        if missing_act:
            detail = "；".join(
                f"步骤{seq}「{'/'.join(a)}」" for seq, _txt, a in missing_act[:6])
            out.append({
                "kind": "step_action_not_in_script", "severity": "blocker", "where": "ui",
                "detail": f"这些步骤写了要做的动作，脚本里找不到对应的元素："
                          f"{detail}"
                          + (f"（另有 {len(missing_act) - 6} 步同样对不上）"
                             if len(missing_act) > 6 else "")
                          + "。步骤里承诺的操作脚本没做，这条用例在页面上就不可复现 —— "
                            "要么把动作补进脚本，要么把步骤改成脚本真正做的事。"
                            "**如果脚本是用 i18n key 或 data-testid 定位的**，"
                            "把 key 和中文的对应写进步骤备注，这条就不会再报。",
            })

    # ── ② 每个预期，脚本里有没有对应的检查 ───────────────────────
    asserts = len(_ASSERT_CALL.findall(script))
    expect_steps = [s for s in steps if str(s.get("expected") or "").strip()]

    if expect_steps and asserts == 0:
        out.append({
            "kind": "no_assertion_for_expectations", "severity": "blocker", "where": "ui",
            "detail": f"{len(expect_steps)} 步写了预期结果，脚本里**一个断言都没有** —— "
                      f"这条脚本跑完永远是绿的，被测系统坏成什么样它都不会红。"
                      f"每个「预期」至少要有一处检查。",
        })
    elif literals and expect_steps:
        # 有断言，但**具体某几条预期**的锚点在脚本里一个字都没出现 = 那几条没查
        missing_exp = []
        for s in expect_steps:
            exp = str(s.get("expected") or "")
            anchors = _anchors(exp)
            if not anchors and not _NUMERIC.search(exp):
                continue            # 隐式预期，这条判据不管（见模块头反例 3）
            if anchors and not _hit(script, anchors):
                missing_exp.append((s.get("seq"), anchors[:3]))
        if missing_exp:
            detail = "；".join(f"步骤{seq}「{'/'.join(a)}」" for seq, a in missing_exp[:6])
            out.append({
                "kind": "expectation_not_asserted", "severity": "blocker", "where": "ui",
                "detail": f"这些步骤的预期，脚本里没有任何一处检查它："
                          f"{detail}"
                          + (f"（另有 {len(missing_exp) - 6} 条同样没查）"
                             if len(missing_exp) > 6 else "")
                          + "。预期写了不查，等于没写 —— 这几处坏掉用例照样绿。"
                            "**如果这几条是靠别的断言间接覆盖的**，"
                            "把那句断言指到这个预期上（或合并步骤），别让它看起来没查。",
            })

    return out
