"""失败分类（A4）——只判「现象」，不判「原因」。

边界（docs/cc-platform-loop-spec.md §2.3 + 红线 3）：
    平台判「是什么」，CC 判「为什么」。

所以这里输出的是 timeout / element_not_found / assertion_mismatch / http_5xx /
script_error / dependency_unresolved 这类**现象**，不是 script_bug / system_bug /
case_expired 这类**归因**。

为什么不让规则去归因：产品改了按钮 id 导致定位器失败，语义上是「用例过期」；
而同一个现象也可能真是脚本写错了选择器。平台从错误栈和 HAR 里**永远**区分不了这两者，
逼规则去猜只会得到一个看着有道理的错答案，而且会把这个错答案当事实存进库。

判不出来就老实标 unknown —— 这不是失败，这是正确行为。unknown 交给 CC，它能看截图、
能读脚本、能比对需求点，比规则引擎强。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

# 六类现象 + 兜底
TIMEOUT = "timeout"
ELEMENT_NOT_FOUND = "element_not_found"
ASSERTION_MISMATCH = "assertion_mismatch"
HTTP_5XX = "http_5xx"
SCRIPT_ERROR = "script_error"
DEPENDENCY_UNRESOLVED = "dependency_unresolved"
UNKNOWN = "unknown"

PHENOMENA = [
    TIMEOUT, ELEMENT_NOT_FOUND, ASSERTION_MISMATCH,
    HTTP_5XX, SCRIPT_ERROR, DEPENDENCY_UNRESOLVED, UNKNOWN,
]

# 失败前多少秒内的请求算「和这次失败相关」。锚点用 HAR 里最后一条请求的时间——
# tea_step 只落 duration_ms、没有绝对时间，拿不到真正的"失败时刻"；而失败总是
# 发生在这次执行的尾部，用最后一条网络活动当锚点足够，也不用改插件。
FAILURE_WINDOW_SECONDS = 5

# 平台自己抛的错，文案是我们自己写的，100% 可判
_DEPENDENCY_RE = re.compile(
    r"变量未解析|未解析的变量|资源探测失败|前置资源|未匹配到目标资源|\$\{[A-Za-z_][\w]*\}",
)
# 脚本自身写坏了 —— Python 层面的错误，和被测系统无关
_SCRIPT_ERROR_RE = re.compile(
    r"\b(NameError|ImportError|ModuleNotFoundError|SyntaxError|IndentationError|"
    r"AttributeError|TypeError|KeyError|IndexError|ZeroDivisionError)\b",
)
# 定位器找不到元素（含 strict mode 命中多个）
_ELEMENT_RE = re.compile(
    r"waiting for (?:locator|selector)|Locator\.\w+:\s*Timeout|strict mode violation|"
    r"resolved to \d+ elements|element is not (?:visible|attached|enabled)|"
    r"locator\([^)]*\) to be",
    re.I,
)
# 断言不匹配。
# ⚠ 这几个模式必须**先于**元素匹配 —— Playwright 的 expect() 失败文本里同时含
# AssertionError 和一段 "waiting for locator" 的 call log，按元素判会把
# "元素找到了但内容不对" 误判成 "元素找不到"（dogfood 实测踩到）。判据是
# 「有实际值」：locator resolved / Actual value 说明元素**找到了**，只是值不对。
_ASSERTION_RE = re.compile(
    r"\bAssertionError\b|Actual value\s*:|Locator expected to|"
    r"unexpected value|Expected:.*Received:|assert .*==",
    re.I,
)
# 元素确实**没找到**的特征：定位器等超时且从没解析到任何元素。
# "locator resolved to <...>" 出现就说明找到了，那是断言问题不是定位问题。
_ELEMENT_RESOLVED_RE = re.compile(r"locator resolved to|Actual value\s*:", re.I)
# Playwright 自己把话说死的措辞：元素**没找到**。
# 出现这句就不必再推断 —— 它比 "Actual value:" 这类间接线索硬。
#
# 上一轮为了修「元素找到了但值不对被误判成 element_not_found」，加了
# "Actual value: 出现即视为元素已解析" 的判据。但 expect(...).to_be_visible()
# 失败时文本是 `Actual value: None ... Error: element(s) not found`——
# None 恰恰说明**没找到**，于是又反向误判成 assertion_mismatch（dogfood2 实测）。
# 两次误判方向相反，说明"靠有没有实际值来推断"本身就不够；这条是直接证据。
_ELEMENT_ABSENT_RE = re.compile(r"element\(s\) not found|Actual value\s*:\s*None", re.I)
# 整体超时（平台执行器的文案 / pytest 超时）
_TIMEOUT_RE = re.compile(r"执行超时|Timeout .*exceeded|TimeoutError|timed out", re.I)


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def _parse_dt(v: Any) -> datetime | None:
    if not v or not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def _tail_5xx(captured: list[dict] | None, base_url: str | None = None,
              failed_at=None) -> list[dict]:
    """失败窗口内、同域的 5xx。

    两道限制缺一不可：
    - **同域**：第三方埋点/CDN 挂了不是被测系统的问题
    - **窗口**：一次登录+CRUD 抓 70 多条，任何位置的一个 5xx 都能让规则误判成
      「系统挂了」，而真实失败可能只是选择器过期。这正是「见 5xx 就判 system_bug」
      那类规则拿高分却在真实场景里全错的原因。

    窗口锚在**失败时刻**（failed_at），不是"最后一条抓包"。两者能差很远：
    页面早就静默了、定位器又干等 10 秒才超时 —— 这时候按最后一条抓包算，
    十几秒前一个无关的 5xx 还在窗口内，就会把"选择器过期"判成"系统挂了"。
    拿不到 failed_at 才退回用最后一条抓包（老数据没有这个时间）。
    """
    if not captured:
        return []
    target_host = _host_of(base_url) if base_url else ""
    if not target_host:
        hosts: dict[str, int] = {}
        for r in captured:
            h = _host_of(r.get("url", ""))
            if h:
                hosts[h] = hosts.get(h, 0) + 1
        target_host = max(hosts, key=hosts.get) if hosts else ""

    anchor = failed_at
    if anchor is None:
        times = [t for t in (_parse_dt(r.get("startedAt")) for r in captured) if t]
        anchor = max(times) if times else None
    cutoff = (anchor - timedelta(seconds=FAILURE_WINDOW_SECONDS)) if anchor else None

    hits = []
    for r in captured:
        st = r.get("status")
        if not isinstance(st, int) or st < 500:
            continue
        if target_host and _host_of(r.get("url", "")) != target_host:
            continue
        if cutoff is not None:
            t = _parse_dt(r.get("startedAt"))
            if t and t < cutoff:
                continue
        hits.append(r)
    return hits


def classify(
    status: str | None,
    error_summary: str | None,
    stdout: str | None = None,
    captured_requests: list[dict] | None = None,
    base_url: str | None = None,
    failed_at=None,
) -> dict:
    """返回 {phenomenon, reason, evidence}。

    通过的执行不分类。判不出来一律 unknown —— 宁可说"我不知道"，也不要给一个
    看起来很有道理的错答案，那会污染后面所有基于它的统计和归因。
    """
    if status == "passed":
        return {"phenomenon": None, "reason": None, "evidence": {}}

    text = f"{error_summary or ''}\n{stdout or ''}"
    fivexx = _tail_5xx(captured_requests, base_url, failed_at)
    evidence: dict = {}
    if fivexx:
        evidence["fiveXX"] = [
            {"method": r.get("method"), "url": r.get("url"), "status": r.get("status")}
            for r in fivexx[:5]
        ]

    # 顺序即优先级，从"最不可能误判"往下排。
    # dependency 是平台自己抛的错（文案我们自己写的）；script_error 是 Python 语言层面的
    # 错误，和被测系统无关；这两类判错的可能性最低，所以放最前。
    if _DEPENDENCY_RE.search(text):
        return {"phenomenon": DEPENDENCY_UNRESOLVED,
                "reason": "命中平台自身的前置数据/变量未解析报错", "evidence": evidence}

    if _SCRIPT_ERROR_RE.search(text):
        return {"phenomenon": SCRIPT_ERROR,
                "reason": "脚本抛出 Python 语言层面的异常，与被测系统无关", "evidence": evidence}

    # 5xx 优先于元素/断言：后端 500 了，前端当然渲染不出元素、断言当然不匹配 ——
    # 这时候报"元素找不到"是在描述症状的下游。
    if fivexx:
        first = fivexx[0]
        return {"phenomenon": HTTP_5XX,
                "reason": f"失败前 {FAILURE_WINDOW_SECONDS}s 内被测系统返回 "
                          f"{first.get('status')}：{first.get('method')} {first.get('url')}",
                "evidence": evidence}

    # Playwright 明说"没找到"的，直接判，不再推断。
    if _ELEMENT_ABSENT_RE.search(text):
        return {"phenomenon": ELEMENT_NOT_FOUND,
                "reason": "定位器没找到元素（Playwright 明确报 element(s) not found）",
                "evidence": evidence}

    # 断言在前：元素**找到了但值不对**，和元素**根本没找到**是两回事。
    # 判据是文本里有没有"实际值"（locator resolved to / Actual value）。
    if _ASSERTION_RE.search(text) and _ELEMENT_RESOLVED_RE.search(text):
        return {"phenomenon": ASSERTION_MISMATCH,
                "reason": "元素找到了，但它的值和期望不符", "evidence": evidence}

    if _ELEMENT_RE.search(text):
        return {"phenomenon": ELEMENT_NOT_FOUND,
                "reason": "定位器没找到元素（或命中多个）", "evidence": evidence}

    if _ASSERTION_RE.search(text):
        return {"phenomenon": ASSERTION_MISMATCH,
                "reason": "断言的期望值与实际不符", "evidence": evidence}

    # 超时放在元素/断言之后：Playwright 的元素等待失败也叫 Timeout，先归元素更准。
    # 走到这里的才是"整体超时"——没有更具体的线索。
    if _TIMEOUT_RE.search(text):
        return {"phenomenon": TIMEOUT, "reason": "执行整体超时", "evidence": evidence}

    return {"phenomenon": UNKNOWN,
            "reason": "错误形态不属于已知六类，交给 CC 看截图和脚本判断",
            "evidence": evidence}
