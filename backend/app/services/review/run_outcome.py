"""真跑挂了到底怪谁 —— 三分归因（review-spec §9）。

**不做这个归因的后果很具体**：被测系统真有 bug 时脚本必然跑红。
这时候直接打回用例，等于「用例被判死刑、bug 没人管」—— 而且被测系统一出问题，
整批用例全红全打回，第二天没人敢信这套审核。

所以真跑失败先分三档：

| 归因 | 结论 | 后续 |
|---|---|---|
| 脚本/断言写错 | 打回用例 | CC 整改后再审 |
| 被测系统 bug | **用例照样可以通过** | 开失败单，留痕迹 |
| 环境 / 数据问题 | **无法审核** | 既不算通过也不算打回 |

## 判据怎么定的（对着 RULES.md 那四条元规则）

归因是**用后果分档**，不是用把握度分档：

- **环境类**和**脚本类**都只认**确定信号**——连不上、DNS 挂了、502、
  选择器语法错、Python/JS 抛异常。这些不需要理解业务就能判，也不存在
  "合法写法长这样"的反例。
- 剩下的（断言没过、接口返业务错误码）**一律不算脚本的错**。
  理由见上：把"没判出来"默认成"用例的错"，正好制造那个最坏后果。
  它落到 `system_bug`，开单留痕，但**不阻止这条用例通过**——
  真正写错的断言由别的判据（恒真、只断状态码、写完没读回）去抓，
  那些判据不需要真跑就成立，不会因为归因保守而漏掉。

反例（这套判据什么时候会冤枉人）：被测系统返回 500 且**恰好**是脚本
传了非法参数导致的，会被归成 system_bug 而不是 script_bug ——
代价是多开一张单，不是错误打回，可以接受。
"""
from __future__ import annotations

import re

OK = "ok"                       # 跑成功了
NO_ENV = "no_env"               # 没环境可跑
NOTHING_TO_RUN = "nothing_to_run"   # 既没 UI 脚本也没接口场景
ENV_DOWN = "env_down"           # 环境本身挂了
SCRIPT_BUG = "script_bug"       # 脚本自己跑不起来
SYSTEM_BUG = "system_bug"       # 跑起来了，被测系统这边不对

# 这三档都是「无法审核」——既不算通过也不算打回
INCONCLUSIVE_KINDS = (NO_ENV, NOTHING_TO_RUN, ENV_DOWN)

# **环境类**：连不上、DNS、网关、证书。全是不需要理解业务就能判的信号。
_ENV_DOWN = re.compile(
    r"ECONNREFUSED|ECONNRESET|ETIMEDOUT|EHOSTUNREACH|ENETUNREACH|ENOTFOUND|"
    r"getaddrinfo|Connection refused|Connection reset|Name or service not known|"
    r"net::ERR_(CONNECTION|NAME_NOT_RESOLVED|ADDRESS_UNREACHABLE|INTERNET_DISCONNECTED)|"
    r"Protocol error|Cannot navigate to invalid URL|"
    r"\b(502|503|504)\b|Bad Gateway|Service Unavailable|Gateway Time-?out|"
    r"SSL|certificate verify failed|CERT_",
    re.I,
)

# **脚本类**：语法错、用错 API、选择器写错。同样是确定信号 ——
# 这些错误无论被测系统好不好都会出现。
_SCRIPT_BUG = re.compile(
    r"SyntaxError|IndentationError|NameError|AttributeError|TypeError|ImportError|"
    r"ModuleNotFoundError|UnboundLocalError|KeyError|is not a function|"
    r"strict mode violation|resolved to \d+ elements|"
    r"waiting for (locator|selector)|locator\.[a-z_]+: |"
    r"Unknown engine|Unsupported token|Invalid selector|"
    r"Target page, context or browser has been closed",
    re.I,
)


def is_env_error(text: str | None) -> bool:
    """这段报错是环境类的吗。给队列熔断用 —— 熔断**只数环境类**，
    脚本挂了、系统有 bug 都不该触发它（那是这条用例自己的事，后面照样该审）。"""
    return bool(text) and bool(_ENV_DOWN.search(str(text)))


def classify(fresh_run: dict | None) -> dict:
    """把一次 `_run_and_diff` 的结果归成一档。

    入参就是 `evidence["freshRun"]`。返回 `{"kind", "reason", "detail"}`。
    """
    fr = fresh_run or {}

    if fr.get("skipped"):
        return {"kind": NO_ENV, "reason": "没有可用环境",
                "detail": str(fr["skipped"])[:400]}
    if fr.get("note"):                      # "既没 UI 脚本也没接口场景，没得跑"
        return {"kind": NOTHING_TO_RUN, "reason": "没有可执行的产物",
                "detail": str(fr["note"])[:400]}

    err = str(fr.get("error") or "")
    status = str(fr.get("status") or "")
    failed_steps = fr.get("failedSteps") or []
    blob = " ".join([err, status, *[str(s) for s in failed_steps]])

    if err or status in ("failed", "error"):
        if _ENV_DOWN.search(blob):
            return {"kind": ENV_DOWN, "reason": "环境连不上或网关挂了",
                    "detail": (err or status)[:400]}
        if _SCRIPT_BUG.search(blob):
            return {"kind": SCRIPT_BUG, "reason": "脚本本身跑不起来",
                    "detail": (err or status)[:400]}
        # 剩下的一律不算脚本的错 —— 见模块头「判据怎么定的」。
        return {"kind": SYSTEM_BUG, "reason": "跑起来了但没跑过",
                "detail": (err or status)[:400]}

    # 接口场景：有 failed 计数
    if (fr.get("failed") or 0) > 0:
        if _SCRIPT_BUG.search(blob):
            return {"kind": SCRIPT_BUG, "reason": "脚本本身跑不起来",
                    "detail": "、".join(str(s) for s in failed_steps[:4])[:400]}
        return {"kind": SYSTEM_BUG, "reason": f"{fr['failed']} 步没过",
                "detail": "、".join(str(s) for s in failed_steps[:4])[:400]}

    return {"kind": OK, "reason": "跑通了", "detail": None}


def to_finding(outcome: dict) -> dict | None:
    """归因结果里需要摆到 findings 上的那部分。

    严重度对着**后果**定：
      · 脚本跑不起来 → blocker（放进回归就是必红，或者根本没在验东西）
      · 被测系统不对 → **不是 blocker**（用例没错，错的是系统）→ 开单，不挡通过
      · 环境类 / 没得跑 → 不发 finding，它走「无法审核」那条路，
        发 finding 会让人以为是用例的问题
    """
    kind = outcome.get("kind")
    if kind == SCRIPT_BUG:
        return {"kind": "script_cannot_run", "severity": "blocker", "where": "run",
                "detail": f"真跑挂在脚本自己身上：{outcome.get('detail') or outcome.get('reason')}。"
                          f"这不是被测系统的问题 —— 脚本这样进回归就是必红。"}
    if kind == SYSTEM_BUG:
        return {"kind": "system_under_test_failed", "severity": "minor", "where": "run",
                "detail": f"真跑没通过，但不像脚本的错：{outcome.get('detail') or outcome.get('reason')}。"
                          f"**按被测系统问题处理**：已开失败跟进单，这条用例本身不因此被打回。"
                          f"如果确认是断言写错了，改完再审一次。"}
    return None
