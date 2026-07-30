"""用例导入服务 — 解析 tea-cases.json 并导入到指定分支配置"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, func
from app.core.audit import audit_log
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case, CaseFolder


async def _get_or_create_folder(
    session: AsyncSession,
    branch_id: uuid.UUID,
    module: str,
    submodule: str | None,
) -> tuple[uuid.UUID | None, int, int]:
    """获取或创建 module/submodule 对应的目录。返回 (folder_id, new_modules, new_submodules)。"""
    new_modules = 0
    new_submodules = 0

    if not module:
        return None, 0, 0

    module_upper = module.upper()
    module_path = module_upper

    # 查找或创建 module 目录（depth=1）
    result = await session.execute(
        select(CaseFolder).where(
            CaseFolder.branch_id == branch_id,
            CaseFolder.path == module_path,
        )
    )
    module_folder = result.scalar_one_or_none()
    if module_folder is None:
        module_folder = CaseFolder(
            branch_id=branch_id,
            parent_id=None,
            name=module_upper,
            path=module_path,
            depth=1,
        )
        session.add(module_folder)
        await session.flush()
        new_modules = 1

    if not submodule:
        return module_folder.id, new_modules, 0

    # 查找或创建 submodule 目录（depth=2）
    sub_upper = submodule.upper()
    sub_path = f"{module_path}/{sub_upper}"

    result = await session.execute(
        select(CaseFolder).where(
            CaseFolder.branch_id == branch_id,
            CaseFolder.path == sub_path,
        )
    )
    sub_folder = result.scalar_one_or_none()
    if sub_folder is None:
        sub_folder = CaseFolder(
            branch_id=branch_id,
            parent_id=module_folder.id,
            name=sub_upper,
            path=sub_path,
            depth=2,
        )
        session.add(sub_folder)
        await session.flush()
        new_submodules = 1

    return sub_folder.id, new_modules, new_submodules


def _module_tag(module: str) -> str:
    """模块名 → case_code 里的模块段（大写字母数字，≤8 位）。

    英文/数字直接用；含中文则取拼音首字母。都取不出来才退回 "MOD"。
    """
    import re

    tag = (module or "").upper().replace("/", "-").replace(" ", "")
    if re.match(r"^[A-Z0-9_-]+$", tag) and tag:
        return tag[:8]

    try:
        from pypinyin import Style, lazy_pinyin

        initials = "".join(lazy_pinyin(module, style=Style.FIRST_LETTER)).upper()
        tag = re.sub(r"[^A-Z0-9]", "", initials)
    except Exception:
        tag = ""
    if not tag:
        tag = re.sub(r"[^A-Z0-9]", "", (module or "").upper())
    return (tag or "MOD")[:8]


async def _next_case_code(
    session: AsyncSession, branch_id: uuid.UUID, module: str
) -> str:
    """生成下一个 case_code: TC-{MODULE}-{seq5}。

    中文模块名取拼音首字母（订阅管理→DYGL、服务管理→FWGL）。
    此前是把非 A-Z0-9 字符全 sub 掉，中文模块名会被清空而落到硬编码兜底 "SVC"——
    结果所有中文模块都叫 TC-SVC-，既看不出属于哪个模块，还共用同一条编号序列
    （订阅管理和服务管理互相抢号）。
    """
    module_tag = _module_tag(module)
    prefix = f"TC-{module_tag}-"

    # 查询当前分支下该模块的最大序号
    result = await session.execute(
        select(func.max(Case.case_code)).where(
            Case.branch_id == branch_id,
            Case.case_code.like(f"{prefix}%"),
        )
    )
    max_code = result.scalar_one_or_none()

    if max_code:
        try:
            seq = int(max_code.replace(prefix, "")) + 1
        except ValueError:
            seq = 1
    else:
        seq = 1

    return f"{prefix}{seq:05d}"


async def import_cases(
    session: AsyncSession, branch_id: uuid.UUID, cases_data: list[dict]
) -> dict:
    """导入用例主函数。

    返回摘要: { "new": N, "updated": M, "removed": K, "skipped": L,
                "new_modules": X, "new_submodules": Y, "skipped_reasons": [...] }
    """
    new_count = 0
    updated_count = 0
    skipped_count = 0
    skipped_reasons = []
    total_new_modules = 0
    total_new_submodules = 0

    # 收集本次导入的所有 tea_id
    imported_tea_ids = set()

    for item in cases_data:
        # 校验必填字段
        tea_id = item.get("tea_id")
        title = item.get("title")
        case_type = item.get("type")
        module = item.get("module")

        if not all([tea_id, title, case_type, module]):
            missing = [f for f in ("tea_id", "title", "type", "module") if not item.get(f)]
            skipped_count += 1
            skipped_reasons.append(f"tea_id={tea_id or '?'}: 缺必填字段 {', '.join(missing)}")
            continue

        imported_tea_ids.add(tea_id)

        # 获取或创建目录
        submodule = item.get("submodule")
        folder_id, nm, ns = await _get_or_create_folder(session, branch_id, module, submodule)
        total_new_modules += nm
        total_new_submodules += ns

        # 按 tea_id 查找已有用例
        result = await session.execute(
            select(Case).where(
                Case.branch_id == branch_id,
                Case.tea_id == tea_id,
            )
        )
        existing = result.scalar_one_or_none()

        # 已删除的记录直接硬删，当作不存在
        if existing is not None and existing.deleted_at is not None:
            await session.delete(existing)
            await session.flush()
            existing = None

        script_ref = item.get("script_ref", {}) or {}
        script_func = script_ref.get("func")
        script_class = script_ref.get("class")
        if script_func and script_class and "::" not in script_func:
            script_func = f"{script_class}::{script_func}"
        priority = item.get("priority", "P2")
        tags = item.get("tags", [])
        preconditions = item.get("preconditions")
        steps = item.get("steps")
        expected_result = item.get("expected_result")
        variables_used = item.get("variables_used")
        api_scenario = item.get("api_scenario")
        ui_scenario = item.get("ui_scenario")
        api_scenario_status = item.get("api_scenario_status", "draft")
        ui_scenario_status = item.get("ui_scenario_status", "draft")

        if existing is None:
            # 新增
            case_code = await _next_case_code(session, branch_id, module)
            case = Case(
                branch_id=branch_id,
                case_code=case_code,
                tea_id=tea_id,
                title=title,
                type=case_type,
                folder_id=folder_id,
                priority=priority,
                preconditions=preconditions,
                steps=steps or [],
                expected_result=expected_result,
                variables_used=variables_used,
                api_scenario=api_scenario,
                ui_scenario=ui_scenario,
                api_scenario_status=api_scenario_status if api_scenario else "draft",
                ui_scenario_status=ui_scenario_status if ui_scenario else "draft",
                source="imported",
                automation_status="automated" if script_ref.get("file") else "pending",
                script_ref_file=script_ref.get("file"),
                script_ref_func=script_func,
                remark=", ".join(tags) if tags else None,
            )
            session.add(case)
            new_count += 1
        else:
            # 更新元数据
            existing.title = title
            existing.priority = priority
            existing.folder_id = folder_id
            existing.script_ref_file = script_ref.get("file")
            existing.script_ref_func = script_func
            existing.remark = ", ".join(tags) if tags else existing.remark
            if preconditions is not None:
                existing.preconditions = preconditions
            if steps is not None:
                existing.steps = steps
            if expected_result is not None:
                existing.expected_result = expected_result
            if variables_used is not None:
                existing.variables_used = variables_used
            if api_scenario is not None:
                existing.api_scenario = api_scenario
            if ui_scenario is not None:
                existing.ui_scenario = ui_scenario
            if script_ref.get("file"):
                existing.automation_status = "automated"
            updated_count += 1

    await session.flush()

    # 软删除本次未出现的已导入用例（下次同步若重新出现会自动新增）
    result = await session.execute(
        select(Case).where(
            Case.branch_id == branch_id,
            Case.source == "imported",
            Case.deleted_at.is_(None),
        )
    )
    all_imported = result.scalars().all()

    removed_count = 0
    now = datetime.now(timezone.utc)
    for case in all_imported:
        if case.tea_id and case.tea_id not in imported_tea_ids:
            case.deleted_at = now
            case.folder_id = None
            removed_count += 1

    if removed_count > 0:
        await session.flush()

    return {
        "new": new_count,
        "updated": updated_count,
        "removed": removed_count,
        "skipped": skipped_count,
        "new_modules": total_new_modules,
        "new_submodules": total_new_submodules,
        "skipped_reasons": skipped_reasons,
    }
