"""模块（用例目录）的摆放：查重、挪位置、合并。

## 事故现场（2026-08-24 用户截图）

网关管理系统里，顶层有「本租户订阅(0)」「跨租户订阅(1)」，同时「订阅管理」下面
又有「本租户订阅(8)」「跨租户订阅(7)」—— **同一个东西摆在两处，用例劈成两半**。

两个缺口各修一半，缺一个都不行：

1. **建的时候没人拦**。intake_gate 的查重（规则 1-4）只装在 MCP 回推口上，
   页面上「+ 新建模块」压根不过那道闸 —— 那两个空的顶层模块就是从页面建出来的。
2. **裂了之后没法修**。页面只有改名和删除，删除又只允许空目录，
   而「跨租户订阅(1)」有一条用例 —— 于是这个裂口在界面上**无解**，只能进数据库改。

## 还踩过的坑（第一版合并按钮的真错）

合并按钮把 `parentId` 传成了**同名那个模块自己的 id**，于是挪出了
`订阅管理/跨租户订阅/跨租户订阅` —— 套了一层空壳，比裂着更难看懂。
合并的正确姿势是挪到**它的上级**下，路径撞上同名才触发合并。
所以 `move_folder` 现在硬拦"挪到同名模块下面"这一步。
"""
import inspect

from app.services import folder_service, intake_gate


def _src(fn):
    return inspect.getsource(fn)


def test_页面建模块也过查重闸():
    """create_folder 必须调 check_module_placement —— 不然 MCP 拦得住的裂法，
    人在页面上照样能建出来（事故现场那两个空顶层模块就是这么来的）。"""
    src = _src(folder_service.create_folder)
    assert "check_module_placement" in src, "页面建模块没过查重闸"
    assert "MODULE_SPLIT" in src, "拦下来要有专门的错误码，前端才能给出正确提示"


def test_同位置已有同名要说清楚该干什么():
    """「目录已存在」这四个字什么都没说。人接着会做的事是改个名再建一个 ——
    于是同一个模块两个写法。必须直接告诉他：往现成那个里面加。"""
    src = _src(folder_service.create_folder)
    assert "直接往它里面加用例" in src


def test_有挪位置的入口():
    """没有它，裂开的两处在界面上无解：改名解决不了归属，删除只允许空目录。"""
    assert hasattr(folder_service, "move_folder"), "没有挪模块的入口"
    sig = inspect.signature(folder_service.move_folder)
    assert "new_parent_id" in sig.parameters, "挪位置得能指定新上级"
    assert "merge" in sig.parameters, "目标已有同名时要能合并"


def test_合并要先问再做():
    """合并会改用例的归属目录。第一次调用只回一句"会搬 N 条，确认？"，
    人点过第二次才真搬 —— 不默默合并。"""
    src = _src(folder_service.move_folder)
    assert "FOLDER_MERGE_REQUIRED" in src, "没有『要不要合并』这一问"
    assert "条用例" in src, "问的时候得说清会搬几条，光问『确认』等于没问"


def test_不许挪到同名模块下面():
    """第一版合并按钮就是这么错的：挪出了「订阅管理/跨租户订阅/跨租户订阅」。
    这不是"合并"，是多套一层空壳 —— 而人以为已经合并好了。"""
    src = _src(folder_service.move_folder)
    assert "NESTED_SAME_NAME" in src, "没拦『挪到同名模块下面』"
    assert "_norm_module" in src, "得按归一化名字比，不然「LLM-X」和「LLM X」漏判"
    assert "上级" in src, "拦下来必须告诉他正确做法是挪到上级"


def test_不许挪到自己的子树下():
    """挪进自己的子孙 = 把这一支从树上摘下来，里面的用例再也翻不到。"""
    src = _src(folder_service.move_folder)
    assert "startswith(folder.path" in src


def test_深度按整棵子树算():
    """自己没超但子模块超了，一样违反 depth <= 4 的库约束。"""
    src = _src(folder_service.move_folder)
    assert "max_rel" in src and "MAX_DEPTH" in src


def test_合并递归_不然撞唯一约束():
    """`A/X` 并进 `B/X` 时，`A/X/Y` 和 `B/X/Y` 可能都存在 ——
    只改 parent_id 会撞 uq_folder_branch_path。"""
    src = _src(folder_service._merge_into)
    assert "_merge_into(session" in src, "子模块没有递归合并"


def test_合并把旧名挂成别名_否则回推会把它复活():
    """CC 手上还是老模块名（写在它自己的笔记和脚本里）。没有别名/回落，
    下一次回推又在顶层建一个，合并等于白做。"""
    assert "former_names" in _src(folder_service._merge_into)

    from app.services import import_service
    assert hasattr(import_service, "_merged_elsewhere"), "回推没有『它已经被并走了』这条回落"
    src = _src(import_service._merged_elsewhere)
    assert "len(rows) != 1" in src, "多处同名时归属说不清，必须只认唯一命中"
    assert "endswith" in src and "former_names" in src, "两种命中写法都要认"

    # 回落必须真的挂在建目录之前
    got = _src(import_service._get_or_create_folder)
    assert got.index("_merged_elsewhere") < got.index("new_modules = 1"), \
        "回落挂在建目录之后 = 没生效"


def test_存量裂口平台自己指出来():
    """规则 4 只能拦住新建。存量的裂口得平台主动说 ——
    没人会想起来去搜一遍"有没有同名模块摆在两处"。"""
    assert hasattr(folder_service, "list_split_modules")
    src = _src(folder_service.list_split_modules)
    assert "find_split_modules" in src, "没复用 intake_gate 的判据，两边口径会漂"
    assert '"parentId"' in _src(folder_service._flat_module_list), \
        "裂口清单必须给出上级的 id —— 前端要拿它当合并目标（传成同名那个自己就套壳了）"
