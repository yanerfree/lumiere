"""探索测试 API — 会话管理 + AI 章程生成 + 发现记录"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.common import BaseSchema
from app.core.exceptions import NotFoundError
from app.deps.auth import require_project_role
from app.deps.db import get_db
from app.models.user import User
from app.models.exploratory import ExploratorySession, ExploratoryFinding

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/projects/{project_id}/exploratory",
    tags=["exploratory"],
)


class CreateSessionRequest(BaseSchema):
    title: str = Field(..., min_length=1, max_length=200)
    target_module: str | None = None
    time_limit_minutes: int = 30


class AddFindingRequest(BaseSchema):
    finding_type: str = Field(..., pattern="^(bug|risk|suggestion)$")
    severity: str = Field(default="medium", pattern="^(critical|high|medium|low)$")
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    checkpoint: str | None = None


def _session_to_dict(s: ExploratorySession) -> dict:
    return {
        "id": str(s.id),
        "title": s.title,
        "targetModule": s.target_module,
        "timeLimitMinutes": s.time_limit_minutes,
        "status": s.status,
        "charter": s.charter,
        "checkpoints": s.checkpoints,
        "completedCheckpoints": s.completed_checkpoints,
        "totalCheckpoints": s.total_checkpoints,
        "summary": s.summary,
        "createdAt": s.created_at.isoformat() if s.created_at else None,
        "completedAt": s.completed_at.isoformat() if s.completed_at else None,
    }


# ── 会话 CRUD ──

@router.get("/sessions")
async def list_sessions(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    result = await session.execute(
        select(ExploratorySession)
        .where(ExploratorySession.project_id == project_id)
        .order_by(ExploratorySession.created_at.desc())
    )
    sessions = result.scalars().all()
    return {"data": [_session_to_dict(s) for s in sessions]}


@router.post("/sessions")
async def create_session(
    project_id: uuid.UUID,
    body: CreateSessionRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    exp = ExploratorySession(
        project_id=project_id,
        title=body.title,
        target_module=body.target_module,
        time_limit_minutes=body.time_limit_minutes,
        created_by=current_user.id,
    )
    session.add(exp)
    await session.flush()
    await session.refresh(exp)
    return {"data": _session_to_dict(exp)}


@router.get("/sessions/{session_id}")
async def get_session(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    exp = await session.get(ExploratorySession, session_id)
    if not exp or exp.project_id != project_id:
        raise NotFoundError(code="NOT_FOUND", message="会话不存在")

    findings_result = await session.execute(
        select(ExploratoryFinding)
        .where(ExploratoryFinding.session_id == session_id)
        .order_by(ExploratoryFinding.created_at)
    )
    findings = findings_result.scalars().all()

    data = _session_to_dict(exp)
    data["findings"] = [
        {
            "id": str(f.id),
            "findingType": f.finding_type,
            "severity": f.severity,
            "title": f.title,
            "description": f.description,
            "checkpoint": f.checkpoint,
            "createdAt": f.created_at.isoformat() if f.created_at else None,
        }
        for f in findings
    ]
    return {"data": data}


# ── 发现记录 ──

@router.post("/sessions/{session_id}/findings")
async def add_finding(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    body: AddFindingRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    exp = await session.get(ExploratorySession, session_id)
    if not exp or exp.project_id != project_id:
        raise NotFoundError(code="NOT_FOUND", message="会话不存在")

    finding = ExploratoryFinding(
        session_id=session_id,
        finding_type=body.finding_type,
        severity=body.severity,
        title=body.title,
        description=body.description,
        checkpoint=body.checkpoint,
    )
    session.add(finding)
    await session.flush()
    await session.refresh(finding)
    return {"data": {"id": str(finding.id), "title": finding.title}}


# ── 完成检查点 ──

@router.post("/sessions/{session_id}/complete-checkpoint")
async def complete_checkpoint(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    exp = await session.get(ExploratorySession, session_id)
    if not exp:
        raise NotFoundError(code="NOT_FOUND", message="会话不存在")
    exp.completed_checkpoints = min(exp.completed_checkpoints + 1, exp.total_checkpoints)
    await session.commit()
    return {"data": {"completed": exp.completed_checkpoints, "total": exp.total_checkpoints}}


# ── 结束会话 ──

@router.post("/sessions/{session_id}/complete")
async def complete_session(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    exp = await session.get(ExploratorySession, session_id)
    if not exp:
        raise NotFoundError(code="NOT_FOUND", message="会话不存在")
    exp.status = "completed"
    exp.completed_at = datetime.now(timezone.utc)
    await session.commit()
    return {"data": _session_to_dict(exp)}


@router.post("/sessions/{session_id}/summary")
async def generate_summary(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """输出探索报告 —— 这一步页面上承诺了很久，但一直没有实现。

    `summary` 字段建表时就有，实测库里 4 个会话全是 NULL：`complete` 只改了状态和
    时间，没有任何产出。而页面顶部写着「生成章程 → 引导检查 → 记录发现 → 输出报告」。

    报告只用会话里**已有的事实**：章程（目标/风险区域/检查点）、勾了几个检查点、
    记了哪些发现。不去猜没查过的东西 —— 探索测试的价值恰恰在于说清"覆盖到哪、
    哪些风险还没碰"，编出来的结论比没有结论更糟。
    """
    from app.core.exceptions import AppError
    from app.services.ai import llm_client
    from app.services.ai_config_resolver import resolve_ai_config

    exp = await session.get(ExploratorySession, session_id)
    if not exp:
        raise NotFoundError(code="NOT_FOUND", message="会话不存在")

    ai_config = await resolve_ai_config(project_id, session, capability="exploratory-charter")
    if not ai_config:
        raise AppError(code="AI_NOT_CONFIGURED", message="AI 服务未配置", status_code=503)

    rows = (await session.execute(
        select(ExploratoryFinding).where(ExploratoryFinding.session_id == session_id)
        .order_by(ExploratoryFinding.created_at)
    )).scalars().all()

    charter = exp.charter or {}
    cps = exp.checkpoints or []
    # 口径跟前端一致：complete-checkpoint 只累加计数、不在 checkpoints JSON 里标 done，
    # 页面也是按"前 N 个打勾"渲染的。报告要和人看到的一致，否则两边对不上。
    n_done = max(0, min(exp.completed_checkpoints or 0, len(cps)))
    done, todo = cps[:n_done], cps[n_done:]
    findings_text = "\n".join(
        f"- [{f.finding_type}/{f.severity}] {f.title}：{(f.description or '')[:200]}" for f in rows
    ) or "（本次没有记录任何发现）"

    messages = [
        {"role": "system", "content": """你是探索测试专家，为一次刚结束的探索测试会话写总结报告。

只使用给定的事实，**不要编造没做过的检查或没发现的问题**。
覆盖率就按"勾了几个检查点"如实说；没查的检查点要点名，并说明它对应哪个风险区域还悬着。

输出 JSON（```json 包裹）：
{
  "conclusion": "一段话说清这次探索覆盖到什么程度、结论是什么",
  "coverage": {"done": 数字, "total": 数字, "uncovered": ["没查的检查点标题"]},
  "keyFindings": [{"title": "发现标题", "impact": "为什么值得管"}],
  "residualRisks": ["还悬着的风险，指明是哪个检查点/风险区域没碰"],
  "nextSteps": ["下一步该做什么，具体可执行"]
}"""},
        {"role": "user", "content": (
            f"会话：{exp.title}\n目标模块：{exp.target_module or '（未填）'}\n"
            f"章程目标：{charter.get('objective', '（无）')}\n"
            f"风险区域：{'; '.join(charter.get('riskAreas') or []) or '（无）'}\n\n"
            f"检查点共 {len(cps)} 个，已完成 {len(done)} 个。\n"
            f"已完成：{'; '.join(c.get('title', '') for c in done) or '（无）'}\n"
            f"未完成：{'; '.join(c.get('title', '') for c in todo) or '（无）'}\n\n"
            f"记录的发现：\n{findings_text}\n\n请生成总结报告。"
        )},
    ]

    full = ""
    async for chunk in llm_client.stream(messages, config=ai_config):
        if chunk.delta:
            full += chunk.delta

    import re
    m = re.search(r"```json\s*\n(.*?)(?:\n```|$)", full, re.DOTALL)
    text = m.group(1) if m else full
    # 两层都要兜住。截到最后一个 } 再 loads **同样会抛** —— 写这段时刚在上面的章程
    # 解析里修过一模一样的坑，这里还是漏了，实测直接 500（报告要不出来还看不出为什么）。
    # 兜底退化成纯文本结论：报告的主要价值就是那段结论，结构化字段缺了不致命。
    summary = None
    for candidate in (text, text[:text.rfind("}") + 1] if text.rfind("}") > 0 else None):
        if not candidate:
            continue
        try:
            summary = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    if not isinstance(summary, dict) or not summary:
        logger.warning("探索报告未解析成 JSON，退化为纯文本。原始前 300 字：%s", full[:300])
        summary = {"conclusion": (full or "AI 没有返回内容").strip()[:2000]}

    exp.summary = summary
    if exp.status != "completed":
        exp.status = "completed"
        exp.completed_at = datetime.now(timezone.utc)
    from app.services.ai.usage import log_ai_call
    await log_ai_call(session, project_id=project_id, capability="exploratory-charter",
                      model=ai_config.model, est_chars=len(full or ""))
    await session.commit()
    return {"data": _session_to_dict(exp)}


# ── AI 生成章程 ──

@router.post("/sessions/{session_id}/generate-charter")
async def generate_charter(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    from app.services.ai_config_resolver import resolve_ai_config
    from app.services.ai import llm_client
    from app.mcp.tools import test_cases, api_endpoints
    from app.core.exceptions import AppError

    exp = await session.get(ExploratorySession, session_id)
    if not exp:
        raise NotFoundError(code="NOT_FOUND", message="会话不存在")

    ai_config = await resolve_ai_config(project_id, session, capability="exploratory-charter")
    if not ai_config:
        raise AppError(code="AI_NOT_CONFIGURED", message="AI 服务未配置", status_code=503)

    api_tree = await api_endpoints.list_api_tree(session, str(project_id))
    endpoints = [n for n in api_tree if n.get("type") == "endpoint"]
    api_text = "\n".join(f"- {ep.get('method','GET')} {ep.get('url','')} ({ep.get('name','')})" for ep in endpoints[:20])

    messages = [
        {"role": "system", "content": """你是探索测试专家。根据项目信息生成结构化探索章程。
输出 JSON（```json包裹）：
{
  "objective": "测试目标",
  "timeBox": "建议时长",
  "checkpoints": [
    {"id": 1, "title": "检查点名称", "description": "具体要检查什么", "priority": "high|medium|low"}
  ],
  "riskAreas": ["高风险区域"],
  "explorationHints": ["探索建议"]
}"""},
        {"role": "user", "content": f"目标模块: {exp.target_module or exp.title}\n时间限制: {exp.time_limit_minutes}分钟\n\n项目API接口:\n{api_text or '（无录入）'}\n\n请生成探索测试章程。"},
    ]

    async def event_stream():
        full = ""
        try:
            async for chunk in llm_client.stream(messages, config=ai_config):
                if chunk.delta:
                    full += chunk.delta
                    yield f"data: {json.dumps({'type': 'chunk', 'content': chunk.delta}, ensure_ascii=False)}\n\n"

            import re
            match = re.search(r"```json\s*\n(.*?)(?:\n```|$)", full, re.DOTALL)
            charter_text = match.group(1) if match else full
            try:
                charter = json.loads(charter_text)
            except json.JSONDecodeError:
                last = charter_text.rfind("}")
                try:
                    charter = json.loads(charter_text[:last + 1]) if last > 0 else {}
                except json.JSONDecodeError:
                    charter = {}

            # 解析不出检查点 = 这次生成失败，别存、别报成功。
            #
            # 原来兜底返回 {} 之后照样存库、照样 yield done，前端弹「章程已生成」，
            # 但 checkpoints 是空的：页面显示「检查点 (0/0)」、「完成当前检查点」永远
            # 禁用、「引导检查」这一步彻底废掉，而人只看到一条成功提示，毫无线索。
            # 实测踩到（AI 这次输出没按 ```json 包裹）—— 一次输出抖动就静默废掉整条链。
            checkpoints = (charter or {}).get("checkpoints") or []
            if not checkpoints:
                logger.warning("探索章程解析失败，原始输出前 300 字：%s", full[:300])
                yield ("data: " + json.dumps({
                    "type": "error",
                    "message": "AI 这次的输出没能解析成章程（没拿到检查点），再点一次「AI 生成章程」重试",
                }, ensure_ascii=False) + "\n\n")
                return

            exp.charter = charter
            exp.checkpoints = checkpoints
            exp.total_checkpoints = len(checkpoints)
            from app.services.ai.usage import log_ai_call
            await log_ai_call(session, project_id=project_id, capability="exploratory-charter",
                              model=ai_config.model, est_chars=len(full or ""))
            await session.commit()

            yield f"data: {json.dumps({'type': 'done', 'charter': charter}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:200]}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
