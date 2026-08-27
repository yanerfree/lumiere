"""接口库（api_nodes）的写入必须留痕 —— 封样。

起因：2026-08-27 有人问「接口库还在被 MCP 写吗」，而这张表当时一个字都不记，
只能靠"节点名长得像页面新建的默认名"来间接推断。**一个查不出来的事实，
等于一个可以随便断言的事实** —— 谁写在注释里就算谁的，而且注释不会自己变红。

所以这里封两件事：
  1. 服务层每个写函数都要记账（下一个人加写函数时不许再漏）；
  2. 页面上那条"这是文档库、不是可执行测试"的说明和操作日志的筛选项还在
     （光有数据没人看得见，等于没有）。
"""
import ast
import io
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICE = ROOT / "backend" / "app" / "services" / "api_collection_service.py"
AUDIT_PAGE = ROOT / "frontend" / "src" / "pages" / "settings" / "AuditLogs.jsx"
API_PAGE = ROOT / "frontend" / "src" / "pages" / "apis" / "ApiManagement.jsx"


def _calls(node: ast.AST) -> set[str]:
    """函数体里调到的名字（含 obj.attr 的 attr）。"""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def test_接口库的写函数都记账了():
    tree = ast.parse(io.open(SERVICE, encoding="utf-8").read())
    missing = []
    for fn in tree.body:
        if not isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        if fn.name.startswith("_"):        # _audit_node / _to_dict 自己不算
            continue
        called = _calls(fn)
        # 判据用 flush 而不是函数名：改了名字（rename_node、move_node…）
        # 照样落在这条规则里。读函数（list_tree/get_node）不 flush。
        if "flush" not in called:
            continue
        if "_audit_node" not in called:
            missing.append(fn.name)
    assert not missing, (
        f"这些写函数没记账: {missing} —— 接口库的写入必须能在「操作日志」里查到，"
        "否则「这块还有没有人在用、是谁在写」又变成只能靠猜的事（2026-08-27 就误判过一次）。"
        "在函数末尾调 _audit_node(session, <action>, node) 即可。"
    )


def test_记账用的是_api_node_这个对象类型():
    """target_type 写错字页面就筛不出来，而筛不出来跟没记一样。"""
    src = io.open(SERVICE, encoding="utf-8").read()
    assert 'target_type="api_node"' in src, "记账的 target_type 不是 api_node 了"


def test_操作日志页能筛出接口库():
    jsx = io.open(AUDIT_PAGE, encoding="utf-8").read()
    assert "'api_node'" in jsx, "TARGET_TYPES 里没有 api_node —— 筛选下拉里选不到它"
    assert "api_node: '接口库'" in jsx, \
        "TARGET_TYPE_LABELS 里没配 api_node，表格里会直接露出裸 key"


def test_接口库页面上写明了它不是可执行测试():
    """这条误会发生在**第一次打开这一页**的人身上，所以说明必须在页面上，
    不能只写在代码注释和文档里 —— 那两处只有改代码的人看得到。"""
    jsx = io.open(API_PAGE, encoding="utf-8").read()
    assert "<Alert" in jsx and "Alert" in jsx.partition("from 'antd'")[0], \
        "说明条没了（或 Alert 没导入）"
    assert "不产生可执行的测试" in jsx, \
        "页面上那句「这是文档、不产生可执行的测试」没了 —— 那正是被误会的那一点"
    assert "绑用例的编排链" in jsx, \
        "说明里得指出可执行的接口测试是什么、在哪，光说「这不是」等于没说"
