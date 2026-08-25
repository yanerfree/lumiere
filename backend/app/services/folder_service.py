"""用例目录服务 — 树形查询、创建、改名、删除"""
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


async def _flat_module_list(session: AsyncSession, branch_id: uuid.UUID) -> list[dict]:
    """全分支模块清单 `[{name, parent, count, id}]` —— intake_gate 的查重判据要的就是这个。

    和 MCP 那边 `_module_tree()` 同一份口径：**页面建模块和 CC 建模块必须撞同一堵墙**，
    否则「顶层和子模块下各一个」这种裂法只是从 MCP 换个入口重新发生一次
    （事故现场那两个空的顶层模块就是人在页面上建的 —— MCP 早就拦得住了）。
    """
    folders = (await session.execute(
        select(CaseFolder).where(CaseFolder.branch_id == branch_id)
    )).scalars().all()
    by_id = {f.id: f for f in folders}
    counts = dict((await session.execute(
        select(Case.folder_id, func.count(Case.id))
        .where(Case.branch_id == branch_id, Case.deleted_at.is_(None))
        .group_by(Case.folder_id)
    )).all())
    return [{
        "id": str(f.id),
        "name": f.name,
        "parent": by_id[f.parent_id].name if f.parent_id and f.parent_id in by_id else None,
        # 上级的 id。合并要的是「挪到**上级**下」（挪过去撞同名才触发合并），
        # 挪到同名那个自己下面只会多套一层目录 —— 第一版就是这么错的。
        "parentId": str(f.parent_id) if f.parent_id else None,
        "count": counts.get(f.id, 0),
    } for f in folders]


async def list_split_modules(session: AsyncSession, branch_id: uuid.UUID) -> list[dict]:
    """存量的「同一个模块被摆到两处」。页面顶上那条提示读它。

    新建这一侧已经拦住了（见 `create_folder`），但**存量裂口没人会想起来去搜一遍** ——
    网关那个项目里顶层的「本租户订阅(0)」「跨租户订阅(1)」和「订阅管理」下的同名模块
    就这么并存了半个月，两边都不完整，谁看导航都以为是两个模块。
    """
    from app.services import intake_gate

    tree = await _flat_module_list(session, branch_id)
    by_key: dict[str, list[dict]] = {}
    for n in tree:
        by_key.setdefault(intake_gate._norm_module(n["name"]), []).append(n)

    out = []
    for s in intake_gate.find_split_modules(tree):
        nodes = by_key.get(intake_gate._norm_module(s["name"]), [])
        tops = [n for n in nodes if not n["parent"]]
        subs = [n for n in nodes if n["parent"]]
        if not tops or not subs:
            continue
        out.append({
            "name": s["name"],
            "hint": s["hint"],
            "top": {"id": tops[0]["id"], "name": tops[0]["name"], "caseCount": tops[0]["count"]},
            "under": [{"id": n["id"], "parent": n["parent"], "parentId": n["parentId"],
                       "caseCount": n["count"]} for n in subs],
        })
    return out


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
        raise ConflictError(
            code="FOLDER_EXISTS",
            message=f"「{name.strip()}」已经在{'这个模块下' if parent_id else '顶层'}了 —— "
                    f"直接往它里面加用例，别再建一个。")

    # **和 MCP 撞同一堵墙**：同名模块「顶层一个、某模块下一个」是裂库的那一刀
    # （intake_gate.check_module_placement 规则 4）。这道闸原来只装在 MCP 回推口上，
    # 页面这一侧完全没有 —— 事故现场那两个空的顶层模块就是从这儿建出来的。
    #
    # 想合并存量的两处，走 `move_folder(..., merge=True)`，不要再建第三个。
    from app.services import intake_gate
    parent_name = parent.name if parent_id else None
    errors, _warns = intake_gate.check_module_placement(
        name.strip(), await _flat_module_list(session, branch_id), parent_name)
    if errors:
        raise ConflictError(code="MODULE_SPLIT", message=errors[0])

    folder = CaseFolder(
        branch_id=branch_id,
        parent_id=parent_id,
        # name 存人写的原样（"LLM Providers"），path 存大写（匹配键）。
        # 原来 name 也强制大写，页面上一律 SHOUTING，还没有任何地方能改回来。
        name=name.strip(),
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


def rewrite_child_path(child_path: str, old_path: str, new_path: str) -> str:
    """把子目录的 path 前缀换成新的。抽成纯函数是为了能直接测**只换前缀这一段**。

    坑在 `str.replace`：模块 `LLM` 改名，子目录 `LLM/LLM CALL` 会被换成两处，
    变成 `新名/新名 CALL`。所以只切一次、且只认「old_path + /」这个边界。
    """
    if child_path == old_path:
        return new_path
    prefix = old_path + "/"
    if not child_path.startswith(prefix):
        return child_path          # 不是它的子孙，一个字都不动
    return new_path + "/" + child_path[len(prefix):]


async def rename_folder(session: AsyncSession, branch_id: uuid.UUID,
                        folder_id: uuid.UUID, new_name: str) -> dict:
    """给模块/子模块改名，并把**跟着这个名字走的东西一起改**。

    改名是显示层的事，匹配层不能跟着晃：
      · `path` 一律大写，它是匹配键 —— CC 回推时按 `module` 字符串找目录
        （import_service._get_or_create_folder 按 path 匹配）。
      · `name` 存人写的原样，页面和导出显示它。
        所以「LLM PROVIDERS」改成「LLM Providers」只动显示，CC 照旧命中同一个目录；
        真改成别的词才会动 path。

    一起改的：自己 + 所有子目录的 path、同名的接口场景目录。
    **不改的：用例编号。** 编号里的模块前缀是生成时算的（TC-LLMPROVI-00001），
    而编号是 CC 回推、脚本文件名、报告、跨分支引用共同的锚点 —— 改了等于
    把已经发出去的引用全断掉。返回值里把这件事说清楚，别让人以为漏改了。
    """
    name = (new_name or "").strip()
    if not name:
        raise ValidationError(code="INVALID_NAME", message="目录名不能为空")
    if "/" in name:
        raise ValidationError(code="INVALID_NAME", message="目录名不能含 /")
    if len(name) > 100:
        raise ValidationError(code="INVALID_NAME", message="目录名最长 100 字")

    folder = (await session.execute(
        select(CaseFolder).where(CaseFolder.id == folder_id,
                                 CaseFolder.branch_id == branch_id)
    )).scalar_one_or_none()
    if folder is None:
        raise NotFoundError(code="FOLDER_NOT_FOUND", message="目录不存在")

    old_name, old_path = folder.name, folder.path
    seg = name.upper()
    parent_path = old_path.rsplit("/", 1)[0] if "/" in old_path else None
    new_path = f"{parent_path}/{seg}" if parent_path else seg

    if new_path != old_path:
        clash = (await session.execute(
            select(CaseFolder).where(CaseFolder.branch_id == branch_id,
                                     CaseFolder.path == new_path)
        )).scalar_one_or_none()
        if clash is not None:
            raise ConflictError(code="FOLDER_EXISTS", message=f"同级下已有「{name}」")

    folder.name = name
    folder.path = new_path

    # 记下旧名。CC 手上还是旧词（它的 module 字符串写在自己的笔记/脚本里），
    # 没有别名它会另建一个旧名目录 —— 同一个模块裂成两个，用例分散在两边。
    olds = [x for x in (folder.former_names or []) if x != seg]
    if old_name.upper() != seg and old_name.upper() not in olds:
        olds.append(old_name.upper())
    folder.former_names = olds[-10:] or None      # 只留最近 10 个，够用且不无限长

    moved = 0
    if new_path != old_path:
        for child in (await session.execute(
            select(CaseFolder).where(CaseFolder.branch_id == branch_id,
                                     CaseFolder.path.startswith(old_path + "/"))
        )).scalars().all():
            child.path = rewrite_child_path(child.path, old_path, new_path)
            moved += 1

    # 接口场景目录跟用例模块同名（CC 回推时按模块名建）。不一起改，
    # 用例侧叫新名、接口场景侧还挂在旧名下，同一个模块看着像两个。
    from app.models.api_test_folder import ApiTestFolder
    api_renamed = 0
    for af in (await session.execute(
        select(ApiTestFolder).where(ApiTestFolder.branch_id == branch_id,
                                    func.upper(ApiTestFolder.name) == old_name.upper())
    )).scalars().all():
        af.name = name
        api_renamed += 1

    cases = (await session.execute(
        select(func.count(Case.id)).where(Case.folder_id == folder_id,
                                          Case.deleted_at.is_(None))
    )).scalar_one()

    await session.flush()
    return {
        "id": str(folder_id),
        "name": name,
        "path": new_path,
        "oldName": old_name,
        "childFoldersUpdated": moved,
        "apiTestFoldersRenamed": api_renamed,
        "cases": cases,
        "matchKeyChanged": new_path != old_path,
        "caseCodesUnchanged": True,
    }


async def move_folder(session: AsyncSession, branch_id: uuid.UUID,
                      folder_id: uuid.UUID,
                      new_parent_id: uuid.UUID | None,
                      merge: bool = False) -> dict:
    """把模块挪到另一个上级下（或挪回顶层）。目标位置已有同名模块时**合并**。

    为什么必须有这个：查重能拦住"再建一个"，但拦不住已经裂了的那两处。
    页面上原来只有改名和删除 —— 而删除只允许空目录，于是
    「顶层的跨租户订阅(1) + 订阅管理下的跨租户订阅(7)」这种裂口**在界面上无解**：
    改名解决不了归属，删不掉（有用例），只能去数据库里改。

    `merge=False` 时目标已有同名 → 抛 `FOLDER_MERGE_REQUIRED`，并在 message 里
    说清会搬多少条。**不默默合并** —— 合并会改用例的归属目录，
    人得先看到数量再点第二次。（用例编号一个字不动，见 `rename_folder` 的说明。）
    """
    folder = (await session.execute(
        select(CaseFolder).where(CaseFolder.id == folder_id,
                                 CaseFolder.branch_id == branch_id)
    )).scalar_one_or_none()
    if folder is None:
        raise NotFoundError(code="FOLDER_NOT_FOUND", message="目录不存在")

    if new_parent_id == folder_id:
        raise ValidationError(code="INVALID_PARENT", message="不能把模块挪到它自己下面")

    parent = None
    if new_parent_id:
        parent = (await session.execute(
            select(CaseFolder).where(CaseFolder.id == new_parent_id,
                                     CaseFolder.branch_id == branch_id)
        )).scalar_one_or_none()
        if parent is None:
            raise NotFoundError(code="FOLDER_NOT_FOUND", message="目标上级模块不存在")
        # 挪到自己的子孙下面 = 把这一支从树上摘下来，用例再也翻不到
        if parent.path == folder.path or parent.path.startswith(folder.path + "/"):
            raise ValidationError(code="INVALID_PARENT",
                                  message="不能把模块挪到它自己的子模块下面")
        # **挪到同名模块下面 = 想合并但点错了地方。**
        # 实测踩到：合并按钮传的是同名那个的 id，结果建出
        # 「订阅管理/跨租户订阅/跨租户订阅」——套了一层空壳，比裂着更难看懂。
        # 合并的正确姿势是挪到**它的上级**下，撞同名时才会触发合并。
        from app.services import intake_gate as _ig
        if _ig._norm_module(parent.name) == _ig._norm_module(folder.name):
            up = "顶层" if parent.parent_id is None else "它的上级模块"
            raise ValidationError(
                code="NESTED_SAME_NAME",
                message=f"「{parent.name}」下面再放一个同名的「{folder.name}」只会多套一层空壳。"
                        f"你要的应该是**合并**：把它挪到{up}（也就是「{parent.name}」所在的那一层），"
                        f"撞上同名时系统会问你要不要把用例并过去。")

    if folder.parent_id == new_parent_id:
        raise ValidationError(code="SAME_PARENT", message="它已经在这个位置了")

    seg = folder.path.rsplit("/", 1)[-1]
    new_path = f"{parent.path}/{seg}" if parent else seg
    new_depth = (parent.depth + 1) if parent else 1

    # 深度要按**整棵子树**算：自己没超但子模块超了，一样是坏的
    descendants = (await session.execute(
        select(CaseFolder).where(CaseFolder.branch_id == branch_id,
                                 CaseFolder.path.startswith(folder.path + "/"))
    )).scalars().all()
    max_rel = max((d.depth - folder.depth for d in descendants), default=0)
    if new_depth + max_rel > 4:
        raise ValidationError(code="MAX_DEPTH",
                              message=f"挪过去会到第 {new_depth + max_rel} 层，目录最多 4 层")

    target = (await session.execute(
        select(CaseFolder).where(CaseFolder.branch_id == branch_id,
                                 CaseFolder.path == new_path)
    )).scalar_one_or_none()

    if target is not None and not merge:
        cases_here = (await session.execute(
            select(func.count(Case.id)).where(Case.folder_id == folder.id,
                                              Case.deleted_at.is_(None))
        )).scalar_one()
        where = f"「{parent.name}」下" if parent else "顶层"
        raise ConflictError(
            code="FOLDER_MERGE_REQUIRED",
            message=f"{where}已经有「{target.name}」了。合并会把这边的 {cases_here} 条用例"
                    f"搬到那个模块下（用例编号不变），然后删掉这个空模块。确认合并？")

    moved_cases = 0
    merged_into = None
    if target is not None:
        merged_into = {"id": str(target.id), "name": target.name, "path": target.path}
        moved_cases = await _merge_into(session, branch_id, folder, target)
    else:
        old_path = folder.path
        folder.parent_id = new_parent_id
        folder.path = new_path
        folder.depth = new_depth
        for d in descendants:
            d.path = rewrite_child_path(d.path, old_path, new_path)
            # 深度按 path 的段数重算。用「相对差」硬加会错：整支挪动时基准变了，
            # 算错一次整棵子树的 depth 全是错的，而 depth 上还挂着 <=4 的约束。
            d.depth = d.path.count("/") + 1

    await session.flush()
    return {
        "id": str(folder_id) if merged_into is None else merged_into["id"],
        "name": folder.name if merged_into is None else merged_into["name"],
        "path": new_path,
        "movedCases": moved_cases,
        "mergedInto": merged_into,
        "childFolders": len(descendants),
        "caseCodesUnchanged": True,
    }


async def _merge_into(session: AsyncSession, branch_id: uuid.UUID,
                      src: CaseFolder, dst: CaseFolder) -> int:
    """把 src 整支并进 dst：用例改挂 dst，子模块递归并过去，最后删掉 src。

    递归是必须的：`A/X` 并进 `B/X` 时，`A/X/Y` 和 `B/X/Y` 可能都存在 ——
    只改 parent_id 会撞 path 唯一约束（`uq_folder_branch_path`）。
    """
    from sqlalchemy import update

    moved = (await session.execute(
        select(func.count(Case.id)).where(Case.folder_id == src.id)
    )).scalar_one()
    await session.execute(
        update(Case).where(Case.folder_id == src.id).values(folder_id=dst.id)
    )

    children = (await session.execute(
        select(CaseFolder).where(CaseFolder.parent_id == src.id)
    )).scalars().all()
    for child in children:
        seg = child.path.rsplit("/", 1)[-1]
        twin = (await session.execute(
            select(CaseFolder).where(CaseFolder.branch_id == branch_id,
                                     CaseFolder.path == f"{dst.path}/{seg}")
        )).scalar_one_or_none()
        if twin is not None:
            moved += await _merge_into(session, branch_id, child, twin)
        else:
            old = child.path
            child.parent_id = dst.id
            child.path = f"{dst.path}/{seg}"
            child.depth = child.path.count("/") + 1
            for d in (await session.execute(
                select(CaseFolder).where(CaseFolder.branch_id == branch_id,
                                         CaseFolder.path.startswith(old + "/"))
            )).scalars().all():
                d.path = rewrite_child_path(d.path, old, child.path)
                d.depth = d.path.count("/") + 1

    # src 的名字挂到 dst 的别名上 —— CC 手上还是老写法（module="跨租户订阅"），
    # 没有别名它下次回推又会在顶层建一个，合并等于白做。
    olds = [x for x in (dst.former_names or [])]
    for alias in [src.path.rsplit("/", 1)[-1], src.name.upper()]:
        if alias and alias != dst.path.rsplit("/", 1)[-1] and alias not in olds:
            olds.append(alias)
    dst.former_names = olds[-10:] or None

    await session.flush()
    await session.delete(src)
    await session.flush()
    return moved


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
