#!/usr/bin/env python
"""量「同一个域评两趟，结论重合多少」—— PRD 的 S6 / epics §9 的 F 条。

    cd backend && .venv/bin/python scripts/qa_review_repeatability.py --domain TEM
    cd backend && .venv/bin/python scripts/qa_review_repeatability.py --domain TEM --show

**这不是单元测试，是一次性实测**，所以做成脚本而不是 pytest：
它要真发两趟模型调用才有意义，而那个既慢又花钱、还依赖 QA 仓和网关都活着。
判定逻辑在 `app/services/qa_catalog_review.repeatability`（纯函数、有单测），
这里只负责取两行结论和排版。

**跑之前先把同一个域评两遍**（页面上点两次，或 `POST .../qa-catalog/reviews`），
本脚本默认取该域**最近两趟 done 的**来比。

⚠ **别拿一个样本去定阈值。** epics「验收」那节写死了：`_NO_SAMPLING_PARAMS` 会摘掉
`temperature=0`，这套评审本来就不确定；先攒 tally 再谈阈值。Epic 0 实测 71%，
几乎没有余量 —— 所以这个数红的时候，第一反应不该是调那个 70%。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import desc, select                              # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings                                  # noqa: E402
from app.models.qa_catalog_review import QaCatalogReview         # noqa: E402
from app.services import qa_catalog_review as qr                 # noqa: E402


async def fetch(domain: str, project: str | None, limit: int) -> list[QaCatalogReview]:
    engine = create_async_engine(settings.database_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        stmt = select(QaCatalogReview).where(
            QaCatalogReview.domain == domain, QaCatalogReview.status == "done")
        if project:
            stmt = stmt.where(QaCatalogReview.project_id == project)
        rows = (await s.execute(
            stmt.order_by(desc(QaCatalogReview.created_at)).limit(limit))).scalars().all()
    await engine.dispose()
    return list(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True, help="域码，如 TEM")
    ap.add_argument("--project", default=None, help="项目 UUID（同一个域可能跨项目评过）")
    ap.add_argument("--show", action="store_true", help="把只有一趟抓到的那些逐条列出来")
    args = ap.parse_args()

    rows = asyncio.run(fetch(args.domain, args.project, 2))
    if len(rows) < 2:
        # 「只有一趟」不许当成"重合度 0"报出去 —— 那是没量过，不是量出来不稳。
        print(f"✗ {args.domain} 只有 {len(rows)} 趟 done 的结论，比不了。先把这个域再评一遍。")
        return 2

    b, a = rows[0], rows[1]          # rows 按时间倒序：a = 先跑的那趟，b = 后跑的
    for tag, r in (("A(先)", a), ("B(后)", b)):
        print(f"{tag} {str(r.id)[:8]} {r.created_at:%Y-%m-%d %H:%M} "
              f"sha={(r.commit_sha or '')[:10]} env={r.environment_name} "
              f"verdict={(r.result or {}).get('verdict')}")
    if (a.commit_sha or "") != (b.commit_sha or ""):
        # 两趟读的不是同一份脚本，那量出来的差异里混着"仓库变了"，跟"模型不稳"分不开。
        print("⚠ 两趟的 commitSha 不同 —— 差异里混着 QA 仓自己的改动，这个数不能当可重复性看。")

    m = qr.repeatability((a.result or {}).get("scriptGaps"),
                         (b.result or {}).get("scriptGaps"))
    if m["state"] == "unmeasurable":
        print("\n结论：unmeasurable —— 两趟都没有能配的 scriptGaps。**不是 0%，是没得比。**")
        return 3

    print(f"\nscriptGaps  A={m['totalA']}（唯一键 {m['keyedA']}，"
          f"没 id/path {m['unkeyedA']}，同键并掉 {m['dupA']}）"
          f"  B={m['totalB']}（唯一键 {m['keyedB']}，"
          f"没 id/path {m['unkeyedB']}，同键并掉 {m['dupB']}）")
    print(f"两趟都抓到 {m['both']} 条 → Jaccard {m['jaccard']:.0%}"
          f"   （A 侧命中 {m['hitRateA']:.0%}，B 侧命中 {m['hitRateB']:.0%}）")
    print(f"S6 阈值 70%：{'过' if m['jaccard'] >= 0.70 else '没过'}"
          f"   —— 别据这一个样本调阈值，见本文件开头。")
    if m["unkeyedA"] or m["unkeyedB"]:
        print(f"⚠ 有 {m['unkeyedA'] + m['unkeyedB']} 条没 id 或没 path，没参与比对 ——"
              f" 上面这个比值是拿剩下的算的。")
    if m["dupA"] or m["dupB"]:
        # 这个不是毛病：同一条场景常被写成两条发现（一条 depth 一条 expect）。
        # 单独报出来只为一件事 —— 别让人把它读成上面那行「没 id/path」。
        print(f"（另有 {m['dupA'] + m['dupB']} 条跟同趟里别的发现同 id 同 path，"
              f"按一条算。这是正常的，跟上一行不是一回事。）")
    if args.show:
        for tag, ks in (("只有 A 抓到", m["onlyA"]), ("只有 B 抓到", m["onlyB"])):
            print(f"\n{tag}（{len(ks)}）:")
            for k in ks:
                print("   ", k)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
