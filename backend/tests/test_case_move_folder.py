"""CC 自己搬目录 —— 「这个目录 cc 为什么不能编辑」。

外部 CC 的原话：建 21 条用例时 submodule 传得不一致（3 条漏传落在了模块根目录），
发现之后**改不了**：`tb_update_case` 只有标题/步骤/预期，没有 module/submodule，
只能让人去界面上一条条拖。漏传是笔误，笔误不该每次都惊动人。

三条纪律钉在这里：
  · 目录不存在要自动建（否则"先建目录再搬"两步，CC 会跳过第一步然后失败）
  · 同名门禁按**搬过去之后**的模块判（同名只在同一模块内算重复）
  · **编号不跟着变** —— 编号是回推、脚本、报告、跨分支引用共用的锚点
"""
from __future__ import annotations

import inspect

from app.mcp.tools import test_cases


def test_有module和submodule两个参数():
    sig = inspect.signature(test_cases.update_case)
    assert "module" in sig.parameters and "submodule" in sig.parameters


def test_传下去给了service():
    """service 里已经有"module 变了就重新找/建目录"的逻辑，别在这儿另写一份。"""
    src = inspect.getsource(test_cases.update_case)
    assert "module=module if (module is not None or submodule is not None) else None" in src
    assert "submodule=submodule" in src


def test_同名检查按搬过去之后的模块判():
    """拿旧目录判，会在"搬家顺带改标题"时判错：旧模块里不重名、新模块里重名。"""
    src = inspect.getsource(test_cases.update_case)
    assert "module = module if module is not None else cur_module" in src
    assert src.index("module = module if module is not None else cur_module") \
        < src.index("intake_gate.check_one"), "兜底要发生在门禁之前"


def test_原始入参单独留一份():
    """module 被"当前目录"兜底覆盖之后，就分不出「调用方要搬家」和
    「调用方只是改标题」了 —— 于是每次改标题都会触发一次目录重算。"""
    src = inspect.getsource(test_cases.update_case)
    assert "module_arg = module" in src
    assert 'result["folderPath"]' in src, "搬完要回落点，只回 folderId 没法确认搬对了"


def test_编号不跟着改():
    src = inspect.getsource(test_cases.update_case)
    assert "case_code =" not in src, "改编号 = 把 CC 手上和报告里的引用全断掉"
    assert "caseCodeUnchanged" in src, "要明说没改，否则 CC 以为漏了"


def test_目录不存在会自动建():
    """走的是 import_service._get_or_create_folder（跟建用例同一条路），
    所以 CC 不需要先建目录 —— 也就不会漏掉那一步。"""
    from app.services.case_service import update_case
    assert "_get_or_create_folder" in inspect.getsource(update_case)


def test_工具描述和规范都写了():
    from app.mcp import TOOL_CATALOG
    from app.mcp.tools.sync import _SPEC_CASE
    d = {t["name"]: t["description"] for t in TOOL_CATALOG}["tb_update_case"]
    assert "module" in d and "submodule" in d
    assert "放错目录自己搬" in _SPEC_CASE
    assert "编号不跟着变" in _SPEC_CASE, "不写这句，CC 会以为搬完编号也该跟着改"
