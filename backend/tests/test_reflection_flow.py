"""回推时的场景级反问 —— 「这个场景验证点是否合理、有没有相关场景没覆盖、是否清晰」。

三档规则全是**步骤级**的（这条断言恒真、那步没验效果），而上面这三件规则判不了，
只有 CC 答得上 —— 它手上有需求和代码。平台能做的是把它糊不过去的事实摊出来。

口径（用户拍的）：**照常入库不拦**。
理由：入库了才能跑，而变异验证/断言咬合这些最硬的证据只能从真跑里来；
拦得住"没答"也拦不住"乱答"，而乱答比不答更糟（有了答案，评审和人都会信它）。
不答的代价放在后面 —— 交付门禁不放行、评审按"自证不全"扣分。
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

from app.services.review import reflect


def _case(title="模块改名-子模块路径跟着改、用例编号不变", reflections=None, note=None):
    return SimpleNamespace(title=title, reflections=reflections,
                           expected_confirmed_note=note, folder_id="f", id="c",
                           api_scenario=None, ui_scenario=None,
                           api_status="completed", ui_status="draft")


def test_四问都在且各有事实():
    """空泛地问一遍没用 —— 每问都要带平台数出来的数，CC 才糊不过去。"""
    qs = reflect.build(_case(), {"steps": [
        {"name": "前置: 登录", "method": "POST", "assertions": [{"type": "status"}]},
        {"name": "验证: 读回", "method": "GET", "assertions": [{"type": "status"}, {"type": "body_field"}]},
    ]}, [{"caseCode": "TC-A-1", "title": "用例增删在列表中如实反映"}])
    keys = [q["key"] for q in qs]
    assert keys == ["verificationPoints", "clarity", "coverage", "expectationSource"]
    for q in qs:
        assert q["facts"], f"{q['key']} 没带事实"
        assert q["question"]


def test_验证点那问要摊出承诺和断言数():
    qs = reflect.build(_case(), {"steps": [
        {"name": "操作: 改名", "method": "PATCH", "assertions": [{"type": "status"}] * 4}]}, [])
    f = qs[0]["facts"]
    assert f["标题承诺"] == "子模块路径跟着改、用例编号不变"
    assert f["断言"] == 4 and f["接口步骤"] == 1


def test_覆盖那问列出邻居和本模块缺的类别():
    qs = reflect.build(_case(), {"steps": []},
                       [{"caseCode": "TC-A-1", "title": "禁用服务后网关停止转发，重新启用后恢复调用"}])
    f = qs[2]["facts"]
    assert "禁用服务后网关停止转发，重新启用后恢复调用" in f["同模块已有"]
    miss = f["本模块还没人写的常见类别"]
    assert "状态切回来（禁用→启用、下线→上线）" not in miss, "邻居里已经有状态切回来了"
    assert any("越权" in m for m in miss)


def test_第一条用例也不空着问():
    qs = reflect.build(_case(), {"steps": []}, [])
    assert qs[2]["facts"]["同模块已有"] == "这是本模块第一条"


def test_标题里有且字会点出来():
    qs = reflect.build(_case(title="改名且删除都要生效"), {"steps": []}, [])
    assert "可能塞了两个功能" in qs[1]["question"]


# ── 答案的收与用 ─────────────────────────────────────────────────

def test_答案只做形状不校验内容():
    """校验内容是评审的活。这里拦"答得不好"只会逼出更漂亮的空话。"""
    out = reflect.normalize({"verificationPoints": "第 8 步", "clarity": "一件事",
                             "coverage": "邻居没有改名", "expectationSource": "按需求"})
    assert out["verificationPoints"] == "第 8 步" and out["answeredAt"] and out["by"]


def test_三问答全才算答完():
    assert reflect.pending(_case(reflections=None)) is True
    assert reflect.pending(_case(reflections={"verificationPoints": "第 8 步"})) is True
    assert reflect.pending(_case(reflections={
        "verificationPoints": "第 8 步", "coverage": "不重复在改名", "expectationSource": "按需求"})) is False


def test_回推不因为没答就拦():
    """判据规范 ①：拦只给"必然出错"。没答反问不影响这条能不能跑。"""
    from app.mcp.tools import sync
    src = inspect.getsource(sync._reflect_block)
    assert "return {" in src and "error" not in src.split("reflectionPending")[0][-300:]
    body = inspect.getsource(sync.sync_orchestrated_scenario)
    assert "_reflect_block" in body
    i = body.index("_reflect_block")
    assert "return {" in body[:i], "反问要在入库之后才装配 —— 拦在前面就是变相硬拦"


def test_提示分三档():
    """混着给的后果实测过：CC 看到"提示 5 条"不知道哪条必须处理，于是一条都不处理。"""
    from app.mcp.tools.sync import _tiered
    out = _tiered([{"kind": "tautology_assertion", "value": "恒真"},
                   {"kind": "status_only_assertion", "value": "只断状态码"}])
    assert out["mustLook"] == ["恒真"] and out["fyi"] == ["只断状态码"]
    assert "别默认忽略" in out["mustLookHint"]


def test_没答的代价落在门禁和评审上():
    from app.mcp.tools import deliverable
    from app.services.review import reviewer
    assert "reflection_pending" in inspect.getsource(deliverable.check_deliverable)
    assert "reflections" in reviewer._SYSTEM and "自证不全" in reviewer._SYSTEM


def test_评审拿答案核对说的和断言的():
    """这是答案真正的用处 —— 评审原来只能从标题猜"这条想验什么"。"""
    assert "说的和断言对不上" in reviewer_system()


def reviewer_system():
    from app.services.review import reviewer
    return reviewer._SYSTEM


def test_MCP工具的返回标注必须跟真实返回一致():
    """活体自测撞出来的：`lum_list_api_tests` 标注 `-> list[dict]`、实际返回
    `{scenarios, total, usage}`。FastMCP 照标注生成 outputSchema，于是 CC 真调时
    客户端拿数组的 schema 去校验对象，直接
    `RuntimeError: Invalid structured content returned by tool ...`。

    **页面侧永远发现不了**（它不校验 schema），只有 MCP 那条路会炸 ——
    所以这条得静态兜住，不能靠"下次有人调到"。
    """
    import ast
    import pathlib

    bad = []
    for f in pathlib.Path("app/mcp/tools").glob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if node.name.startswith("_") or not node.returns:
                continue
            ann = ast.unparse(node.returns)
            shapes = set()
            for r in [n for n in ast.walk(node) if isinstance(n, ast.Return) and n.value]:
                if isinstance(r.value, ast.Dict):
                    shapes.add("dict")
                elif isinstance(r.value, (ast.List, ast.ListComp)):
                    shapes.add("list")
            if ann.startswith("list") and "dict" in shapes:
                bad.append(f"{f.name}:{node.name} 标注 {ann} 但 return 了 dict")
            if ann == "dict" and shapes == {"list"}:
                bad.append(f"{f.name}:{node.name} 标注 dict 但 return 了 list")
    assert not bad, "标注和真实返回对不上（MCP 那条路会炸）：" + "；".join(bad)
