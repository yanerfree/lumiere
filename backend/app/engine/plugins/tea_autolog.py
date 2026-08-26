"""tea_autolog — 让**不改一行**的普通 Playwright 脚本也能出步骤和验证结果。

为什么要它：`tea_step` 需要脚本自己 `with tea_step(...)` 包起来，而 CC 回推的脚本是
普通 Playwright —— 没有理由要求它先学平台的私有写法。于是执行历史展开之后只有
pytest 那一行 `1 passed in 12.56s`：**脚本里 16 个 expect() 断言一个都不落**，
跑挂了也只知道"挂了"，看不出挂在哪一步、验的是什么。实测就是这么被指出来的。

做法：把 Playwright 的断言方法和几个关键动作各包一层，每次调用打一对
`##STEP_START##` / `##STEP_END##`，格式跟 tea_step 完全一致 ——
所以 SSE 转发、结果解析、前端渲染三处都不用改。

只包**语义清楚**的那几个：断言是"验了什么"，goto/click/fill 是"做了什么"。
不包 locator()/get_by_*() —— 那些只是取元素，包进来会把一屏刷满噪音，
而噪音多了人就不看了，等于白做。

埋点是附加价值，**绝不能拖垮执行本身**：任何一处包不上就跳过那一处，
整体挂了就退回原样（只有 pytest 一行），而不是让一条本来能跑的用例因埋点而失败。
"""
from __future__ import annotations

import json
import re
import time

_seq = [0]

# 断言方法名 → **结论式中文**。这是"验证结果"那一半，最不能少。
#
# 为什么要翻：`验证 button「禁用」 to_have_count = 0` 是给机器看的 ——
# 测试人员读不出它的意思，而它恰恰是这条用例最关键的一条断言
# （草稿态不该出现启用/禁用按钮）。`to_have_count = 0` 尤其反直觉：
# 字面是"数量等于 0"，实际意思是"这东西不该存在"。
#
# 值用 {v} 占位，没有期望值的就不带。
_ASSERT_CN = {
    "to_be_visible": "应可见",
    "not_to_be_visible": "应不可见",
    "to_be_hidden": "应隐藏",
    "not_to_be_hidden": "应不隐藏",
    "to_contain_text": "应包含文本「{v}」",
    "not_to_contain_text": "应不含文本「{v}」",
    "to_have_text": "文本应为「{v}」",
    "not_to_have_text": "文本应不为「{v}」",
    # 0 个单独说 —— 「应有 0 个」还是绕，「不应出现」才是人话
    "to_have_count": "应有 {v} 个",
    "to_have_value": "值应为「{v}」",
    "to_have_attribute": "应带属性 {v}",
    "to_be_enabled": "应可用",
    "to_be_disabled": "应禁用",
    "to_be_checked": "应已勾选",
    "not_to_be_checked": "应未勾选",
    "to_have_url": "地址应为「{v}」",
    "to_have_title": "标题应为「{v}」",
}
_ASSERTIONS = tuple(_ASSERT_CN)


def _quote(d: str) -> str:
    """给定位描述加引号，但**不套两层** —— `_pretty` 翻出来的 `button「禁用」`
    本身已经带书名号，再包一层就是 `「button「禁用」」`，比不加更难读。"""
    return d if "「" in d else f"「{d}」"


def _assert_label(target_desc: str, name: str, args) -> str:
    """一条断言写成人能读的结论。"""
    v = args[0] if args else ""
    t = _quote(target_desc)
    if name == "to_have_count" and v == 0:
        return f"{t} 不应出现"
    tpl = _ASSERT_CN.get(name, name)
    return f"{t} " + tpl.format(v=v)


# 密码字段的特征。**值必须遮掉** —— 执行历史是给人看、会被分享、还会进 CC 的上下文，
# 把明文密码印进去是纯粹的泄漏。实测第 3 步就印出了 'Admin@123'。
_SECRET_SEL_RE = re.compile(r"password|passwd|pwd|secret|token|api[_-]?key", re.I)


def _pretty(sel: str) -> str:
    """把 Playwright 的内部选择器翻成人能读的。

    不翻的话步骤名长这样，中文全是转义、`internal:` 前缀刷屏：
        验证 .menu-item >> internal:has-text="\\u53d1\\u5e03\\u4e0a\\u7ebf"i >> nth=0
    翻完是：
        验证 .menu-item 含文本「发布上线」第1个
    ——**看不懂的步骤名等于没有步骤名**，这一步不做，前面的埋点白搭。
    """
    # Playwright 内部选择器把非 ASCII 编码成 \uXXXX，而且**可能是双重转义**
    # （`\\u8bf7`）。只解一次的话第一层脱完还剩 `请`，页面上仍然是一串转义 ——
    # 实测「验证 .input 含文本「请选择」」就是这么漏出去的。
    # 所以循环解到解不动为止，加个次数上限免得遇到怪串死循环。
    for _ in range(3):
        if "\\u" not in sel:
            break
        try:
            decoded = sel.encode("latin-1", "ignore").decode("unicode_escape")
        except Exception:                             # noqa: BLE001
            break
        if decoded == sel:
            break
        sel = decoded
    sel = re.sub(r'internal:role=(\w+)\[name="([^"]*)"[is]?\]', r'\1「\2」', sel)
    sel = re.sub(r'internal:attr=\[([\w-]+)="([^"]*)"[is]?\]', r'\1=「\2」', sel)
    sel = re.sub(r'internal:testid=\[data-testid="([^"]*)"[is]?\]', r'testid=\1', sel)
    sel = re.sub(r'internal:has-text="([^"]*)"[is]?', r'含文本「\1」', sel)
    sel = re.sub(r'internal:text="([^"]*)"[is]?', r'文本「\1」', sel)
    sel = re.sub(r'internal:label="([^"]*)"[is]?', r'标签「\1」', sel)
    sel = re.sub(r">> nth=(\d+)", lambda m: f"第{int(m.group(1)) + 1}个", sel)
    return re.sub(r"\s*>>\s*", " ", sel).strip()[:80]


def _desc(target) -> str:
    """把元素/页面还原成人能读的定位描述。

    Locator 的 repr 形如 `<Locator frame=... selector='tbody tr >> ...'>`，
    里面的 selector 才是人要看的东西。
    """
    sel = getattr(target, "_selector", None)
    if isinstance(sel, str) and sel:
        return _pretty(sel)
    s = str(target)
    if "selector=" in s:
        return _pretty(s.split("selector=", 1)[1].strip("'\"> "))
    if type(target).__name__ == "Page":
        return "页面"
    return type(target).__name__


def _emit(action: str, phase: str, fn, *a, **kw):
    """跑一步并同时落两个地方 —— 两条执行路径各读一个，缺一个就有一条路看不到步骤。

    · `print` 的标记：SSE 流式路径逐行读 stdout，用来做**实时**进度。
    · `tea_step` 的步骤表：非流式路径（lum_run_ui_script / 批量回归）压根不读 stdout，
      它读 tea_capture 在测试结束时 flush 出来的 `.tea_results/{func}.json`。
      第一版只打了 print，结果非流式路径跑完还是 0 步 —— 实测踩到。
    """
    _seq[0] += 1
    n = _seq[0]
    step = {"seq": n, "action": action, "phase": phase, "status": "passed",
            "duration_ms": 0, "requests": []}
    _record_start(step)
    print("##STEP_START##" + json.dumps(
        {"seq": n, "action": action, "phase": phase}, ensure_ascii=False), flush=True)
    t0 = time.monotonic()
    status, err = "passed", ""
    try:
        return fn(*a, **kw)
    except Exception as e:                            # noqa: BLE001
        status, err = "failed", str(e)[:200]
        raise
    finally:
        ms = int((time.monotonic() - t0) * 1000)
        step["status"], step["duration_ms"] = status, ms
        if err:
            step["error"] = err
        _record_end(step)
        print("##STEP_END##" + json.dumps(
            {"seq": n, "status": status, "duration_ms": ms, "error": err},
            ensure_ascii=False), flush=True)


def _record_start(step: dict) -> None:
    """挂进 tea_step 的步骤表，让 flush_steps 能把它写进 JSON。"""
    try:
        import tea_step
        tea_step._current_steps.append(step)
        tea_step._step_stack.append(step)     # tea_capture 靠栈顶把 HTTP 请求归到本步
    except Exception:                                 # noqa: BLE001
        pass


def _record_end(step: dict) -> None:
    try:
        import tea_step
        if tea_step._step_stack and tea_step._step_stack[-1] is step:
            tea_step._step_stack.pop()
    except Exception:                                 # noqa: BLE001
        pass


def _fill_label(target, args) -> str:
    """填写动作。密码类字段只说"填写"，不印值。"""
    d = _desc(target)
    if not args:
        return f"填写 {_quote(d)}"
    if _SECRET_SEL_RE.search(d):
        return f"填写 {_quote(d)} = ***"
    return f"在 {_quote(d)} 填入 {str(args[0])[:40]}"


def install() -> int:
    """装上埋点，返回成功包住的方法数（0 表示什么都没包上）。"""
    wrapped = 0
    try:
        import playwright.sync_api as pw
    except Exception:                                 # noqa: BLE001
        return 0

    orig_expect = getattr(pw, "expect", None)
    if orig_expect is not None and not getattr(orig_expect, "_tea_wrapped", False):
        def wrap_expect(target, *ea, **ekw):
            obj = orig_expect(target, *ea, **ekw)
            label_of = _desc(target)
            for name in _ASSERTIONS:
                orig = getattr(obj, name, None)
                if orig is None:
                    continue

                def make(nm, f):
                    def inner(*a, **kw):
                        return _emit(_assert_label(label_of, nm, a), "verify", f, *a, **kw)
                    return inner
                try:
                    setattr(obj, name, make(name, orig))
                except AttributeError:
                    pass      # 只读属性就跳过，别因为埋点把执行搞挂
            return obj

        wrap_expect._tea_wrapped = True
        pw.expect = wrap_expect
        wrapped += 1

    # 动作：做了什么。参数里带 URL/文本的直接写进步骤名，人一眼看得出。
    targets = (
        (pw.Page, "goto", "action", lambda s, a: f"打开页面 {str(a[0])[:70]}" if a else "打开页面"),
        (pw.Page, "wait_for_url", "action", lambda s, a: "等待页面跳转"),
        (pw.Locator, "click", "action", lambda s, a: f"点击 {_quote(_desc(s))}"),
        (pw.Locator, "fill", "action", lambda s, a: _fill_label(s, a)),
    )
    for cls, meth, phase, labeler in targets:
        orig = getattr(cls, meth, None)
        if orig is None or getattr(orig, "_tea_wrapped", False):
            continue

        def make(f, ph, lb):
            def inner(self, *a, **kw):
                return _emit(lb(self, a), ph, f, self, *a, **kw)
            inner._tea_wrapped = True
            return inner
        try:
            setattr(cls, meth, make(orig, phase, labeler))
            wrapped += 1
        except (AttributeError, TypeError):
            pass
    return wrapped


def reset() -> None:
    _seq[0] = 0
