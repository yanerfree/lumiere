"""项目 Skill 注册表 —— 校验 / frontmatter 解析 / 打包解包 / upsert。

HTTP（页面、curl）和 MCP（外部 Claude Code）两条写入通道共用这一层，
校验规则只有一份，不会出现「页面拦住了、MCP 放进来了」这种偏差。
"""
from __future__ import annotations

import io
import logging
import re
import tarfile
import uuid
import zipfile

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.skill import Skill, SkillVersion

logger = logging.getLogger(__name__)

# 名字直接当文件名/目录名用（导出成 .claude/skills/<name>/），必须白名单。
# 这条正则也是防路径穿越的唯一闸门 —— 写入通道开放，不能靠调用方自律。
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

MAX_CONTENT_BYTES = 512 * 1024
MAX_FILE_COUNT = 50
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_PATH_DEPTH = 5

KINDS = ("client", "platform")
VISIBILITIES = ("public", "project")


def validate_name(name: str) -> str:
    """校验 skill 名。不合法直接抛，绝不做「清洗后放过」。"""
    name = (name or "").strip()
    if not SKILL_NAME_RE.match(name):
        raise AppError(
            code="INVALID_SKILL_NAME",
            message=(
                f"skill 名 '{name}' 不合法：只允许小写字母、数字、'-'、'_'、'.'，"
                "以字母或数字开头，最长 64 字符"
            ),
            status_code=400,
        )
    if ".." in name:
        raise AppError(code="INVALID_SKILL_NAME", message="skill 名不能包含 '..'", status_code=400)
    return name


def _validate_file_path(path: str) -> str:
    """校验附属文件的相对路径。"""
    p = (path or "").strip().replace("\\", "/")
    if not p:
        raise AppError(code="INVALID_FILE_PATH", message="附属文件路径不能为空", status_code=400)
    if p.startswith("/") or ":" in p:
        raise AppError(code="INVALID_FILE_PATH", message=f"附属文件路径必须是相对路径：{path}", status_code=400)
    segments = [s for s in p.split("/") if s]
    if any(s in ("..", ".") for s in segments):
        raise AppError(code="INVALID_FILE_PATH", message=f"附属文件路径不能包含 '.' 或 '..'：{path}", status_code=400)
    if len(segments) > MAX_PATH_DEPTH:
        raise AppError(
            code="INVALID_FILE_PATH",
            message=f"附属文件路径层级不能超过 {MAX_PATH_DEPTH}：{path}",
            status_code=400,
        )
    if len(p) > 200:
        raise AppError(code="INVALID_FILE_PATH", message=f"附属文件路径过长：{path}", status_code=400)
    return "/".join(segments)


def normalize_files(files: dict | None, content: str) -> dict[str, str]:
    """规范化附属文件字典，并连同 SKILL.md 一起做总量限制。"""
    if not files:
        return {}
    if not isinstance(files, dict):
        raise AppError(code="INVALID_FILES", message="files 必须是 {相对路径: 文本内容} 形式", status_code=400)

    out: dict[str, str] = {}
    for raw_path, raw_text in files.items():
        path = _validate_file_path(str(raw_path))
        if path.upper() == "SKILL.MD":
            # SKILL.md 走 content 字段，放进 files 会出现两份真相
            raise AppError(
                code="INVALID_FILES",
                message="SKILL.md 请放在 content 字段，不要放进 files",
                status_code=400,
            )
        if not isinstance(raw_text, str):
            raise AppError(
                code="INVALID_FILES",
                message=f"附属文件 {path} 的内容必须是文本 —— skill 里不存二进制",
                status_code=400,
            )
        out[path] = raw_text

    if len(out) > MAX_FILE_COUNT:
        raise AppError(
            code="TOO_MANY_FILES",
            message=f"附属文件不能超过 {MAX_FILE_COUNT} 个（当前 {len(out)}）",
            status_code=400,
        )

    total = len(content.encode("utf-8")) + sum(len(v.encode("utf-8")) for v in out.values())
    if total > MAX_TOTAL_BYTES:
        raise AppError(
            code="SKILL_TOO_LARGE",
            message=f"skill 总大小不能超过 {MAX_TOTAL_BYTES // 1024} KB（当前 {total // 1024} KB）",
            status_code=400,
        )
    return out


def validate_content(content: str) -> str:
    content = content or ""
    if not content.strip():
        raise AppError(code="EMPTY_SKILL", message="SKILL.md 内容不能为空", status_code=400)
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise AppError(
            code="SKILL_TOO_LARGE",
            message=f"SKILL.md 不能超过 {MAX_CONTENT_BYTES // 1024} KB",
            status_code=400,
        )
    return content


def parse_frontmatter(content: str) -> dict:
    """解析 SKILL.md 顶部的 YAML frontmatter。解析失败返回 {}，不抛。

    上传方可能压根没写 frontmatter —— 那不该拒收，只是拿不到 name/description 兜底。
    """
    match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", content, re.DOTALL)
    if not match:
        return {}
    try:
        import yaml

        meta = yaml.safe_load(match.group(1))
        return meta if isinstance(meta, dict) else {}
    except Exception as exc:  # noqa: BLE001 — frontmatter 写坏不该让上传失败
        logger.warning("SKILL.md frontmatter 解析失败，忽略：%s", exc)
        return {}


def detect_declared_tools(content: str) -> list[str]:
    """从 frontmatter 的 tools 列表里挑出平台 MCP 工具（tb_ 前缀）。

    用途：一个客户端 skill 如果声明了大量 tb_* 工具，它其实处在边界上
    （既在客户端跑、又强依赖平台数据）。列出来让人看见，不做拦截。
    """
    meta = parse_frontmatter(content)
    tools = meta.get("tools")
    if not isinstance(tools, list):
        return []
    return [str(t) for t in tools if str(t).startswith("tb_")]


# ── 打包 / 解包 ──────────────────────────────────────────────

def pack_bundle(name: str, content: str, files: dict[str, str]) -> bytes:
    """打成 tar.gz，解出来就是一个 <name>/ 目录，可直接落到 .claude/skills/。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def _add(rel_path: str, text: str) -> None:
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name=f"{name}/{rel_path}")
            info.size = len(data)
            info.mode = 0o644
            # mtime 固定为 0：同样内容打出同样的包，便于 CI 里做缓存/比对
            info.mtime = 0
            tar.addfile(info, io.BytesIO(data))

        _add("SKILL.md", content)
        for path, text in sorted(files.items()):
            _add(path, text)
    return buf.getvalue()


def unpack_bundle(filename: str, raw: bytes) -> tuple[str | None, str, dict[str, str]]:
    """解 .zip / .tar.gz，返回 (目录名, SKILL.md 全文, 附属文件)。

    容忍两种结构：包根就是 SKILL.md，或包里套一层 <skill-name>/ 目录。
    """
    fname = (filename or "").lower()
    entries: dict[str, str] = {}

    try:
        if fname.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                names = [n for n in zf.namelist() if not n.endswith("/")]
                if len(names) > MAX_FILE_COUNT + 1:
                    raise AppError(code="TOO_MANY_FILES", message=f"包内文件不能超过 {MAX_FILE_COUNT + 1} 个", status_code=400)
                for n in names:
                    if zf.getinfo(n).file_size > MAX_CONTENT_BYTES:
                        raise AppError(code="SKILL_TOO_LARGE", message=f"包内文件 {n} 过大", status_code=400)
                    entries[n] = zf.read(n).decode("utf-8")
        elif fname.endswith(".tar.gz") or fname.endswith(".tgz"):
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
                members = [m for m in tar.getmembers() if m.isfile()]
                if len(members) > MAX_FILE_COUNT + 1:
                    raise AppError(code="TOO_MANY_FILES", message=f"包内文件不能超过 {MAX_FILE_COUNT + 1} 个", status_code=400)
                for m in members:
                    if m.size > MAX_CONTENT_BYTES:
                        raise AppError(code="SKILL_TOO_LARGE", message=f"包内文件 {m.name} 过大", status_code=400)
                    fh = tar.extractfile(m)
                    if fh is None:
                        continue
                    entries[m.name] = fh.read().decode("utf-8")
        else:
            raise AppError(
                code="INVALID_FILE",
                message="仅接受 .zip / .tar.gz / .tgz，或直接用 JSON 提交 content",
                status_code=400,
            )
    except AppError:
        raise
    except UnicodeDecodeError:
        raise AppError(
            code="INVALID_FILE",
            message="包内含非 UTF-8 文本（二进制？）—— skill 只接受文本文件",
            status_code=400,
        )
    except (zipfile.BadZipFile, tarfile.TarError) as exc:
        raise AppError(code="INVALID_FILE", message=f"压缩包解析失败：{exc}", status_code=400)

    if not entries:
        raise AppError(code="INVALID_FILE", message="包是空的", status_code=400)

    # 找 SKILL.md，确定要不要剥掉外层目录
    skill_key = next((k for k in entries if k.split("/")[-1].upper() == "SKILL.MD"), None)
    if skill_key is None:
        raise AppError(code="MISSING_SKILL_MD", message="包里找不到 SKILL.md", status_code=400)

    prefix_parts = skill_key.split("/")[:-1]
    prefix = "/".join(prefix_parts)
    dir_name = prefix_parts[-1] if prefix_parts else None

    content = entries.pop(skill_key)
    files: dict[str, str] = {}
    for key, text in entries.items():
        rel = key[len(prefix) + 1:] if prefix and key.startswith(prefix + "/") else key
        if not rel or rel.startswith("."):
            continue  # 跳过 .DS_Store / .git 之类
        files[_validate_file_path(rel)] = text

    return dir_name, content, files


# ── 读写 ────────────────────────────────────────────────────

async def get_skill(session: AsyncSession, project_id: uuid.UUID, name: str) -> Skill | None:
    result = await session.execute(
        select(Skill).where(Skill.project_id == project_id, Skill.name == name)
    )
    return result.scalar_one_or_none()


async def list_skills(
    session: AsyncSession,
    project_id: uuid.UUID | None = None,
    kind: str | None = None,
    include_shared: bool = False,
) -> list[Skill]:
    """列 skill。

    include_shared=True 时，除本项目的之外，把其它项目 visibility=public 的也带出来
    —— 这就是「别的项目能取用」的读路径。
    """
    stmt = select(Skill)
    if project_id is not None:
        if include_shared:
            stmt = stmt.where(
                (Skill.project_id == project_id) | (Skill.visibility == "public")
            )
        else:
            stmt = stmt.where(Skill.project_id == project_id)
    elif not include_shared:
        # 既不给项目、又不要共享的 —— 没有意义的查询，返回空比返回全表安全
        return []
    else:
        stmt = stmt.where(Skill.visibility == "public")

    if kind:
        stmt = stmt.where(Skill.kind == kind)

    result = await session.execute(stmt.order_by(Skill.name))
    return list(result.scalars().all())


async def get_shared_skill(
    session: AsyncSession, skill_id: uuid.UUID, requester_project_id: uuid.UUID
) -> Skill:
    """按 id 取一个 skill，用于跨项目取用。

    放行条件：它是 public，或者它本来就属于取用方项目。
    visibility=project 的别人拿不走 —— 这是 visibility 唯一的作用点。
    """
    result = await session.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if skill is None:
        raise AppError(code="SKILL_NOT_FOUND", message="skill 不存在", status_code=404)
    if skill.visibility != "public" and skill.project_id != requester_project_id:
        raise AppError(
            code="SKILL_NOT_SHARED",
            message=f"skill '{skill.name}' 未共享，只有来源项目可取用",
            status_code=403,
        )
    return skill


async def load_project_names(
    session: AsyncSession, project_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """批量取项目名，给共享列表标「这是谁传的」。"""
    if not project_ids:
        return {}
    from app.models.project import Project

    result = await session.execute(
        select(Project.id, Project.name).where(Project.id.in_(project_ids))
    )
    return {row[0]: row[1] for row in result.all()}


async def upsert_skill(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    name: str,
    content: str,
    files: dict | None = None,
    kind: str = "client",
    visibility: str = "public",
    description: str | None = None,
    source: str = "mcp",
    created_by: uuid.UUID | None = None,
    overwrite: bool = True,
) -> tuple[Skill, bool]:
    """写入一个 skill，返回 (skill, 是否新建)。

    覆盖前先把旧内容存进 skill_versions —— 写入通道对外开放，
    一次手滑覆盖必须能翻回来。
    """
    content = validate_content(content)
    name = validate_name(name or str(parse_frontmatter(content).get("name") or ""))
    files = normalize_files(files, content)

    if kind not in KINDS:
        raise AppError(code="INVALID_KIND", message=f"kind 只能是 {'/'.join(KINDS)}", status_code=400)
    if visibility not in VISIBILITIES:
        raise AppError(
            code="INVALID_VISIBILITY",
            message=f"visibility 只能是 {'/'.join(VISIBILITIES)}",
            status_code=400,
        )

    meta = parse_frontmatter(content)
    if not description:
        raw_desc = meta.get("description")
        description = str(raw_desc).strip() if raw_desc else None

    existing = await get_skill(session, project_id, name)
    if existing is not None:
        if not overwrite:
            raise AppError(
                code="SKILL_EXISTS",
                message=f"skill '{name}' 在本项目已存在。要覆盖请传 overwrite=true（旧版本会自动留档）",
                status_code=409,
            )
        session.add(SkillVersion(
            skill_id=existing.id,
            version=existing.version,
            content=existing.content,
            files=existing.files or {},
            note=f"被 {source} 通道覆盖前留档",
        ))
        existing.content = content
        existing.files = files
        existing.description = description
        existing.kind = kind
        existing.visibility = visibility
        existing.source = source
        existing.version = existing.version + 1
        await session.flush()
        logger.info("Skill '%s' 更新至 v%d（project=%s, 通道=%s）", name, existing.version, project_id, source)
        return existing, False

    skill = Skill(
        project_id=project_id,
        name=name,
        kind=kind,
        visibility=visibility,
        description=description,
        content=content,
        files=files,
        version=1,
        source=source,
        created_by=created_by,
    )
    session.add(skill)
    await session.flush()
    logger.info("Skill '%s' 新建（project=%s, 通道=%s）", name, project_id, source)
    return skill, True


def to_summary(skill: Skill) -> dict:
    """列表用的精简表示 —— 不带正文，列表页不需要几百 KB。"""
    return {
        "id": str(skill.id),
        "projectId": str(skill.project_id),
        "name": skill.name,
        "kind": skill.kind,
        "visibility": skill.visibility,
        "description": skill.description or "",
        "version": skill.version,
        "source": skill.source,
        "fileCount": len(skill.files or {}),
        "contentLength": len(skill.content or ""),
        "platformTools": detect_declared_tools(skill.content or ""),
        "createdAt": skill.created_at.isoformat() if skill.created_at else None,
        "updatedAt": skill.updated_at.isoformat() if skill.updated_at else None,
    }


def to_detail(skill: Skill) -> dict:
    data = to_summary(skill)
    data["content"] = skill.content
    data["files"] = skill.files or {}
    return data
