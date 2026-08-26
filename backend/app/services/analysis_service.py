"""CC 归因的入库校验与人工确认（B2/B3/B4）。

设计要点全在一句话里：**CC 是运动员兼裁判，不禁止它归因，但让它的结论碰不到状态。**

结构性偏差是确定存在且方向可预测的：
- 系统性低估"脚本自己有问题" —— LLM 归因自己的产物时倾向归给外部，
  于是**测试缺陷被伪装成产品缺陷**，浪费开发时间还让"发现的 bug 数"虚高
- 系统性低估 flaky —— CC 看不到历史，同一条挂三次它分析三次，每次归因还可能不同

三条对策（这个模块实现前两条，第三条是 B6 的指标）：
1. 归因**必须自证**：evidence 必须引用平台侧证据的具体位置，引不出来直接拒收
2. 归因**不改状态**：写进 cc_analysis，人确认写进 confirmed_cause，两个字段
3. 反向指标：cc_analysis vs confirmed_cause 的一致率
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.script import ScriptRun

# 归因的可选值。注意和「现象」（failure_phenomenon）不是一回事：
# 现象是"元素找不到"，原因是"产品改了 id，用例过期了"。
logger = logging.getLogger(__name__)

CAUSES = {
    "product_defect": "被测系统的缺陷 —— 该提单给开发",
    "test_defect": "脚本自己写错了 —— 该改脚本",
    "case_expired": "需求变了，用例过期 —— 该改用例",
    "env_issue": "环境/依赖问题（服务没起、token 过期、共享资源缺失）",
    "data_issue": "测试数据问题（脏数据、前置数据没准备好）",
    "flaky": "不稳定，同样的脚本时好时坏",
    "requirement_unclear": "需求本身的问题 —— 需求没写清、或需求和实现冲突该以哪个为准。**只有人能定**",
    "unknown": "看不出来 —— 拿不准就选这个，别硬猜",
}

# ── 自证：哪些归因 CC 可以自己处置，不用等人 ────────────────────────
#
# 原来一律等人确认，理由是怕"跑不过就说是产品的锅"。但 CC 有代码、能活体复现，
# 大部分情况拦它没意义。所以改成**按证据齐不齐分流，不按类型分流**：
#   · 脚本自己错 / 用例过期 / 环境 / 数据 → 自己改，天然闸门是「改完必须跑绿才关单」
#   · 产品缺陷 → 要三样齐全（活体复现 + 代码依据 + 单号）才放行；缺一样落回等人确认
#   · 需求问题 / 拿不准 → 只有人能定
#
# 产品缺陷放行之后也拿不到好处：挂了单号只是回归**不再刷红**，
# 交付门禁照旧算「卡在产品缺陷」——不是通过。所以甩锅没收益。
SELF_SERVE = {"test_defect", "case_expired", "env_issue", "data_issue", "flaky"}
NEEDS_HUMAN = {"requirement_unclear", "unknown"}

# ── 抽检：自动化会把体温计一起收走 ────────────────────────────────
#
# §2.4 上线的反向指标 `CC归因 vs 人确认` 一致率（agreement_stats）是平台**唯一**
# 能量出"CC 有没有系统性把测试缺陷说成产品缺陷"的东西。它的分母是
# `confirmed_cause` —— 只有人确认过的才进。
#
# 而自证放行（SELF_SERVE）压根不经过人。所以**自证比例越高，这个指标越测不出来**：
# 不是"少做了个功能"，是**把人从确认里拿掉，同时也就拿掉了度量它准不准的手段**。
#
# 所以自证的也抽一部分送人复核。**不阻塞 CC** —— 它照旧自己改，只是这几条人会
# 另外看一眼，用来校准。
SAMPLE_EVERY = 10          # 每 10 条抽 1 条
# **写死，不做成可配置**：又一个能被调成 0 的开关，和「检查项不做成可勾选」同一条纪律。

# 真正在等人的两种去向。`lum_list_pending_confirm` 默认只列这些 ——
# 此前它列的是"所有还没确认的"，于是自证放行的也混在里面（CC 明明被告知
# "你自己改不用等"），队列里绝大多数是不需要人动的东西，人就不看了。
WAITING_ON_HUMAN = ("needs_human", "self_serve_sampled")


def sampled(case_id) -> bool:
    """这条自证归因要不要抽出来送人复核。

    **按哈希不按随机**：同一条用例反复提交归因，抽中与否必须每次一样。
    随机的话 CC 会看到"同样的归因这次要等人、上次不用等"，像是平台行为不稳定 ——
    而它没法从返回里分辨这是抽检还是判据变了。
    """
    h = hashlib.sha1(str(case_id).encode("utf-8")).hexdigest()
    return int(h[:8], 16) % SAMPLE_EVERY == 0
# 产品缺陷自证要齐的三样
DEFECT_EVIDENCE = {
    "liveVerified": "活体复现记录：你**真的**又调/点了一遍，写清怎么复现、看到什么",
    "codeRefs": "代码或需求依据：文件:行，或需求文档的出处",
    "issue": "单号：按 skill 规范提的那张单（编号或 URL）",
}


def route(payload: dict) -> tuple[str, list[str]]:
    """这条归因该走自证还是等人。返回 (去向, 缺什么)。

    去向：`self_serve`（CC 自己处置）/ `needs_human`（等人确认）
    """
    cause = (payload.get("cause") or "").strip()
    if cause in NEEDS_HUMAN:
        return "needs_human", []
    if cause in SELF_SERVE:
        return "self_serve", []
    if cause == "product_defect":
        ev = payload.get("evidence") or {}
        ev = ev if isinstance(ev, dict) else {}
        missing = [f"{k}（{desc}）" for k, desc in DEFECT_EVIDENCE.items()
                   if not str(ev.get(k) or "").strip()]
        return ("self_serve", []) if not missing else ("needs_human", missing)
    return "needs_human", []

CONFIDENCE = ("high", "medium", "low")
FIX_TARGETS = ("script", "product", "data", "case", "env", "none")


def validate(payload: dict, run: ScriptRun) -> list[str]:
    """校验一份归因。返回问题列表，空列表 = 通过。"""
    problems: list[str] = []

    cause = (payload.get("cause") or "").strip()
    if cause not in CAUSES:
        problems.append(f"cause 必须是这几个之一：{'、'.join(CAUSES)}（拿不准就填 unknown，别硬猜）")

    conf = (payload.get("confidence") or "").strip()
    if conf not in CONFIDENCE:
        problems.append(f"confidence 必须是 {'/'.join(CONFIDENCE)}")
    elif conf == "low" and cause not in ("unknown", "flaky"):
        problems.append(
            "confidence=low 却给了一个具体 cause —— 低置信就标 unknown。"
            "一个看起来很有道理的错答案，比一句「我不知道」有害得多"
        )

    target = (payload.get("proposedFixTarget") or payload.get("proposed_fix_target") or "").strip()
    if target not in FIX_TARGETS:
        problems.append(f"proposedFixTarget 必须是 {'/'.join(FIX_TARGETS)}")

    reasoning = (payload.get("reasoning") or "").strip()
    if len(reasoning) < 15:
        problems.append("reasoning 太短 —— 写清楚为什么是这个原因而不是别的。写不出因果的归因基本都是在瞎猜")

    problems.extend(_validate_evidence(payload.get("evidence"), run))
    return problems


def _validate_evidence(evidence, run: ScriptRun) -> list[str]:
    """evidence 必须**指向平台侧记录的具体位置**，不能只有 CC 自己的推理。

    这是把"运动员"锁在"必须出示裁判自己的录像"上 —— 没有这一条，
    归因会退化成一段读着很顺、但和实际证据没关系的文字。
    """
    # **两种形状都收**（活体撞出来的：自证要传对象，老校验只收数组，
    # 于是"自己处置不用等人"这条路压根走不通）：
    #   · 数组     —— 证据指针，形如 [{"type":"request","ref":"POST /x -> 500"}]
    #   · 对象     —— 自证用的：{liveVerified, codeRefs, issue, items?[指针]}
    # 对象形态里的 `items` 仍按指针校验；没有 items 时，至少要有一个非空自证字段。
    if isinstance(evidence, dict):
        items = evidence.get("items")
        self_fields = [k for k in ("liveVerified", "codeRefs", "issue", "reasoningRefs")
                       if str(evidence.get(k) or "").strip()]
        if not items and not self_fields:
            return ["evidence 是对象时，至少要有一个：liveVerified（活体怎么复现的）/ "
                    "codeRefs（文件:行 或需求出处）/ issue（单号）/ items（证据指针数组）"]
        if not items:
            return []
        evidence = items

    if not isinstance(evidence, list) or not evidence:
        return ["evidence 要么是非空数组（每条指向平台侧证据的具体位置，形如 "
                "{\"type\":\"request\",\"ref\":\"POST /api/projects -> 500\"}），"
                "要么是对象（自证用：liveVerified / codeRefs / issue）"]

    valid_types = {"error_summary", "request", "screenshot", "stdout", "phenomenon"}
    problems = []
    n_reqs = len(run.captured_requests or [])
    n_shots = len(run.screenshots or [])

    for i, e in enumerate(evidence):
        if not isinstance(e, dict):
            problems.append(f"evidence[{i}] 不是对象")
            continue
        t = (e.get("type") or "").strip()
        ref = str(e.get("ref") or "").strip()
        if t not in valid_types:
            problems.append(f"evidence[{i}].type 必须是 {'/'.join(sorted(valid_types))}")
            continue
        if not ref:
            problems.append(f"evidence[{i}].ref 不能为空 —— 要指出是哪一条/哪一句")
            continue
        # 引用的东西这次执行里得**真有**，否则就是凭空捏造的引用。
        # 光判"有没有截图"不够 —— 只有 1 张时引用"第 9 张"照样能蒙混过关，
        # 而捏造引用恰恰是这道校验要拦的东西。
        if t == "request":
            if n_reqs == 0:
                problems.append(f"evidence[{i}] 引用了网络请求，但这次执行没有任何流量记录")
            elif not _ref_hits_request(ref, run.captured_requests or []):
                problems.append(
                    f"evidence[{i}].ref「{ref[:60]}」在这次执行的流量里找不到对应请求 —— "
                    "引用要能对上，别凭印象写"
                )
        elif t == "screenshot":
            if n_shots == 0:
                problems.append(f"evidence[{i}] 引用了截图，但这次执行没有截图")
            else:
                bad = _out_of_range(ref, n_shots)
                if bad is not None:
                    problems.append(
                        f"evidence[{i}] 引用了第 {bad} 张截图，但这次执行只有 {n_shots} 张"
                    )
        elif t == "error_summary" and not run.error_summary:
            problems.append(f"evidence[{i}] 引用了 error_summary，但这次执行的 error_summary 是空的")
        elif t == "stdout" and not run.stdout:
            problems.append(f"evidence[{i}] 引用了 stdout，但这次执行没有输出")
    return problems


def _out_of_range(ref: str, count: int) -> int | None:
    """ref 里如果写了序号，且超出实际数量，返回那个越界的序号。"""
    import re
    for m in re.findall(r"\d+", ref):
        n = int(m)
        if n > count:
            return n
    return None


def _ref_hits_request(ref: str, reqs: list[dict]) -> bool:
    """ref 里提到的 URL 片段 / 方法 / 状态码，得能在流量里对上一条。"""
    low = ref.lower()
    for r in reqs:
        url = (r.get("url") or "").lower()
        method = (r.get("method") or "").lower()
        status = str(r.get("status") or "")
        # URL 的路径片段能对上就算命中（CC 通常写 "POST /api/projects -> 500"）
        path = url.split("?")[0]
        tail = "/" + path.split("/", 3)[-1] if path.count("/") >= 3 else path
        if tail and len(tail) > 3 and tail in low:
            return True
        if method and status and method in low and status in low:
            return True
    return False


async def submit(session: AsyncSession, run_id: str, payload: dict, author: str | None) -> dict:
    """CC 提交归因。校验不过直接拒收，且**不写任何状态**。"""
    try:
        rid = uuid.UUID(run_id)
    except ValueError:
        return {"error": "run_id 不是合法 UUID"}

    run = (await session.execute(select(ScriptRun).where(ScriptRun.id == rid))).scalar_one_or_none()
    if not run:
        return {"error": "找不到这次执行记录。先调 lum_get_ui_script_result 拿 run_id"}
    if run.status == "passed":
        return {"error": "这次执行是通过的，没有需要归因的失败"}

    problems = validate(payload, run)
    if problems:
        return {
            "error": "归因没通过校验，改完再提交：",
            "problems": problems,
            "hint": "evidence 必须指向平台侧证据的具体位置 —— 没有它，归因就只是一段"
                    "读着顺但和实际证据无关的文字。拿不准就 cause=unknown + confidence=low。",
        }

    run.cc_analysis = {
        "cause": payload["cause"],
        "confidence": payload["confidence"],
        "proposedFixTarget": payload.get("proposedFixTarget") or payload.get("proposed_fix_target"),
        "reasoning": payload["reasoning"].strip()[:4000],
        "evidence": payload["evidence"],
        "author": author or "cc",
        "submittedAt": datetime.now(timezone.utc).isoformat(),
        # 绑定这次执行 —— 脚本改过之后旧归因就该失效，不能让三周前的结论
        # 挂在今天的失败上误导人
        "runId": str(run.id),
        "scriptId": str(run.script_id) if run.script_id else None,
        # 存下平台当时的现象初判，方便事后比对（分类器准不准 / CC 有没有无视它）
        "phenomenonAtSubmit": run.failure_phenomenon,
    }
    where, missing = route(payload)
    # 自证放行的抽一部分仍然送人复核 —— 见 SAMPLE_EVERY 那段的理由。
    # 抽中不改变"CC 自己处置"这件事，只是让它同时留在人的待确认队列里。
    if where == "self_serve" and sampled(run.case_id):
        where = "self_serve_sampled"
    run.cc_analysis["route"] = where
    if missing:
        run.cc_analysis["missingEvidence"] = missing
    self_serving = where in ("self_serve", "self_serve_sampled")

    # 同步失败跟进单 —— 不同步的话单子永远停在「待分析」，中间几步是断的
    ticket_status = None
    try:
        from app.models.failure_ticket import OPEN_STATUSES, FailureTicket
        t = (await session.execute(
            select(FailureTicket).where(
                FailureTicket.case_id == run.case_id,
                FailureTicket.script_type == run.script_type,
                FailureTicket.status.in_(OPEN_STATUSES),
            ).order_by(FailureTicket.created_at.desc()))).scalars().first()
        if t is not None:
            t.cc_analysis = run.cc_analysis
            if self_serving:
                if payload["cause"] == "product_defect":
                    # 挂了单号的产品缺陷：回归不再刷红，但**交付门禁照旧算卡住**
                    t.status = "known"
                    t.disposition = "product_defect"
                    t.closed_reason = f"产品缺陷已提单：{(payload.get('evidence') or {}).get('issue')}"
                else:
                    t.status = "fixing"          # 自己改，改完跑绿才关单
                    t.disposition = payload["cause"]
            else:
                t.status = "analyzed"            # 等人确认
            ticket_status = t.status
    except Exception:  # noqa: BLE001
        logger.exception("同步失败跟进单出错（不影响归因入库）")

    await session.commit()
    if self_serving:
        msg = ("归因已记录，**你自己处置**，不用等人：" + (
            "产品缺陷已挂单号，这条回归不再刷红；但交付门禁照旧算「卡在产品缺陷」——"
            "不是通过。等缺陷修好后复跑，绿了跟进单自动关。"
            if payload["cause"] == "product_defect" else
            "改完**必须复跑跑绿**，跟进单才会关 —— 跑不绿它会一直挂在「处置中」。"))
        if where == "self_serve_sampled":
            msg += ("（这条被**抽中复核**：自证的每 10 条抽 1 条另外送人看一眼，"
                    "用来校准归因准不准。**不影响你** —— 照旧自己改，不用等。）")
    else:
        msg = "归因已记录，进入**待确认**队列 —— 它不改用例状态、不进通过率、不改报告结论。"
        if missing:
            msg += "（产品缺陷要自证得三样齐全，你缺：" + "；".join(missing) + "）"
        elif payload["cause"] in NEEDS_HUMAN:
            msg += "（这一类只有人能定）"
    return {
        "status": "ok",
        "runId": str(run.id),
        "cause": run.cc_analysis["cause"],
        "route": where,
        "missingEvidence": missing or None,
        "ticketStatus": ticket_status,
        "message": msg,
    }


async def confirm(
    session: AsyncSession, run_id: uuid.UUID, cause: str, note: str, user_id: uuid.UUID
) -> ScriptRun:
    """人工确认。**这是状态的唯一写入口。**"""
    run = (await session.execute(select(ScriptRun).where(ScriptRun.id == run_id))).scalar_one_or_none()
    if not run:
        raise ValueError("执行记录不存在")
    if cause not in CAUSES:
        raise ValueError(f"cause 必须是：{'、'.join(CAUSES)}")
    if not (note or "").strip():
        raise ValueError("必须写一句理由 —— 确认不是走形式，它是唯一算数的结论")

    run.confirmed_cause = cause
    run.confirmed_note = note.strip()[:2000]
    run.confirmed_by = user_id
    run.confirmed_at = datetime.now(timezone.utc)

    # 同步跟进单：确认完就该轮到 CC 动手了，单子要从「等你确认」往前走
    try:
        from app.models.failure_ticket import OPEN_STATUSES, FailureTicket
        t = (await session.execute(
            select(FailureTicket).where(
                FailureTicket.case_id == run.case_id,
                FailureTicket.script_type == run.script_type,
                FailureTicket.status.in_(OPEN_STATUSES),
            ).order_by(FailureTicket.created_at.desc()))).scalars().first()
        if t is not None:
            t.confirmed_cause = cause
            t.confirmed_note = run.confirmed_note
            t.confirmed_by = str(user_id)
            t.disposition = cause
            # 产品缺陷/需求问题：人拍板之后也不是"CC 去修"，标成已知问题挂着
            t.status = "known" if cause in ("product_defect", "requirement_unclear") else "confirmed"
    except Exception:  # noqa: BLE001
        logger.exception("同步失败跟进单出错（不影响人工确认）")

    await session.commit()
    return run


async def agreement_stats(session: AsyncSession, project_id: uuid.UUID | None = None) -> dict:
    """CC 归因 vs 人确认 的一致率（B6）。

    为什么要这个指标：CC 是运动员兼裁判，偏差**确定存在且方向可预测** ——
    LLM 归因自己的产物时倾向归给外部，于是测试缺陷被伪装成产品缺陷。
    这个指标是唯一能把那个偏差量出来的东西。

    重点看 `product_defect` 桶的推翻率：CC 说"是产品的锅"而人推翻的比例
    超过 30%，说明它在系统性甩锅，该收紧提示词或降低它这一类的权重。

    平台此前只有 lum_get_generation_stats（量生成通过率），**没有任何东西量
    AI 判断准不准**。
    """
    from app.models.case import Case
    from app.models.project import Branch

    stmt = select(ScriptRun.cc_analysis, ScriptRun.confirmed_cause, ScriptRun.failure_phenomenon).where(
        ScriptRun.cc_analysis.isnot(None)
    )
    if project_id:
        stmt = stmt.join(Case, Case.id == ScriptRun.case_id).join(
            Branch, Branch.id == Case.branch_id
        ).where(Branch.project_id == project_id)
    rows = (await session.execute(stmt)).all()

    buckets: dict[str, dict] = {}
    confirmed_total = agreed_total = 0
    # **抽检样本和人主动确认的必须分开算。** 人主动去看的那批有选择偏差 ——
    # 往往正是他已经觉得可疑的那些，算出来的一致率天然偏低，混在一起会误报成
    # 「CC 在系统性甩锅」。抽检是按哈希均匀抽的，**只有它能代表总体**。
    by_source: dict[str, dict] = {
        "sampled": {"label": "抽检（按哈希均匀抽，可代表总体）",
                    "confirmed": 0, "agreed": 0},
        "other":   {"label": "人主动确认（有选择偏差，别当总体看）",
                    "confirmed": 0, "agreed": 0},
    }
    for cc, confirmed, _phen in rows:
        cause = (cc or {}).get("cause") or "unknown"
        src = "sampled" if (cc or {}).get("route") == "self_serve_sampled" else "other"
        b = buckets.setdefault(cause, {"submitted": 0, "confirmed": 0, "agreed": 0, "overturnedTo": {}})
        b["submitted"] += 1
        if not confirmed:
            continue
        b["confirmed"] += 1
        confirmed_total += 1
        by_source[src]["confirmed"] += 1
        if confirmed == cause:
            b["agreed"] += 1
            agreed_total += 1
            by_source[src]["agreed"] += 1
        else:
            b["overturnedTo"][confirmed] = b["overturnedTo"].get(confirmed, 0) + 1

    alerts = []
    for cause, b in buckets.items():
        if b["confirmed"] >= 5:
            rate = 1 - b["agreed"] / b["confirmed"]
            b["overturnRate"] = round(rate * 100, 1)
            if cause == "product_defect" and rate > 0.30:
                alerts.append(
                    f"product_defect 桶推翻率 {b['overturnRate']}% > 30% —— "
                    "CC 可能在系统性把测试缺陷说成产品缺陷，该复核它的归因能力"
                )
        else:
            b["overturnRate"] = None  # 样本太少，不给数字免得被当结论

    # 用数组而不是以 cause 为键的字典：响应会经 camelCase 中间件，
    # 字典键会被一起转（test_defect → testDefect），跟枚举值对不上。
    by_cause = [
        {
            "cause": cause,
            "label": CAUSES.get(cause, cause),
            "submitted": b["submitted"],
            "confirmed": b["confirmed"],
            "agreed": b["agreed"],
            "overturnRate": b["overturnRate"],
            "overturnedTo": [{"cause": k, "count": v} for k, v in b["overturnedTo"].items()],
        }
        for cause, b in sorted(buckets.items(), key=lambda kv: -kv[1]["submitted"])
    ]
    for v in by_source.values():
        v["agreementRate"] = (round(v["agreed"] / v["confirmed"] * 100, 1)
                              if v["confirmed"] >= 5 else None)
    return {
        "totalAnalyses": len(rows),
        "confirmed": confirmed_total,
        "pending": len(rows) - confirmed_total,
        "agreementRate": round(agreed_total / confirmed_total * 100, 1) if confirmed_total else None,
        "byCause": by_cause,
        # 同样用数组不用字典：响应过 camelCase 中间件会把字典键一起转
        "bySource": [{"source": k, **v} for k, v in by_source.items()],
        "alerts": alerts,
        "note": ("样本 <5 的桶不给推翻率 —— 小样本的百分比会被当成结论，那比没有数字更糟。"
                 "**看总体准不准要看 bySource 里的抽检那一行**：人主动确认的那批"
                 "有选择偏差（他挑的本来就是可疑的），不能代表全部归因的水平。"),
    }
