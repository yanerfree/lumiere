#!/usr/bin/env python
"""复量「额度」那几个常量该定多少 —— 也就是 `qa_catalog_review.py` 顶上那一块。

    cd backend && .venv/bin/python scripts/qa_review_remeasure.py
    cd backend && .venv/bin/python scripts/qa_review_remeasure.py --top 24

只读本地那份 bare clone（`show` / `ls-tree`，QA 仓永远只读），**不发网络请求、
不调模型**，秒级出结果。输出一行行对应常量块括号里的那些数，抄过去就行。

**为什么必须有这么个东西。** 那几个数是"够不够装"的判断，而它们量的是**别人的仓库**：
uag-qa 两天之内 MCP 域从 409KB 长到 576KB（+41%），三个常量同时被跨过去 ——
63 份脚本先被 MAX_SCRIPTS=60 砍成 60、再被总量预算砍到 50，**13 份没进模型**。
注释里写「2026-08-28 复量，MCP 49 份」当时是真的，两天后就是假的了，
而**一个查不出来的事实等于一个可以随便断言的事实**（CLAUDE.md 那条）。
所以复量方式必须是**一条能跑的命令**，不是注释里的一句"我量过"。

⚠ 报出来的数**不会自动变成常量**，也不该。提额度是有代价的（批数↑ ⇒ 模型调用↑ ⇒
墙钟↑，见 MAX_OUTPUT_TOKENS 那段的 120s 超时），要人看着数决定。
真跨线了本来也有一道看门人：报告里那句「这个域挂了 N 份、这次只读进 M 份」。
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select                                    # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings                                  # noqa: E402
from app.models.project import Project                           # noqa: E402
from app.services import qa_catalog, qa_catalog_review as qr      # noqa: E402


async def _pick_project(project_id: str | None) -> tuple[str, dict]:
    engine = create_async_engine(settings.database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as s:
            rows = (await s.execute(select(Project))).scalars().all()
            cands = [p for p in rows if (p.qa_repo or {}).get("url")]
            if project_id:
                cands = [p for p in cands if str(p.id) == project_id]
            if not cands:
                raise SystemExit("没有配了 QA 仓的项目（qa_repo.url 为空）")
            # 配了多个就要求点名 —— 默认挑一个等于把"量的是哪个仓"藏起来
            if len(cands) > 1 and not project_id:
                names = "\n".join(f"  {p.id}  {p.name}  {(p.qa_repo or {})['url']}"
                                  for p in cands)
                raise SystemExit(f"配了 QA 仓的项目不止一个，用 --project 点名：\n{names}")
            return str(cands[0].id), cands[0].qa_repo or {}
    finally:
        await engine.dispose()


def measure(project_id: str, cfg: dict) -> dict:
    catalog = qa_catalog.cached_read(project_id, cfg, False)
    repo_dir = qa_catalog._repo_dir(project_id)
    ref, _ = qa_catalog._resolve_ref(repo_dir, cfg.get("branch") or "")

    sizes: list[int] = []
    seen: dict[str, int] = {}       # path -> bytes，跨域去重，别把共享脚本数两遍
    rows = []
    for d in catalog.get("domains") or []:
        code = d["code"]
        scen = [x for x in catalog.get("scenarios") or [] if x.get("domain") == code]
        paths: list[str] = []
        for x in scen:
            for c in x.get("scripts") or []:
                if c["path"] not in paths:
                    paths.append(c["path"])
        total = 0
        for p in paths:
            if p not in seen:
                seen[p] = len((qa_catalog._show(repo_dir, ref, p) or "").encode())
                sizes.append(seen[p])
            total += seen[p]
        rows.append({"code": code, "scenarios": len(scen),
                     "scripts": len(paths), "bytes": total})
    rows.sort(key=lambda r: -r["scripts"])
    return {"ref": ref, "rows": rows, "sizes": sizes, "unique": len(seen)}


def report(m: dict, top: int) -> None:
    rows, sizes = m["rows"], m["sizes"]
    print(f"ref={m['ref']}\n")
    print(f"{'域':<8}{'场景':>5}{'脚本(未截断)':>14}{'正文合计':>11}")
    for r in rows[:top]:
        print(f"{r['code']:<8}{r['scenarios']:>5}{r['scripts']:>14}"
              f"{r['bytes'] / 1024:>10.0f}K")
    if len(rows) > top:
        print(f"…还有 {len(rows) - top} 个域（--top 看全部）")
    print(f"\n共 {len(rows)} 个域，去重脚本 {m['unique']} 份")

    if not sizes:
        raise SystemExit("!! 一份脚本都没读到 —— 是仓库没拉下来还是清单解析空了？"
                         "这不是「都在限内」")
    mx = max(sizes)
    print(f"单份正文：中位 {statistics.median(sizes) / 1024:.1f}K  "
          f"p90 {statistics.quantiles(sizes, n=10)[8] / 1024:.1f}K  "
          f"最大 {mx / 1024:.1f}K")

    # ── 对着常量逐条判，跨线的要说清丢了多少，别只说"超了" ──
    print("\n对账（当前常量 vs 实测）：")
    checks = [
        ("MAX_SCENARIOS", qr.MAX_SCENARIOS, max(r["scenarios"] for r in rows), "条场景"),
        ("MAX_SCRIPTS", qr.MAX_SCRIPTS, max(r["scripts"] for r in rows), "份脚本"),
        ("MAX_SCRIPT_BYTES", qr.MAX_SCRIPT_BYTES, mx, "字节/份"),
        ("TOTAL_SCRIPT_BYTES", qr.TOTAL_SCRIPT_BYTES,
         max(r["bytes"] for r in rows), "字节/域"),
    ]
    over = 0
    for name, cur, obs, unit in checks:
        room = (cur - obs) / cur * 100
        flag = "跨线" if obs > cur else ("余量薄" if room < 15 else "OK")
        over += obs > cur
        print(f"  {flag:<4} {name:<20} 现值 {cur:>8}  实测 {obs:>8} {unit}"
              f"  余量 {room:>5.0f}%")
    if over:
        print(f"\n⚠ {over} 个常量已被跨过 —— 超出的脚本**不会进模型**。"
              f"\n  报告里那句「挂了 N 份、只读进 M 份」会响，但没人盯着它就等于没响。")
    else:
        print("\n都在限内。余量薄的那几条留意，这个仓两天涨过 41%。")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", help="项目 UUID（配了 QA 仓的项目不止一个时必填）")
    ap.add_argument("--top", type=int, default=8, help="列前几个域（默认 8）")
    a = ap.parse_args()
    pid, cfg = asyncio.run(_pick_project(a.project))
    print(f"project={pid}  repo={cfg.get('url')}")
    report(measure(pid, cfg), a.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
