"""CC 反馈 service —— 收、并、分诊、回音。

四件事的顺序是有讲究的：**先归并再校验配额**（撞同一个坑第五次不该被罚），
**先短路再建行**（wont_fix 过的东西不该再占一行）。写反了这两条，
闸门就从「挡噪音」变成「挡认真写的人」。

判据和取舍写在 docs/cc-feedback-channel.md，这里只放实现。
"""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.models.cc_feedback import (
    CATEGORIES,
    CATEGORY_LABEL,
    DECIDERS,
    OPEN_STATUSES,
    PENDING_STATUSES,
    SEVERITIES,
    STATUS_LABEL,
    STATUSES,
    WONT_FIX_SAMPLE_EVERY,
    CCFeedback,
)
from app.models.project import Project

# 正文下限。「这个工具不好用」这种拒收 —— 没有现象就没有可处理的东西，
# 而收下它的代价不是多一行，是让这张表变成一个没人愿意打开的地方。
MIN_BODY = 40
MAX_BODY = 8000

# 同一把 Key 24h 内的**新指纹**配额。归并命中不计入。
# 定 40 不定 3：2026-09-01 那份汇总一次就是 31 条，那是真实的一轮的量级 ——
# **配额必须高过它，否则这道闸挡掉的正好是它声称要放行的那种人**（一度写成 20，
# 就是拿一个自己举的反例当上限，认真写满一轮的人会在第 21 条上被弹回来）。
# 闸门要挡的是「同一件事说五遍」和「无证据刷条数」：前者走归并本来就不占配额，
# 后者被正文 40 字 + bug 必须写 expected/actual 挡住了 —— 配额是第三道，不是第一道。
QUOTA_PER_DAY = 40

# 新反馈进来自动跑一次 AI 处置。**默认开** —— 人是来看结果的，不是来点每一条的。
# 关掉的唯一用途是测试：单测里不该真打模型（打了会在没有网关的机器上变成偶发红）。
AUTO_TRIAGE = os.getenv("CC_FEEDBACK_AUTO_TRIAGE", "1") != "0"

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[，。、；：！？,.;:!?（）()「」【】\[\]“”\"'·\-—_/]+")


def fingerprint_of(tool_name: str | None, title: str) -> str:
    """(工具名, 标题) 归一后的指纹。

    **故意只用这两样**，不掺正文：同一个坑第二次报，正文措辞几乎一定不一样，
    掺进去就永远并不上，归并等于没做。标题进指纹则保证「同一个工具的两个不同毛病」
    不会被并成一条。
    """
    t = _PUNCT.sub("", _WS.sub("", (title or "").strip().lower()))
    k = f"{(tool_name or '').strip().lower()}|{t}"
    return hashlib.sha256(k.encode("utf-8")).hexdigest()[:32]


def _body_key(text: str) -> str:
    """正文归一化，只用来判「这次重报有没有新东西」。

    去掉空白和标点再比：改个标点、换行重排不算新证据。**不做同义词/语义比较** ——
    那种判错了没人看得出来，而这里判错的后果是一条裁定被复读推翻。
    """
    return _PUNCT.sub("", _WS.sub("", (text or "").strip().lower()))


def _err(msg: str, **extra) -> dict:
    return {"error": msg, **extra}


def _restart_hint() -> str:
    return ("平台后端**不带 --reload**，改完必须 `bash deploy/restart-backend.sh` 才生效。"
            "不重启就验，会得出「你没修」的结论 —— 那是旧进程在跑。")


def brief(f: CCFeedback, *, with_body: bool = False) -> dict:
    """一条反馈的对外形状。页面和 MCP 共用同一份，避免两处漂移。"""
    d = {
        "id": str(f.id),
        "title": f.title,
        "status": f.status,
        "statusLabel": STATUS_LABEL.get(f.status, f.status),
        "category": f.category,
        "categoryLabel": CATEGORY_LABEL.get(f.category or "", None),
        "reportedCategory": f.reported_category,
        "reportedCategoryLabel": CATEGORY_LABEL.get(f.reported_category or "", None),
        # CC 报的类和平台判的类不一致 —— 页面高亮它，因为这正是
        # 「他判断错了/没找对方法」这件事唯一的可统计形状
        "categoryMismatch": bool(
            f.category and f.reported_category and f.category != f.reported_category),
        "severity": f.severity,
        "toolName": f.tool_name,
        "reporter": f.reporter,
        "source": f.source,
        "projectId": str(f.project_id) if f.project_id else None,
        "occurrences": f.occurrences,
        "resolution": f.resolution,
        "duplicateOf": str(f.duplicate_of) if f.duplicate_of else None,
        "reopenedFrom": str(f.reopened_from) if f.reopened_from else None,
        # 谁落的这个裁定。页面上「来源」列旁边那个标就是它，
        # CC 那边也要知道 —— AI 判的 wont_fix 是可以带新证据翻案的，人判的不行。
        "decidedBy": f.decided_by,
        "needsHuman": f.needs_human,
        "sampled": f.sampled,
        "handledBy": f.handled_by,
        "handledAt": f.handled_at.isoformat() if f.handled_at else None,
        "ackAt": f.ack_at.isoformat() if f.ack_at else None,
        "createdAt": f.created_at.isoformat() if f.created_at else None,
        "lastSeenAt": f.last_seen_at.isoformat() if f.last_seen_at else None,
    }
    if with_body:
        d["body"] = f.body
        d["evidence"] = f.evidence
        d["aiAnalysis"] = f.ai_analysis
    return d


# ── 收 ────────────────────────────────────────────────────────────

def validate(title: str, body: str, category: str, evidence: dict | None) -> dict | None:
    """入口校验。返回 None 表示通过，否则返回**带出路的**拒绝信息。

    拒绝时只报字段名是不够的 —— 那会让人把缺的那半句删掉了事（项目须知超长那条
    踩过一模一样的坑）。所以每条拒绝都写清「怎么补」。
    """
    title = (title or "").strip()
    body = (body or "").strip()
    if not title:
        return _err("title 不能为空", howTo="一句话说清是什么毛病，例：「lum_get_case 不返回 bugRefs」")
    if len(title) > 200:
        return _err(f"标题太长（{len(title)} 字），200 字以内")
    if category not in CATEGORIES:
        return _err(
            f"category 只能是 {' / '.join(CATEGORIES)}",
            howTo="bug=说了会做 A 实际做了 B（含静默失败）；"
                  "improvement=行为没错但代价不合理/容易把人带错路；"
                  "requirement=平台今天没有这个能力。"
                  "拿不准就按你的判断填，平台分诊时会重新判一次 —— "
                  "**报错类不扣分**，两边不一致本身就是有用的信号。")
    if len(body) < MIN_BODY:
        return _err(
            f"正文只有 {len(body)} 字，至少 {MIN_BODY} 字。",
            why="「这个工具不好用」没法处理。收下它的代价不是多一行，"
                "是让这张表变成一个没人愿意打开的地方。",
            howTo="写三段：①你想干什么 ②平台实际怎么反应的（原始返回/报错抄一段）"
                  "③你期望它怎么反应。")
    if len(body) > MAX_BODY:
        return _err(f"正文 {len(body)} 字，超过 {MAX_BODY} 字。一条只说一件事，说不完拆成两条。")
    if category == "bug":
        ev = evidence or {}
        missing = [k for k in ("expected", "actual") if not str(ev.get(k) or "").strip()]
        if missing:
            # howTo 里**不写 evidence={...} 这种线上形状** —— 同一个 report() 有两条入口，
            # 而它俩的证据长得不一样：MCP 工具是四个平参（expected/actual/repro/refs，
            # 摊平是故意的，必填字段藏在 dict 里会变成「dict 收下了、校验才炸」），
            # 页面上是「期望/实际」两个输入框。照着 dict 抄的 CC 会得到一个不存在的参数名，
            # 而那个错来自我们自己给的指引 —— 比不给指引更糟。
            return _err(
                f"category=bug 时缺 {' 和 '.join(missing)}",
                why="「说好的是什么 / 实际是什么」—— 报缺陷本来就得先想清这两句，"
                    "想不清的多半不是缺陷，是用法。",
                howTo="expected=文档/工具描述说会怎样，actual=实际返回了什么"
                      "（页面上就是「期望」「实际」两个框）；"
                      "另有选填的 repro=怎么复现、refs=用例编号/场景id/运行id")
    return None


async def report(
    session: AsyncSession,
    *,
    title: str,
    body: str,
    category: str,
    tool_name: str | None = None,
    evidence: dict | None = None,
    project_id: str | None = None,
    reporter: str | None = None,
    source: str = "cc",
) -> dict:
    """CC 上报一条。返回三种形状之一：新建 / 归并 / 被 wont_fix 短路。"""
    bad = validate(title, body, category, evidence)
    if bad:
        return bad

    title = title.strip()
    body = body.strip()
    fp = fingerprint_of(tool_name, title)

    # ① 先看这个指纹有没有已经了结过 —— **短路优先于建行**。
    #    最近一条同指纹的记录说了算（reopened 之后老的那条不该再挡新的）。
    prev = (await session.execute(
        select(CCFeedback).where(CCFeedback.fingerprint == fp)
        .order_by(CCFeedback.created_at.desc()).limit(1)
    )).scalars().first()

    now = datetime.now(timezone.utc)

    if prev is not None and prev.status == "wont_fix":
        # 这条通道最要紧的一个行为：「不需要处理」必须挡得住第二次上报。
        # 挡不住的话，「回复原因」就只是一句客套 —— 下一轮它照样再来一遍。
        prev.occurrences += 1
        prev.last_seen_at = now

        # **但 AI 判的挡不住带新证据的重报。** 这是「让 AI 自己落裁定」的代价里
        # 唯一一个不可逆的：wont_fix 会永久短路同指纹，判错了不报错，只是安静地
        # 少一批反馈。对不可逆性的正确处置是把它拆掉，不是拿一道人工闸围住每一条 ——
        # 所以：换了说法再报一次，就转给人拍板；人判的才是终局。
        renewed = _body_key(body) != _body_key(prev.body)
        if prev.decided_by == "ai" and renewed and not prev.needs_human:
            ev = dict(prev.evidence or {})
            hist = list(ev.get("reReported") or [])
            hist.append({"at": now.isoformat(), "reporter": reporter, "body": body[:2000]})
            ev["reReported"] = hist[-5:]          # 整份重新赋值，JSONB 不认原地改
            prev.evidence = ev
            prev.needs_human = (
                f"AI 判过「不需要处理」，CC 换了说法又报了一次（第 {prev.occurrences} 次）。"
                "人来拍板：翻案（改成已认下）还是维持（维持就落成人判的，从此终局）。"
                "新那段说法在证据里的 reReported。")
            await session.flush()
            await write_audit_log(
                session, action="update", target_type="cc_feedback",
                target_id=prev.id, target_name=prev.title, project_id=prev.project_id,
                changes={"needsHuman": "AI 判的 wont_fix 被带新证据重报"})
            await session.commit()
            return {
                "id": str(prev.id),
                "alreadyDecided": "wont_fix",
                "escalated": "needs_human",
                "occurrences": prev.occurrences,
                "resolution": prev.resolution,
                "note": "上次这条是**AI** 判的「不需要处理」（理由见上）。你这次带了新的说法，"
                        "已经转给人拍板，不用再报了 —— 结论会从回音里回来。",
            }

        await session.commit()
        note = ("这条上次已经判为「不需要处理」，没有新建记录。上面就是当时给的理由和做法 —— "
                "如果你认为那个判断本身错了（不是同一件事、或者情况变了），"
                "换一个标题重报，并在正文里写清楚**跟上次那条有什么不同**。")
        if prev.needs_human:
            note = ("这条已经在等人拍板了（上次是 AI 判的「不需要处理」，你之前带新证据重报过一次），"
                    "这次只是次数 +1。结论会从回音里回来。")
        elif prev.decided_by == "ai" and not renewed:
            # 一个字没改地再报一遍 → 不翻案。翻案的判据是「有新东西」，
            # 不是「又说了一遍」；否则复读就能推翻裁定，那道闸等于没有。
            note += "（上次是 AI 判的，可以翻案 —— 但要写出**跟上次不同**的现象，照原样重报不算。）"
        return {
            "id": str(prev.id),
            "alreadyDecided": "wont_fix",
            "decidedBy": prev.decided_by,
            "occurrences": prev.occurrences,
            "resolution": prev.resolution,
            "note": note,
        }

    if prev is not None and prev.status == "duplicate" and prev.duplicate_of:
        target = await session.get(CCFeedback, prev.duplicate_of)
        if target is not None and target.status == "wont_fix":
            prev.occurrences += 1
            prev.last_seen_at = now
            await session.commit()
            return {"id": str(target.id), "alreadyDecided": "wont_fix",
                    "decidedBy": target.decided_by,
                    "resolution": target.resolution,
                    "note": "这条被并到另一条上，那条判的是「不需要处理」。"
                            + ("（那条是 AI 判的 —— 有新现象就换个标题报一条新的，"
                               "会转给人。）" if target.decided_by == "ai" else "")}

    # ② 还开着的同指纹 → 并进去，不新建，也不占配额
    if prev is not None and prev.status in OPEN_STATUSES:
        prev.occurrences += 1
        prev.last_seen_at = now
        if prev.project_id is None and project_id:
            prev.project_id = uuid.UUID(project_id)
        await session.commit()
        return {
            "id": str(prev.id),
            "merged": True,
            "occurrences": prev.occurrences,
            "currentStatus": prev.status,
            "statusLabel": STATUS_LABEL.get(prev.status, prev.status),
            "note": "同一件事已经报过了，这次只是次数 +1（撞得越多越靠前）。"
                    "有回音时会出现在 lum_next_duty 的「平台反馈有回音」队列里。",
        }

    # ③ 配额只管**新指纹**
    if reporter:
        since = now - timedelta(hours=24)
        used = (await session.execute(
            select(func.count()).select_from(CCFeedback)
            .where(CCFeedback.reporter == reporter, CCFeedback.created_at >= since)
        )).scalar_one()
        if used >= QUOTA_PER_DAY:
            return _err(
                f"这把 Key 24 小时内已经报了 {used} 条新问题，到上限（{QUOTA_PER_DAY}）了。",
                why="这道闸挡的是「同一件事说五遍」和无证据刷条数 —— "
                    "撞同一个坑再多次都不占配额（那走归并）。",
                howTo="先看 lum_list_my_feedback 里已经报过的，能合并的合并；"
                      "剩下的等回音，或者攒到明天。")

    # ④ done 之后同一个指纹又出现 = 回归，新建 + 记从哪张复发的（并进老账会把它埋掉）
    reopened_from = prev.id if prev is not None and prev.status == "done" else None

    row = CCFeedback(
        project_id=uuid.UUID(project_id) if project_id else None,
        source=source,
        reporter=reporter,
        tool_name=(tool_name or None),
        fingerprint=fp,
        title=title,
        body=body,
        evidence=evidence or None,
        reported_category=category,
        status="new",
        reopened_from=reopened_from,
        last_seen_at=now,
    )
    session.add(row)
    await session.flush()
    # 记账：反馈是 MCP 唯一一条「往平台自己身上写」的路，来源标签
    # （actor_type=mcp + Key 名）由 mcp/middleware.py 注入，这里不重复填。
    await write_audit_log(session, action="create", target_type="cc_feedback",
                          target_id=row.id, target_name=title,
                          project_id=row.project_id,
                          changes={"category": category, "toolName": tool_name})
    await session.commit()
    out = {
        "id": str(row.id),
        "status": "new",
        "note": "已收到。回音会出现在 lum_next_duty 的「平台反馈有回音」队列里，"
                "也可以随时调 lum_list_my_feedback 查。",
    }
    # **不等人点** —— 反馈一进来就自己跑一次分诊。丢后台是因为 CC 那边在等这次返回，
    # 而一次模型调用是秒级到十几秒；让上报挂在模型上等于把分诊延迟摊到每一次上报。
    if AUTO_TRIAGE:
        _spawn_auto(str(row.id))
        out["note"] += "（已自动交给 AI 分诊，通常一会儿就有结论。）"

    if reopened_from:
        out["reopenedFrom"] = str(reopened_from)
        out["note"] = ("已收到 —— 这个问题**之前修过一次又复现了**，按回归新开了一条"
                       "（没有并进老账，并进去会看不出中间好过）。")
    return out


# ── 读 ────────────────────────────────────────────────────────────

async def list_feedback(
    session: AsyncSession,
    *,
    status: str | None = None,
    pending_only: bool = False,
    category: str | None = None,
    project_id: str | None = None,
    keyword: str | None = None,
    awaiting_human: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int, dict]:
    q = select(CCFeedback)
    # 「等人拍板」是**跨状态**的一撮：AI 说自己判不了（还挂在 new 上）、
    # 和 AI 判的 wont_fix 被带新证据重报（挂在 wont_fix 上）。所以它按
    # needs_human 筛，不按 status —— 按状态筛会把后一种整个漏掉。
    if awaiting_human:
        q = q.where(CCFeedback.needs_human.isnot(None))
    elif pending_only:
        q = q.where(CCFeedback.status.in_(PENDING_STATUSES))
    elif status:
        q = q.where(CCFeedback.status == status)
    if category:
        q = q.where(CCFeedback.category == category)
    if project_id:
        q = q.where(CCFeedback.project_id == uuid.UUID(project_id))
    if keyword:
        like = f"%{keyword}%"
        q = q.where(or_(CCFeedback.title.ilike(like), CCFeedback.body.ilike(like),
                        CCFeedback.tool_name.ilike(like)))

    total = (await session.execute(
        select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await session.execute(
        q.order_by(CCFeedback.status != "new", CCFeedback.last_seen_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()

    # 项目名一起给：页面上「来源」那一列要显示的是名字，不是一串 uuid
    pids = {r.project_id for r in rows if r.project_id}
    names: dict[uuid.UUID, str] = {}
    if pids:
        for pid, name in (await session.execute(
                select(Project.id, Project.name).where(Project.id.in_(pids)))).all():
            names[pid] = name

    items = []
    for r in rows:
        d = brief(r)
        d["projectName"] = names.get(r.project_id) if r.project_id else None
        items.append(d)

    counts_rows = (await session.execute(
        select(CCFeedback.status, func.count()).group_by(CCFeedback.status))).all()
    counts = {s: n for s, n in counts_rows}
    awaiting = (await session.execute(
        select(func.count()).select_from(CCFeedback)
        .where(CCFeedback.needs_human.isnot(None)))).scalar_one()
    summary = {
        "total": sum(counts.values()),
        "pending": sum(counts.get(s, 0) for s in PENDING_STATUSES),
        # 页面上真正要人动手的只有这个数 —— 其余都是 AI 判完的，人是来看的
        "awaitingHuman": awaiting,
        "byStatus": counts,
        "batch": batch_status(),
    }
    return items, total, summary


async def get_detail(session: AsyncSession, feedback_id: str) -> dict | None:
    row = await session.get(CCFeedback, uuid.UUID(feedback_id))
    if row is None:
        return None
    d = brief(row, with_body=True)
    if row.project_id:
        d["projectName"] = (await session.execute(
            select(Project.name).where(Project.id == row.project_id))).scalar_one_or_none()
    return d


# ── 处置 ──────────────────────────────────────────────────────────

async def triage(
    session: AsyncSession,
    feedback_id: str,
    *,
    status: str,
    category: str | None = None,
    severity: str | None = None,
    resolution: str | None = None,
    duplicate_of: str | None = None,
    actor: str | None = None,
    decided_by: str = "human",
) -> dict:
    """分诊 / 处置。**校验在这一层**，页面和脚本两条路走的是同一份规矩。

    走到这里就是**人在拍板**（默认 decided_by='human'）—— AI 那条路走
    ai_handle()，它自己落裁定、自己记 decided_by='ai'。两者的区别不在权限，
    在**可不可逆**：人判的 wont_fix 从此终局，AI 判的能被带新证据的重报翻案。
    """
    row = await session.get(CCFeedback, uuid.UUID(feedback_id))
    if row is None:
        return _err("反馈不存在")
    if status not in STATUSES:
        return _err(f"status 只能是 {' / '.join(STATUSES)}")
    if category is not None and category not in CATEGORIES:
        return _err(f"category 只能是 {' / '.join(CATEGORIES)}")
    if severity is not None and severity not in SEVERITIES:
        return _err(f"severity 只能是 {' / '.join(SEVERITIES)}")

    resolution = (resolution or "").strip() or None

    # 用户定的规矩：「就回复他原因，并置为不需要处理」—— 落成硬校验，
    # 否则「回复原因」这一步一定会在赶时间的时候被跳过，而这条通道的全部价值就在回音上。
    if status in ("done", "wont_fix") and not resolution:
        return _err(
            f"置为「{STATUS_LABEL[status]}」必须写回音（resolution）",
            howTo=("说清为什么不做；如果是他没找对方法，**把正确方法写出来** —— "
                   "只说「你错了」等于没回音，下一轮照原样再撞一次。")
            if status == "wont_fix" else
            ("写「现在该怎么做」，不是「修好了」。" + _restart_hint()))
    if status == "duplicate" and not duplicate_of:
        return _err("标为重复必须给 duplicate_of（并到哪一条上）")
    # 认下来（triaged 及之后）就该有类 —— 没有类的「已认下」等于什么都没认
    if status in ("triaged", "in_progress", "done") and not (category or row.category):
        return _err("认下一条反馈时必须定类（bug / improvement / requirement）")

    if category:
        row.category = category
    if severity:
        row.severity = severity
    if duplicate_of:
        row.duplicate_of = uuid.UUID(duplicate_of)
    if resolution:
        row.resolution = resolution
    row.status = status
    row.handled_by = actor
    row.handled_at = datetime.now(timezone.utc)
    if decided_by in DECIDERS:
        row.decided_by = decided_by
    # 人拍完板，这条就不欠人什么了 —— 从「等人拍板」里消失
    row.needs_human = None
    row.sampled = False
    # 有了新回音 → 重新变成「未读」，CC 下一轮的 next_duty 才看得到
    if status in ("done", "wont_fix", "duplicate"):
        row.ack_at = None
    await session.flush()
    await write_audit_log(session, action="update", target_type="cc_feedback",
                          target_id=row.id, target_name=row.title,
                          project_id=row.project_id,
                          changes={"status": status, "category": row.category,
                                   "decidedBy": row.decided_by,
                                   "resolution": (resolution or "")[:200]})
    await session.commit()
    return brief(row, with_body=True)


# ── 回音 ──────────────────────────────────────────────────────────

def _echo_of(f: CCFeedback) -> dict:
    d = {
        "id": str(f.id),
        "title": f.title,
        "status": f.status,
        "statusLabel": STATUS_LABEL.get(f.status, f.status),
        "category": f.category,
        "categoryLabel": CATEGORY_LABEL.get(f.category or "", None),
        "resolution": f.resolution,
    }
    d["decidedBy"] = f.decided_by
    if f.status == "done":
        d["beforeYouVerify"] = _restart_hint()
    if f.status == "wont_fix" and f.decided_by == "ai":
        # 不写这句，AI 的一次误判在 CC 那边看起来就是终局 —— 而它不是
        d["canReopen"] = ("这条是 **AI** 判的。如果你认为判错了，"
                          "**带上跟上次不同的现象**原标题再报一次，会转给人拍板。"
                          "照原样复读不算新证据，不会翻案。")
    if f.category and f.reported_category and f.category != f.reported_category:
        d["categoryChanged"] = (
            f"你报的是「{CATEGORY_LABEL.get(f.reported_category)}」，"
            f"平台判为「{CATEGORY_LABEL.get(f.category)}」—— 回音里写了为什么。")
    return d


async def unread_echoes(
    session: AsyncSession,
    *,
    project_id: str | None = None,
    reporter: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """有结论、CC 还没取走的那些。next_duty 的第 ⑧ 队列就是它。

    project_id 和 reporter **是或的关系**：同一个项目可能换过 Key，
    换了 Key 就看不到自己项目的回音那才是坏的。
    """
    q = select(CCFeedback).where(
        CCFeedback.status.in_(("done", "wont_fix", "duplicate")),
        CCFeedback.ack_at.is_(None),
        CCFeedback.resolution.isnot(None),
    )
    conds = []
    if project_id:
        conds.append(CCFeedback.project_id == uuid.UUID(project_id))
    if reporter:
        conds.append(CCFeedback.reporter == reporter)
    if conds:
        q = q.where(or_(*conds))
    rows = (await session.execute(
        q.order_by(CCFeedback.handled_at.desc()).limit(limit))).scalars().all()
    return [_echo_of(r) for r in rows]


async def list_mine(
    session: AsyncSession,
    *,
    project_id: str | None = None,
    reporter: str | None = None,
    status: str | None = None,
    unread_only: bool = True,
    limit: int = 30,
) -> dict:
    """CC 查自己报过的 + 下场，**并把读到的回音标记为已读**。

    标记必须在这里做：不标的话 next_duty 那个队列会一直挂着同一条，
    几轮之后 CC 就学会无视它了 —— 一个永远不消的待办等于没有待办。
    """
    q = select(CCFeedback)
    conds = []
    if project_id:
        conds.append(CCFeedback.project_id == uuid.UUID(project_id))
    if reporter:
        conds.append(CCFeedback.reporter == reporter)
    if conds:
        q = q.where(or_(*conds))
    if status:
        q = q.where(CCFeedback.status == status)
    elif unread_only:
        q = q.where(CCFeedback.status.in_(("done", "wont_fix", "duplicate")),
                    CCFeedback.ack_at.is_(None))
    rows = (await session.execute(
        q.order_by(CCFeedback.last_seen_at.desc()).limit(limit))).scalars().all()

    now = datetime.now(timezone.utc)
    acked = 0
    out = []
    for r in rows:
        d = brief(r)
        if r.status in ("done", "wont_fix", "duplicate") and r.resolution:
            d.update(_echo_of(r))
            if r.ack_at is None:
                r.ack_at = now
                acked += 1
        out.append(d)
    if acked:
        await session.commit()

    return {
        "feedback": out,
        "total": len(out),
        "markedRead": acked,
        "usage": "回音看完就算读过了，不会再出现在 lum_next_duty 里。"
                 "被判「不需要处理」的那些，**别原样再报一遍** —— 同指纹会被短路，"
                 "认为判错了就换个标题、写清跟上次有什么不同。",
    }


# ── AI 处置：判得了的自己判 ─────────────────────────────────────────
#
# 这一节 2026-09-01 改过一次口径，改动本身比代码重要，所以写在这儿：
#
# **原来**：AI 只出建议存进 ai_analysis，状态只有人能落。理由是 wont_fix 的回音会
# 永久短路后续同指纹上报，让 AI 单方面下这个判定等于给它一个「把一类反馈永久关死、
# 以后没人再看得到」的开关，而这种错不报错。
#
# **现在**：AI 自己落。上面那个理由说的是**一个不可逆性**，而对不可逆性的正确处置
# 是把它拆掉，不是拿一道人工闸围住它 —— 围住的代价是每条反馈都要等人，那和平台
# 整体的分工反着来（人是来看结果、或者点一下执行的）。拆法两条，都在 report() 里：
#   · AI 判的 wont_fix **挡不住带新证据的重报** → 转 needs_human 交人。人判的才终局。
#   · AI 判的 wont_fix 每 WONT_FIX_SAMPLE_EVERY 条抽 1 给人复核（校准，不拦裁定）。
#
# 另一半是**判据**。原来的提示词只喂了标题+正文+证据 —— 那样「自己判」就是瞎判，
# 尤其判不了最要紧的那一类：「平台其实有这个能力，是 CC 没找对方法」。所以现在喂
# 三样平台自己的事实（_platform_facts）：撞到的那个工具的完整描述、它的**实现源码**、
# 以及**全部工具的清单**（判「有没有别的工具已经能干这件事」只能靠它）。


def _first_sentence(text: str, limit: int = 110) -> str:
    """工具描述的第一句，用来铺全量清单。描述里有大量 ** 和括号补语，先摘掉。"""
    t = re.sub(r"\*\*|【|】", "", (text or "").strip())
    for sep in ("。", "；", "\n"):
        if sep in t:
            t = t.split(sep)[0]
            break
    return t[:limit]


def _platform_facts(tool_name: str | None, *, max_src: int = 5000) -> str:
    """喂给模型的**平台自身事实**。判据不足的「自己判」就是瞎判，这个函数就是那个判据。

    故意不读 docs/*.md：那些是给人看的取舍记录，篇幅大、和某一条反馈的相关性只能靠
    关键词猜。判得动的是**可执行的事实** —— 工具描述（平台「说了会做什么」，bug 的
    定义就是它和实际不一致）、实现源码（实际做了什么）、全量工具清单（「他其实没找
    对方法」唯一的判据）。
    """
    try:
        import inspect

        from app.mcp import TOOL_CATALOG, TOOL_FUNCS
    except Exception:  # pragma: no cover - 只在 MCP 没装起来时走到
        return "（取不到工具清单）"

    parts: list[str] = []

    hit = next((t for t in TOOL_CATALOG if t["name"] == tool_name), None)
    if hit:
        parts.append(
            f"### 撞到的这个工具：{hit['name']}（分类：{hit['category']}）\n"
            f"平台对外承诺的行为（工具描述原文，**bug 的判据就是它和实际不一致**）：\n"
            f"{hit['description']}\n\n参数：{hit['params']}")
        func = TOOL_FUNCS.get(tool_name)
        if func is not None:
            try:
                src = inspect.getsource(func)  # type: ignore[arg-type]
                where = f"{inspect.getsourcefile(func)}:{inspect.getsourcelines(func)[1]}"
                if len(src) > max_src:
                    src = src[:max_src] + "\n…（截断）"
                parts.append(f"### 它的实现（{where}）\n```python\n{src}\n```")
            except Exception:
                pass
    elif tool_name:
        parts.append(f"### 注意：CC 报的工具名 `{tool_name}` **不在工具清单里**。"
                     f"要么他记错了名字，要么这个能力真的不存在（那就是 requirement）。")

    lines = [f"- {t['name']}（{t['category']}）：{_first_sentence(t['description'])}"
             for t in TOOL_CATALOG]
    parts.append("### 平台现有的全部工具（共 %d 个）—— 判「他其实没找对方法」就看这份\n%s"
                 % (len(TOOL_CATALOG), "\n".join(lines)))
    return "\n\n".join(parts)


_HANDLE_PROMPT = """你是 Lumiere 测试平台的维护者，正在处置一条外部 Claude Code（CC）报回来的反馈。
CC 是通过 MCP 调用平台工具来梳理用例的自动化使用者，它报的是**平台自己的问题**。

**你的裁定直接生效，不经人复核。** 所以别写"建议"、别留待定 —— 判得了就判，
判不了就明说（verdict=needs_human），那会转给人，而人的时间是稀缺的：
**只有真判不了才用它**（缺需求出处、需要产品方向上的取舍、证据不足到无法判）。

## 先判 verdict，四选一

· `triaged` —— 认下了，这是平台该改的。定类 + 严重度，接下来维护者去改代码。
· `wont_fix` —— 不该改。**两种情况，回音写法不一样**：
    ① 这件事本身不该做（例：给 mock 表加 project_id 是假隔离）→ 说清为什么。
    ② **CC 判断错了 / 没找对方法** —— 平台其实已经有这个能力，他没找到，或者理解反了。
       → **必须把正确做法写出来**（调哪个工具、传什么参数）。只说「你错了」等于没回音，
       下一轮他照原样再撞一次。判这一条**看下面的全量工具清单**，别凭印象。
· `duplicate` —— 和候选里某一条是同一件事。必须给出 duplicateOf（候选里的 id 原文）。
· `needs_human` —— 你判不了。needsHuman 里写**判不了什么、缺的是什么**，别写"建议人工确认"。

**不要输出 `done`。** done 的含义是「代码已经改完了」，而你没有改过代码。

## 再定 category（平台的裁定，可以和 CC 自报的不一致 —— 不一致本身是有用的信号）

· bug = 平台**说了会做 A、实际做了 B**（含静默失败：知道出错却返回语法上合法的结果）。
  判它必须对着上面的「工具描述原文」和「实现源码」—— 描述里没承诺过的行为不算 bug。
· improvement = 行为没错，但代价不合理、或容易把使用者带错路（工具描述有歧义也算）。
· requirement = 平台今天没有这个能力。

## severity：会不会导致**假绿**或让人做出错误决定

会 → high；只是费事、绕得过去 → medium；纯体验 → low。

## 只输出 JSON，不要任何解释文字

{{"verdict":"triaged|wont_fix|duplicate|needs_human",
  "category":"bug|improvement|requirement",
  "severity":"high|medium|low",
  "resolution":"给 CC 的回音正文。wont_fix 时必填，如果是他没找对方法，把正确做法写出来",
  "duplicateOf":"verdict=duplicate 时必填，候选里的 id",
  "needsHuman":"verdict=needs_human 时必填：判不了什么、缺什么",
  "reasoning":"两三句话说清判据，引用工具描述或源码里的具体一句",
  "fixHint":"如果要改，改哪个文件/哪一段（给维护者指路，不确定就留空）",
  "risk":"不处理会怎样"}}

═══════ 平台自身的事实（判据）═══════
{facts}

═══════ 已有的相近反馈（判 duplicate 用；不像就别硬并）═══════
{siblings}

═══════ 这条反馈 ═══════
工具：{tool}
CC 自报的类：{reported}
撞到的次数：{occurrences}
标题：{title}
正文：
{body}
证据：
{evidence}
"""


async def _siblings_for(session: AsyncSession, row: CCFeedback, limit: int = 8) -> str:
    """同工具名的其它反馈 —— 判 duplicate 的候选。

    **只给同工具的**：跨工具的「看起来像」并起来是有害的，那会把两个不同的缺陷合成
    一条，修了一个就整条关掉，另一个从此没有家。
    """
    if not row.tool_name:
        return "（这条没填工具名，不给候选 —— 没有工具名的「看起来像」不足以并条）"
    rows = (await session.execute(
        select(CCFeedback)
        .where(CCFeedback.id != row.id, CCFeedback.tool_name == row.tool_name)
        .order_by(CCFeedback.last_seen_at.desc()).limit(limit))).scalars().all()
    if not rows:
        return "（同一个工具下没有别的反馈）"
    return "\n".join(
        f"- id={r.id} 状态={STATUS_LABEL.get(r.status, r.status)} "
        f"类={CATEGORY_LABEL.get(r.category or '', '未定')} 标题={r.title}"
        for r in rows)


async def _sample_this_wont_fix(session: AsyncSession) -> bool:
    """这条 AI 判的 wont_fix 要不要抽给人复核。每 WONT_FIX_SAMPLE_EVERY 条抽 1。

    按「已经判了几条」取模，**不掷骰子**：掷骰子会出现连续二十条一条没抽到的走运
    区间，而抽检的全部意义就在于稳定的覆盖率。
    """
    n = (await session.execute(
        select(func.count()).select_from(CCFeedback).where(
            CCFeedback.status == "wont_fix", CCFeedback.decided_by == "ai"))).scalar_one()
    return (n + 1) % WONT_FIX_SAMPLE_EVERY == 0


async def ai_handle(session: AsyncSession, feedback_id: str) -> dict:
    """跑一次 AI 处置：分析 + **直接落裁定**。判不了的落 needs_human 交人。

    页面上那个按钮和新反馈进来时的自动触发走的是同一个这里 —— 两条路各写一套规矩，
    迟早会漂成两种行为。
    """
    import json

    from app.services.ai.llm_client import complete
    from app.services.ai_config_resolver import resolve_ai_config

    row = await session.get(CCFeedback, uuid.UUID(feedback_id))
    if row is None:
        return _err("反馈不存在")

    cfg = await resolve_ai_config(row.project_id, session, capability="cc-feedback-triage")
    if cfg is None:
        return _err("没有可用的 AI 配置",
                    howTo="去「AI 服务配置 → AI 能力→模型」给「CC 反馈分诊」绑一个模型。")

    prompt = _HANDLE_PROMPT.format(
        facts=_platform_facts(row.tool_name),
        siblings=await _siblings_for(session, row),
        tool=row.tool_name or "（未填）",
        reported=CATEGORY_LABEL.get(row.reported_category or "", "（未填）"),
        occurrences=row.occurrences,
        title=row.title,
        body=row.body,
        evidence=json.dumps(row.evidence or {}, ensure_ascii=False, indent=2),
    )
    resp = await complete([{"role": "user", "content": prompt}], config=cfg, max_tokens=2500)
    text = (getattr(resp, "content", None) or "").strip()
    if not text:
        # 一个字都没回来 —— **报错，别落库**。落了就是页面上一块空的「AI 分析」，
        # 看着像模型认真读过、觉得没啥可说，实际是这次调用整个没产出；
        # 而且它还会把上一次真有内容的分析覆盖掉。
        # 实测成因（2026-09-01，dev 库真跑）：主路 429 → 降级到 CLI 通道
        # （claude-proxy :38210），那条通道后面是 Claude Code —— 反馈正文本身
        # 就长得像一件待办，它会去**做事**而不是作答，回给我们的 text 就是空的。
        # 这种情况这条反馈**留在 new 上**等下一次触发，也**不写 needs_human**：
        # 模型没说过话，不能替它说「它判不了」（那把一次限流记成了一次判不动）。
        return _err(
            "模型没有返回内容",
            why="主路限流时会降级到 CLI 通道，那条通道拿到这种「像一件待办」的正文"
                "会去做事而不是作答，回来是空的。这条反馈仍留在「待处理」，没被改动。",
            howTo="过一会儿重试一次（主路不限流就不会降级）；一直空就去"
                  "「AI 服务配置 → AI 能力→模型」给「CC 反馈分诊」换一个模型。",
        )

    # 模型经常裹一层 ```json —— 剥掉再解析。
    m = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except Exception:
        data = {}

    model_name = getattr(cfg, "model", None)
    now = datetime.now(timezone.utc)

    # ── 落裁定。**校验不合格一律降级成 needs_human，不猜** ──
    # 猜的代价不对称：猜错一个 wont_fix 会挡住后续上报（一类反馈就此消失且不报错），
    # 而转给人的代价只是多等一会儿。
    verdict = (data.get("verdict") or "").strip()
    category = (data.get("category") or "").strip() or None
    severity = (data.get("severity") or "").strip() or None
    resolution = (data.get("resolution") or "").strip() or None
    fallback: str | None = None

    if not data:
        fallback = "模型没输出可解析的 JSON（原文已存进 ai_analysis）—— 这条得人看一眼。"
        data = {"parseFailed": True, "raw": text[:2000]}
    elif verdict == "done":
        # 它落不了 done（没改过代码），但既然判了「该改」，认下来是对的
        verdict = "triaged"
        data["coercedFromDone"] = "AI 给了 done，按 triaged 落 —— done 的含义是代码改完了。"

    if fallback is None and verdict not in ("triaged", "wont_fix", "duplicate", "needs_human"):
        fallback = f"模型给的 verdict 是 {verdict!r}，不在四选一里。"
    if fallback is None and verdict != "needs_human" and category not in CATEGORIES:
        fallback = f"模型没给出合法的 category（拿到 {category!r}）。"
    if fallback is None and verdict == "wont_fix" and not resolution:
        # 判「不做」却写不出理由 = 没判明白。这条不能放过去：wont_fix 的全部价值在回音上
        fallback = "模型判了「不需要处理」但没写回音 —— 判不做而说不出正确做法，等于没判。"
    dup_id = None
    if fallback is None and verdict == "duplicate":
        cand = (data.get("duplicateOf") or "").strip()
        try:
            dup_id = uuid.UUID(cand)
        except Exception:
            dup_id = None
        if dup_id is None or (await session.get(CCFeedback, dup_id)) is None:
            fallback = f"模型说这条和 {cand!r} 重复，但那个 id 找不到。"
        elif dup_id == row.id:
            fallback = "模型把这条并到了它自己身上。"

    data["model"] = model_name
    data["at"] = now.isoformat()
    row.ai_analysis = data

    if fallback is not None or verdict == "needs_human":
        row.needs_human = ((data.get("needsHuman") or "").strip()
                           or fallback or "模型说它判不了")
        row.decided_by = None          # 没落裁定，就不算「谁判的」
        await session.flush()
        await write_audit_log(session, action="update", target_type="cc_feedback",
                              target_id=row.id, target_name=row.title,
                              project_id=row.project_id,
                              changes={"needsHuman": row.needs_human[:200]})
        await session.commit()
        return {"id": str(row.id), "verdict": "needs_human",
                "needsHuman": row.needs_human, "aiAnalysis": data,
                "note": "AI 说它判不了，这条留给人 —— 页面上「等人拍板」筛得到。"}

    if category:
        row.category = category
    if severity in SEVERITIES:
        row.severity = severity
    if resolution:
        row.resolution = resolution
    if dup_id:
        row.duplicate_of = dup_id
    row.status = verdict
    row.decided_by = "ai"
    row.needs_human = None
    row.handled_by = f"AI（{model_name or '未知模型'}）"
    row.handled_at = now
    if verdict in ("wont_fix", "duplicate"):
        row.ack_at = None              # 有新回音 → CC 下一轮的 next_duty 看得到
        if verdict == "wont_fix":
            row.sampled = await _sample_this_wont_fix(session)
    await session.flush()
    await write_audit_log(session, action="update", target_type="cc_feedback",
                          target_id=row.id, target_name=row.title,
                          project_id=row.project_id,
                          changes={"status": verdict, "category": row.category,
                                   "decidedBy": "ai", "sampled": row.sampled,
                                   "resolution": (resolution or "")[:200]})
    await session.commit()
    out = brief(row, with_body=True)
    out["verdict"] = verdict
    out["note"] = f"AI 已落「{STATUS_LABEL.get(verdict, verdict)}」，不用再点确认。"
    if row.sampled:
        out["note"] += "这条被抽中复核（每 %d 条抽 1）—— 裁定已经生效，复核只为校准。" % (
            WONT_FIX_SAMPLE_EVERY,)
    return out


async def auto_handle_later(feedback_id: str) -> None:
    """新反馈进来后**自动**跑一次处置。开一个独立 session，别蹭调用方那个。

    为什么不同步跑：CC 那边 `lum_report_feedback` 会等着，而一次模型调用是秒级到
    十几秒。让上报挂在模型上，等于把平台的分诊延迟摊到 CC 的每一次上报上。
    为什么整个包在 try 里：这是**旁路**，它失败不该动摇「反馈已收到」这个事实 ——
    失败的结果只是这条留在 new 上，人在页面上点一下按钮就能重跑。
    """
    from app.deps.db import async_session_factory

    try:
        async with async_session_factory() as session:
            await ai_handle(session, feedback_id)
    except Exception:  # pragma: no cover - 旁路，不许把异常带回上报链路
        import logging
        logging.getLogger(__name__).warning(
            "CC 反馈自动分诊失败：%s", feedback_id, exc_info=True)


# ── 批量：勾一批丢给 AI，人不用一条条点 ──────────────────────────
#
# 为什么要有它：一轮汇总就是 31 条（2026-09-01 那份真实数据）。一条条点，人要点
# 31 次、每次等十几秒 —— 那和「人是来看结果的」这个分工反着来。自动分诊之前建的
# 存量、以及自动分诊那次失败留在「待处理」上的，都要靠这个入口一次推完。

# 全局单批，不是每人一批。**故意的**：并发打模型只会一起撞 429 然后一起降级到
# CLI 通道，而那条通道对这种正文会回空（见 ai_handle 里的实测记录）—— 结果是
# 一批全废。宁可排队。
_BATCH: dict = {
    "running": False, "total": 0, "done": 0, "failed": 0, "needsHuman": 0,
    "startedAt": None, "finishedAt": None, "current": None, "startedBy": None,
}
_BG: set = set()


def batch_status() -> dict:
    """当前批次的进度。页面靠它显示进度条 —— 没有进度的批量在人眼里等于卡死了。"""
    return dict(_BATCH)


def _spawn(coro) -> None:
    """丢后台跑。存一份强引用：asyncio 只弱引用 task，不存会被 GC 掉在半路。"""
    import asyncio

    try:
        t = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:  # 没有运行中的 loop（同步脚本里调到了）
        coro.close()
        return
    _BG.add(t)
    t.add_done_callback(_BG.discard)


def _spawn_auto(feedback_id: str) -> None:
    _spawn(auto_handle_later(feedback_id))


async def start_batch(session: AsyncSession, ids: list[str] | None = None, *,
                      actor: str | None = None) -> dict:
    """勾一批（或不勾 = 全部还没判的）丢给 AI。立刻返回，进度走 batch_status()。

    不勾就是「全部待处理」：页面上最常见的动作是「把积压的一次推完」，
    而让人先全选 31 行再点，只是把同一件事变成两步。
    """
    if _BATCH["running"]:
        return _err(
            "已经有一批在跑了",
            why="全局只跑一批 —— 并发打模型会一起撞限流然后一起降级，那条降级通道"
                "对这种正文会回空，结果是一批全废。",
            howTo="等这批跑完（页面上有进度），或者只挑几条单独处理。")

    if ids:
        pending = [str(uuid.UUID(i)) for i in ids]
    else:
        rows = (await session.execute(
            select(CCFeedback.id).where(CCFeedback.status.in_(PENDING_STATUSES))
            .order_by(CCFeedback.last_seen_at.desc()))).scalars().all()
        pending = [str(i) for i in rows]

    if not pending:
        return _err("没有要处理的反馈", howTo="勾几条，或者先看看「待处理」里还有没有。")

    _BATCH.update(running=True, total=len(pending), done=0, failed=0, needsHuman=0,
                  startedAt=datetime.now(timezone.utc).isoformat(), finishedAt=None,
                  current=None, startedBy=actor)
    _spawn(_run_batch(pending))
    return {"accepted": len(pending), "batch": batch_status(),
            "note": "已经交给 AI 了，会一条条判完（顺序跑，避免一起撞限流）。"
                    "页面上刷新就能看到状态在变；判不了的会落到「等人拍板」。"}


async def _run_batch(ids: list[str]) -> None:
    """顺序跑完一批。**每条一个独立 session**：一条炸了不该带走后面 30 条。"""
    import logging

    from app.deps.db import async_session_factory

    log = logging.getLogger(__name__)
    try:
        for fid in ids:
            _BATCH["current"] = fid
            try:
                async with async_session_factory() as session:
                    out = await ai_handle(session, fid)
                if out.get("error"):
                    _BATCH["failed"] += 1
                    log.warning("批量分诊这条没成：%s —— %s", fid, out.get("error"))
                else:
                    _BATCH["done"] += 1
                    if out.get("verdict") == "needs_human":
                        _BATCH["needsHuman"] += 1
            except Exception:
                _BATCH["failed"] += 1
                log.warning("批量分诊这条炸了：%s", fid, exc_info=True)
    finally:
        # finally 里收尾：中途抛异常也得把 running 放掉，否则整个进程再也开不了第二批
        _BATCH.update(running=False, current=None,
                      finishedAt=datetime.now(timezone.utc).isoformat())
