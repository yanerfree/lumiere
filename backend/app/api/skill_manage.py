"""平台内置 Skill 管理 API — 列表 + 查看 + 编辑 + 下载。

这里管的是 `app/skills/preset/` 下的 `tb-*`，**平台侧执行**：skill_executor 读
SKILL.md 当 prompt 喂后端 LLM，每个都在「AI 能力→模型」绑档位。
项目侧（Claude Code 执行）的 skill 不在这儿，见 api/project_skills.py。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.core.exceptions import AppError
from app.services.skill_registry import SKILL_NAME_RE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/skills", tags=["skills"])

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills" / "preset"


def _skill_dir(skill_name: str) -> Path:
    """把 skill 名解析成目录，并挡住路径穿越。

    skill_name 直接拼进文件路径，没有这道闸门 `../../` 就能读写仓库任意文件。
    双保险：先白名单正则，再校验解析后的真实路径仍在 SKILLS_DIR 底下。
    """
    if not SKILL_NAME_RE.match(skill_name or ""):
        raise AppError(
            code="INVALID_SKILL_NAME",
            message=f"skill 名 '{skill_name}' 不合法",
            status_code=400,
        )
    resolved = (SKILLS_DIR / skill_name).resolve()
    if resolved != SKILLS_DIR.resolve() and SKILLS_DIR.resolve() not in resolved.parents:
        raise AppError(code="INVALID_SKILL_NAME", message="非法路径", status_code=400)
    return resolved


def _safe_ts(version_ts: str) -> str:
    """版本时间戳同样会拼进文件名，同样要挡。格式固定 YYYYmmdd_HHMMSS。"""
    if not re.fullmatch(r"\d{8}_\d{6}", version_ts or ""):
        raise AppError(
            code="INVALID_VERSION",
            message=f"版本号 '{version_ts}' 格式不合法",
            status_code=400,
        )
    return version_ts


@router.get("")
async def list_skills():
    skills = []
    if SKILLS_DIR.exists():
        for skill_dir in sorted(SKILLS_DIR.iterdir()):
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                content = skill_file.read_text(encoding="utf-8")
                import re, yaml
                fm_match = re.match(r"^---\s*\n(.+?)\n---\s*\n", content, re.DOTALL)
                meta = yaml.safe_load(fm_match.group(1)) if fm_match else {}
                skills.append({
                    "name": meta.get("name", skill_dir.name),
                    "description": meta.get("description", ""),
                    "version": meta.get("version", 1),
                    "tools": meta.get("tools", []),
                    "contentLength": len(content),
                })
    return {"data": skills}


@router.get("/preset/{skill_name}/download")
async def download_skill(skill_name: str):
    skill_file = _skill_dir(skill_name) / "SKILL.md"
    if not skill_file.exists():
        return PlainTextResponse(f"Skill '{skill_name}' not found", status_code=404)
    return PlainTextResponse(
        skill_file.read_text(encoding="utf-8"),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="SKILL.md"'},
    )


@router.get("/{skill_name}")
async def get_skill(skill_name: str):
    skill_file = _skill_dir(skill_name) / "SKILL.md"
    if not skill_file.exists():
        return {"data": None, "error": f"Skill '{skill_name}' 不存在"}
    return {"data": {"name": skill_name, "content": skill_file.read_text(encoding="utf-8")}}


@router.put("/{skill_name}")
async def update_skill(skill_name: str, body: dict):
    skill_file = _skill_dir(skill_name) / "SKILL.md"
    if not skill_file.exists():
        return {"data": None, "error": f"Skill '{skill_name}' 不存在"}
    content = body.get("content", "")
    if not content.strip():
        return {"error": "内容不能为空"}

    # 保存旧版本到 versions 目录
    versions_dir = _skill_dir(skill_name) / "versions"
    versions_dir.mkdir(exist_ok=True)
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    old_content = skill_file.read_text(encoding="utf-8")
    (versions_dir / f"SKILL_{ts}.md").write_text(old_content, encoding="utf-8")

    skill_file.write_text(content, encoding="utf-8")
    logger.info("Skill '%s' updated, length=%d, version saved as %s", skill_name, len(content), ts)
    return {"data": {"name": skill_name, "updated": True, "versionBackup": ts}}


@router.get("/{skill_name}/versions")
async def list_skill_versions(skill_name: str):
    versions_dir = _skill_dir(skill_name) / "versions"
    if not versions_dir.exists():
        return {"data": []}
    versions = []
    for f in sorted(versions_dir.glob("SKILL_*.md"), reverse=True):
        versions.append({
            "filename": f.name,
            "timestamp": f.name.replace("SKILL_", "").replace(".md", ""),
            "size": f.stat().st_size,
        })
    return {"data": versions}


@router.post("/{skill_name}/rollback/{version_ts}")
async def rollback_skill(skill_name: str, version_ts: str):
    skill_file = _skill_dir(skill_name) / "SKILL.md"
    version_file = _skill_dir(skill_name) / "versions" / f"SKILL_{_safe_ts(version_ts)}.md"
    if not version_file.exists():
        return {"error": f"版本 {version_ts} 不存在"}

    # 先备份当前版本
    versions_dir = _skill_dir(skill_name) / "versions"
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    (versions_dir / f"SKILL_{ts}.md").write_text(skill_file.read_text(encoding="utf-8"), encoding="utf-8")

    # 回滚
    skill_file.write_text(version_file.read_text(encoding="utf-8"), encoding="utf-8")
    logger.info("Skill '%s' rolled back to %s", skill_name, version_ts)
    return {"data": {"name": skill_name, "rolledBackTo": version_ts}}
