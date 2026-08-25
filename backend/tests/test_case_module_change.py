"""换模块：把一条用例从模块 A 挪到模块 B。

用户原话：「模块我说的不是修改模块名称，我说的是用例需要调整模块，
如现在是模块A，我想调整到模块B中，无法修改」。

## 为什么之前做不到

- 详情页那一行模块是 `ReadonlyProp` —— 纯灰字，点不动。
- 列表的批量条里有归档/优先级/删除，**没有"移动"** —— 而后端
  `batch_cases(action="move")` 一直在，只是页面从来没给过入口。
- 「模块设置」改的是**模块自己的名字/位置**，跟"这条用例归谁"是两件事。

于是建错了模块只有两条路：删了重建（编号变、历史断），或者进数据库改 folder_id。

## 为什么用 folder_id 而不是 module/submodule 名字

① 名字只能表达两层，目录最深四层；
② 按名字走 `_get_or_create_folder` 会**顺手建目录** —— 人在页面上挑的是一个
   已经存在的目录，建出个新的等于把用例挪进一个刚冒出来的空模块；
③ 详情页保存会把整份表单原样回传（含没动的 module/submodule），名字那条路会
   拿两层名字覆盖掉刚挑的三层目录，用例又跳回上一级。
"""
import inspect

from app.services import case_service


def test_单条能换目录且folder_id优先():
    src = inspect.getsource(case_service.update_case)
    assert "data.folder_id is not None" in src, "改一条用例换不了目录"
    # 必须在 module 那条分支**之前**，否则整份表单回传时被名字覆盖
    assert src.index("data.folder_id is not None") < src.index("data.module is not None")
    assert "elif data.module is not None" in src, "两条路要互斥，不然一次请求挪两回"
    assert "FOLDER_NOT_FOUND" in src, "目录不存在还回 200 = 人以为挪好了"


def test_目标目录必须在本分支():
    src = inspect.getsource(case_service.update_case)
    assert "CaseFolder.branch_id == case.branch_id" in src, \
        "不校验分支就能把用例挪到别的分支的目录下"


def test_批量move必须给目录():
    """folder_id=None 会把整批用例的归属清空（从模块树里集体消失），
    而调用方拿到的是 200 + succeeded=N。"""
    src = inspect.getsource(case_service.batch_cases)
    assert "FOLDER_REQUIRED" in src
    assert "CaseFolder.branch_id == branch_id" in src


def test_页面两个入口都在():
    from pathlib import Path
    base = Path(__file__).resolve().parents[2] / "frontend/src/pages/cases"

    detail = (base / "CaseDetail.jsx").read_text(encoding="utf-8")
    assert "把这条用例挪到" in detail, "详情页还是只读的模块"
    assert "folderId" in detail and "setFolderId" in detail

    lst = (base / "CaseManagement.jsx").read_text(encoding="utf-8")
    assert "移动到模块" in lst, "列表没有批量移动"
    assert "action: 'move'" in lst


def test_挪动不改用例编号():
    """编号是 CC 回推、脚本文件名、报告、跨分支引用共同的锚点。
    页面上必须写出来，否则人看到 TC-DYGL- 前缀还在会以为没挪成。"""
    from pathlib import Path
    base = Path(__file__).resolve().parents[2] / "frontend/src/pages/cases"
    assert "不改用例编号" in (base / "CaseDetail.jsx").read_text(encoding="utf-8")
    assert "编号不变" in (base / "CaseManagement.jsx").read_text(encoding="utf-8")

    # 后端也别顺手重算编号
    src = inspect.getsource(case_service.update_case)
    assert "_next_case_code" not in src, "换模块时重算了编号 —— 已发出去的引用全断"


def test_脏检查不受键序影响():
    """快照是两处手写的对象字面量，键序一不一样，JSON 串就不等 →
    页面恒判「有未保存的修改」，点返回必弹确认框。"""
    from pathlib import Path
    detail = (Path(__file__).resolve().parents[2]
              / "frontend/src/pages/cases/CaseDetail.jsx").read_text(encoding="utf-8")
    assert "function stableSnap" in detail
    assert "savedRef.current = stableSnap(vals)" in detail
    assert "const currentSnap = stableSnap({" in detail
