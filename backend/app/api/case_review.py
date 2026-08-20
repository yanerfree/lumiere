"""AI 评审端点 —— 单条、批量。目标是替掉人工那道「待审」。

批量刻意做成**逐条评**（并发有限），不是"把一个模块塞进一次 prompt 让它整体评"：
后者出来的是"缺少安全测试场景"这类放到哪个项目都成立的话（上一版就是这么做的，
用户看完的评价是"不适用"）。逐条评贵一些，但每条结论都能指到具体步骤。
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.deps.auth import require_project_role
from app.deps.db import get_db
from app.models.case import Case
from app.models.user import User
from app.services.ai_config_resolver import resolve_ai_config
from app.services.review import reviewer

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/projects/{project_id}/branches/{branch_id}",
    tags=["review"],
)

MAX_BATCH = 30          # 一次最多评这么多条 —— 再多就该分模块评，报告也没人看得完


async def _config(project_id: uuid.UUID, session: AsyncSession):
    cfg = await resolve_ai_config(project_id, session, capability="tb-quality-review")
    if not cfg:
        raise AppError(code="AI_NOT_CONFIGURED", message="AI 服务未配置", status_code=503)
    return cfg


@router.post("/cases/{case_id}/ai-review")
async def review_one(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_id: uuid.UUID,
    run_first: bool = Query(default=False, alias="runFirst"),
    env_id: str | None = Query(default=None, alias="envId"),
    persist: bool = Query(default=True),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """评审一条用例。runFirst=true 会先真跑一遍接口场景再评（debug 模式，不进通过率）。"""
    cfg = await _config(project_id, session)
    out = await reviewer.review_case(session, case_id, ai_config=cfg,
                                    persist=persist, run_first=run_first, env_id=env_id)
    if out.get("error"):
        raise AppError(code="REVIEW_FAILED", message=out["error"], status_code=502)
    return {"data": out}


@router.post("/ai-review/batch")
async def review_batch(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    case_ids: list[uuid.UUID] | None = Body(default=None, embed=True, alias="caseIds"),
    folder_id: uuid.UUID | None = Body(default=None, embed=True, alias="folderId"),
    persist: bool = Body(default=True, embed=True),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """按勾选或按模块批量评审。逐条评，返回每条结论 + 汇总。"""
    cfg = await _config(project_id, session)

    if not case_ids:
        stmt = select(Case.id).where(Case.branch_id == branch_id, Case.deleted_at.is_(None))
        if folder_id:
            stmt = stmt.where(Case.folder_id == folder_id)
        case_ids = [r[0] for r in (await session.execute(stmt.limit(MAX_BATCH + 1))).all()]

    if not case_ids:
        raise AppError(code="NO_CASES", message="没有可评审的用例", status_code=400)
    truncated = len(case_ids) > MAX_BATCH
    case_ids = case_ids[:MAX_BATCH]

    results = []
    # 并发 3：评审是长请求，网关有 429（见 docs/ai-gateway-and-models.md），
    # 一次并发十几条会把限流打满、整批失败。
    sem = asyncio.Semaphore(3)

    async def one(cid):
        async with sem:
            try:
                return await reviewer.review_case(session, cid, ai_config=cfg, persist=persist)
            except Exception as e:  # noqa: BLE001
                logger.exception("评审失败 case=%s", cid)
                return {"caseId": str(cid), "error": str(e)[:200]}

    for cid in case_ids:            # 串行提交、并发受 sem 控制；session 不是线程安全的
        results.append(await one(cid))

    ok = [r for r in results if not r.get("error")]
    return {"data": {
        "total": len(results),
        "approved": len([r for r in ok if r.get("verdict") == "approved"]),
        "rejected": len([r for r in ok if r.get("verdict") == "rejected"]),
        "failed": len(results) - len(ok),
        "avgScore": round(sum(r.get("total", 0) for r in ok) / max(len(ok), 1)),
        "blockerCases": [r["caseCode"] for r in ok if r.get("blockerCount")],
        "truncated": truncated,
        "results": results,
    }}
