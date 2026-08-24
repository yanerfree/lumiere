"""MCP 工具 — CC 归因回填（B3）。"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import analysis_service


def _loads(v: Any) -> Any:
    """MCP 客户端可能把对象序列化成字符串传过来，两种都认。"""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:  # noqa: BLE001
            return v
    return v


async def submit_analysis(
    session: AsyncSession,
    run_id: str,
    cause: str,
    confidence: str,
    reasoning: str,
    evidence: Any = None,
    proposed_fix_target: str = "none",
) -> dict:
    """把你对某次失败的归因写回平台。

    先调 tb_get_ui_script_result 拿证据包和 run_id，看完截图和流量再来。

    **去向按证据齐不齐分流，不是一律等人**（见 analysis_service.route）：
    · test_defect / case_expired / env_issue / data_issue / flaky → `self_serve`，
      你自己改；闸门是"改完复跑跑绿，跟进单才关"
    · product_defect → evidence 里 liveVerified + codeRefs + issue 三样齐全才放行，
      缺一样落回 `needs_human` 并告诉你缺什么
    · requirement_unclear / unknown → 只有人能定，直接进待确认

    两条都成立的边界：**用例状态、通过率、报告结论一个字节都不动**
    （`confirmed_cause` 只由人写，红线 3）；self_serve 动的是失败跟进单的状态。
    """
    from app.mcp.middleware import current_caller_user_id

    author = None
    try:
        uid = await current_caller_user_id()
        if uid:
            from sqlalchemy import select

            from app.models.user import User
            author = (await session.execute(
                select(User.username).where(User.id == uid)
            )).scalar_one_or_none()
    except Exception:  # noqa: BLE001
        pass

    return await analysis_service.submit(
        session, run_id,
        {
            "cause": cause,
            "confidence": confidence,
            "reasoning": reasoning,
            "evidence": _loads(evidence),
            "proposedFixTarget": proposed_fix_target,
        },
        author=author or "cc",
    )


async def list_pending_confirm(
    session: AsyncSession,
    project_id: str | None = None,
    limit: int = 20,
) -> dict:
    """列出「已归因、等人确认」的失败 —— 你交上去还没被拍板的那些。"""
    import uuid

    from sqlalchemy import select

    from app.models.case import Case
    from app.models.project import Branch
    from app.models.script import ScriptRun

    stmt = (
        select(ScriptRun, Case.case_code, Case.title)
        .join(Case, Case.id == ScriptRun.case_id)
        .where(ScriptRun.cc_analysis.isnot(None), ScriptRun.confirmed_cause.is_(None))
    )
    if project_id:
        stmt = stmt.join(Branch, Branch.id == Case.branch_id).where(
            Branch.project_id == uuid.UUID(project_id)
        )
    rows = (await session.execute(stmt.order_by(ScriptRun.created_at.desc()).limit(limit))).all()
    return {
        "total": len(rows),
        "pending": [{
            "runId": str(r.ScriptRun.id),
            "caseCode": r.case_code,
            "caseTitle": r.title,
            "phenomenon": r.ScriptRun.failure_phenomenon,
            "ccCause": (r.ScriptRun.cc_analysis or {}).get("cause"),
            "ccConfidence": (r.ScriptRun.cc_analysis or {}).get("confidence"),
            "submittedAt": (r.ScriptRun.cc_analysis or {}).get("submittedAt"),
        } for r in rows],
        "usage": "这些还没人确认，所以还没改动任何状态。确认在平台页面上做。",
    }
