"""「现在该干什么」—— 一个入口，四个队列。

以前 CC 得自己拼：拉报告 → 拉失败清单 → 逐条拿证据 → 提归因 → 等人确认 → 改 → 复跑。
每一步都要它自己记得，哪步忘了链就断了。这里把四个来源汇成一份待办，
**不产生新数据**，只是把平台已经知道的事按"该谁动手"排好。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def next_duty(session: AsyncSession, branch_id: str, limit: int = 10) -> dict:
    """这一轮该干什么。四个队列，按"堵得最死的先做"排。

    ① 待归因   —— 红了但还没分析：拿证据、判原因、tb_submit_analysis
    ② 待复跑   —— 已经修好/bug 标 fixed 的：跑一遍，绿了单子自动关
    ③ 待补场景 —— 审核时被反复提到的模块级缺口：补用例
    ④ 待自证   —— 回推四问没答的：补 reflections

    每条都带**下一步该调哪个工具**，不用回头翻规范。
    """
    from app.models.case import Case
    from app.models.failure_ticket import OPEN_STATUSES, FailureTicket
    from app.models.script import ScriptRun
    from app.services.review import reflect

    bid = uuid.UUID(branch_id)
    cases = {c.id: c for c in (await session.execute(
        select(Case).where(Case.branch_id == bid, Case.deleted_at.is_(None))
    )).scalars().all()}
    if not cases:
        return {"error": "这个分支下没有用例"}

    tickets = (await session.execute(
        select(FailureTicket).where(
            FailureTicket.case_id.in_(list(cases)),
            FailureTicket.status.in_(OPEN_STATUSES),
        ).order_by(FailureTicket.recurrence.desc(), FailureTicket.occurrences.desc())
    )).scalars().all()

    to_analyze, to_rerun = [], []
    for t in tickets:
        c = cases.get(t.case_id)
        row = {"caseCode": c.case_code, "caseId": str(c.id), "ticketId": str(t.id),
               "现象": t.phenomenon, "红了几次": t.occurrences,
               "第几次复发": t.recurrence or None,
               "状态": t.status}
        if t.status in ("open", "analyzed"):
            row["下一步"] = ("拿证据 tb_get_ui_script_result(case_id) → 判原因 → "
                             "tb_submit_analysis(cause=...)" if t.status == "open"
                            else "已归因，等人确认。别自己往下走")
            to_analyze.append(row)
        elif t.status in ("confirmed", "fixing", "verifying"):
            row["下一步"] = ("改完了就跑一遍：tb_run_ui_script / tb_run_api_test，"
                             "绿了这张单自动关")
            row["人确认的原因"] = t.confirmed_cause
            to_rerun.append(row)

    # 这里原来还有一条来源：「关联 bug 标了 fixed → 待重跑」。**去掉了** ——
    # 关联 bug 的语义后来改过：`fixed` 的含义是"你回来调通了"（终态、留痕），
    # 不是"据说修好了、等人复跑"。所以那个队列在新语义下压根不存在。
    # 还卡着的产品 bug 走 `known` 状态的跟进单，不进待办（回归会跳过它们）。

    # 审核时反复被提到的模块级缺口
    gaps: dict[str, dict] = {}
    for c in cases.values():
        for g in ((c.review_reason or {}).get("coverageGaps") or []):
            key = str(g).replace("模块级缺口：", "").replace("模块级：", "")[:12]
            slot = gaps.setdefault(key, {"缺口": str(g)[:160], "被提到": 0, "来自": []})
            slot["被提到"] += 1
            slot["来自"].append(c.case_code)
    to_cover = sorted(gaps.values(), key=lambda x: -x["被提到"])[:limit]

    to_selfcheck = [{"caseCode": c.case_code, "caseId": str(c.id),
                     "下一步": "tb_sync_orchestrated_scenario(reflections={...}) 补上四问"}
                    for c in cases.values()
                    if (c.api_status != "draft" or c.ui_status != "draft") and reflect.pending(c)]

    counts = {"待归因": len(to_analyze), "待复跑": len(to_rerun),
              "待补场景": len(to_cover), "待自证": len(to_selfcheck)}
    order = [k for k, v in counts.items() if v]
    return {
        "summary": counts,
        "建议顺序": order or ["没有待办 —— 这一轮干净"],
        "待归因": to_analyze[:limit],
        "待复跑": to_rerun[:limit],
        "待补场景": to_cover,
        "待自证": to_selfcheck[:limit],
        "usage": "待归因堵得最死（不判原因，后面全卡着）；待复跑最便宜（跑一遍就可能关单）；"
                 "待补场景是攒出来的欠账，被提到次数越多越该补。"
                 "**别自己关单**：跑绿了平台自动关；要强行放过就人工关闭并写原因。",
    }
