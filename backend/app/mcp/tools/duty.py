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
    # **废弃的不进待办。** 一条被批准废弃的用例还挂在待办队列里，
    # 等于让 CC 去修一个已经决定不再测的场景。
    cases = {c.id: c for c in (await session.execute(
        select(Case).where(Case.branch_id == bid, Case.deleted_at.is_(None),
                           Case.lifecycle_status != "deprecated")
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

    # ⑤⑥⑦ 版本升级·分支对账带来的三个队列。**清单必须落平台**（CC 一关会话
    # 上下文就没了，续不上），所以从 endpoint_diff_* 两张表读回来。
    to_revise, to_cover_new, awaiting_human = await _diff_duties(session, bid, cases, limit)

    counts = {"待归因": len(to_analyze), "待复跑": len(to_rerun),
              "待处理接口变动": len(to_revise), "待补用例": len(to_cover_new),
              "待补场景": len(to_cover), "待自证": len(to_selfcheck),
              "等人拍板的废弃": len(awaiting_human)}
    order = [k for k, v in counts.items() if v]
    return {
        "summary": counts,
        "建议顺序": order or ["没有待办 —— 这一轮干净"],
        "待归因": to_analyze[:limit],
        "待复跑": to_rerun[:limit],
        "待处理接口变动": to_revise[:limit] or None,
        "待补用例": to_cover_new[:limit] or None,
        "待补场景": to_cover,
        "待自证": to_selfcheck[:limit],
        "等人拍板的废弃": awaiting_human[:limit] or None,
        "usage": "待归因堵得最死（不判原因，后面全卡着）；待复跑最便宜（跑一遍就可能关单）；"
                 "待处理接口变动是版本升级对账算出来的，一条条改（**预期按新版本的需求写，"
                 "不是按新版本的实测抄**）；待补用例是新版本的新端点，谁都没覆盖它；"
                 "待补场景是攒出来的欠账，被提到次数越多越该补。"
                 "**别自己关单**：跑绿了平台自动关；要强行放过就人工关闭并写原因。",
    }


async def _diff_duties(session: AsyncSession, bid, cases: dict, limit: int):
    """分支对账清单 → 三个队列：要改的、要补的、等人拍板的废弃。"""
    from app.models.endpoint_diff import EndpointDiffBatch, EndpointDiffHit

    batches = (await session.execute(
        select(EndpointDiffBatch).where(EndpointDiffBatch.branch_id == bid)
        .order_by(EndpointDiffBatch.created_at)
    )).scalars().all()
    if not batches:
        return [], [], []

    hits = (await session.execute(
        select(EndpointDiffHit)
        .where(EndpointDiffHit.batch_id.in_([b.id for b in batches]))
        .order_by(EndpointDiffHit.created_at)
    )).scalars().all()

    # 一条用例的多处命中合成一行 —— 分开列的话一条用例改一次就要划掉 5 行
    per_case: dict = {}
    for h in hits:
        c = cases.get(h.case_id)
        if c is None:
            continue            # 已废弃或已删的，上面查用例时就滤掉了
        slot = per_case.setdefault(h.case_id, {
            "caseCode": c.case_code, "caseId": str(h.case_id),
            "标题": c.title, "撞了几处": 0, "变动": [],
            "审核标签": c.review_status or "待提审",
        })
        slot["撞了几处"] += 1
        if len(slot["变动"]) < 6:
            slot["变动"].append({
                "kind": h.kind, "method": h.method, "url": h.url,
                "步骤": h.step_name, "detail": h.detail,
            })

    # 已经提请废弃、正等人拍板的，不再进「待处理接口变动」——
    # 它已经在「等人拍板的废弃」那个队列里了，同一件事让人看两遍就是噪音，
    # 而且这条的下一步是**等人**，不是"去改它"。
    pending_dep = {cid for cid, c in cases.items() if c.deprecate_status == "requested"}

    to_revise = []
    for cid, slot in per_case.items():
        if cid in pending_dep:
            continue
        removed = [v for v in slot["变动"] if v["kind"] == "removed"]
        if removed:
            slot["下一步"] = ("端点没了 → 先判是真没了还是改名/挪位置。真没了走 "
                              "tb_request_deprecate(case_id, reason, evidence) 交证据；"
                              "**别自己废** —— 「我在页面上找不到」不等于「功能没了」。")
        else:
            slot["下一步"] = ("读新版本的需求/OpenAPI/代码 → tb_update_case 改预期"
                              "（带 expected_confirmed_note 落款）→ "
                              "tb_sync_orchestrated_scenario(mode='patch') 只改动的那几步 → "
                              "tb_run_api_test → tb_check_assertion_bite → tb_review_case。"
                              "**预期按新版本的需求写，不是打开新版本跑一遍照着改** —— "
                              "那是把实现抄了一遍，新版本引入的 bug 会被固化成预期。")
        to_revise.append(slot)
    to_revise.sort(key=lambda x: -x["撞了几处"])

    # 待补用例：新端点，谁都不命中
    seen = set()
    to_cover_new = []
    for b in batches:
        for pn in (b.pending_new or []):
            key = (pn.get("method"), pn.get("url"))
            if key in seen:
                continue
            seen.add(key)
            to_cover_new.append({
                **pn,
                "下一步": ("这是新版本的新端点，现有用例一条都没覆盖它。"
                           "按平常那套来：读需求 → 活体验证 → tb_create_case + "
                           "tb_sync_orchestrated_scenario。不补的话这块功能零覆盖，"
                           "而且**永远不会报错**。"),
            })

    # 等人拍板的废弃：AI 探不出来落到人手里的
    from app.models.case import Case as _Case
    pend = (await session.execute(
        select(_Case).where(_Case.branch_id == bid, _Case.deleted_at.is_(None),
                            _Case.deprecate_status == "requested")
    )).scalars().all()
    awaiting_human = [{
        "caseCode": c.case_code, "caseId": str(c.id), "标题": c.title,
        "理由": (c.deprecate_reason or {}).get("reason"),
        "平台/AI 说": (c.deprecate_reason or {}).get("note"),
        "下一步": "等人在列表页或详情页确认/驳回。**别绕过它自己废。**",
    } for c in pend]

    return to_revise, to_cover_new, awaiting_human
