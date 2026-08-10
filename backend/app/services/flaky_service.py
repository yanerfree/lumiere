"""flaky 自动判定与隔离。

## 为什么要有

原来的 `is_flaky` 是个纯手动布尔：人勾上，执行器就永远跳过这条用例。
问题不在"手动"，在**没有回来的路** —— 脚本修好了它还在被跳过，而且没人记得当初
为什么标它。一条被无声跳过的用例，比一条会偶发失败的用例更危险：后者至少还在报警。

## 判定用什么依据

**必须限定在同一个脚本版本内。** 实测库里 TC-XMGL-00001 有两个脚本版本、各自有失败
—— 不按 script_id 分组的话，"换了新版本脚本"的状态变化会被算成翻转，把一次成功的
修复判成 flaky。这是这套逻辑最容易错的地方。

同版本内、**最近至多 WINDOW 次**执行里翻转 >= FLIPS 次 → 隔离 QUARANTINE_DAYS 天。
（"至多"很重要：不满 WINDOW 次也判，只要已有的记录里就够 2 次翻转 ——
否则强交替的脚本要白等几轮。）

## 阈值是实测校准出来的，不再是拍脑袋

原来是"最近 3 次里 2 次翻转"，写着"没有数据支撑"。样本不够就自己造：
造了 stable / 40% 失败 / 10% 失败三条脚本，每条**真跑 24 轮**（每轮真起浏览器），
拿到的实测序列：

    stable   PPPPPPPPPPPPPPPPPPPPPPPP        实际失败率  0%
    flaky40  FFFFPPPPFFFPPFFFFPPPPFPP        实际失败率 50%
    flaky10  PPPPPPFPFFPFPFPPPPPPPPPP        实际失败率 21%

**结论一：噪音底线是 0。** 稳定脚本 24 轮零翻转，说明环境抖动不会造成误隔离，
判据可以做得更灵敏而不必担心误伤。

**结论二：原来的 3 窗口是错的 —— 连续失败不产生翻转。**
`FFFF` 的翻转数和 `PPPP` 一样是 0，于是"成片挂"的脚本躲过阈值：
50% 失败率的那条要跑到**第 23 轮**才被抓，而 21% 的第 8 轮就抓到了 ——
**严重程度和检出速度反相关**，这是彻底的错。

**结论三：方向判据（>=2 次翻转）是对的，不能换成"窗口里既有 P 又有 F"。**
二值序列里连续两次翻转必然一来一回，所以 ">=2 翻转" 天然免疫"修好了"这种
单向变化（`FFFF→PPPP` 只有 1 次翻转）。而"混合"判据会把**修好的用例关起来**
（实测在第 5 轮就误判）。错的只是窗口太短。

所以只改窗口：3 → **最近至多 7 次**。实测对比（✓ = 不触发，正确）：

| | 一直通过 | 一直失败 | 修好了 | 刚开始挂 | flaky40 | flaky10 | 强交替 |
|---|---|---|---|---|---|---|---|
| 原 固定3窗 | ✓ | ✓ | ✓ | ✓ | 第23轮 | 第8轮 | 第3轮 |
| **现 最近至多7次** | ✓ | ✓ | ✓ | ✓ | **第9轮** | 第8轮 | 第3轮 |

蒙特卡洛（跑 10 轮内触发概率）：真实失败率 0% 和 100% 时都是 **0%**（不误判、
真坏的继续报警）；50% 时 83% → **98%**；20% 时 71% → **81%**。

还能再校的：QUARANTINE_DAYS=14 仍是拍的，它取决于"人多久会回来看"，
那是团队习惯不是统计量，得等真实使用数据。
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case
from app.models.script import ScriptRun

# 看最近至多几次执行（实测校准：3 太短，连续失败不产生翻转，成片挂的会漏）
WINDOW = int(os.getenv("FLAKY_WINDOW", "7"))
# 窗口内翻转几次算 flaky（翻转 = 相邻两次结果不同）
# 2 次翻转 = 一来一回，这是"时好时坏"的定义，也是免疫"修好了"的关键
FLIPS = int(os.getenv("FLAKY_FLIPS", "2"))
# 少于这么多次记录不判 —— 不足 3 次不可能有 2 次翻转
MIN_RUNS = FLIPS + 1
# 隔离多久。到期自动回到执行队列，不需要定时任务
QUARANTINE_DAYS = int(os.getenv("FLAKY_QUARANTINE_DAYS", "14"))


def is_quarantined(case: Case, now: datetime | None = None) -> bool:
    """还在隔离期内吗。过期即失效，不需要谁去清理。"""
    if not case.quarantined_until:
        return False
    return case.quarantined_until > (now or datetime.now(timezone.utc))


def should_skip(case: Case, now: datetime | None = None) -> bool:
    """执行时该不该跳过这条用例。

    **只认人主动表达的意愿**：手动 `is_flaky`，或人主动点了隔离。
    自动检测到不稳定**不会**让它进这里 —— 检测只标记和给线索，跳不跳由人定。
    """
    return bool(case.is_flaky) or is_quarantined(case, now)


async def evaluate(session: AsyncSession, case_id: uuid.UUID, script_id: uuid.UUID | None) -> dict | None:
    """一次执行记账之后调用。判定是否要隔离，是则落库并返回依据，否则返回 None。

    只看**同一个脚本版本**的执行历史。script_id 为空（旧式沙箱脚本）时不判 ——
    没有版本边界就分不清"偶发"和"改好了"，宁可不判也不要误判。
    """
    if script_id is None:
        return None

    runs = (await session.execute(
        select(ScriptRun)
        .where(ScriptRun.case_id == case_id, ScriptRun.script_id == script_id)
        .order_by(ScriptRun.created_at.desc())
        .limit(WINDOW)
    )).scalars().all()

    if len(runs) < MIN_RUNS:
        return None   # 不足 3 次不可能有 2 次翻转，不判
        # 注意是 MIN_RUNS 不是 WINDOW：窗口是"最近**至多** 7 次"。
        # 卡到攒满 7 次才判的话，强交替（PFPFP）要白等 4 轮才被抓 —— 实测差 3 轮。

    ordered = list(reversed(runs))          # 时间正序
    seq = ["passed" if r.status == "passed" else "failed" for r in ordered]
    flips = sum(1 for a, b in zip(seq, seq[1:]) if a != b)
    if flips < FLIPS:
        return None

    case = await session.get(Case, case_id)
    if case is None or case.is_flaky:
        return None   # 人工已经标过了，不覆盖人的判断

    now = datetime.now(timezone.utc)
    runs_detail = [
        {
            "runId": str(r.id),
            "status": r.status,
            "at": r.created_at.isoformat() if r.created_at else None,
            "error": (r.error_summary or "")[:160] or None,
            "phenomenon": r.failure_phenomenon,
        }
        for r in ordered
    ]
    evidence = {
        "detectedAt": now.isoformat(),
        "scriptId": str(script_id),
        "window": WINDOW,
        "flips": flips,
        "threshold": FLIPS,
        "note": f"同一脚本版本最近 {len(ordered)} 次执行里结果翻转了 {flips} 次",
        "runs": runs_detail,
        # 检测出来之后**不隔离**，给的是"往哪儿看"。见下面 _diagnose 的说明。
        "diagnosis": _diagnose(runs_detail),
    }
    # ⚠ 这里**不动 quarantined_until**。
    # 检测到不稳定 ≠ 该把它藏起来 —— 时好时坏本身就是信息（时序、脏数据、并发、
    # 环境抖动），自动隔离 14 天等于 14 天不看它，问题一直在。
    # 隔离改成人主动要的动作（quarantine()），默认只标记 + 给诊断线索。
    case.flaky_evidence = evidence
    await session.flush()
    return evidence


# 错误摘要里天然带**每次都不同**的东西：内存地址、随机值、时间戳、UUID、耗时、行号。
# 不归一化就直接比字符串的话，"同一个错"永远被数成"好多种错" —— 实测：
#   AssertionError: 校准用的随机失败\nassert 0.272... = <... object at 0xcf91c50>
#   AssertionError: 校准用的随机失败\nassert 0.156... = <... object at 0x3f314c50>
# 这是同一个断言失败，却被判成 4 种不同的错，于是诊断给出**完全相反**的结论
# （"更像环境问题"，而实际是稳定的同一个失败）。
_NOISE = [
    (re.compile(r"0x[0-9a-fA-F]+"), "0xADDR"),                       # 内存地址
    (re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"), "UUID"),         # UUID
    (re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}[.\d]*"), "TS"),  # 时间戳
    (re.compile(r"\d+\.\d+"), "NUM"),                                # 小数（随机值/耗时）
    (re.compile(r"\b\d+\b"), "N"),                                    # 整数（行号/计数）
]


def _err_shape(msg: str) -> str:
    """把错误摘要归一成"形状"，用来判断两次失败是不是同一个错。"""
    out = (msg or "").strip()
    for pat, rep in _NOISE:
        out = pat.sub(rep, out)
    return out[:200]


def _diagnose(runs: list[dict]) -> dict:
    """给"该往哪儿看"，不下结论。

    平台能看见的只有执行记录，判不出根因；但它能把**失败之间的共性/差异**摆出来，
    这是人去查的起点：

    · 失败的错误摘要**全一样** → 稳定的失败模式，更像被测系统真有问题（偶发触发），
      而不是环境抖动 —— 该照着这条错误去查业务。
    · 失败的错误摘要**各不相同** → 更像环境/时序/资源竞争 —— 该查执行环境和并发。
    · 现象分类集中在 timeout / element_not_found → 多半是等待策略或数据准备时机。

    另外给出一对"最近一次成功 vs 最近一次失败"的 runId：这两条的截图和流量摆在
    一起对比，是最快能看出差别的方式。
    """
    fails = [r for r in runs if r["status"] != "passed"]
    passes = [r for r in runs if r["status"] == "passed"]
    msgs = {_err_shape(r.get("error")) for r in fails if (r.get("error") or "").strip()}
    phen: dict[str, int] = {}
    for r in fails:
        if r.get("phenomenon"):
            phen[r["phenomenon"]] = phen.get(r["phenomenon"], 0) + 1

    if not fails:
        hint = "窗口里没有失败记录 —— 翻转来自状态本身的变化，先看执行日志。"
    elif len(msgs) <= 1:
        hint = ("每次失败的错误都一样 —— 更像被测系统真有问题（只是偶发触发），"
                "照着这条错误去查业务逻辑，别先怀疑环境。")
    else:
        hint = (f"{len(fails)} 次失败报了 {len(msgs)} 种不同的错 —— 更像环境/时序/资源竞争，"
                "先查执行环境、并发和数据准备时机。")

    return {
        "failCount": len(fails),
        "passCount": len(passes),
        "distinctErrors": len(msgs),
        "phenomena": phen,
        "hint": hint,
        # 拿这两条的截图和流量对比，最快看出差别在哪
        "compare": {
            "lastPassed": passes[-1]["runId"] if passes else None,
            "lastFailed": fails[-1]["runId"] if fails else None,
        },
    }


async def quarantine(session: AsyncSession, case_id: uuid.UUID, days: int | None = None) -> Case | None:
    """人主动要求隔离 —— "我知道它不稳，先别让它挡路"。

    这是唯一会设置 quarantined_until 的地方。自动检测不再做这件事：
    平台替人决定"这条先不跑了"，等于替人决定不查这个问题。
    """
    case = await session.get(Case, case_id)
    if case is None:
        return None
    case.quarantined_until = datetime.now(timezone.utc) + timedelta(days=days or QUARANTINE_DAYS)
    await session.flush()
    return case


async def release(session: AsyncSession, case_id: uuid.UUID) -> Case | None:
    """人工解除隔离（脚本修好了、环境稳了）。保留 evidence 备查。"""
    case = await session.get(Case, case_id)
    if case is None:
        return None
    case.quarantined_until = None
    await session.flush()
    return case
