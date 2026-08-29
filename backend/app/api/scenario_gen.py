"""功能场景测试模块 — 生成任务 API（ft-1-4 底座）

端点（前缀 /api/projects/{project_id}/branches/{branch_id}/scenario-gen）:
- POST /tasks               创建生成任务（S2.1 将扩展增强上下文与提取 runner 挂接）
- GET  /tasks               历史任务列表（FR36）
- GET  /tasks/{id}          任务全量快照（含 last_seq，SSE 回放起点）
- POST /tasks/{id}/abort    中止任务
- GET  /tasks/{id}/events   SSE：after_seq 增量回放 → 追平后实时推送（ADR-3 / FR63）
"""
import asyncio
import json
import time
import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import permissions as perms
from app.core.exceptions import AppError, NotFoundError
from app.schemas.common import BaseSchema
from app.deps.auth import require_project_role
from app.deps.db import async_session_factory, get_db
from app.models.scenario_gen import GenerationTask, GenerationItem, RequirementDoc, RequirementPoint, ScenarioModel, TaskEvent
from app.models.user import User
from app.services.scenario_gen import pipeline
from app.services.scenario_gen.preprocessor import preprocess
from app.services.scenario_gen import runner as gen_runner
from app.core.audit import write_audit_log

router = APIRouter(
    prefix="/api/projects/{project_id}/branches/{branch_id}/scenario-gen",
    tags=["scenario-gen"],
)

# S1.2 阈值配置链接入后改读配置
MAX_CONTENT_CHARS = 200_000

EDIT_ROLES = perms.TIER_WRITE
READ_ROLES = perms.TIER_READ


def _task_to_dict(t: GenerationTask, *, last_seq: int | None = None, has_model: bool | None = None) -> dict:
    d = {
        "id": str(t.id),
        "project_id": str(t.project_id),
        "branch_id": str(t.branch_id),
        "doc_id": str(t.doc_id) if t.doc_id else None,
        "title": t.title,
        "status": t.status,
        "settings": t.settings,
        "context_summary": t.context_summary,
        "health_check": t.health_check,
        "progress": t.progress,
        "token_estimated": t.token_estimated,
        "token_used": t.token_used,
        "error_message": t.error_message,
        "created_by": str(t.created_by) if t.created_by else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }
    if last_seq is not None:
        d["last_seq"] = last_seq
    if has_model is not None:
        d["hasModel"] = has_model
    return d


class CreateTaskRequest(BaseSchema):
    title: str
    content_markdown: str
    source: str = "paste"  # paste / upload
    filename: str | None = None
    settings: dict | None = None


@router.post("/tasks", status_code=201)
async def create_task(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: CreateTaskRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*EDIT_ROLES)),
):
    """创建生成任务（含需求材料快照）。提取 runner 由 S2.3 挂接。"""
    title = body.title.strip()
    if not title:
        raise AppError(code="TITLE_REQUIRED", message="任务名称不能为空", status_code=400)
    content = body.content_markdown
    if not content or not content.strip():
        raise AppError(code="CONTENT_REQUIRED", message="需求材料不能为空", status_code=400)
    if len(content) > MAX_CONTENT_CHARS:
        raise AppError(
            code="CONTENT_TOO_LONG",
            message=f"需求材料超过上限（{MAX_CONTENT_CHARS} 字符），请拆分后分批生成",
            status_code=400,
        )
    if body.source not in ("paste", "upload"):
        raise AppError(code="INVALID_SOURCE", message="source 仅支持 paste/upload", status_code=400)

    doc = RequirementDoc(
        project_id=project_id,
        branch_id=branch_id,
        source=body.source,
        filename=body.filename,
        content_markdown=content,
        content_meta=preprocess(content),
        created_by=current_user.id,
    )
    session.add(doc)
    await session.flush()

    task = GenerationTask(
        project_id=project_id,
        branch_id=branch_id,
        doc_id=doc.id,
        title=title[:200],
        status="extracting",
        settings=body.settings,
        created_by=current_user.id,
    )
    session.add(task)
    await session.flush()
    seq = await pipeline.emit_event(session, task.id, "task_state", {"status": "extracting"})
    await session.commit()

    await write_audit_log(
        session, action="create", target_type="generation_task",
        target_id=task.id, target_name=task.title,
        user_id=current_user.id, project_id=project_id,
    )

    # 后台触发 AI 需求点提取
    pipeline.spawn(gen_runner.run_extraction(task.id, project_id), name=f"extract-{task.id}", gen_task_id=task.id)

    return {"data": _task_to_dict(task, last_seq=seq)}


@router.get("/tasks")
async def list_tasks(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    status: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*READ_ROLES)),
):
    """历史生成任务列表（FR36）"""
    cond = [GenerationTask.project_id == project_id, GenerationTask.branch_id == branch_id]
    if status:
        cond.append(GenerationTask.status == status)
    total = (await session.execute(select(func.count(GenerationTask.id)).where(*cond))).scalar_one()
    result = await session.execute(
        select(GenerationTask).where(*cond)
        .order_by(GenerationTask.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )
    items = [_task_to_dict(t) for t in result.scalars().all()]
    return {"data": {"items": items, "total": total, "page": page, "page_size": page_size}}


async def _get_task_checked(
    session: AsyncSession, project_id: uuid.UUID, branch_id: uuid.UUID, task_id: uuid.UUID
) -> GenerationTask:
    task = await session.get(GenerationTask, task_id)
    if not task or task.project_id != project_id or task.branch_id != branch_id:
        raise NotFoundError(code="NOT_FOUND", message="生成任务不存在")
    return task


@router.get("/tasks/{task_id}")
async def get_task(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*READ_ROLES)),
):
    """任务全量快照 + last_seq（前端先渲染快照，再带 after_seq=last_seq 订阅增量）"""
    task = await _get_task_checked(session, project_id, branch_id, task_id)
    seq = await pipeline.last_seq(session, task.id)
    has_model = None
    if task.status in ("model_ready", "confirmed"):
        model_exists = (await session.execute(
            select(ScenarioModel.id).where(ScenarioModel.task_id == task_id).limit(1)
        )).scalar_one_or_none()
        has_model = model_exists is not None
    return {"data": _task_to_dict(task, last_seq=seq, has_model=has_model)}


@router.post("/tasks/{task_id}/abort")
async def abort_task(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*EDIT_ROLES)),
):
    task = await _get_task_checked(session, project_id, branch_id, task_id)
    runner = pipeline._RUNNING.get(task.id)
    if runner is not None and not runner.done():
        runner.cancel()
    try:
        await pipeline.transition(session, task, "aborted", error_message="用户中止")
    except pipeline.InvalidTransition as e:
        raise AppError(code="INVALID_TRANSITION", message=str(e), status_code=409)
    await session.commit()
    await session.refresh(task)  # onupdate 的 updated_at 由 DB 生成，取回避免惰性 IO
    await write_audit_log(
        session, action="abort", target_type="generation_task",
        target_id=task.id, target_name=task.title,
        user_id=current_user.id, project_id=project_id,
    )
    return {"data": _task_to_dict(task)}


# ── 需求点 CRUD（S2.5 / FR3-FR4）─────────────────────────────────────

def _point_to_dict(p: RequirementPoint) -> dict:
    return {
        "id": str(p.id),
        "taskId": str(p.task_id),
        "code": p.code,
        "title": p.title,
        "quoteText": p.quote_text,
        "quoteOffset": p.quote_offset,
        "anchorStatus": p.anchor_status,
        "status": p.status,
        "naReason": p.na_reason,
        "createdByAi": p.created_by_ai,
        "sortOrder": p.sort_order,
    }


@router.get("/tasks/{task_id}/requirement-points")
async def list_requirement_points(
    project_id: uuid.UUID, branch_id: uuid.UUID, task_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*READ_ROLES)),
):
    await _get_task_checked(session, project_id, branch_id, task_id)
    result = await session.execute(
        select(RequirementPoint).where(RequirementPoint.task_id == task_id)
        .order_by(RequirementPoint.sort_order)
    )
    return {"data": [_point_to_dict(p) for p in result.scalars().all()]}


class UpdatePointRequest(BaseSchema):
    title: str | None = None
    status: str | None = None
    na_reason: str | None = None


@router.put("/tasks/{task_id}/requirement-points/{point_id}")
async def update_requirement_point(
    project_id: uuid.UUID, branch_id: uuid.UUID, task_id: uuid.UUID, point_id: uuid.UUID,
    body: UpdatePointRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*EDIT_ROLES)),
):
    await _get_task_checked(session, project_id, branch_id, task_id)
    point = await session.get(RequirementPoint, point_id)
    if not point or point.task_id != task_id:
        raise NotFoundError(code="NOT_FOUND", message="需求点不存在")
    if body.title is not None:
        point.title = body.title[:300]
    if body.status is not None:
        if body.status not in ("active", "not_applicable"):
            raise AppError(code="INVALID_STATUS", message="status 仅支持 active/not_applicable", status_code=400)
        point.status = body.status
    if body.na_reason is not None:
        point.na_reason = body.na_reason
    await session.commit()
    await session.refresh(point)
    return {"data": _point_to_dict(point)}


@router.delete("/tasks/{task_id}/requirement-points/{point_id}")
async def delete_requirement_point(
    project_id: uuid.UUID, branch_id: uuid.UUID, task_id: uuid.UUID, point_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*EDIT_ROLES)),
):
    await _get_task_checked(session, project_id, branch_id, task_id)
    point = await session.get(RequirementPoint, point_id)
    if not point or point.task_id != task_id:
        raise NotFoundError(code="NOT_FOUND", message="需求点不存在")
    await session.delete(point)
    await session.commit()
    return {"data": {"deleted": True}}


class CreatePointRequest(BaseSchema):
    title: str
    quote_text: str | None = None


@router.post("/tasks/{task_id}/requirement-points", status_code=201)
async def create_requirement_point(
    project_id: uuid.UUID, branch_id: uuid.UUID, task_id: uuid.UUID,
    body: CreatePointRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*EDIT_ROLES)),
):
    """手工新建需求点（FR4 手工框选原文）"""
    task = await _get_task_checked(session, project_id, branch_id, task_id)
    max_order = (await session.execute(
        select(func.max(RequirementPoint.sort_order)).where(RequirementPoint.task_id == task_id)
    )).scalar_one_or_none() or 0
    max_code_num = (await session.execute(
        select(func.count(RequirementPoint.id)).where(RequirementPoint.task_id == task_id)
    )).scalar_one() or 0

    from app.services.scenario_gen.extractor import anchor_quote
    doc = await session.get(RequirementDoc, task.doc_id) if task.doc_id else None
    doc_text = doc.content_markdown if doc else ""
    anchor_status, offset = anchor_quote(doc_text, body.quote_text or "") if body.quote_text else ("unanchored", None)

    point = RequirementPoint(
        task_id=task_id,
        doc_id=task.doc_id,
        code=f"R{max_code_num + 1}",
        title=body.title[:300],
        quote_text=body.quote_text[:2000] if body.quote_text else None,
        quote_offset=offset,
        anchor_status=anchor_status,
        status="active",
        created_by_ai=False,
        sort_order=max_order + 1,
    )
    session.add(point)
    await session.commit()
    await session.refresh(point)
    return {"data": _point_to_dict(point)}


# ── 需求质量检测端点（S2.4 / FR5 — 软门禁）─────────────────────────

@router.get("/tasks/{task_id}/health-check")
async def get_health_check(
    project_id: uuid.UUID, branch_id: uuid.UUID, task_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*READ_ROLES)),
):
    """返回已存储的质量检测结果（创建任务时自动执行）"""
    task = await _get_task_checked(session, project_id, branch_id, task_id)
    return {"data": task.health_check or {"score": None, "issues": [], "below_threshold": False}}


# ── 任务阶段推进（确认需求点 → 触发场景模型生成）───────────────────────

@router.post("/tasks/{task_id}/confirm-requirements")
async def confirm_requirements(
    project_id: uuid.UUID, branch_id: uuid.UUID, task_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*EDIT_ROLES)),
):
    """用户确认需求点后，推进到 model_ready → 触发场景模型生成（S3.1 挂接 runner）"""
    task = await _get_task_checked(session, project_id, branch_id, task_id)
    if task.status not in ("extracting", "model_ready"):
        raise AppError(code="INVALID_STATUS", message=f"当前状态 {task.status} 不支持此操作", status_code=409)
    points_count = (await session.execute(
        select(func.count(RequirementPoint.id)).where(
            RequirementPoint.task_id == task_id,
            RequirementPoint.status == "active",
        )
    )).scalar_one()
    if points_count == 0:
        raise AppError(code="NO_POINTS", message="至少需要一个有效需求点", status_code=400)
    try:
        if task.status == "extracting":
            await pipeline.transition(session, task, "model_ready")
    except pipeline.InvalidTransition as e:
        raise AppError(code="INVALID_TRANSITION", message=str(e), status_code=409)
    await session.commit()
    await session.refresh(task)

    # 后台触发 AI 场景模型生成
    pipeline.spawn(gen_runner.run_modeling(task.id, project_id), name=f"model-{task.id}", gen_task_id=task.id)

    return {"data": _task_to_dict(task)}


# ── 场景模型（S3.1-S3.2 / FR7-FR12）─────────────────────────────────

def _model_to_dict(m: ScenarioModel) -> dict:
    return {
        "id": str(m.id),
        "taskId": str(m.task_id),
        "flows": m.flows,
        "stateTransitions": m.state_transitions,
        "roleMatrix": m.role_matrix,
        "testPoints": m.test_points,
        "status": m.status,
        "editedFields": m.edited_fields,
        "confirmedAt": m.confirmed_at.isoformat() if m.confirmed_at else None,
    }


@router.get("/tasks/{task_id}/scenario-model")
async def get_scenario_model(
    project_id: uuid.UUID, branch_id: uuid.UUID, task_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*READ_ROLES)),
):
    await _get_task_checked(session, project_id, branch_id, task_id)
    result = await session.execute(
        select(ScenarioModel).where(ScenarioModel.task_id == task_id)
    )
    model = result.scalar_one_or_none()
    if not model:
        raise NotFoundError(code="NOT_FOUND", message="场景模型尚未生成")
    return {"data": _model_to_dict(model)}


class UpdateModelRequest(BaseSchema):
    flows: list | None = None
    state_transitions: list | None = None
    role_matrix: list | None = None
    test_points: list | None = None
    edited_fields: dict | None = None


@router.put("/tasks/{task_id}/scenario-model")
async def update_scenario_model(
    project_id: uuid.UUID, branch_id: uuid.UUID, task_id: uuid.UUID,
    body: UpdateModelRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*EDIT_ROLES)),
):
    """用户编辑场景模型（FR9）"""
    await _get_task_checked(session, project_id, branch_id, task_id)
    result = await session.execute(
        select(ScenarioModel).where(ScenarioModel.task_id == task_id)
    )
    model = result.scalar_one_or_none()
    if not model:
        raise NotFoundError(code="NOT_FOUND", message="场景模型尚未生成")
    if model.status not in ("draft", "confirmed"):
        raise AppError(code="MODEL_LOCKED", message="模型已锁定，不可编辑", status_code=409)
    if body.flows is not None:
        model.flows = body.flows
    if body.state_transitions is not None:
        model.state_transitions = body.state_transitions
    if body.role_matrix is not None:
        model.role_matrix = body.role_matrix
    if body.test_points is not None:
        from app.services.scenario_gen.modeler import DIMENSION_WHITELIST
        for tp in body.test_points:
            if isinstance(tp, dict) and tp.get("dimension") not in DIMENSION_WHITELIST:
                tp["dimension"] = "positive"
        model.test_points = body.test_points
    if body.edited_fields is not None:
        model.edited_fields = {**(model.edited_fields or {}), **body.edited_fields}
    await session.commit()
    await session.refresh(model)
    return {"data": _model_to_dict(model)}


@router.post("/tasks/{task_id}/confirm-model")
async def confirm_model(
    project_id: uuid.UUID, branch_id: uuid.UUID, task_id: uuid.UUID,
    skip: bool = Query(default=False, description="跳过确认直接生成（FR10/FR69）"),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*EDIT_ROLES)),
):
    """确认场景模型 → 推进任务到 confirmed（S3.2 前端调用）"""
    task = await _get_task_checked(session, project_id, branch_id, task_id)
    if task.status != "model_ready":
        raise AppError(code="INVALID_STATUS", message=f"当前状态 {task.status} 不支持确认模型", status_code=409)
    result = await session.execute(select(ScenarioModel).where(ScenarioModel.task_id == task_id))
    model = result.scalar_one_or_none()
    if not model:
        raise AppError(code="NO_MODEL", message="场景模型尚未生成", status_code=400)
    tp_count = len(model.test_points) if model.test_points else 0
    if tp_count == 0:
        raise AppError(code="NO_TEST_POINTS", message="场景模型中无测试点", status_code=400)

    from datetime import datetime, timezone
    model.status = "skipped" if skip else "confirmed"
    model.confirmed_by = current_user.id
    model.confirmed_at = datetime.now(timezone.utc)

    try:
        await pipeline.transition(session, task, "confirmed")
    except pipeline.InvalidTransition as e:
        raise AppError(code="INVALID_TRANSITION", message=str(e), status_code=409)
    await session.commit()
    await session.refresh(task)
    await write_audit_log(
        session, action="confirm_model", target_type="generation_task",
        target_id=task.id, target_name=task.title,
        user_id=current_user.id, project_id=project_id,
    )

    # 后台触发 AI 用例批量展开（状态需先推进到 generating）
    async def _start_expansion():
        async with async_session_factory() as s:
            t = await s.get(GenerationTask, task_id)
            if t and t.status == "confirmed":
                await pipeline.transition(s, t, "generating")
                await s.commit()
        await gen_runner.run_expansion(task_id, project_id)

    pipeline.spawn(_start_expansion(), name=f"expand-{task.id}", gen_task_id=task.id)

    return {"data": {**_task_to_dict(task), "testPointCount": tp_count}}


# ── 断点续生成（S4.6 / FR35 / NFR8）─────────────────────────────────

@router.post("/tasks/{task_id}/resume")
async def resume_task(
    project_id: uuid.UUID, branch_id: uuid.UUID, task_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*EDIT_ROLES)),
):
    """从失败/部分失败状态续跑：只处理非 succeeded 的 item"""
    task = await _get_task_checked(session, project_id, branch_id, task_id)
    if task.status not in ("partial_failed", "failed"):
        raise AppError(code="INVALID_STATUS", message=f"当前状态 {task.status} 不支持续跑", status_code=409)

    # 查询未完成 item 数量
    pending_count = (await session.execute(
        select(func.count(GenerationItem.id)).where(
            GenerationItem.task_id == task_id,
            GenerationItem.status.in_(["pending", "failed"]),
        )
    )).scalar_one()

    if pending_count == 0:
        raise AppError(code="NO_PENDING", message="没有可续跑的 item", status_code=400)

    try:
        await pipeline.transition(session, task, "generating")
    except pipeline.InvalidTransition as e:
        raise AppError(code="INVALID_TRANSITION", message=str(e), status_code=409)
    await session.commit()
    await session.refresh(task)

    # 后台触发续跑展开
    pipeline.spawn(gen_runner.run_expansion(task_id, project_id), name=f"resume-{task.id}", gen_task_id=task.id)

    return {"data": {**_task_to_dict(task), "pendingItems": pending_count}}


# ── 覆盖矩阵（S6.1 / FR29-FR33）─────────────────────────────────────

@router.get("/tasks/{task_id}/coverage-matrix")
async def get_coverage_matrix_endpoint(
    project_id: uuid.UUID, branch_id: uuid.UUID, task_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*READ_ROLES)),
):
    """覆盖矩阵：需求点 × 维度，含零覆盖告警"""
    task = await _get_task_checked(session, project_id, branch_id, task_id)
    from app.services.scenario_gen.matrix import get_coverage_matrix
    matrix = await get_coverage_matrix(session, task.id, task.branch_id)
    return {"data": matrix}


# ── 质量统计（S7.2 / FR48-FR50）─────────────────────────────────────

@router.get("/stats")
async def get_generation_stats(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*READ_ROLES)),
):
    """生成质量统计：评审通过率/拒绝率/编辑率/拒绝理由分布"""
    from app.models.case import Case

    base_cond = [Case.branch_id == branch_id, Case.source == "ai", Case.deleted_at.is_(None)]
    total = (await session.execute(select(func.count(Case.id)).where(*base_cond))).scalar_one()
    approved = (await session.execute(
        select(func.count(Case.id)).where(*base_cond, Case.review_status == "approved")
    )).scalar_one()
    rejected = (await session.execute(
        select(func.count(Case.id)).where(*base_cond, Case.review_status == "rejected")
    )).scalar_one()
    pending = (await session.execute(
        select(func.count(Case.id)).where(*base_cond, Case.review_status == "pending_review")
    )).scalar_one()

    return {"data": {
        "total": total,
        "approved": approved,
        "rejected": rejected,
        "pending": pending,
        "approvalRate": round(approved / total * 100, 1) if total > 0 else 0,
        "rejectionRate": round(rejected / total * 100, 1) if total > 0 else 0,
    }}


# ── SSE：回放 + 实时（ADR-3）────────────────────────────────────────

SSE_POLL_INTERVAL = 0.5      # 秒；轮询 DB 增量（NFR4 <2s 余量充足）
SSE_HEARTBEAT_SECONDS = 15   # 空闲心跳，防中间层断连
SSE_MAX_DURATION = 30 * 60   # 单连接上限，前端会自动重连续传
SSE_BATCH_LIMIT = 200


def _sse_line(event: TaskEvent) -> str:
    data = {
        "seq": event.id,
        "type": event.event_type,
        "payload": event.payload,
        "ts": event.created_at.isoformat() if event.created_at else None,
    }
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/tasks/{task_id}/events")
async def task_events_stream(
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    task_id: uuid.UUID,
    after_seq: int = Query(default=0, ge=0, alias="afterSeq"),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*READ_ROLES)),
):
    """先回放 seq>after_seq 的历史事件，追平后转实时推送；任务终态且无新事件时发 stream_end 关闭。

    每轮轮询用独立短会话，不长期占用连接池。
    """
    await _get_task_checked(session, project_id, branch_id, task_id)

    async def gen():
        cursor = after_seq
        started = time.monotonic()
        last_sent = time.monotonic()
        while True:
            async with async_session_factory() as s:
                result = await s.execute(
                    select(TaskEvent)
                    .where(TaskEvent.task_id == task_id, TaskEvent.id > cursor)
                    .order_by(TaskEvent.id)
                    .limit(SSE_BATCH_LIMIT)
                )
                events = list(result.scalars().all())
                task_status = (
                    await s.execute(select(GenerationTask.status).where(GenerationTask.id == task_id))
                ).scalar_one_or_none()

            for ev in events:
                yield _sse_line(ev)
                cursor = ev.id
                last_sent = time.monotonic()

            if len(events) == SSE_BATCH_LIMIT:
                continue  # 还有积压，立即继续回放

            if task_status is None or task_status in pipeline.TERMINAL_STATUSES:
                yield f"data: {json.dumps({'type': 'stream_end', 'task_status': task_status, 'seq': cursor}, ensure_ascii=False)}\n\n"
                return
            if time.monotonic() - started > SSE_MAX_DURATION:
                yield f"data: {json.dumps({'type': 'stream_end', 'reason': 'max_duration', 'seq': cursor}, ensure_ascii=False)}\n\n"
                return
            if time.monotonic() - last_sent > SSE_HEARTBEAT_SECONDS:
                yield ": ping\n\n"
                last_sent = time.monotonic()
            await asyncio.sleep(SSE_POLL_INTERVAL)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
