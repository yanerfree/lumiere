"""CC 反馈的终端入口 —— 用户在这台终端说「处理一下 CC 反馈的问题」时走这条路。

    cd backend
    .venv/bin/python scripts/cc_feedback.py list                    # 待办队列
    .venv/bin/python scripts/cc_feedback.py list --status done      # 按状态
    .venv/bin/python scripts/cc_feedback.py show <id 前几位>        # 看全文
    .venv/bin/python scripts/cc_feedback.py reply <id> --status done \
        --category bug --severity high --resolution '……'            # 处置 + 回音
    .venv/bin/python scripts/cc_feedback.py import                  # 存量导入（试运行）
    .venv/bin/python scripts/cc_feedback.py import --apply

## 为什么要有这个 CLI，页面不是已经能处理了吗

因为处理的人**不在页面上** —— 修这些问题的是本机的 Claude Code，
它手里有仓库、有 pytest、有重启脚本，唯独没有浏览器。让它为了写一句回音去开页面，
回音就会被跳过；**而这条通道的全部价值就在回音上**（没有回音，CC 下一轮照原样再撞一次）。

## 所有写操作一律走 cc_feedback_service，**不手写 UPDATE**

页面、MCP、这个 CLI 三条入口共用同一份校验：done/wont_fix 必须写回音、
认下必须定类、wont_fix 永久短路同指纹。三条路各写一套，迟早漂成三种规矩，
而漂了之后最先失效的一定是最麻烦的那条 —— 也就是回音。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid as _uuid
from pathlib import Path

# **必须在 import app 之前**：venv 里那个 editable 安装把 `app` 硬指向
# /home/dreamer/lumiere/backend/app。以 `python scripts/xxx.py` 起时 sys.path[0]
# 是 scripts/ 而不是 backend/，于是 `app` 会解析到**另一份 checkout** ——
# 在 worktree 里改的代码根本没跑到，而且不报错，只是行为对不上。
# 把 backend/ 插到最前面，让 PathFinder 先命中本目录（它排在那个 meta finder 前面）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models.cc_feedback import CATEGORY_LABEL, STATUS_LABEL, CCFeedback
from app.services import cc_feedback_service as svc

C = {"bug": "\033[31m", "improvement": "\033[33m", "requirement": "\033[36m"}
DIM, BOLD, RST = "\033[2m", "\033[1m", "\033[0m"


def _sessionmaker():
    return async_sessionmaker(create_async_engine(settings.database_url, echo=False),
                              expire_on_commit=False)


async def _resolve(session, prefix: str) -> CCFeedback | None:
    """按 id 前缀找一条 —— 全长 uuid 在终端里没人愿意抄。"""
    p = prefix.strip().lower()
    try:
        return await session.get(CCFeedback, _uuid.UUID(p))
    except (ValueError, AttributeError):
        pass
    rows = (await session.execute(select(CCFeedback))).scalars().all()
    hit = [r for r in rows if str(r.id).replace("-", "").startswith(p.replace("-", ""))]
    if len(hit) == 1:
        return hit[0]
    if len(hit) > 1:
        print(f"前缀 {prefix!r} 命中 {len(hit)} 条，写长一点：")
        for r in hit:
            print(f"  {str(r.id)[:8]}  {r.title}")
    return None


# ── list ──────────────────────────────────────────────────────────

async def cmd_list(args) -> None:
    async with _sessionmaker()() as session:
        items, total, summary = await svc.list_feedback(
            session,
            status=args.status,
            pending_only=not args.status and not args.all,
            category=args.category,
            page=1, page_size=args.limit,
        )
        by = summary["byStatus"]
        print(f"{BOLD}共 {summary['total']} 条{RST}，待处理 {summary['pending']} 条  "
              + DIM + "  ".join(f"{STATUS_LABEL.get(k, k)}={v}" for k, v in sorted(by.items()))
              + RST)
        if not items:
            print("\n（没有匹配的反馈）")
            return
        print()
        for it in items:
            cat = it["category"] or it["reportedCategory"] or ""
            tag = CATEGORY_LABEL.get(cat, "?")
            if not it["category"]:
                tag += "?"          # 平台还没判过类，显示的是 CC 自报的
            mism = f" {C['improvement']}(他报的是{it['reportedCategoryLabel']}){RST}" \
                if it["categoryMismatch"] else ""
            occ = f" ×{it['occurrences']}" if it["occurrences"] > 1 else ""
            print(f"{str(it['id'])[:8]}  {C.get(cat, '')}{tag:5}{RST} "
                  f"[{it['statusLabel']}]{occ}{mism}  {it['title']}")
            print(f"{DIM}          {it['toolName'] or '—'} · {it['reporter'] or '—'}"
                  f" · {(it['lastSeenAt'] or '')[:16].replace('T', ' ')}{RST}")
        if total > len(items):
            print(f"\n{DIM}还有 {total - len(items)} 条，--limit 调大{RST}")


# ── show ──────────────────────────────────────────────────────────

async def cmd_show(args) -> None:
    async with _sessionmaker()() as session:
        row = await _resolve(session, args.id)
        if row is None:
            print(f"没找到 {args.id!r}")
            sys.exit(1)
        d = await svc.get_detail(session, str(row.id))

        print(f"{BOLD}{d['title']}{RST}")
        print(f"{DIM}{d['id']}{RST}")
        cat = d["category"] or d["reportedCategory"]
        line = f"[{d['statusLabel']}] {CATEGORY_LABEL.get(cat or '', '未定类')}"
        if not d["category"]:
            line += "（CC 自报，平台未判）"
        if d["categoryMismatch"]:
            line += f" ← CC 报的是 {d['reportedCategoryLabel']}"
        if d["severity"]:
            line += f" · 严重度 {d['severity']}"
        if d["occurrences"] > 1:
            line += f" · 撞了 {d['occurrences']} 次"
        print(line)
        print(f"来源：{d['source']} · {d['reporter'] or '—'} · 工具 {d['toolName'] or '—'}"
              + (f" · 项目 {d.get('projectName')}" if d.get("projectName") else ""))
        if d["reopenedFrom"]:
            print(f"{C['bug']}复发：这个问题之前修过一次又回来了（原记录 "
                  f"{d['reopenedFrom'][:8]}）{RST}")
        print(f"\n{BOLD}正文{RST}\n{d['body']}")

        ev = d.get("evidence") or {}
        if ev:
            print(f"\n{BOLD}证据{RST}")
            for k, label in (("expected", "说好的"), ("actual", "实际"), ("repro", "复现")):
                if ev.get(k):
                    print(f"  {label}：{ev[k]}")
            for r in ev.get("refs") or []:
                print(f"  出处：{r}")

        if d.get("resolution"):
            读 = "已被取走" if d.get("ackAt") else "还没取走"
            print(f"\n{BOLD}回音{RST}（{读}）\n{d['resolution']}")
        ai = d.get("aiAnalysis") or {}
        if ai and not ai.get("parseFailed"):
            print(f"\n{BOLD}AI 分诊建议{RST}{DIM}（只是建议，状态还得自己改）{RST}")
            print(f"  判类 {CATEGORY_LABEL.get(ai.get('category', ''), ai.get('category'))}"
                  f" · 严重度 {ai.get('severity')} · 建议 {ai.get('suggestedStatus')}")
            for k, label in (("reasoning", "判据"), ("risk", "不处理会怎样"),
                             ("suggestedResolution", "建议回音")):
                if ai.get(k):
                    print(f"  {label}：{ai[k]}")


# ── reply ─────────────────────────────────────────────────────────

async def cmd_reply(args) -> None:
    async with _sessionmaker()() as session:
        row = await _resolve(session, args.id)
        if row is None:
            print(f"没找到 {args.id!r}")
            sys.exit(1)
        dup = None
        if args.duplicate_of:
            t = await _resolve(session, args.duplicate_of)
            if t is None:
                print(f"--duplicate-of {args.duplicate_of!r} 没找到")
                sys.exit(1)
            dup = str(t.id)
        res = await svc.triage(
            session, str(row.id),
            status=args.status, category=args.category, severity=args.severity,
            resolution=args.resolution, duplicate_of=dup,
            actor=args.actor,
        )
        if res.get("error"):
            # 拒绝理由本身是设计的一部分（每条都带出路），原样打出来
            print(f"{C['bug']}拒绝：{res['error']}{RST}")
            for k in ("why", "howTo"):
                if res.get(k):
                    print(f"  {k}: {res[k]}")
            sys.exit(1)
        print(f"{str(res['id'])[:8]}  → [{res['statusLabel']}] "
              f"{CATEGORY_LABEL.get(res['category'] or '', '')}  {res['title']}")
        if res.get("resolution"):
            print(f"{DIM}回音已写入，CC 下一轮 lum_next_duty / lum_list_my_feedback "
                  f"就能看到{RST}")


# ── analyze ───────────────────────────────────────────────────────

async def cmd_analyze(args) -> None:
    async with _sessionmaker()() as session:
        row = await _resolve(session, args.id)
        if row is None:
            print(f"没找到 {args.id!r}")
            sys.exit(1)
        res = await svc.ai_triage(session, str(row.id))
        if res.get("error"):
            print(f"{C['bug']}{res['error']}{RST}")
            if res.get("howTo"):
                print(f"  {res['howTo']}")
            sys.exit(1)
        ai = res["aiAnalysis"]
        if ai.get("parseFailed"):
            print(f"{DIM}模型没吐出可解析的 JSON，原文：{RST}\n{ai.get('raw')}")
            return
        print(f"判类 {CATEGORY_LABEL.get(ai.get('category', ''), ai.get('category'))}"
              f" · 严重度 {ai.get('severity')} · 建议 {ai.get('suggestedStatus')}")
        for k, label in (("reasoning", "判据"), ("risk", "不处理会怎样"),
                         ("suggestedResolution", "建议回音")):
            if ai.get(k):
                print(f"{label}：{ai[k]}")
        print(f"\n{DIM}{res['note']}{RST}")


# ── import ────────────────────────────────────────────────────────

async def cmd_import(args) -> None:
    """把 2026-09-01 那份汇总的 31 条搬进来。

    **幂等靠「指纹已存在就跳过」，不靠 report() 的归并** —— 归并会把
    occurrences 每跑一次 +1，于是重跑两次的存量条目看起来像「CC 撞了三次」。
    次数是排序依据（撞得越多越靠前），拿导入次数污染它，等于把队列顺序拧歪。
    """
    from scripts.cc_feedback_seed_20260901 import ITEMS, REPORTER, SOURCE, evidence_of

    async with _sessionmaker()() as session:
        existing = set((await session.execute(select(CCFeedback.fingerprint))).scalars().all())
        new, skipped, failed = 0, 0, []
        for it in ITEMS:
            fp = svc.fingerprint_of(it["tool"], it["title"])
            if fp in existing:
                skipped += 1
                continue
            if not args.apply:
                bad = svc.validate(it["title"], it["body"], it["category"], evidence_of(it))
                if bad:
                    failed.append((it["ref"], bad["error"]))
                else:
                    new += 1
                    print(f"  + {it['ref']:5} {CATEGORY_LABEL[it['category']]:3} {it['title']}")
                continue
            res = await svc.report(
                session,
                title=it["title"], body=it["body"], category=it["category"],
                tool_name=it["tool"], evidence=evidence_of(it),
                reporter=REPORTER, source=SOURCE,
            )
            if res.get("error"):
                failed.append((it["ref"], res["error"]))
            else:
                new += 1
                existing.add(fp)
                print(f"  + {str(res['id'])[:8]} {it['ref']:5} "
                      f"{CATEGORY_LABEL[it['category']]:3} {it['title']}")

        print(f"\n新增 {new} 条，跳过（已存在）{skipped} 条"
              + ("" if args.apply else "  ← 试运行，未写入；加 --apply 落库"))
        for ref, err in failed:
            print(f"{C['bug']}  ✗ {ref}: {err}{RST}")
        if failed:
            sys.exit(1)


def main() -> None:
    p = argparse.ArgumentParser(description="CC 反馈：查、处置、导入存量")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列反馈（默认只看待处理的）")
    p_list.add_argument("--status", choices=list(STATUS_LABEL))
    p_list.add_argument("--category", choices=list(CATEGORY_LABEL))
    p_list.add_argument("--all", action="store_true", help="含已了结的")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.set_defaults(fn=cmd_list)

    p_show = sub.add_parser("show", help="看一条的全文（id 可以只写前几位）")
    p_show.add_argument("id")
    p_show.set_defaults(fn=cmd_show)

    p_rep = sub.add_parser("reply", help="处置 + 写回音")
    p_rep.add_argument("id")
    p_rep.add_argument("--status", required=True, choices=list(STATUS_LABEL))
    p_rep.add_argument("--category", choices=list(CATEGORY_LABEL))
    p_rep.add_argument("--severity", choices=("high", "medium", "low"))
    p_rep.add_argument("--resolution", help="回音正文。done / wont_fix 必填")
    p_rep.add_argument("--duplicate-of", dest="duplicate_of")
    p_rep.add_argument("--actor", default="本机 CC（终端处理）")
    p_rep.set_defaults(fn=cmd_reply)

    p_ai = sub.add_parser("analyze", help="跑一次 AI 分诊建议（不改状态）")
    p_ai.add_argument("id")
    p_ai.set_defaults(fn=cmd_analyze)

    p_imp = sub.add_parser("import", help="导入 2026-09-01 那 31 条存量")
    p_imp.add_argument("--apply", action="store_true")
    p_imp.set_defaults(fn=cmd_import)

    args = p.parse_args()
    asyncio.run(args.fn(args))


if __name__ == "__main__":
    main()
