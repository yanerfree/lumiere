"""CC 反馈 service —— 收、并、分诊、回音。

四件事的顺序是有讲究的：**先归并再校验配额**（撞同一个坑第五次不该被罚），
**先短路再建行**（wont_fix 过的东西不该再占一行）。写反了这两条，
闸门就从「挡噪音」变成「挡认真写的人」。

判据和取舍写在 docs/cc-feedback-channel.md，这里只放实现。
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.models.cc_feedback import (
    CATEGORIES,
    CATEGORY_LABEL,
    OPEN_STATUSES,
    PENDING_STATUSES,
    SEVERITIES,
    STATUS_LABEL,
    STATUSES,
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
        await session.commit()
        return {
            "id": str(prev.id),
            "alreadyDecided": "wont_fix",
            "occurrences": prev.occurrences,
            "resolution": prev.resolution,
            "note": "这条上次已经判为「不需要处理」，没有新建记录。上面就是当时给的理由和做法 —— "
                    "如果你认为那个判断本身错了（不是同一件事、或者情况变了），"
                    "换一个标题重报，并在正文里写清楚**跟上次那条有什么不同**。",
        }

    if prev is not None and prev.status == "duplicate" and prev.duplicate_of:
        target = await session.get(CCFeedback, prev.duplicate_of)
        if target is not None and target.status == "wont_fix":
            prev.occurrences += 1
            prev.last_seen_at = now
            await session.commit()
            return {"id": str(target.id), "alreadyDecided": "wont_fix",
                    "resolution": target.resolution,
                    "note": "这条被并到另一条上，那条判的是「不需要处理」。"}

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
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int, dict]:
    q = select(CCFeedback)
    if pending_only:
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
    summary = {
        "total": sum(counts.values()),
        "pending": sum(counts.get(s, 0) for s in PENDING_STATUSES),
        "byStatus": counts,
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
) -> dict:
    """分诊 / 处置。**校验在这一层**，页面和脚本两条路走的是同一份规矩。"""
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
    # 有了新回音 → 重新变成「未读」，CC 下一轮的 next_duty 才看得到
    if status in ("done", "wont_fix", "duplicate"):
        row.ack_at = None
    await session.flush()
    await write_audit_log(session, action="update", target_type="cc_feedback",
                          target_id=row.id, target_name=row.title,
                          project_id=row.project_id,
                          changes={"status": status, "category": row.category,
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
    if f.status == "done":
        d["beforeYouVerify"] = _restart_hint()
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


# ── AI 分诊建议 ────────────────────────────────────────────────────

_TRIAGE_PROMPT = """你是 Lumiere 测试平台的维护者，正在分诊一条外部 Claude Code（CC）报回来的反馈。
CC 是通过 MCP 调用平台工具来梳理用例的自动化使用者，它报的是**平台自己的问题**。

请判断三件事：
1. 这条属于 bug / improvement / requirement 中的哪一类。
   · bug = 平台说了会做 A、实际做了 B（含静默失败：知道出错却返回语法上合法的结果）
   · improvement = 行为没错，但代价不合理、或容易把使用者带错路（工具描述有歧义也算）
   · requirement = 平台今天没有这个能力
2. 严重度 high / medium / low。判据是**会不会导致假绿或让人做出错误决定**：
   会 → high；只是费事、绕得过去 → medium；纯体验 → low。
3. 建议怎么处置。特别注意第三种可能：**CC 判断错了 / 没找对方法** ——
   平台其实已经有这个能力，只是他没找到，或者他理解反了。
   这种要给 wont_fix，并且**必须把正确做法写出来**（只说「你错了」等于没回音）。

只输出 JSON，不要任何解释文字，形如：
{{"category":"bug|improvement|requirement","severity":"high|medium|low",
 "suggestedStatus":"triaged|wont_fix","reasoning":"两三句话说清判据",
 "suggestedResolution":"给 CC 的回音正文；wont_fix 时必须写正确做法","risk":"如果不处理会怎样"}}

── 反馈内容 ──
工具：{tool}
CC 自报的类：{reported}
标题：{title}
正文：
{body}
证据：
{evidence}
"""


async def ai_triage(session: AsyncSession, feedback_id: str) -> dict:
    """跑一次 AI 分诊，**只写建议不改状态**。

    为什么不让它直接落状态：wont_fix 的回音会永久短路后续同指纹上报
    （见 report() 第 ① 步）。让 AI 单方面下这个判定，等于给它一个
    「把一类反馈永久关死、而且以后没人会再看到」的开关 —— 这类权力
    出错时不报错，只是安静地少一批反馈。
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

    prompt = _TRIAGE_PROMPT.format(
        tool=row.tool_name or "（未填）",
        reported=CATEGORY_LABEL.get(row.reported_category or "", "（未填）"),
        title=row.title,
        body=row.body,
        evidence=json.dumps(row.evidence or {}, ensure_ascii=False, indent=2),
    )
    resp = await complete([{"role": "user", "content": prompt}], config=cfg, max_tokens=1500)
    text = (getattr(resp, "content", None) or "").strip()
    if not text:
        # 一个字都没回来 —— **报错，别落库**。落了就是页面上一块空的「AI 分析」，
        # 看着像模型认真读过、觉得没啥可说，实际是这次调用整个没产出；
        # 而且它还会把上一次真有内容的分析覆盖掉。
        # 实测成因（2026-09-01，dev 库真跑）：主路 429 → 降级到 CLI 通道
        # （claude-proxy :38210），那条通道后面是 Claude Code —— 反馈正文本身
        # 就长得像一件待办，它会去**做事**而不是作答，回给我们的 text 就是空的。
        # 同一条提示词把「只输出 JSON」换成「先用一句话回答」，它回的是
        # 「我先去看执行器实际怎么做的,再判。」—— 更能说明它在干活不是在答题。
        return _err(
            "模型没有返回内容",
            why="主路限流时会降级到 CLI 通道，那条通道拿到这种「像一件待办」的正文"
                "会去做事而不是作答，回来是空的。",
            howTo="过一会儿重试一次（主路不限流就不会降级）；一直空就去"
                  "「AI 服务配置 → AI 能力→模型」给「CC 反馈分诊」换一个模型。",
        )

    # 模型经常裹一层 ```json —— 剥掉再解析。解析失败不算失败：
    # 原文照样存进去给人看，总比「AI 分析失败」这四个字有用。
    m = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except Exception:
        data = {}
    if not data:
        data = {"parseFailed": True, "raw": text[:2000]}

    data["model"] = getattr(cfg, "model", None)
    data["at"] = datetime.now(timezone.utc).isoformat()
    row.ai_analysis = data
    await session.commit()
    return {
        "id": str(row.id),
        "aiAnalysis": data,
        "note": "这是**建议**，不改状态。看过之后自己按「采纳」或直接改 —— "
                "尤其 wont_fix：它会永久短路同指纹的后续上报，得人点头。",
    }
