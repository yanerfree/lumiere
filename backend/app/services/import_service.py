"""用例导入服务 — 解析 tea-cases.json 并导入到指定分支配置"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, func, text
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

    # **先 strip**。回推里带一个尾空格（"平台自身 "）就会建出第二个同名模块 ——
    # 页面上两行长得一模一样，谁都看不出为什么用例分散在两边。实测踩到过。
    module = module.strip()
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
        # 精确路径没命中，再看有没有哪个顶级目录**改名前**叫这个。
        # 人在页面上把「LLM PROVIDERS」改成「模型供应商」之后，CC 手上还是旧词，
        # 直接建新目录的话同一个模块就裂成两个了。
        module_folder = await _by_former_name(session, branch_id, module_upper, None)
    if module_folder is None:
        module_folder = CaseFolder(
            branch_id=branch_id,
            parent_id=None,
            name=module,          # 显示名存原样，path 才是大写匹配键
            path=module_path,
            depth=1,
        )
        session.add(module_folder)
        await session.flush()
        new_modules = 1

    if not submodule:
        return module_folder.id, new_modules, 0

    # 查找或创建 submodule 目录（depth=2）
    submodule = submodule.strip()
    if not submodule:
        return module_folder.id, new_modules, 0
    sub_upper = submodule.upper()
    # 路径从**父目录当前的 path** 拼，不是从传进来的字符串拼 ——
    # 父目录可能是通过旧名命中的，用旧名拼出来的路径谁都不匹配。
    sub_path = f"{module_folder.path}/{sub_upper}"

    result = await session.execute(
        select(CaseFolder).where(
            CaseFolder.branch_id == branch_id,
            CaseFolder.path == sub_path,
        )
    )
    sub_folder = result.scalar_one_or_none()
    if sub_folder is None:
        sub_folder = await _by_former_name(session, branch_id, sub_upper, module_folder.id)
    if sub_folder is None:
        sub_folder = CaseFolder(
            branch_id=branch_id,
            parent_id=module_folder.id,
            name=submodule,
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


async def _by_former_name(session: AsyncSession, branch_id: uuid.UUID,
                          name_upper: str, parent_id: uuid.UUID | None):
    """按「改名前的名字」找目录。找不到返回 None。

    别名只在**同一层、同一个父**下认：顶级模块跟别人的子模块重名是常事
    （「订阅管理」既可能是顶级模块，也可能是别的模块下的子模块）。
    """
    q = select(CaseFolder).where(
        CaseFolder.branch_id == branch_id,
        CaseFolder.former_names.contains([name_upper]),
    )
    q = q.where(CaseFolder.parent_id == parent_id) if parent_id else q.where(CaseFolder.parent_id.is_(None))
    return (await session.execute(q)).scalars().first()


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

    # 事务级 advisory lock，按 (branch, 模块前缀) 排队。
    #
    # 只靠"撞唯一约束再重试"救不回并发：并发的几个请求都还没提交，回滚后重算
    # MAX 依然看不到彼此的行，会一直撞同一个号直到重试耗尽（实测 8 并发挂 1 个）。
    # advisory lock 持有到**提交**才释放，所以后来者拿到锁时，前一个的行已经可见，
    # MAX 自然往前走。锁粒度是"这条分支的这个模块"，不影响别的模块并行建。
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"case_code:{branch_id}:{prefix}"},
    )

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
    """导入用例主函数。**只新增和更新，从不删除任何东西。**

    这里原来是全量同步：传一个只含 1 条用例的 Excel，会把该分支所有
    source='imported' 的用例软删除、并把 folder_id 清成 NULL（原值不留、
    恢复不回来）。实测点一次删掉了 3 条无关用例，而界面上只写「导入」。

    第一版修法是"改成默认不删 + 显式勾选"，被评审否掉了，理由成立：
    **一个勾选框能软删几百条用例，破坏性动作不该藏在便利入口里。**
    要"以这个文件为准整体覆盖"是另一件事（迁移），该是独立入口 +
    先预览将删除哪些 + 二次确认，不是导入顺手带的一个开关。

    文件里没有的用例，如实报在 notInFile 里让人知道，但一条都不动。

    返回摘要: { "new": N, "updated": M, "notInFile": K, "skipped": L,
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

    # 只统计，不动手。告诉人"有几条不在这个文件里"，删不删由他另外去做。
    stale = [c for c in all_imported if c.tea_id and c.tea_id not in imported_tea_ids]

    return {
        "new": new_count,
        "updated": updated_count,
        # 把"文件里没有的那几条"如实报出来 —— 人知道有这回事就够了，
        # 这个入口不负责删。
        "notInFile": len(stale),
        "notInFileSample": [c.title for c in stale[:5]],
        "skipped": skipped_count,
        "new_modules": total_new_modules,
        "new_submodules": total_new_submodules,
        "skipped_reasons": skipped_reasons,
    }
