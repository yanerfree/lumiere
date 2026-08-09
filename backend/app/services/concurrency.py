"""并发写入的重试小工具（B0）。

背景：平台里有两处经典的读-改-写竞态，都是 `SELECT MAX(...)` → +1 → `INSERT`：
- `_next_case_code`：两个 CC 同时在同一 branch+module 建用例
- `create_script`：同一条用例被两人同时回推

好消息是唯一约束（`uq_case_branch_code` / `uq_script_case_type_version`）在，
**数据不会写坏**。坏消息是第二个写入方拿到的是一个原始的 IntegrityError 500 ——
CC 那边看到的只是一个语焉不详的失败，它很可能去改脚本，而正确动作只是重试一次。

这两处都是**幂等重试安全**的：重跑一遍 MAX 就会拿到新号。
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable, TypeVar

from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def retry_on_conflict(
    op: Callable[[], Awaitable[T]],
    session,
    *,
    attempts: int = 3,
    what: str = "写入",
) -> T:
    """撞唯一约束就回滚重试。

    每次重试前必须 rollback —— 冲突之后这个 session 处于失效事务里，
    不回滚接着做任何查询都会报 `PendingRollbackError`，看起来像另一个 bug。
    """
    last: IntegrityError | None = None
    for i in range(attempts):
        try:
            return await op()
        except IntegrityError as e:  # noqa: PERF203
            last = e
            await session.rollback()
            if i == attempts - 1:
                break
            # 抖动一下，避免两个写入方一直卡在同一个节拍上
            await asyncio.sleep(0.02 * (i + 1) + random.uniform(0, 0.03))
            logger.info("%s 撞并发冲突，第 %s 次重试", what, i + 2)
    raise last  # type: ignore[misc]
