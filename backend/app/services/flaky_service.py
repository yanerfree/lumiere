"""flaky 自动判定与隔离。

## 为什么要有

原来的 `is_flaky` 是个纯手动布尔：人勾上，执行器就永远跳过这条用例。
问题不在"手动"，在**没有回来的路** —— 脚本修好了它还在被跳过，而且没人记得当初
为什么标它。一条被无声跳过的用例，比一条会偶发失败的用例更危险：后者至少还在报警。

## 判定用什么依据

**必须限定在同一个脚本版本内。** 实测库里 TC-XMGL-00001 有两个脚本版本、各自有失败
—— 不按 script_id 分组的话，"换了新版本脚本"的状态变化会被算成翻转，把一次成功的
修复判成 flaky。这是这套逻辑最容易错的地方。

同版本内、最近 WINDOW 次执行里翻转 >= FLIPS 次 → 隔离 QUARANTINE_DAYS 天。

## 阈值是经验值，不是结论

`3 轮内 2 次翻转` 没有数据支撑（Murat 原话）。实测这个库里只有 3 条用例、43 次执行，
且失败大多是调试时人为造的 —— **这个量级校准不出任何东西**。所以：
- 阈值集中放在这里，可用环境变量覆盖，不散落在调用处
- 判定依据（哪几次、什么时候、什么结果）一律落库，攒够真实数据后能回头校准
- UI 上标明它是经验值

攒够数据后怎么校准：把 script_runs 按 (case_id, script_id) 排序算翻转率分布，
取"稳定脚本"的 95 分位作为阈值下限。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case
from app.models.script import ScriptRun

# 看最近几次执行
WINDOW = int(os.getenv("FLAKY_WINDOW", "3"))
# 窗口内翻转几次算 flaky（翻转 = 相邻两次结果不同）
FLIPS = int(os.getenv("FLAKY_FLIPS", "2"))
# 隔离多久。到期自动回到执行队列，不需要定时任务
QUARANTINE_DAYS = int(os.getenv("FLAKY_QUARANTINE_DAYS", "14"))


def is_quarantined(case: Case, now: datetime | None = None) -> bool:
    """还在隔离期内吗。过期即失效，不需要谁去清理。"""
    if not case.quarantined_until:
        return False
    return case.quarantined_until > (now or datetime.now(timezone.utc))


def should_skip(case: Case, now: datetime | None = None) -> bool:
    """执行时该不该跳过这条用例：人工标记的 flaky，或还在自动隔离期内。"""
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

    if len(runs) < WINDOW:
        return None   # 样本不够，不判

    ordered = list(reversed(runs))          # 时间正序
    seq = ["passed" if r.status == "passed" else "failed" for r in ordered]
    flips = sum(1 for a, b in zip(seq, seq[1:]) if a != b)
    if flips < FLIPS:
        return None

    case = await session.get(Case, case_id)
    if case is None or case.is_flaky:
        return None   # 人工已经标过了，不覆盖人的判断

    now = datetime.now(timezone.utc)
    evidence = {
        "detectedAt": now.isoformat(),
        "scriptId": str(script_id),
        "window": WINDOW,
        "flips": flips,
        "threshold": FLIPS,
        "note": f"同一脚本版本最近 {WINDOW} 次执行里结果翻转了 {flips} 次",
        "runs": [
            {
                "runId": str(r.id),
                "status": r.status,
                "at": r.created_at.isoformat() if r.created_at else None,
                "error": (r.error_summary or "")[:160] or None,
            }
            for r in ordered
        ],
    }
    case.quarantined_until = now + timedelta(days=QUARANTINE_DAYS)
    case.flaky_evidence = evidence
    await session.flush()
    return evidence


async def release(session: AsyncSession, case_id: uuid.UUID) -> Case | None:
    """人工解除隔离（脚本修好了、环境稳了）。保留 evidence 备查。"""
    case = await session.get(Case, case_id)
    if case is None:
        return None
    case.quarantined_until = None
    await session.flush()
    return case
