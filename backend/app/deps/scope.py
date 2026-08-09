"""路径归属校验 —— 挡住"路径写自己的项目、id 填别人的"这类越权。

`require_project_role` 只回答一个问题：**你是不是路径里那个 project_id 的成员**。
它不检查路径里后面那些 id 到底属不属于这个项目。实测（造了个只属于 A 项目的
tester）：

    GET  /projects/{A}/branches/{A的分支}/cases/{B的用例id}   → 200，读到了 B 的用例正文
    GET  /projects/{A}/branches/{B的分支id}/cases             → 200，列出了 B 的整个用例列表
    PUT  /projects/{A}/branches/{A的分支}/cases/{B的用例id}   → 200，**改掉了 B 的用例标题**

第三条是真改了数据（靠审计日志才还原回来）。

所以补一道"链路归属"校验：路径里出现哪几段就验哪几段
（project → branch → case），挂在路由器上，那条路由器下的所有端点自动都有。

**故意返回 404 而不是 403**：403 等于告诉对方"这个 id 是存在的，只是你没权限"，
那本身就是一次信息泄露。不是你的东西，对你来说就该是"不存在"。
"""
from __future__ import annotations

import uuid

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.deps.auth import get_current_user as _current_user
from app.deps.db import get_db


def _uuid(v) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v))
    except (ValueError, AttributeError, TypeError):
        return None


async def verify_path_scope(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> None:
    """校验路径里各段 id 的归属链。

    用 `request.path_params` 而不是声明形参：这样同一个依赖能挂在任何路由器上，
    路径里有 branch_id 就验 branch、有 case_id 就验 case，没有就跳过。
    """
    from app.models.case import Case
    from app.models.project import Branch

    p = request.path_params
    pid, bid, cid = _uuid(p.get("project_id")), _uuid(p.get("branch_id")), _uuid(p.get("case_id"))

    if pid and bid:
        owner = (await session.execute(
            select(Branch.project_id).where(Branch.id == bid)
        )).scalar_one_or_none()
        if owner is None or owner != pid:
            raise NotFoundError(code="BRANCH_NOT_FOUND", message="分支不存在")

    if bid and cid:
        owner = (await session.execute(
            select(Case.branch_id).where(Case.id == cid)
        )).scalar_one_or_none()
        if owner is None or owner != bid:
            raise NotFoundError(code="CASE_NOT_FOUND", message="用例不存在")


async def verify_case_access(
    request: Request,
    current_user=Depends(_current_user),
    session: AsyncSession = Depends(get_db),
) -> None:
    """路径里**只有 case_id、没有 project_id** 时用这个。

    `/api/cases/{case_id}/file` 和 `/provenance` 是这种形状 —— 路径里没有项目，
    上面那个链路校验无从下手，而它们原来只要求"登录了"，于是任何登录用户
    拿一个 case_id 就能读到别的项目的用例病历/溯源。

    这里反过来查：用例 → 分支 → 项目，再看当前用户是不是这个项目的成员。
    同样返回 404，不告诉对方"存在但你没权限"。
    """
    from app.models.case import Case
    from app.models.project import Branch, ProjectMember

    cid = _uuid(request.path_params.get("case_id"))
    if cid is None:
        return
    if getattr(current_user, "role", "") == "admin":
        return                                   # 系统管理员绕过，和 require_project_role 一致

    pid = (await session.execute(
        select(Branch.project_id).join(Case, Case.branch_id == Branch.id).where(Case.id == cid)
    )).scalar_one_or_none()
    if pid is None:
        raise NotFoundError(code="CASE_NOT_FOUND", message="用例不存在")

    member = (await session.execute(
        select(ProjectMember.id).where(
            ProjectMember.project_id == pid,
            ProjectMember.user_id == current_user.id,
        )
    )).scalar_one_or_none()
    if member is None:
        raise NotFoundError(code="CASE_NOT_FOUND", message="用例不存在")
