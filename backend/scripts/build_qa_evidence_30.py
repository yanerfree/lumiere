"""建 S3 那个欠着的「手工验过的 30 条」回归集。

PRD 的 S3 验收方法写的是「拿手工验过的 30 条当回归集」，此前**没建** ——
实际用的是哨兵 + 变异 + 每趟的活体数。活体数每跑一趟就变，
所以「假警报率 0%」说的一直是"这几趟碰巧一条没误报"，不是一个能复现的数。

这个脚本从三个域最新那趟的 `scriptGaps` 里取样，落成一份固定 JSON：
判据（evidence）+ 够它判的那段脚本正文摘录 + **人工复核过的**期望状态。
`qa_evidence_check` 是纯函数零 IO，所以这份集子一旦钉住就能永远重放。
"""
import asyncio
import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models.qa_catalog_review import QaCatalogReview
from app.services import qa_catalog, qa_evidence_check as ec

PID = "1a1fb724-e252-4fd2-a7f1-3bc6bfdc5cbe"
PAD = 3
OUT = pathlib.Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "qa_evidence_30.json"

# ⚠ **重建会换掉一份已经人工核过的回归集。**
# 建集时的自动校验只保证「摘录没改变状态」，它保证不了「这条状态是对的」——
# 后者是我逐条对着 QA 仓原文看出来的。所以重建之后**必须重新人工核一遍**，
# 尤其是 unmatched / wrong-path 那几条：判据说它们是模型编的，
# 而"判据误报"和"模型真编了"在结果里长得一模一样，只有看原文能分开。
# 重建后还要同步改 `tests/test_qa_evidence_30.py` 里的 EXPECT_DIST 和那几个数。


def excerpt(text: str, evidence: str) -> str | None:
    """取一个覆盖所有 needle 行的窗口。取不到就整份留着。

    **宁可大，不可切歪**：切歪会把状态改掉，而改掉之后这份集子钉的就不是真事了。
    """
    lines = text.splitlines()
    norm = [ec._norm(x) for x in lines]
    idx = []
    for n in ec._needles(evidence):
        hit = [i for i, x in enumerate(norm) if n in x]
        if not hit:
            return None  # 有 needle 落不到单独一行上（跨行拼的），别切
        idx.extend(hit)
    if not idx:
        return None
    lo, hi = max(0, min(idx) - PAD), min(len(lines), max(idx) + PAD + 1)
    return "\n".join(lines[lo:hi]) + "\n"


async def main() -> None:
    engine = create_async_engine(settings.database_url)
    async with async_sessionmaker(engine)() as s:
        runs = {}
        for dom in ("MCP", "TEM", "AUT"):
            runs[dom] = (await s.execute(
                select(QaCatalogReview)
                .where(QaCatalogReview.domain == dom, QaCatalogReview.status == "done")
                .order_by(desc(QaCatalogReview.created_at)).limit(1))).scalars().first()
    await engine.dispose()

    d = qa_catalog._repo_dir(PID)
    ref, _ = qa_catalog._resolve_ref(d, "")
    cache: dict[str, str] = {}

    def body(p: str) -> str:
        if p not in cache:
            cache[p] = qa_catalog._show(d, ref, p) or ""
        return cache[p]

    pool = [(dom, g) for dom, r in runs.items()
            for g in ((r.result or {}).get("scriptGaps") or [])]

    # 取样顺序：不通过的排最前，然后 stitched / reflowed，最后拿 verbatim 补满 30。
    # **不通过的和非 verbatim 的一条都不许漏** —— 那些才是判据真正被考的地方。
    # 全挑 verbatim 的回归集等于挑软柿子：它会给出一个漂亮的数，比没有还坏。
    order = {"unmatched": 0, "wrong-path": 1, "stitched": 2, "reflowed": 3, "verbatim": 4}
    pool.sort(key=lambda x: (order.get(x[1].get("evidenceCheck"), 9),
                             x[0], x[1].get("path") or ""))

    picked, seen = [], set()
    for dom, g in pool:
        key = (g.get("path"), g.get("evidence"))
        if key in seen:
            continue
        seen.add(key)
        picked.append((dom, g))
        if len(picked) >= 30:
            break

    out, dropped = [], []
    for dom, g in picked:
        st = g.get("evidenceCheck")
        ev = g.get("evidence") or ""
        claimed = g.get("path")
        paths = [claimed] + ([g["evidenceFoundIn"]] if g.get("evidenceFoundIn") else [])
        scripts = []
        for p in paths:
            t = body(p)
            ex = excerpt(t, ev) if st != "unmatched" else None
            scripts.append({"path": p, "content": ex or t})

        # 建集时就验：摘录之后判据给的状态必须跟真跑那趟**一模一样**。
        # 对不上就剔掉，不留在集子里 —— 一条状态被摘录改掉的样本，
        # 钉住的是摘录的性质，不是判据的性质。
        probe = [{"path": claimed, "evidence": ev}]
        ec.check_evidence(probe, scripts)
        if probe[0]["evidenceCheck"] != st:
            dropped.append((claimed, st, probe[0]["evidenceCheck"]))
            continue

        naive = "hit" if any(ev and ev in x["content"] for x in scripts) else "miss"
        out.append({
            "domain": dom, "path": claimed, "evidence": ev,
            "expected": st, "naiveExact": naive,
            "foundIn": g.get("evidenceFoundIn"), "scripts": scripts,
        })

    print(f"取样 {len(picked)} 条，入集 {len(out)} 条，"
          f"摘录改了状态被剔掉 {len(dropped)} 条：{dropped}")
    print("分布：", dict(collections.Counter(o["expected"] for o in out)))
    print("朴素 exact 匹配：", dict(collections.Counter(o["naiveExact"] for o in out)))
    blob = json.dumps(out, ensure_ascii=False, indent=1)
    print(f"约 {len(blob.encode()) / 1024:.1f} KB")
    OUT.write_text(blob, encoding="utf-8")


asyncio.run(main())
