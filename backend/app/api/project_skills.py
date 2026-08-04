"""项目 Skill API —— 客户端(Claude Code)侧执行的 skill，推上来、取下去、管理员可改可删。

URL 空间刻意跟平台内置的 `/api/skills` 分开：
  /api/skills                      → 内置 tb-*（平台侧执行，见 skill_manage.py）
  /api/projects/{pid}/skills       → 项目 skill（客户端侧执行，本文件）
分开的理由不是洁癖：内置 skill 会被 skill_executor 当 prompt 喂给后端 LLM、
还要在「AI 能力→模型」里绑档位；项目 skill 两件都不参与。混一个列表会让
AI 能力页冒出一批绑不上模型的空档位。
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from pydantic import Field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.deps.auth import require_project_role
from app.deps.db import get_db
from app.models.skill import SkillVersion
from app.models.user import User
from app.schemas.common import BaseSchema
from app.services import skill_registry as reg

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}/skills", tags=["skills"])

# 谁能写：项目管理员/开发/测试都行 —— skill 是干活的工具，不该只有管理员能推。
WRITE_ROLES = ("project_admin", "developer", "tester")
# 谁能读：加上 guest，取用是只读行为
READ_ROLES = (*WRITE_ROLES, "guest")
# 谁能删：只有项目管理员。删除不可逆且影响其它取用方。
DELETE_ROLES = ("project_admin",)


class UpsertSkillRequest(BaseSchema):
    name: str | None = Field(default=None, max_length=64, description="不传则取 frontmatter 里的 name")
    content: str = Field(..., min_length=1, description="SKILL.md 全文")
    files: dict[str, str] | None = Field(default=None, description="附属文件 {相对路径: 文本}")
    description: str | None = None
    kind: str = Field(default="client")
    visibility: str = Field(default="public")
    overwrite: bool = True


@router.get("")
async def list_project_skills(
    project_id: uuid.UUID,
    kind: str | None = Query(default=None, description="client / platform"),
    include_shared: bool = Query(default=False, description="带上其它项目共享(public)的"),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*READ_ROLES)),
):
    skills = await reg.list_skills(
        session, project_id=project_id, kind=kind, include_shared=include_shared
    )
    data = []
    for s in skills:
        item = reg.to_summary(s)
        item["own"] = s.project_id == project_id
        data.append(item)
    return {"data": data}


@router.post("")
async def upsert_project_skill(
    project_id: uuid.UUID,
    body: UpsertSkillRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*WRITE_ROLES)),
):
    """JSON 方式写入 —— 页面新建、curl、CI 都走这个。"""
    skill, created = await reg.upsert_skill(
        session,
        project_id=project_id,
        name=body.name or "",
        content=body.content,
        files=body.files,
        kind=body.kind,
        visibility=body.visibility,
        description=body.description,
        source="ui",
        created_by=current_user.id,
        overwrite=body.overwrite,
    )
    await session.commit()
    return {"data": {**reg.to_summary(skill), "created": created}}


@router.post("/upload")
async def upload_project_skill(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    visibility: str = Query(default="public"),
    overwrite: bool = Query(default=True),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*WRITE_ROLES)),
):
    """打包上传整个 skill 目录（.zip / .tar.gz），带 references/ 之类附属文件。"""
    raw = await file.read()
    if len(raw) > reg.MAX_TOTAL_BYTES * 2:
        raise AppError(
            code="FILE_TOO_LARGE",
            message=f"压缩包不能超过 {reg.MAX_TOTAL_BYTES * 2 // 1024} KB",
            status_code=400,
        )

    dir_name, content, files = reg.unpack_bundle(file.filename or "", raw)
    fm_name = str(reg.parse_frontmatter(content).get("name") or "")
    # 优先用 frontmatter 里的 name（那是 skill 的自我声明），其次用目录名
    skill, created = await reg.upsert_skill(
        session,
        project_id=project_id,
        name=fm_name or dir_name or "",
        content=content,
        files=files,
        visibility=visibility,
        source="upload",
        created_by=current_user.id,
        overwrite=overwrite,
    )
    await session.commit()
    return {"data": {**reg.to_summary(skill), "created": created}}


# ── 跨项目取用 ──────────────────────────────────────────────
# 注意：这三条必须注册在 GET/{name} 之前 —— "shared" 本身是合法 skill 名，
# 顺序反了会被当成「查一个叫 shared 的 skill」。
# 路径里的 project_id 是**取用方**（用来鉴权你是某项目成员），
# skill_id 才是被取的那个，它可能属于别的项目。

@router.get("/shared")
async def list_shared_skills(
    project_id: uuid.UUID,
    kind: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*READ_ROLES)),
):
    """全平台共享(visibility=public)的 skill —— 别的项目上传的也在这。"""
    skills = await reg.list_skills(session, project_id=None, kind=kind, include_shared=True)
    project_names = await reg.load_project_names(session, {s.project_id for s in skills})
    return {"data": [
        {
            **reg.to_summary(s),
            "own": s.project_id == project_id,
            "sourceProject": project_names.get(s.project_id, ""),
        }
        for s in skills
    ]}


@router.get("/shared/{skill_id}")
async def get_shared_skill(
    project_id: uuid.UUID,
    skill_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*READ_ROLES)),
):
    skill = await reg.get_shared_skill(session, skill_id, requester_project_id=project_id)
    return {"data": reg.to_detail(skill)}


@router.get("/shared/{skill_id}/bundle")
async def download_shared_skill_bundle(
    project_id: uuid.UUID,
    skill_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*READ_ROLES)),
):
    skill = await reg.get_shared_skill(session, skill_id, requester_project_id=project_id)
    blob = reg.pack_bundle(skill.name, skill.content, skill.files or {})
    return Response(
        content=blob,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{skill.name}.tar.gz"'},
    )


@router.get("/{name}")
async def get_project_skill(
    project_id: uuid.UUID,
    name: str,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*READ_ROLES)),
):
    name = reg.validate_name(name)
    skill = await reg.get_skill(session, project_id, name)
    if skill is None:
        raise AppError(code="SKILL_NOT_FOUND", message=f"skill '{name}' 不存在", status_code=404)
    return {"data": reg.to_detail(skill)}


@router.put("/{name}")
async def update_project_skill(
    project_id: uuid.UUID,
    name: str,
    body: UpsertSkillRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role(*WRITE_ROLES)),
):
    """管理员/成员在页面里改。旧版本自动留档。"""
    name = reg.validate_name(name)
    existing = await reg.get_skill(session, project_id, name)
    if existing is None:
        raise AppError(code="SKILL_NOT_FOUND", message=f"skill '{name}' 不存在", status_code=404)

    skill, _created = await reg.upsert_skill(
        session,
        project_id=project_id,
        name=name,
        content=body.content,
        files=body.files if body.files is not None else existing.files,
        kind=body.kind,
        visibility=body.visibility,
        description=body.description,
        source="ui",
        created_by=current_user.id,
        overwrite=True,
    )
    await session.commit()
    return {"data": reg.to_summary(skill)}


@router.delete("/{name}")
async def delete_project_skill(
    project_id: uuid.UUID,
    name: str,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*DELETE_ROLES)),
):
    name = reg.validate_name(name)
    skill = await reg.get_skill(session, project_id, name)
    if skill is None:
        raise AppError(code="SKILL_NOT_FOUND", message=f"skill '{name}' 不存在", status_code=404)
    await session.delete(skill)  # skill_versions 靠 FK CASCADE 一起走
    await session.commit()
    logger.info("Skill '%s' 已删除（project=%s）", name, project_id)
    return {"data": {"name": name, "deleted": True}}


@router.get("/{name}/bundle")
async def download_project_skill_bundle(
    project_id: uuid.UUID,
    name: str,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*READ_ROLES)),
):
    """下载 tar.gz —— 解出来就是 <name>/ 目录，直接丢进 .claude/skills/。"""
    name = reg.validate_name(name)
    skill = await reg.get_skill(session, project_id, name)
    if skill is None:
        raise AppError(code="SKILL_NOT_FOUND", message=f"skill '{name}' 不存在", status_code=404)
    blob = reg.pack_bundle(skill.name, skill.content, skill.files or {})
    return Response(
        content=blob,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{skill.name}.tar.gz"'},
    )


@router.get("/{name}/versions")
async def list_project_skill_versions(
    project_id: uuid.UUID,
    name: str,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*READ_ROLES)),
):
    name = reg.validate_name(name)
    skill = await reg.get_skill(session, project_id, name)
    if skill is None:
        raise AppError(code="SKILL_NOT_FOUND", message=f"skill '{name}' 不存在", status_code=404)
    result = await session.execute(
        select(SkillVersion)
        .where(SkillVersion.skill_id == skill.id)
        .order_by(SkillVersion.version.desc())
    )
    return {"data": [
        {
            "version": v.version,
            "note": v.note or "",
            "contentLength": len(v.content or ""),
            "fileCount": len(v.files or {}),
            "createdAt": v.created_at.isoformat() if v.created_at else None,
        }
        for v in result.scalars().all()
    ], "currentVersion": skill.version}


@router.post("/{name}/rollback/{version}")
async def rollback_project_skill(
    project_id: uuid.UUID,
    name: str,
    version: int,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_project_role(*WRITE_ROLES)),
):
    name = reg.validate_name(name)
    skill = await reg.get_skill(session, project_id, name)
    if skill is None:
        raise AppError(code="SKILL_NOT_FOUND", message=f"skill '{name}' 不存在", status_code=404)

    result = await session.execute(
        select(SkillVersion).where(
            SkillVersion.skill_id == skill.id, SkillVersion.version == version
        )
    )
    snapshot = result.scalar_one_or_none()
    if snapshot is None:
        raise AppError(code="VERSION_NOT_FOUND", message=f"版本 v{version} 不存在", status_code=404)

    # 回滚也是一次覆盖 —— 当前内容同样要留档，否则回滚本身不可撤销
    session.add(SkillVersion(
        skill_id=skill.id,
        version=skill.version,
        content=skill.content,
        files=skill.files or {},
        note=f"回滚到 v{version} 前留档",
    ))
    skill.content = snapshot.content
    skill.files = snapshot.files or {}
    skill.version = skill.version + 1
    await session.commit()
    logger.info("Skill '%s' 回滚到 v%d（新版本 v%d）", name, version, skill.version)
    return {"data": {"name": name, "rolledBackTo": version, "version": skill.version}}
