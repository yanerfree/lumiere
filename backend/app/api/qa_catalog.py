"""QA 场景清单（只读）。

配了 QA 仓才有数据；没配返回 configured=false，页面照样把表头画出来，
不要用 404/空响应把"没配置"和"出错了"混成一件事。
"""
import uuid

import anyio
from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.exceptions import AppError
from app.deps.auth import require_project_role
from app.deps.db import get_db
from app.models.environment import Environment
from app.models.project import Project
from app.models.qa_catalog_review import QaCatalogReview
from app.models.user import User
from app.schemas.project import QaRepoConfig
from app.services import project_service, qa_catalog, qa_catalog_review
from app.services.git_service import GitError

router = APIRouter(prefix="/api/projects/{project_id}/qa-catalog", tags=["qa-catalog"])

_EMPTY = {
    "repo": None,
    "summary": {
        "total": 0, "covered": 0, "gap": 0, "deprecated": 0, "scripts": 0,
        "knownBugScenarios": 0, "knownBugRefs": 0, "coveredWithBugs": 0,
        "claimedButUncovered": 0, "orphanScripts": 0, "riskMismatch": 0,
        "unparsedRows": 0, "duplicateIds": 0, "byPriority": {},
    },
    "domains": [],
    "scenarios": [],
    "orphanScriptList": [],
    "knownBugRefList": [],
    # 键要跟 parse_catalog 的真返回**一字不差**：空态少一个键，前端就得写
    # `?.` 兜底，而兜底之后"这个仓根本没配"和"读到了但一条都没漏"渲染成同一个样子。
    "catalogIssues": {"unparsedRows": [], "duplicateIds": [],
                      "domainGroupsUnreadable": []},
}


async def _get_project(session: AsyncSession, project_id: uuid.UUID) -> Project | None:
    return (await session.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()


def _cfg_out(cfg: dict | None) -> dict:
    """回给页面的配置原样（用于回填配置弹窗）。没配就是一份空表单。"""
    cfg = cfg or {}
    return {
        "url": cfg.get("url") or "",
        "branch": cfg.get("branch") or "",
        "catalogPath": cfg.get("catalogPath") or "",
        "caseGlobs": cfg.get("caseGlobs") or [],
    }


async def _load(session: AsyncSession, project_id: uuid.UUID, refresh: bool) -> dict:
    project = await _get_project(session, project_id)
    cfg = (project.qa_repo if project else None) or None
    if not cfg or not cfg.get("url"):
        return {"data": {"configured": False, "error": None, "config": _cfg_out(None), **_EMPTY}}

    try:
        data = await anyio.to_thread.run_sync(
            lambda: qa_catalog.cached_read(str(project_id), cfg, refresh)
        )
    except GitError as e:
        # 读不到就把原因显示在页面上（认证失败 / 分支不存在 / 清单路径写错都是常见的
        # 配置问题），别静默返回空清单——那会被当成"QA 一条用例都没有"
        return {"data": {
            "configured": True,
            "error": e.message,
            "config": _cfg_out(cfg),
            **_EMPTY,
            "repo": {"url": cfg.get("url"), "branch": cfg.get("branch"), "catalogPath": cfg.get("catalogPath")},
        }}

    return {"data": {"configured": True, "error": None, "config": _cfg_out(cfg), **data}}


@router.get("")
async def get_qa_catalog(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """读取 QA 场景清单（用本地只读缓存，不打远端）。"""
    return await _load(session, project_id, refresh=False)


@router.post("/refresh")
async def refresh_qa_catalog(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """从 QA 仓 fetch 最新 commit 后重新解析。**只 fetch，不写远端。**"""
    return await _load(session, project_id, refresh=True)


@router.put("/config")
async def save_qa_repo_config(
    project_id: uuid.UUID,
    body: QaRepoConfig,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin")),
):
    """保存 QA 仓配置，并立刻按新配置读一遍。

    配置放在这一页而不是"编辑项目"弹窗：它只影响这一页，认错了也只在这一页报错，
    改的人和看结果的人是同一个。顺带 `PUT /api/projects/{id}` 要系统 admin，
    而项目管理员就该能接自己项目的 QA 仓。

    只有 url 必填；其余留空 = 自动识别。url 传空串 = 取消配置。
    """
    project = await project_service.set_qa_repo(session, project_id, body)
    await write_audit_log(
        session, action="update", target_type="project",
        target_id=project.id, target_name=project.name,
        changes={"qaRepo": project.qa_repo},
    )
    # 刚改完配置就得知道认没认出来，所以这里带 fetch 读一次
    return await _load(session, project_id, refresh=bool((body.url or "").strip()))


# ── 打开脚本看内容 ────────────────────────────────────────────

async def _require_cfg(session: AsyncSession, project_id: uuid.UUID) -> dict:
    project = await _get_project(session, project_id)
    cfg = (project.qa_repo if project else None) or {}
    if not cfg.get("url"):
        raise AppError(code="QA_REPO_NOT_CONFIGURED", message="这个项目还没配 QA 仓", status_code=400)
    return cfg


@router.get("/file")
async def read_qa_file(
    project_id: uuid.UUID,
    path: str = Query(..., description="仓库内相对路径，必须是清单引用到的文件"),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """看 QA 仓里某个文件的内容（`git show`，只读）。

    只开放**清单引用到的**那些路径（见 `qa_catalog.readable_paths`）。不做路径清洗 ——
    白名单挡得住的东西，黑名单每次都会漏掉一种写法。
    """
    cfg = await _require_cfg(session, project_id)
    try:
        data = await anyio.to_thread.run_sync(
            lambda: qa_catalog.read_file(str(project_id), cfg, path))
    except GitError as e:
        raise AppError(code="QA_FILE_UNREADABLE", message=e.message, status_code=404) from e
    return {"data": data}


# ── 域级 AI 评审 ──────────────────────────────────────────────

async def _pick_env(session: AsyncSession, project_id: uuid.UUID, env_id) -> Environment:
    """挑这次在哪个环境上评。**环境是结论的一部分**（review-spec §5）。

    不要求环境有 BASE_URL —— 用例审核那边要求，是因为它真会去跑；这里一行都不跑，
    只拿环境的**变量名**跟脚本引用的变量对账。拿 BASE_URL 卡住只会把
    "只配了凭证的环境"挡在门外。
    """
    if env_id:
        e = await session.get(Environment, uuid.UUID(str(env_id)))
        if e is None or str(e.project_id) != str(project_id):
            raise AppError(code="ENV_NOT_FOUND", message="环境不存在或不属于这个项目", status_code=400)
        return e
    e = (await session.execute(
        select(Environment).where(Environment.project_id == project_id)
        .order_by(Environment.sort_order, Environment.created_at).limit(1))).scalars().first()
    if e is None:
        raise AppError(code="NO_ENVIRONMENT",
                       message="这个项目还没配环境，先去「项目设置 → 环境」加一个再来评审",
                       status_code=400)
    return e


@router.post("/reviews")
async def start_qa_review(
    project_id: uuid.UUID,
    domain: str = Body(..., embed=True),
    env_id: uuid.UUID | None = Body(default=None, embed=True, alias="envId"),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """对一个**域**发起 AI 评审。立刻返回，真正的活在后台跑。

    **只做域级**：一条场景单独评没有信息量（脚本是按域组织的，一个脚本常常覆盖同域
    好几条），而"这个域的脚本撑不撑得起这个域的清单"才是能拿去排活的结论。

    评的是别人仓库里的文本，**结论只落本库**，QA 仓那边不会有任何变化。
    """
    cfg = await _require_cfg(session, project_id)

    try:
        catalog = await anyio.to_thread.run_sync(
            lambda: qa_catalog.cached_read(str(project_id), cfg, False))
    except GitError as e:
        raise AppError(code="QA_REPO_UNREADABLE", message=e.message, status_code=400) from e

    info, scenarios, paths = qa_catalog_review.collect(catalog, domain)
    if not scenarios:
        raise AppError(code="DOMAIN_NOT_FOUND", message=f"清单里没有 {domain} 这个域",
                       status_code=400)

    # 同一个域已经在跑就把那一条还回去。不拦的话人点两下就是两次模型调用，
    # 而且后回来的那条会盖掉先回来的
    running = (await session.execute(
        select(QaCatalogReview).where(
            QaCatalogReview.project_id == project_id, QaCatalogReview.domain == domain,
            QaCatalogReview.status.in_(("queued", "running")))
        .order_by(desc(QaCatalogReview.created_at)).limit(1))).scalars().first()
    if running is not None:
        return {"data": qa_catalog_review.to_dict(running, with_dims=True)}

    env = await _pick_env(session, project_id, env_id)
    review = qa_catalog_review.new_review(
        project_id, domain=info, repo=catalog.get("repo") or {}, env=env,
        actor=current_user.username, scenario_count=len(scenarios), script_count=len(paths))
    session.add(review)
    # **先落库再起后台任务**：后台那趟自己开 session，这里不提交它就查不到这一行
    await session.commit()

    qa_catalog_review.spawn(
        qa_catalog_review.execute(project_id, review.id, cfg, domain))
    return {"data": qa_catalog_review.to_dict(review, with_dims=True)}


@router.get("/reviews")
async def list_qa_reviews(
    project_id: uuid.UUID,
    domain: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """这个项目的评审记录。不传 domain = 每个域只回**最近一次**（页面上那一列徽标）。"""
    stmt = select(QaCatalogReview).where(QaCatalogReview.project_id == project_id)
    if domain:
        stmt = stmt.where(QaCatalogReview.domain == domain)
    rows = (await session.execute(
        stmt.order_by(desc(QaCatalogReview.created_at)).limit(200))).scalars().all()
    if not domain:
        latest: dict[str, QaCatalogReview] = {}
        for r in rows:                      # 已按时间倒序，第一条就是最近的
            latest.setdefault(r.domain, r)
        rows = list(latest.values())
    # 列表**不带** with_dims：一次几十行，每行挂一份口径就是同一段常量发几十遍。
    return {"data": {"reviews": [qa_catalog_review.to_dict(r) for r in rows]}}


@router.get("/reviews/{review_id}/export")
async def export_qa_review(
    project_id: uuid.UUID,
    review_id: uuid.UUID,
    fmt: str = Query(default="md", alias="format", description="md | json"),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """把一次评审导出成能拿走的文本。**QA 那边取结论走这里（拉），不是我们推。**

    平台永远不往 QA 仓写（`docs/qa-repo-readonly-catalog.md` §1）。所以"让 QA 拿到
    评审结果"只能做成一份**他自己来拉**的产物：这个接口给全文，
    MCP 的 `lum_get_qa_review` 给同一份东西（QA 那边跑 Claude Code 时用）。
    拿到之后放不放进他自己的仓库、放哪，是他的决定，不是我们的动作。

    `format=md` 回 Markdown 全文（含判据锚点，可直接贴 issue / 交给 AI 改脚本），
    `format=json` 回结构化原文（要自己拼报表时用）。
    """
    r = await session.get(QaCatalogReview, review_id)
    if r is None or str(r.project_id) != str(project_id):
        raise AppError(code="REVIEW_NOT_FOUND", message="找不到这次评审", status_code=404)
    if r.status != "done":
        raise AppError(code="REVIEW_NOT_DONE",
                       message=f"这次评审还没出结论（{r.status}），没有可导出的内容",
                       status_code=400)
    if fmt == "json":
        return {"data": qa_catalog_review.to_dict(r, with_dims=True)}
    return {"data": {
        "filename": f"qa-review-{r.domain}-{(r.commit_sha or '')[:7]}.md",
        "markdown": qa_catalog_review.to_markdown(r),
    }}


@router.get("/reviews/{review_id}")
async def get_qa_review(
    project_id: uuid.UUID,
    review_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role("project_admin", "developer", "tester", "guest")),
):
    """轮询用：一条评审现在怎么样了。"""
    r = await session.get(QaCatalogReview, review_id)
    if r is None or str(r.project_id) != str(project_id):
        raise AppError(code="REVIEW_NOT_FOUND", message="找不到这次评审", status_code=404)
    return {"data": qa_catalog_review.to_dict(r, with_dims=True)}
