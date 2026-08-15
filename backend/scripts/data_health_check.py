#!/usr/bin/env python
"""扫全库的数据不变量，报可疑的静默损坏。

    cd backend && .venv/bin/python scripts/data_health_check.py            # 只看
    cd backend && .venv/bin/python scripts/data_health_check.py --strict   # 有 high 就非零退出（CI 用）

为什么需要它：这一轮最严重的 bug（驼峰中间件改坏请求体）**没有任何一次请求失败、
没有一行日志、页面上也看不出来** —— 库里存的和显示的都是被改过的样子，只会让人
以为用例本来就写错了。这类形状单元测试和执行都发现不了，只有把库扫一遍才能对出来。

判据放 app/services/data_health.py（纯函数、有单测），这里只负责取数据和排版。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select                                    # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings                                 # noqa: E402
from app.models.api_test import ApiTestScenario, ApiTestStep    # noqa: E402
from app.models.case import Case                                # noqa: E402
from app.models.project import Project                          # noqa: E402
from app.services.data_health import check_step, dominant_style  # noqa: E402


async def scan() -> list[dict]:
    engine = create_async_engine(settings.database_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    findings: list[dict] = []
    async with Session() as s:
        projects = {p.id: p.name for p in (await s.execute(select(Project))).scalars()}
        scenarios = (await s.execute(select(ApiTestScenario))).scalars().all()
        steps_by_scenario: dict = defaultdict(list)
        for st in (await s.execute(select(ApiTestStep))).scalars():
            steps_by_scenario[st.scenario_id].append(st)
        case_codes = {c.id: c.case_code for c in (await s.execute(select(Case))).scalars()}

        # 主流风格按**项目**统计 —— 同一个被测系统的命名风格是一致的，
        # 而不同项目可能一个蛇形一个驼峰（实测就是这样，不能全库一刀切）。
        bodies_by_project: dict = defaultdict(list)
        for sc in scenarios:
            for st in steps_by_scenario.get(sc.id, []):
                if isinstance(st.body, dict):
                    bodies_by_project[sc.project_id].append(st.body)
        style = {pid: dominant_style(bs) for pid, bs in bodies_by_project.items()}

        for sc in scenarios:
            for st in steps_by_scenario.get(sc.id, []):
                for issue in check_step(st.body, st.assertions, style.get(sc.project_id)):
                    findings.append({
                        **issue,
                        "project": projects.get(sc.project_id, "?"),
                        "scenario": sc.code,
                        "boundCase": case_codes.get(sc.source_case_id) if sc.source_case_id else None,
                        "step": f"#{st.sort_order + 1} {st.name}",
                    })
    await engine.dispose()
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="有 high 级别问题就非零退出")
    args = ap.parse_args()

    findings = asyncio.run(scan())
    if not findings:
        print("数据体检通过：没有发现可疑的静默损坏。")
        return 0

    by_kind: dict = defaultdict(list)
    for f in findings:
        by_kind[f["kind"]].append(f)

    for kind, items in by_kind.items():
        print(f"\n【{kind}】{len(items)} 处（{items[0]['severity']}）")
        print(f"  {items[0]['why']}")
        for f in items[:20]:
            owner = f"用例 {f['boundCase']}" if f["boundCase"] else "接口测试模块（无主）"
            extra = f"  键={f['keys']}" if f.get("keys") else (f"  字段={f.get('field')}" if f.get("field") else "")
            print(f"    {f['project']} / {f['scenario']} / {owner} / {f['step']}{extra}")
        if len(items) > 20:
            print(f"    …… 另有 {len(items) - 20} 处")

    high = [f for f in findings if f["severity"] == "high"]
    print(f"\n合计 {len(findings)} 处，其中 high {len(high)} 处。")
    return 1 if (args.strict and high) else 0


if __name__ == "__main__":
    raise SystemExit(main())
