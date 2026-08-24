"""审核轮次的读写 —— 让「跟进到哪了」这件事有据可查。"""
from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.review_round import CaseReviewRound


def _api_part(steps) -> str:
    """场景步骤摊平。**只摊定义不摊执行结果** —— 见 `content_signature` 的说明。"""
    if steps is None:
        return "api:none"
    return json.dumps([[st.method, st.url, st.assertions, st.body] for st in steps],
                      ensure_ascii=False, sort_keys=True, default=str)


def _ui_part(ui_version) -> str:
    return f"ui:v{ui_version}" if ui_version is not None else "ui:none"


def _sign(api_part: str, ui_part: str) -> str:
    """**单条和批量必须走同一段哈希**。两处各写一遍公式的话，任何一处漂移
    （多一个分隔符、少一个 sort_keys）都会让批量算出来的签名跟落库那份对不上，
    于是整库的 approved 全被标成"过期" —— 一个假警报比不报警更贵。
    """
    return hashlib.sha1("|".join([api_part, ui_part]).encode("utf-8")).hexdigest()[:32]


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

    sc_id = (await session.execute(
        select(ApiTestScenario.id).where(ApiTestScenario.source_case_id == case_id)
    )).scalars().first()
    steps = None
    if sc_id is not None:
        steps = (await session.execute(
            select(ApiTestStep).where(ApiTestStep.scenario_id == sc_id)
            .order_by(ApiTestStep.sort_order)
        )).scalars().all()
    ui_version = (await session.execute(
        select(Script.version).where(Script.case_id == case_id, Script.script_type == "ui",
                                     Script.status == "active")
    )).scalars().first()
    return _sign(_api_part(steps), _ui_part(ui_version))


async def stale_map(session: AsyncSession, case_ids) -> dict[uuid.UUID, bool]:
    """这些用例的**最新一轮审核结论**是不是已经对不上现在的内容了。

    为什么列表也要知道（原反馈 #1 的遗留）：`list_rounds` 的 `stale` 只到详情页
    的时间线上，列表上那个"通过/打回"标签照旧 —— 于是一条 approved 的用例被
    `tb_sync_ui_script` 换过脚本之后，在列表上仍然是干干净净的"通过"，
    没人会想到去点开看那个结论是对着哪一版算的。

    **只读派生，不动 `review_status`**：照抄 `display_status()` 的取舍
    （见那个函数的说明）—— 不塞进枚举，就不牵连门禁、筛选、批量操作。

    判据跟详情页完全一致，包括"不猜"：
    · 最新一轮没有 `content_hash`（存量轮次，或最新是人工/系统那一轮）→ 不收录。
      **`None`/缺键的意思是"判不出来"，不是"没过期"** —— 人工在 AI 审之后又点过一次
      通过的话，那次改动到底发生在人点之前还是之后，库里没有依据。
    · 签名一致 → `False`；不一致 → `True`。

    调用方负责先筛出"列表上真的显示了结论"的那些（`review_status` 是
    approved/rejected）—— 没结论的用例算过期没有意义，也白付查询成本。
    """
    from app.models.api_test import ApiTestScenario, ApiTestStep
    from app.models.script import Script

    ids = [i if isinstance(i, uuid.UUID) else uuid.UUID(str(i)) for i in (case_ids or [])]
    if not ids:
        return {}

    # ── 最新一轮的签名（一次查完，不是每条查一次）───────────────
    rows = (await session.execute(
        select(CaseReviewRound.case_id, CaseReviewRound.round, CaseReviewRound.content_hash)
        .where(CaseReviewRound.case_id.in_(ids))
        .order_by(CaseReviewRound.case_id, CaseReviewRound.round)
    )).all()
    latest: dict[uuid.UUID, str | None] = {}
    for cid, _rnd, chash in rows:            # 按 round 升序，后写的覆盖前面的
        latest[cid] = chash
    want = [cid for cid, chash in latest.items() if chash]
    if not want:
        return {}

    # ── 当前签名的两半 ─────────────────────────────────────
    sc_rows = (await session.execute(
        select(ApiTestScenario.id, ApiTestScenario.source_case_id)
        .where(ApiTestScenario.source_case_id.in_(want))
    )).all()
    sc_of: dict[uuid.UUID, list[uuid.UUID]] = {}
    for sc_id, cid in sc_rows:
        sc_of.setdefault(cid, []).append(sc_id)

    step_rows = (await session.execute(
        select(ApiTestStep).where(ApiTestStep.scenario_id.in_([s for v in sc_of.values()
                                                              for s in v]))
        .order_by(ApiTestStep.sort_order)
    )).scalars().all() if sc_of else []
    steps_of: dict[uuid.UUID, list] = {}
    for st in step_rows:
        steps_of.setdefault(st.scenario_id, []).append(st)

    ui_rows = (await session.execute(
        select(Script.case_id, Script.version)
        .where(Script.case_id.in_(want), Script.script_type == "ui",
               Script.status == "active")
    )).all()
    ui_of: dict[uuid.UUID, list] = {}
    for cid, ver in ui_rows:
        ui_of.setdefault(cid, []).append(ver)

    out: dict[uuid.UUID, bool] = {}
    for cid in want:
        scs, uis = sc_of.get(cid) or [], ui_of.get(cid) or []
        if len(scs) > 1 or len(uis) > 1:
            # 一条用例挂了两个场景/两份 active UI 脚本时，单条那边是
            # `.first()` 取的（没有 order_by，顺序由库定）—— 批量这边猜一个
            # 就可能跟落库那份对不上，凭空报一个"过期"。这种少数情况直接
            # 走单条那段代码，保证判据只有一份。
            out[cid] = latest[cid] != await content_signature(session, cid)
            continue
        # 有场景但一步都没有 → `[]`（跟单条那边的 `json.dumps([])` 一致）；
        # 根本没场景 → `None` → `"api:none"`。这两个不是一回事。
        steps = (steps_of.get(scs[0]) or []) if scs else None
        sig = _sign(_api_part(steps), _ui_part(uis[0] if uis else None))
        out[cid] = latest[cid] != sig
    return out


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
