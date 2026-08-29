import uuid

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.core.audit import audit_log
from app.models.project import Branch, Project, ProjectMember
from app.models.user import User
from app.schemas.project import CreateProjectRequest, QaRepoConfig, UpdateProjectRequest


def _normalize_qa_repo(cfg: QaRepoConfig | None) -> dict | None:
    """QA 仓配置落库前归一化。url 为空 = 没配（存 NULL，别存一个空壳）。

    存空壳的后果是页面判不出"没配"和"配了但填错"——前者该显示引导，后者该显示报错。
    """
    if cfg is None:
        return None
    url = (cfg.url or "").strip()
    if not url:
        return None
    globs = [g.strip() for g in (cfg.case_globs or []) if g and g.strip()]
    # 除 url 外都可以是空串：空 = 自动识别（分支跟 HEAD、清单按内容找、脚本按 @scenario 捞）。
    # 别在这里补 "main" 之类的默认值——那是 uag-qa 一家的习惯，填进去等于替下一个仓库猜错。
    return {
        "url": url,
        "branch": (cfg.branch or "").strip(),
        "catalogPath": (cfg.catalog_path or "").strip(),
        "caseGlobs": globs,
    }


@audit_log(action="create", target_type="project")
async def create_project(
    session: AsyncSession, data: CreateProjectRequest, creator: User
) -> Project:
    """创建项目 + 默认 branch + 将创建者加入 project_members。"""
    project = Project(
        name=data.name,
        description=data.description,
        git_url=data.git_url,
        script_base_path=data.script_base_path,
        qa_repo=_normalize_qa_repo(data.qa_repo),
    )
    session.add(project)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise ConflictError(code="PROJECT_NAME_EXISTS", message="项目名称已存在")

    # 默认分支配置
    default_branch = Branch(
        project_id=project.id,
        name="default",
        branch="main",
    )
    session.add(default_branch)

    # 默认环境 + 默认全局变量。项目化之后新项目是空的，不铺的话第一件事
    # 是手工建 4 个环境，而且 TEST_LANGUAGE 不存在会让 t() 少一层兜底。
    # 默认环境**不带变量** —— 预埋假凭证比没凭证更坏，见 project_defaults 的说明。
    from app.services.project_defaults import build_defaults
    envs, gvars = build_defaults(project.id)
    session.add_all(envs)
    session.add_all(gvars)

    # 创建者自动加入为项目管理员
    member = ProjectMember(
        project_id=project.id,
        user_id=creator.id,
        role="manager",
    )
    session.add(member)
    await session.flush()
    await session.refresh(project)
    return project


async def list_projects(session: AsyncSession, current_user: User) -> list[Project]:
    """查询项目列表。admin 看全部，普通用户看已绑定的。"""
    if current_user.role == "admin":
        stmt = select(Project).order_by(Project.created_at.desc())
    else:
        stmt = (
            select(Project)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(ProjectMember.user_id == current_user.id)
            .order_by(Project.created_at.desc())
        )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_project(session: AsyncSession, project_id: uuid.UUID) -> Project:
    """根据 ID 获取项目，不存在抛 NotFoundError。"""
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise NotFoundError(code="PROJECT_NOT_FOUND", message="项目不存在")
    return project


@audit_log(action="update", target_type="project")
async def update_project(
    session: AsyncSession, project_id: uuid.UUID, data: UpdateProjectRequest
) -> Project:
    """更新项目信息。"""
    project = await get_project(session, project_id)
    if data.description is not None:
        project.description = data.description
    if data.git_url is not None:
        project.git_url = data.git_url
    if data.script_base_path is not None:
        project.script_base_path = data.script_base_path
    if data.qa_repo is not None:
        # url 传空串就是清空（见 _normalize_qa_repo）；整个字段不传才是"不动它"
        project.qa_repo = _normalize_qa_repo(data.qa_repo)
    await session.flush()
    await session.refresh(project)
    return project


async def set_qa_repo(
    session: AsyncSession, project_id: uuid.UUID, cfg: QaRepoConfig
) -> Project:
    """只改 QA 仓配置。

    单独开一条是因为 QA 仓在「QA 场景清单」页里维护，而整项目更新
    （`PUT /api/projects/{id}`）要系统 admin —— 项目管理员接自己项目的 QA 仓
    不该去求平台管理员。url 传空串 = 取消配置（见 _normalize_qa_repo）。
    """
    project = await get_project(session, project_id)
    project.qa_repo = _normalize_qa_repo(cfg)
    await session.flush()
    await session.refresh(project)
    return project


# 有这些数据就不许删项目 —— 都是人写出来的、删掉不可再生的东西。
# (表, 计数用的 SQL, 中文名)
_DELETE_BLOCKERS = [
    # 用例挂在 branch 上，不是直接挂 project；UI 脚本和接口脚本是用例的字段，
    # 数够用例就等于把它们都数进来了
    ("cases", "SELECT count(*) FROM cases c JOIN branches b ON c.branch_id = b.id"
              " WHERE b.project_id = :pid", "用例"),
    ("knowledge_entries", "SELECT count(*) FROM knowledge_entries WHERE project_id = :pid", "知识条目"),
    ("requirement_docs", "SELECT count(*) FROM requirement_docs WHERE project_id = :pid", "需求文档"),
]


async def assert_project_deletable(session: AsyncSession, project_id: uuid.UUID) -> None:
    """项目下还有人工资产就拒绝删除。

    外键现在全是 ON DELETE CASCADE（见 zzd0fkc1 迁移），也就是说删项目会连用例、
    脚本、知识一起物理删掉且不可恢复 —— 实测有项目挂着 330 条用例，仅凭一个
    Popconfirm 就能一键抹掉。所以这里挡住，不提供 force 之类的绕过口子：
    要删项目，先自己把用例清空或转移，让删除这个动作本身变成低风险操作。

    计划和报告不算门槛：那是执行痕迹，重跑能再生，跟着项目一起清掉是预期行为。
    """
    found: list[str] = []
    for _table, sql, label in _DELETE_BLOCKERS:
        n = await session.scalar(text(sql), {"pid": str(project_id)})
        if n:
            found.append(f"{label} {n} 条")
    if found:
        raise ConflictError(
            code="PROJECT_NOT_EMPTY",
            message=f"项目下还有{'、'.join(found)}，不能删除。请先清空或转移后再删项目。",
            detail="；".join(found),
        )


@audit_log(action="delete", target_type="project")
async def delete_project(session: AsyncSession, project_id: uuid.UUID) -> None:
    """删除项目（CASCADE 自动清理 branches / 成员 / 场景 / 计划 / 报告等子表）。

    ⚠ 删之前必须过 assert_project_deletable —— 级联现在是真的会把所有子表删干净。
    """
    project = await get_project(session, project_id)
    await assert_project_deletable(session, project_id)
    await session.delete(project)
    await session.flush()
