"""审核轮次的读写 —— 让「跟进到哪了」这件事有据可查。"""
from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.review_round import CaseReviewRound


async def content_signature(session: AsyncSession, case_id: uuid.UUID) -> str:
    """当前这条用例的场景步骤 + UI 脚本版本号摊平成一个签名。

    只用来判断"跟上一次审的还是不是同一份东西"，不是语义级比对——
    步骤顺序变了、断言改了、脚本被新版本覆盖，签名就会变。反过来，
    只是重新跑了一遍（execution 的 last_status/last_response 变了）不会变，
    因为这里只摊场景的定义（url/method/assertions/body），不摊执行结果，
    否则每次 run_first 重跑都会把上一轮的 approved 标成"过期"，
    而内容其实一个字都没改。
    """
    from app.models.api_test import ApiTestScenario, ApiTestStep
    from app.models.script import Script

    parts: list[str] = []
    sc_id = (await session.execute(
        select(ApiTestScenario.id).where(ApiTestScenario.source_case_id == case_id)
    )).scalars().first()
    if sc_id is not None:
        steps = (await session.execute(
            select(ApiTestStep).where(ApiTestStep.scenario_id == sc_id)
            .order_by(ApiTestStep.sort_order)
        )).scalars().all()
        parts.append(json.dumps(
            [[st.method, st.url, st.assertions, st.body] for st in steps],
            ensure_ascii=False, sort_keys=True, default=str))
    else:
        parts.append("api:none")
    ui_version = (await session.execute(
        select(Script.version).where(Script.case_id == case_id, Script.script_type == "ui",
                                     Script.status == "active")
    )).scalars().first()
    parts.append(f"ui:v{ui_version}" if ui_version is not None else "ui:none")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:32]


async def _next_round(session: AsyncSession, case_id: uuid.UUID) -> int:
    n = (await session.execute(
        select(func.max(CaseReviewRound.round)).where(CaseReviewRound.case_id == case_id)
    )).scalar_one_or_none()
    return (n or 0) + 1


async def record(session: AsyncSession, case_id, kind: str, **kw) -> CaseReviewRound:
    """记一轮。**不 commit** —— 让调用方跟它自己那笔改动在同一个事务里，
    否则会出现"审核记录写进去了、用例状态没改"这种半截状态。"""
    cid = case_id if isinstance(case_id, uuid.UUID) else uuid.UUID(str(case_id))
    row = CaseReviewRound(case_id=cid, round=await _next_round(session, cid), kind=kind,
                          **{k: v for k, v in kw.items() if v is not None})
    session.add(row)
    await session.flush()
    return row


async def list_rounds(session: AsyncSession, case_id) -> list[dict]:
    cid = case_id if isinstance(case_id, uuid.UUID) else uuid.UUID(str(case_id))
    rows = (await session.execute(
        select(CaseReviewRound).where(CaseReviewRound.case_id == cid)
        .order_by(CaseReviewRound.round.desc())
    )).scalars().all()
    # 只有需要判断"是不是还对得上现在的内容"时才算一次当前签名——
    # 没有任何一条带 content_hash 的存量数据不必付这个查询成本。
    current_sig = None
    if any(r.content_hash for r in rows):
        current_sig = await content_signature(session, cid)
    return [{
        "round": r.round, "kind": r.kind, "verdict": r.verdict, "total": r.total,
        "dimensions": r.dimensions, "findings": r.findings or [],
        "coverageGaps": r.coverage_gaps or [], "summary": r.summary,
        "changed": r.changed, "actor": r.actor, "model": r.model,
        # 静态审核和执行式审核在列表里长得一模一样，是这一页最要紧的一条缺口 ——
        # 结论强度差一个量级，而"凭什么过的"原来看不出来。老轮次是 None，显示成未知。
        "reviewMode": r.review_mode, "trafficSeen": r.traffic_seen,
        # 场景/脚本被后续 sync 覆盖之后，这条 verdict 就是对着已经不存在的内容
        # 算出来的——findings 文本原样留着却没有任何标记，险些把人导向去重写
        # 一个本来就能跑的脚本。没存签名（存量轮次）时不猜，一律 None，
        # 不是"一定没过期"。
        "stale": (None if not r.content_hash else r.content_hash != current_sig),
        "at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]


async def display_status(session: AsyncSession, case) -> str:
    """审核这一维当前显示成什么。**整改待复审是派生的** ——
    不塞进 review_status 的枚举里，免得动那个字段牵连门禁、筛选、批量操作一大片。
    """
    latest = (await session.execute(
        select(CaseReviewRound).where(CaseReviewRound.case_id == case.id)
        .order_by(CaseReviewRound.round.desc()).limit(1)
    )).scalars().first()
    if latest is not None and latest.kind == "cc_resubmit":
        return "resubmitted"          # 改完了，等再审
    return case.review_status or "not_submitted"
