"""QA 场景清单（只读）。

配了 QA 仓才有数据；没配返回 configured=false，页面照样把表头画出来，
不要用 404/空响应把"没配置"和"出错了"混成一件事。
"""
import uuid

import anyio
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.deps.auth import require_project_role
from app.deps.db import get_db
from app.models.project import Project
from app.models.user import User
from app.schemas.project import QaRepoConfig
from app.services import project_service, qa_catalog
from app.services.git_service import GitError

router = APIRouter(prefix="/api/projects/{project_id}/qa-catalog", tags=["qa-catalog"])

_EMPTY = {
    "repo": None,
    "summary": {
        "total": 0, "covered": 0, "gap": 0, "deprecated": 0, "scripts": 0,
        "knownBugScenarios": 0, "coveredWithBugs": 0, "claimedButUncovered": 0,
        "orphanScripts": 0, "riskMismatch": 0, "byPriority": {},
    },
    "domains": [],
    "scenarios": [],
    "orphanScriptList": [],
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
