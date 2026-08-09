import uuid

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.script import Script


async def create_script(
    session: AsyncSession,
    case_id: uuid.UUID,
    script_type: str,
    content: str,
    file_name: str | None = None,
    func_name: str | None = None,
    language: str = "python",
    source: str = "manual",
    commit_sha: str | None = None,
    created_by: uuid.UUID | None = None,
) -> Script:
    """创建或更新脚本。AI 生成的脚本直接覆盖，手动编辑的递增版本。

    版本号走 SELECT MAX + 1，并发回推会撞 uq_script_case_type_version；
    包一层冲突重试（重跑 MAX 就拿到新号），别把原始 IntegrityError 抛给 CC。
    """
    from app.services.concurrency import retry_on_conflict
    return await retry_on_conflict(
        lambda: _create_script_once(
            session, case_id, script_type, content, file_name, func_name,
            language, source, commit_sha, created_by,
        ),
        session, what="回推脚本",
    )


async def _create_script_once(
    session: AsyncSession,
    case_id: uuid.UUID,
    script_type: str,
    content: str,
    file_name: str | None = None,
    func_name: str | None = None,
    language: str = "python",
    source: str = "manual",
    commit_sha: str | None = None,
    created_by: uuid.UUID | None = None,
) -> Script:
    current = await get_active_script(session, case_id, script_type)
    if current and current.content == content:
        return current

    # AI 生成/修复：直接覆盖当前版本，不创建新版本
    if source == "ai_generated" and current:
        current.content = content
        if file_name:
            current.file_name = file_name
        current.language = language
        current.source = source
        await session.flush()
        return current

    # 手动编辑/其他来源：递增版本
    result = await session.execute(
        select(func.max(Script.version)).where(
            Script.case_id == case_id,
            Script.script_type == script_type,
        )
    )
    max_ver = result.scalar_one_or_none() or 0

    if current:
        current.status = "archived"
        # 必须先把归档 flush 出去：SQLAlchemy 一次 flush 里 INSERT 先于 UPDATE，
        # 新的 active 会撞上还没改状态的旧记录，触发 uq_script_one_active。
        await session.flush()

    # 同一条用例被两人同时回推 → 都算出同一个 version → uq_script_case_type_version
    # 拦住第二个。数据不会写坏，但对方拿到的是原始 IntegrityError；重跑 MAX 即可。
    return await _insert_version(
        session, case_id, script_type, max_ver + 1, content,
        file_name, func_name, language, source, commit_sha, created_by,
    )


async def _insert_version(
    session, case_id, script_type, version, content,
    file_name, func_name, language, source, commit_sha, created_by,
) -> Script:
    script = Script(
        case_id=case_id,
        script_type=script_type,
        version=version,
        language=language,
        content=content,
        file_name=file_name,
        func_name=func_name,
        status="active",
        source=source,
        commit_sha=commit_sha,
        created_by=created_by,
    )
    session.add(script)
    await session.flush()
    return script


async def get_active_script(
    session: AsyncSession,
    case_id: uuid.UUID,
    script_type: str,
) -> Script | None:
    """获取当前 active 版本的脚本。"""
    result = await session.execute(
        select(Script).where(
            Script.case_id == case_id,
            Script.script_type == script_type,
            Script.status == "active",
        )
    )
    return result.scalar_one_or_none()


async def list_versions(
    session: AsyncSession,
    case_id: uuid.UUID,
    script_type: str,
) -> list[Script]:
    """列出指定用例+类型的所有版本，按版本号倒序。"""
    result = await session.execute(
        select(Script).where(
            Script.case_id == case_id,
            Script.script_type == script_type,
        ).order_by(Script.version.desc())
    )
    return list(result.scalars().all())


async def get_script_by_id(
    session: AsyncSession,
    script_id: uuid.UUID,
) -> Script | None:
    result = await session.execute(
        select(Script).where(Script.id == script_id)
    )
    return result.scalar_one_or_none()


async def activate_version(
    session: AsyncSession,
    script_id: uuid.UUID,
) -> Script | None:
    """将指定版本设为 active，其他同 case+type 的版本归档。"""
    script = await get_script_by_id(session, script_id)
    if not script:
        return None

    # 归档同 case+type 的其他 active 版本
    result = await session.execute(
        select(Script).where(
            Script.case_id == script.case_id,
            Script.script_type == script.script_type,
            Script.status == "active",
            Script.id != script_id,
        )
    )
    for old in result.scalars().all():
        old.status = "archived"

    script.status = "active"
    await session.flush()
    return script
