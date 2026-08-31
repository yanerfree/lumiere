"""一次性：把 target_level 改过、派生状态没跟着重算的用例拨回来。

成因见 tests/test_dim_display_and_lifecycle.py 里「改覆盖计划」那一段：
target_level 以前在三条写入路径上都绕开了派生逻辑，于是库里留下
「UI·草稿 + 状态·完成」这种同一行自相矛盾的数据。代码已经堵上，
但已经写坏的行不会自己好 —— 得跑一遍这个。

**不手写 UPDATE**：直接调线上那个 sync_after_plan_change，
修出来的结果就和以后代码自己算的一模一样，不会有第二套规则。

    python scripts/backfill_lifecycle_after_plan_change.py           # 试运行
    python scripts/backfill_lifecycle_after_plan_change.py --apply   # 写入
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models.case import Case
from app.services.script_run_service import plan_satisfied, sync_after_plan_change

APPLY = "--apply" in sys.argv


async def main() -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        rows = (await session.execute(
            select(Case).where(Case.deleted_at.is_(None),
                               Case.lifecycle_status == "done")
        )).scalars().all()
        bad = [c for c in rows if not plan_satisfied(c)]
        for c in bad:
            before = (c.lifecycle_status, c.review_status)
            sync_after_plan_change(c)
            print(f"{c.case_code:16} {c.target_level:9} "
                  f"m={c.manual_status[:4]} a={c.api_status[:4]} u={c.ui_status[:4]}  "
                  f"{before[0]}/{before[1]} -> {c.lifecycle_status}/{c.review_status}")
        print(f"\n共 {len(bad)} 条" + ("，已写入" if APPLY else "（试运行，未写入）"))
        if APPLY:
            await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
