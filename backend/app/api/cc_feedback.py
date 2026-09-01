"""CC 反馈 API —— 外部 Claude Code 报回来的**平台自身问题**的处理面。

**全局，不挂项目**（判据见 models/cc_feedback.py 的 docstring 和
docs/cc-feedback-channel.md §2）：反馈的对象是 Lumiere 自己，一个平台缺陷
不该按项目分成 N 条，处理方也只有维护者一拨。

守卫用 `require_role("admin")` —— 和「服务监控」「操作日志」同档。
权限点 `system.feedback.manage` 只管**前端呈现**（菜单藏不藏），
真正把门的是这里的守卫和游客非 GET 闸门。这一点全站一致，见
docs/permission-audit-2026-08.md。

**每个返回都裹一层 `{"data": ...}`** —— 全站约定（前端 `utils/request.js` 直接
把响应体交给调用方，页面读的是 `res.data.xxx`）。漏了不会报错：接口 200、
页面拿到 undefined，渲染成一张空表 —— 看着像「还没有反馈」，而不是「接错了」。
2026-09-01 这一版就是这么漏的，靠 tests/api/cc_feedback/ 才捞出来。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.deps.auth import require_role
from app.deps.db import get_db
from app.models.user import User
from app.services import cc_feedback_service as svc

router = APIRouter(prefix="/api/cc-feedback", tags=["cc-feedback"])


def _raise_if_err(result: dict) -> dict:
    """service 层用 {"error": ...} 表达拒绝（MCP 那边要的是能读的文字，不是异常）。
    HTTP 这边翻成 400，**把 why/howTo 一起带出去** —— 只回一句 "invalid" 的话，
    页面上的人只能靠猜，而这些拒绝理由本身就是设计的一部分。"""
    if isinstance(result, dict) and result.get("error"):
        extra = " ".join(str(v) for k, v in result.items()
                         if k in ("why", "howTo") and v)
        raise AppError(
            code="CC_FEEDBACK_REJECTED",
            message=result["error"],
            status_code=400,
            detail=extra or None,
        )
    return result


@router.get("")
async def list_feedback(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
    status: str | None = Query(default=None),
    pending_only: bool = Query(default=False, alias="pendingOnly"),
    category: str | None = Query(default=None),
    project_id: str | None = Query(default=None, alias="projectId"),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
):
    items, total, summary = await svc.list_feedback(
        session, status=status, pending_only=pending_only, category=category,
        project_id=project_id, keyword=keyword, page=page, page_size=page_size)
    return {"data": {"items": items, "total": total, "page": page,
                     "pageSize": page_size, "summary": summary}}


@router.get("/{feedback_id}")
async def get_feedback(
    feedback_id: str,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    row = await svc.get_detail(session, feedback_id)
    if row is None:
        raise AppError(code="NOT_FOUND", message="反馈不存在", status_code=404)
    return {"data": row}


@router.post("")
async def create_feedback(
    body: dict,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    """页面上手工建一条（source=human）。

    走的是**和 MCP 完全同一个 report()**，所以证据闸门、归并、wont_fix 短路
    对人工录入一样生效 —— 两条路各写一套校验，迟早会漂成两种规矩。
    """
    return {"data": _raise_if_err(await svc.report(
        session,
        title=body.get("title") or "",
        body=body.get("body") or "",
        category=body.get("category") or "",
        tool_name=body.get("toolName"),
        evidence=body.get("evidence"),
        project_id=body.get("projectId"),
        reporter=body.get("reporter") or user.username,
        source=body.get("source") or "human",
    ))}


@router.post("/{feedback_id}/triage")
async def triage_feedback(
    feedback_id: str,
    body: dict,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    """分诊 / 处置。done 和 wont_fix 必须带回音（service 里硬校验）。"""
    return {"data": _raise_if_err(await svc.triage(
        session, feedback_id,
        status=body.get("status") or "",
        category=body.get("category"),
        severity=body.get("severity"),
        resolution=body.get("resolution"),
        duplicate_of=body.get("duplicateOf"),
        actor=user.username,
    ))}


@router.post("/{feedback_id}/analyze")
async def analyze_feedback(
    feedback_id: str,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    """AI 分诊建议。**只写建议不改状态** —— 采纳与否是人的动作。"""
    return {"data": _raise_if_err(await svc.ai_triage(session, feedback_id))}
