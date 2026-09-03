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
    # 故障域（「坏在哪一块」）。特殊值 **"__none__" = 还没判过域的那些** ——
    # 它和 "other"（判过了、不属于任何一块）不是一回事，混起来的话
    # 「还有几条没归位」这个欠账就永远看不见了。
    area: str | None = Query(default=None),
    project_id: str | None = Query(default=None, alias="projectId"),
    keyword: str | None = Query(default=None),
    # 页面上真正要人动手的只有这一撮。**跨状态**（AI 说判不了的还挂在 new 上、
    # AI 判的 wont_fix 被带新证据重报的挂在 wont_fix 上），所以是独立开关不是 status 值。
    awaiting_human: bool = Query(default=False, alias="awaitingHuman"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
):
    items, total, summary = await svc.list_feedback(
        session, status=status, pending_only=pending_only, category=category,
        area=area, project_id=project_id, keyword=keyword,
        awaiting_human=awaiting_human,
        page=page, page_size=page_size)
    return {"data": {"items": items, "total": total, "page": page,
                     "pageSize": page_size, "summary": summary}}


@router.get("/batch-status")
async def batch_status(_: User = Depends(require_role("admin"))):
    """当前批次跑到哪了。**这条必须声明在 `/{feedback_id}` 前面** —— 否则
    "batch-status" 会被当成 id 塞进那条动态路由，然后炸在 uuid 解析上。"""
    return {"data": svc.batch_status()}


@router.post("/ai-handle")
async def ai_handle_batch(
    body: dict | None = None,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    """勾一批（`ids`）丢给 AI；不传 ids = 全部还没判的。

    立刻返回，判是后台顺序跑的 —— 一批 31 条按十几秒一条算是几分钟，
    挂在 HTTP 上必超时。进度走 GET /batch-status。
    """
    ids = (body or {}).get("ids") or None
    return {"data": _raise_if_err(
        await svc.start_batch(session, ids, actor=user.username))}


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
    """手工建一条（source=human）。

    **页面上不再露这个入口**（2026-09-01）：这张表的正常来源是 CC 自己报，
    人打开这一页是来看结论、或者拍板 AI 判不了的那几条的。接口留着是因为
    导入/回填脚本和 API 测试都走它，而且它和 MCP 共用同一个 report()。

    走的是**和 MCP 完全同一个 report()**，所以证据闸门、归并、wont_fix 短路
    对人工录入一样生效 —— 两条路各写一套校验，迟早会漂成两种规矩。
    """
    return {"data": _raise_if_err(await svc.report(
        session,
        title=body.get("title") or "",
        body=body.get("body") or "",
        category=body.get("category") or "",
        tool_name=body.get("toolName"),
        area=body.get("area"),
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
    """**人**拍板。done 和 wont_fix 必须带回音（service 里硬校验）。

    走到这条路上的只有两种情况：AI 说自己判不了（`needsHuman`），或者人改判
    AI 的结论。所以这里写死 decided_by="human" —— 人判的 wont_fix 从此终局，
    不再被带新证据的重报翻案。
    """
    return {"data": _raise_if_err(await svc.triage(
        session, feedback_id,
        status=body.get("status") or "",
        category=body.get("category"),
        severity=body.get("severity"),
        area=body.get("area"),
        resolution=body.get("resolution"),
        duplicate_of=body.get("duplicateOf"),
        actor=user.username,
        decided_by="human",
    ))}


@router.post("/{feedback_id}/analyze")
async def analyze_feedback(
    feedback_id: str,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    """AI 处置这一条：分析 + **直接落裁定**。

    2026-09-01 改的口径（原来是「只写建议不改状态」）：AI 判得了的自己判，
    人只兜底 —— 判不了的会落 needs_human，页面上「等人拍板」筛得到。
    路径保留 `/analyze` 是为了不动已有的调用方，但它现在会改状态。
    """
    return {"data": _raise_if_err(await svc.ai_handle(session, feedback_id))}
