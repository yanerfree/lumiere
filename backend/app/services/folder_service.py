"""用例目录服务 — 树形查询、创建、删除"""
import uuid

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.case import Case, CaseFolder


async def list_folder_tree(session: AsyncSession, branch_id: uuid.UUID) -> list[dict]:
    """返回目录树（含每个节点的用例计数）。

    返回格式: [{ id, name, path, depth, caseCount, children: [...] }, ...]
    """
    # 查所有目录
    result = await session.execute(
        select(CaseFolder)
        .where(CaseFolder.branch_id == branch_id)
        .order_by(CaseFolder.depth, CaseFolder.sort_order, CaseFolder.name)
    )
    folders = result.scalars().all()

    # 每个 folder 的**直属**用例数。父目录的合计由下面的 _sum_counts 递归汇总，
    # 别在这里再累加一遍 —— 那会把子目录的数算两次
    # （实测「项目管理」1 + 子目录 11 会显示成 23）。
    count_result = await session.execute(
        select(Case.folder_id, func.count(Case.id))
        .where(Case.branch_id == branch_id, Case.deleted_at.is_(None))
        .group_by(Case.folder_id)
    )
    count_map = {row[0]: row[1] for row in count_result.all()}

    # 构建树
    node_map = {}
    roots = []

    for f in folders:
        node = {
            "id": str(f.id),
            "name": f.name,
            "path": f.path,
            "depth": f.depth,
            "caseCount": count_map.get(f.id, 0),
            "children": [],
        }
        node_map[f.id] = node

        if f.parent_id and f.parent_id in node_map:
            node_map[f.parent_id]["children"].append(node)
        else:
            roots.append(node)

    # 从叶子到根汇总 caseCount
    def _sum_counts(node):
        total = node["caseCount"]
        for child in node["children"]:
            total += _sum_counts(child)
        node["caseCount"] = total
        return total

    for root in roots:
        _sum_counts(root)

    return roots


async def create_folder(
    session: AsyncSession,
    branch_id: uuid.UUID,
    name: str,
    parent_id: uuid.UUID | None = None,
) -> dict:
    """创建目录（模块或子模块）。"""
    name_upper = name.upper()

    if parent_id:
        # 子目录：查父目录获取 path 和 depth
        result = await session.execute(
            select(CaseFolder).where(CaseFolder.id == parent_id)
        )
        parent = result.scalar_one_or_none()
        if parent is None:
            raise NotFoundError(code="FOLDER_NOT_FOUND", message="父目录不存在")
        path = f"{parent.path}/{name_upper}"
        depth = parent.depth + 1
    else:
        # 顶级模块
        path = name_upper
        depth = 1

    if depth > 4:
        raise ValidationError(code="MAX_DEPTH", message="目录最多 4 层")

    # 检查同分支下 path 是否重复
    existing = await session.execute(
        select(CaseFolder).where(
            CaseFolder.branch_id == branch_id,
            CaseFolder.path == path,
        )
    )
    if existing.scalar_one_or_none():
        raise ConflictError(code="FOLDER_EXISTS", message="目录已存在")

    folder = CaseFolder(
        branch_id=branch_id,
        parent_id=parent_id,
        name=name_upper,
        path=path,
        depth=depth,
    )
    session.add(folder)
    await session.flush()
    await session.refresh(folder)

    return {
        "id": str(folder.id),
        "name": folder.name,
        "path": folder.path,
        "depth": folder.depth,
        "caseCount": 0,
        "children": [],
    }


async def delete_folder(session: AsyncSession, folder_id: uuid.UUID) -> None:
    """删除目录。该目录及子目录下有活跃用例时拒绝，否则级联删除子目录。"""
    result = await session.execute(
        select(CaseFolder).where(CaseFolder.id == folder_id)
    )
    folder = result.scalar_one_or_none()
    if folder is None:
        raise NotFoundError(code="FOLDER_NOT_FOUND", message="目录不存在")

    descendant_ids = await _collect_descendant_ids(session, folder_id)
    all_ids = [folder_id] + descendant_ids

    # 检查该目录及所有子目录下是否有活跃用例
    case_count = await session.execute(
        select(func.count(Case.id)).where(
            Case.folder_id.in_(all_ids),
            Case.deleted_at.is_(None),
        )
    )
    count = case_count.scalar_one()
    if count > 0:
        raise ValidationError(
            code="FOLDER_NOT_EMPTY",
            message=f"该目录下存在 {count} 条用例，请先移动或删除",
        )

    # 解除软删除用例的 folder_id 引用
    from sqlalchemy import update, delete as sql_delete
    await session.execute(
        update(Case).where(Case.folder_id.in_(all_ids)).values(folder_id=None)
    )

    # 清除子目录的 parent_id 引用后批量删除
    await session.execute(
        update(CaseFolder).where(CaseFolder.parent_id.in_(all_ids)).values(parent_id=None)
    )
    await session.flush()
    await session.execute(sql_delete(CaseFolder).where(CaseFolder.id.in_(all_ids)))
    await session.flush()


async def _collect_descendant_ids(session: AsyncSession, parent_id: uuid.UUID) -> list:
    """递归收集所有子目录 ID。"""
    result = await session.execute(
        select(CaseFolder.id).where(CaseFolder.parent_id == parent_id)
    )
    child_ids = [row[0] for row in result.all()]
    all_ids = list(child_ids)
    for cid in child_ids:
        all_ids.extend(await _collect_descendant_ids(session, cid))
    return all_ids


async def list_empty_folders(session: AsyncSession, branch_id: uuid.UUID) -> list[dict]:
    """空目录：没有任何用例（含软删的）、也没有子目录。

    为什么会攒出一堆：目录是建用例时按 module 顺带创建的，而彻底删除用例
    从不回收目录（已在 case_service 修掉），加上手动建了没用的。实测某库
    93 个目录里 51 个从来没装过用例 —— 打开用例导航一屏 (0)，
    分不清哪些是真模块。
    """
    from sqlalchemy import func

    from app.models.case import Case

    rows = (await session.execute(
        select(CaseFolder).where(CaseFolder.branch_id == branch_id)
    )).scalars().all()

    out = []
    for f in rows:
        cases = (await session.execute(
            select(func.count()).select_from(Case).where(Case.folder_id == f.id)
        )).scalar_one()
        children = (await session.execute(
            select(func.count()).select_from(CaseFolder).where(CaseFolder.parent_id == f.id)
        )).scalar_one()
        if cases or children:
            continue
        out.append({
            "id": str(f.id), "name": f.name, "path": f.path, "depth": f.depth,
            "createdAt": f.created_at.isoformat() if f.created_at else None,
        })
    return out


async def prune_empty_folders(
    session: AsyncSession, branch_id: uuid.UUID, folder_ids: list[uuid.UUID]
) -> int:
    """删掉名单里**当前确实为空**的目录。返回删掉几个。

    服务端重判一次而不是信名单：页面拉到名单和点确认之间，别人可能刚往里
    放了用例。删错一个目录会连带把用例的归属抹掉，宁可少删。
    """
    from sqlalchemy import func

    from app.models.case import Case

    if not folder_ids:
        return 0
    rows = (await session.execute(
        select(CaseFolder).where(
            CaseFolder.id.in_(folder_ids), CaseFolder.branch_id == branch_id
        )
    )).scalars().all()

    pruned = 0
    for f in rows:
        cases = (await session.execute(
            select(func.count()).select_from(Case).where(Case.folder_id == f.id)
        )).scalar_one()
        children = (await session.execute(
            select(func.count()).select_from(CaseFolder).where(CaseFolder.parent_id == f.id)
        )).scalar_one()
        if cases or children:
            continue
        await session.delete(f)
        pruned += 1
    await session.flush()
    return pruned
