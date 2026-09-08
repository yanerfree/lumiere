"""QA 域评审的**活体那一半**：真跑一趟页面枚举，跑完做三边对账。

跟清单那一页（`qa_catalog.py`）分成两个 router，因为**失败的方式完全不同**：
那边失败是「仓库读不到」，这边失败是「环境登不上 / 页面打不开」。混在一个接口里
报错的人分不清该去修哪一头。

**只读**：三层写守卫在 `app/services/qa_survey_guard.py`，这一层不重复它的判断。
判据和三批计划见 `docs/qa-domain-live-verification-plan.md`。
"""
import uuid

import anyio
from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# 跟清单那一页共用这两个，**不抄一份**：错误码（`QA_REPO_NOT_CONFIGURED` /
# `ENV_NOT_FOUND` / `NO_ENVIRONMENT`）前端只认一套，抄一份两边就会各自漂 ——
# 而漂出来的表现是"点了没反应"，不是报错。
from app.api.qa_catalog import _pick_env, _require_cfg
from app.core import permissions as perms
from app.core.audit import write_audit_log
from app.core.exceptions import AppError
from app.deps.auth import require_project_role
from app.deps.db import get_db
from app.engine.tasks.page_survey import run_page_survey
from app.engine.task_status import set_task_status
from app.models.qa_page_survey import QaPageSurvey, QaPageSurveyItem
from app.models.user import User
from app.services import qa_catalog, qa_catalog_review, qa_live_survey
from app.services.git_service import GitError
from app.services.qa_page_survey import latest_survey

router = APIRouter(prefix="/api/projects/{project_id}/qa-survey", tags=["qa-survey"])

# 同项目 + 同环境正在跑的那一趟：`(projectId, envId) -> taskId`。
#
# **这是进程内的字典，不是分布式锁，也不打算是。** 它挡的是"人连点两下"和
# "页面自动重试"——那两件真会发生。多进程部署下它挡不住，而挡不住的后果是
# **多爬一趟**（代价看得见、只读、有守卫），不是数据错。为这件事引一把 redis 锁，
# 换来的是一类新的故障（锁没释放 ⇒ 这个环境再也起不了）。
_INFLIGHT: dict[tuple[str, str], str] = {}


async def _guarded(key: tuple[str, str], coro):
    """跑完（含跑崩）都要把在跑标记摘掉 —— 摘不掉的话这个环境就再也起不来了。"""
    try:
        return await coro
    finally:
        _INFLIGHT.pop(key, None)


def _survey_out(s: QaPageSurvey, item_count: int) -> dict:
    """一趟枚举回给页面的样子。

    `pageEdges` 不整份回（几十个页面的请求边，几百 KB），但**得区分"没算过"和
    "算过是 0 条"** —— 所以回的是 `None` 或一个整数，不是"没有就 0"。
    """
    return {
        "id": str(s.id),
        "status": s.status,
        "envId": str(s.env_id) if s.env_id else None,
        "envName": s.env_name or "",
        "roles": s.roles or [],
        "buildFingerprint": s.build_fingerprint or "",
        "routeTableHash": s.route_table_hash or "",
        "ledger": s.ledger or {},
        "itemCount": item_count,
        "pageEdgeCount": None if s.page_edges is None else len(s.page_edges),
        "startedAt": s.started_at.isoformat() if s.started_at else None,
        "finishedAt": s.finished_at.isoformat() if s.finished_at else None,
        "error": s.error,
    }


@router.post("/runs")
async def start_page_survey(
    project_id: uuid.UUID,
    env_id: uuid.UUID | None = Body(default=None, embed=True, alias="envId"),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*perms.TIER_WRITE)),
):
    """起一趟页面枚举 + 对账。**计划在请求里算完，爬取丢后台。**

    计划（路由表、选择器清单、这一趟有哪些角色）算在请求里，是为了让配置类的错
    ——没 BASE_URL、没只读账号、认不出 `selectors.ts`——**立刻**变成一句人话；
    丢进后台的话人看到的是一条转十几秒再 failed 的任务，同一句话晚十几秒。

    返回的 `plan` 走 `public_plan` 出网：那份计划里带着**完整可用的账号密码**。
    """
    cfg = await _require_cfg(session, project_id)

    # 先把 QA 仓读一遍：一是拿 `commitSha`（它是复用判据的一半），二是**这一下
    # 保证本地 bare 仓存在** —— `prepare` 里读 `selectors.ts` 是直接打本地仓的。
    try:
        catalog = await anyio.to_thread.run_sync(
            lambda: qa_catalog.cached_read(str(project_id), cfg, False))
    except GitError as e:
        raise AppError(code="QA_REPO_UNREADABLE", message=e.message,
                       status_code=400) from e

    env = await _pick_env(session, project_id, env_id)
    key = (str(project_id), str(env.id))

    running = _INFLIGHT.get(key)
    if running:
        # **不再起一趟**：这件事会真的去访问别人的测试环境，重复一趟的成本不在我们这边。
        # `plan` 给 `None` 而不是空对象 —— 这一次确实没算计划。
        return {"data": {
            "taskId": running, "started": False, "plan": None,
            "note": (f"这个环境上已经有一趟在跑（任务 {running}），没有再起一趟 ——"
                     f"页面枚举会真的去打开被测环境的页面。等它跑完再看。")}}

    try:
        plan = await qa_live_survey.prepare(
            session=session, project_id=project_id, cfg=cfg, env=env)
    except ValueError as e:
        # 配置不全。**照原样把那句话回出去**：它已经写清了缺什么、去哪儿配。
        raise AppError(code="SURVEY_NOT_READY", message=str(e), status_code=400) from e

    task_id = uuid.uuid4().hex
    await set_task_status(task_id, "pending",
                          message=f"页面枚举已提交：{plan['counters']['pages']} 个页面、"
                                  f"{plan['counters']['roles']} 个角色")
    await write_audit_log(
        session, action="create", target_type="qa_page_survey",
        target_name=f"{env.name}·{plan['counters']['pages']} 页",
        project_id=project_id,
        changes={"taskId": task_id, "envId": str(env.id),
                 "baseUrl": plan["baseUrl"], "mainRole": plan["mainRole"],
                 "counters": plan["counters"]})
    # 起后台任务之前提交：审计日志和这一趟是同一件事，任务崩了也该留下"谁点的"
    await session.commit()

    _INFLIGHT[key] = task_id
    # **进程内起**（`spawn` = `asyncio.create_task`），不走 arq enqueue：
    # `plan` 里带凭证，而 arq 的 job 参数是要写进 redis 的。
    # 封样在 `tests/test_qa_page_survey_task.py::test_活体计划不许走_arq_enqueue`。
    qa_catalog_review.spawn(_guarded(key, run_page_survey(
        {}, task_id, str(project_id),
        roles=plan["roles"], page_paths=plan["pagePaths"],
        base_url=plan["baseUrl"], env_id=plan["envId"], env_name=plan["envName"],
        build_fingerprint=plan["buildFingerprint"],
        route_table_hash=plan["routeTableHash"],
        qa_commit_sha=(catalog.get("repo") or {}).get("commitSha") or "",
        plan=plan)))

    return {"data": {"taskId": task_id, "started": True,
                     "plan": qa_live_survey.public_plan(plan)}}


@router.get("")
async def get_page_survey(
    project_id: uuid.UUID,
    env_id: uuid.UUID | None = Query(default=None, alias="envId"),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*perms.TIER_READ)),
):
    """这个环境上**最近落库的那一趟**（含账本里的选择器报告和三边对账）。

    两处刻意：

    1. **不按状态筛**（`latest_survey` 的纪律）。最近一趟是 `failed` 是个事实，
       跳过它去翻更早那趟 `done`，页面上就会显示一份**比现在旧**的结论，
       而没有任何地方说它旧。
    2. 一趟都没有 ⇒ `survey: null` + `hasRun: false`，**不是一份 0 计数的空壳**。
    """
    env_uuid = uuid.UUID(str(env_id)) if env_id else None
    if env_id is None:
        # 没指定环境就用清单那边同一套挑法（顺序第一个），免得两页各看一个环境
        env = await _pick_env(session, project_id, None)
        env_uuid = env.id
    survey = await latest_survey(session, project_id, env_uuid)
    if survey is None:
        return {"data": {"hasRun": False, "survey": None,
                         "envId": str(env_uuid) if env_uuid else None}}
    count = (await session.execute(
        select(func.count()).select_from(QaPageSurveyItem)
        .where(QaPageSurveyItem.survey_id == survey.id))).scalar() or 0
    return {"data": {"hasRun": True, "envId": str(env_uuid) if env_uuid else None,
                     "survey": _survey_out(survey, int(count))}}
