"""模块改名 —— 「这个模块建议可以修改，改了之后应用到的都一起改」。

改名前是没有入口的：目录名建了就定死，而且 name 被强制大写，
CC 回推一个 `module="LLM Providers"` 进来，列表上永远显示 `LLM PROVIDERS`。

改名要分清两层，混了就出事：
  · `path` 是**匹配键**（全大写）—— CC 回推按模块字符串找目录，路径一变就找不到，
    下次回推会另建一个同名目录，同一个模块裂成两个。
  · `name` 是**显示名**（人写的原样）—— 页面、导出、接口场景目录用它。
只调整大小写时 path 不动，CC 照旧命中；换成别的词才动 path。

不跟着改的只有一样：**用例编号**。编号是 CC 回推、脚本、报告共用的锚点。
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace

import pytest

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.folder_service import rename_folder, rewrite_child_path


# ── 纯函数：只换前缀那一段 ────────────────────────────────────────

def test_只换前缀不换同名的其他段():
    """`str.replace` 会把 `LLM/LLM CALL` 换成两处，变成 `新名/新名 CALL`。"""
    assert rewrite_child_path("LLM/LLM CALL", "LLM", "GATEWAY") == "GATEWAY/LLM CALL"


def test_自己也算():
    assert rewrite_child_path("LLM", "LLM", "GATEWAY") == "GATEWAY"


def test_同前缀但不是子孙的一个字都不动():
    """`LLM PROVIDERS` 不是 `LLM` 的子目录 —— 按字符串前缀判会把它一起改掉。"""
    assert rewrite_child_path("LLM PROVIDERS", "LLM", "GATEWAY") == "LLM PROVIDERS"


def test_深层子孙保留剩下的路径():
    assert rewrite_child_path("A/B/C", "A/B", "A/X") == "A/X/C"


# ── 服务：假 session 走一遍 ───────────────────────────────────────

class _Res:
    def __init__(self, rows, scalar=None):
        self._rows, self._scalar = rows, scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._scalar if self._scalar is not None else len(self._rows)


class _Session:
    """按顺序喂：查自己 → 查重名 → 查子目录 → 查接口场景目录 → 数用例。"""

    def __init__(self, *batches):
        self._queue = list(batches)

    async def execute(self, _stmt):
        return self._queue.pop(0) if self._queue else _Res([])

    async def flush(self):
        pass


BID = uuid.uuid4()
FID = uuid.uuid4()


def _folder(name="LLM PROVIDERS", path="LLM PROVIDERS", depth=1):
    return SimpleNamespace(id=FID, branch_id=BID, name=name, path=path, depth=depth,
                           former_names=None)


@pytest.mark.asyncio
async def test_只改大小写时匹配键不动():
    """「LLM PROVIDERS」→「LLM Providers」只是显示好看点。
    如果这也把 path 改了，CC 下次回推 module="LLM Providers" 照旧算出
    大写路径能命中 —— 但**子目录路径白改一遍**，而且改名记录里看着像结构动过。
    """
    f = _folder()
    out = await rename_folder(_Session(_Res([f]), _Res([]), _Res([]), _Res([]), _Res([], 3)),
                              BID, FID, "LLM Providers")
    assert f.name == "LLM Providers", "显示名要按人写的存"
    assert f.path == "LLM PROVIDERS", "匹配键不能动"
    assert out["matchKeyChanged"] is False
    assert out["childFoldersUpdated"] == 0


@pytest.mark.asyncio
async def test_真改名时子目录路径一起改():
    f = _folder()
    kids = [SimpleNamespace(path="LLM PROVIDERS/KEY"), SimpleNamespace(path="LLM PROVIDERS/KEY/X")]
    out = await rename_folder(_Session(_Res([f]), _Res([]), _Res(kids), _Res([]), _Res([], 2)),
                              BID, FID, "模型供应商")
    assert f.path == "模型供应商"
    assert [k.path for k in kids] == ["模型供应商/KEY", "模型供应商/KEY/X"]
    assert out["childFoldersUpdated"] == 2 and out["matchKeyChanged"] is True


@pytest.mark.asyncio
async def test_子模块改名只动自己那一段():
    f = _folder(name="KEY", path="LLM PROVIDERS/KEY", depth=2)
    out = await rename_folder(_Session(_Res([f]), _Res([]), _Res([]), _Res([]), _Res([], 0)),
                              BID, FID, "密钥")
    assert f.path == "LLM PROVIDERS/密钥", "父路径必须留着，否则子模块被提到顶级"
    assert out["path"] == "LLM PROVIDERS/密钥"


@pytest.mark.asyncio
async def test_同级重名拒掉():
    """放过去就撞 uq_folder_branch_path，报出来的是数据库约束名，人看不懂。"""
    f = _folder()
    with pytest.raises(ConflictError):
        await rename_folder(_Session(_Res([f]), _Res([_folder(name="别人", path="订阅管理")])),
                            BID, FID, "订阅管理")


@pytest.mark.asyncio
async def test_同名的接口场景目录一起改():
    """接口场景目录按模块名建（sync 里 ApiTestFolder(name=folder_name)）。
    只改用例侧，同一个模块在两个页面上叫两个名字。"""
    f = _folder()
    af = SimpleNamespace(name="LLM PROVIDERS")
    out = await rename_folder(_Session(_Res([f]), _Res([]), _Res([]), _Res([af]), _Res([], 1)),
                              BID, FID, "模型供应商")
    assert af.name == "模型供应商"
    assert out["apiTestFoldersRenamed"] == 1


@pytest.mark.asyncio
async def test_名字里有斜杠拒掉():
    """path 用 / 分层，名字里带 / 会凭空多出一层，子目录从此找不到父。"""
    with pytest.raises(ValidationError):
        await rename_folder(_Session(_Res([_folder()])), BID, FID, "LLM/KEY")


@pytest.mark.asyncio
async def test_空名字拒掉():
    with pytest.raises(ValidationError):
        await rename_folder(_Session(_Res([_folder()])), BID, FID, "   ")


@pytest.mark.asyncio
async def test_跨分支改不了():
    """只按 folder_id 查就能改到别的分支的目录去。"""
    with pytest.raises(NotFoundError):
        await rename_folder(_Session(_Res([])), BID, FID, "随便")


@pytest.mark.asyncio
async def test_不碰用例编号():
    """编号里的模块前缀是生成时算的。改名顺手改编号 =
    CC 手上的编号、脚本文件名、历史报告全部指向不存在的用例。"""
    src = inspect.getsource(rename_folder)
    assert "case_code" not in src and "Case.case_code" not in src
    f = _folder()
    out = await rename_folder(_Session(_Res([f]), _Res([]), _Res([]), _Res([]), _Res([], 5)),
                              BID, FID, "模型供应商")
    assert out["caseCodesUnchanged"] is True, "得明说没改，否则人以为漏了去手改编号"
    assert out["cases"] == 5, "要告诉人这次影响到几条用例"


# ── 接线 ─────────────────────────────────────────────────────────

def test_接口挂上去了():
    from app.api import cases
    src = inspect.getsource(cases)
    assert '@folders_router.patch("/{folder_id}")' in src
    assert "rename_folder" in src


def test_建目录不再强制大写显示名():
    """强制大写是"匹配"的需要，落到 name 上就变成页面一律 SHOUTING。"""
    from app.services.folder_service import create_folder
    src = inspect.getsource(create_folder)
    assert "name=name.strip()" in src, "显示名要存人写的原样"
    assert "path = path" not in src.replace("path=path", "")  # path 仍用大写键


# ── 改完名，CC 手上的旧模块名还要命中同一个目录 ──────────────────────

@pytest.mark.asyncio
async def test_改名时记下旧名():
    """CC 的 module 字符串写在它自己的笔记和脚本里，不会因为平台改名就跟着变。
    没有别名，下次回推按旧词**另建一个目录**：同一个模块裂成两个，
    用例分散在两边，页面上看不出任何异常 —— 这是最难查的那种。
    """
    f = _folder()
    f.former_names = None
    await rename_folder(_Session(_Res([f]), _Res([]), _Res([]), _Res([]), _Res([], 0)),
                        BID, FID, "模型供应商")
    assert f.former_names == ["LLM PROVIDERS"]


@pytest.mark.asyncio
async def test_只改大小写不算改名不记别名():
    """「LLM PROVIDERS」→「LLM Providers」大写键没变，记进去是一条永远命中不了的垃圾。"""
    f = _folder()
    f.former_names = None
    await rename_folder(_Session(_Res([f]), _Res([]), _Res([]), _Res([]), _Res([], 0)),
                        BID, FID, "LLM Providers")
    assert not f.former_names


@pytest.mark.asyncio
async def test_改回去时把别名摘掉():
    """A→B→A：别名里留着 A，而 A 现在就是正名 —— 精确匹配先命中，
    留着不致命，但它会让「这个目录曾经叫 A」这句话变成假的。"""
    f = _folder(name="模型供应商", path="模型供应商")
    f.former_names = ["LLM PROVIDERS"]
    await rename_folder(_Session(_Res([f]), _Res([]), _Res([]), _Res([]), _Res([], 0)),
                        BID, FID, "LLM PROVIDERS")
    assert f.former_names == ["模型供应商"]


@pytest.mark.asyncio
async def test_别名最多留十条():
    f = _folder()
    f.former_names = [f"N{i}" for i in range(12)]
    await rename_folder(_Session(_Res([f]), _Res([]), _Res([]), _Res([]), _Res([], 0)),
                        BID, FID, "模型供应商")
    assert len(f.former_names) == 10 and f.former_names[-1] == "LLM PROVIDERS"


def test_回推匹配真的查了别名():
    """只加字段不查等于没做。"""
    from app.services.import_service import _get_or_create_folder
    src = inspect.getsource(_get_or_create_folder)
    assert src.count("_by_former_name") == 2, "模块和子模块两层都要查别名"


def test_子模块路径按父目录当前path拼():
    """父目录是通过旧名命中的，用传进来的旧字符串拼子路径 → 谁都不匹配，
    于是子模块也被重建一份，裂成两个的问题只是从一层挪到了另一层。"""
    from app.services.import_service import _get_or_create_folder
    src = inspect.getsource(_get_or_create_folder)
    assert 'sub_path = f"{module_folder.path}/{sub_upper}"' in src


def test_别名只在同层同父下认():
    """「订阅管理」既可能是顶级模块，也可能是别的模块下的子模块。
    不限制父目录，改名后的别名会把另一棵树上的同名目录也认成自己。"""
    from app.services.import_service import _by_former_name
    src = inspect.getsource(_by_former_name)
    assert "parent_id" in src and "is_(None)" in src
