"""操作日志必须说得出**是谁、在哪个项目**干的。

现象：「操作日志」页面上，CC 改的每一条用例，操作人和所属项目都是「-」。
一份说不出是谁干的日志不叫审计日志 —— 现在写库的主力是 CC，
分不出哪些是它改的、哪些是人改的，出问题只能靠猜。

两条独立的漏：
① 审计上下文只在 HTTP 认证依赖（deps/auth.py）里设过，**MCP 那条路整条没设**。
   身份本来就有（Key 决定，script_runs.executed_by 一直在用它），审计从没问过它。
② 所属项目只有**项目级 HTTP 路由**才设（deps/auth.py:72）。用例的增删改不走那种
   路由，MCP 更没有 —— 于是这一列一片「-」，几百条日志没法按项目筛。

**这两处的代码都被 try/except 包着**（记账不能拖垮主业务），所以写错了不会报错，
只会继续记成「-」。我第一版就把 Branch 从 app.models.branch 导入（实际在
project.py 里），静默走 except，等于没修。所以这里必须**真跑**，不能只读源码。
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from app.core import audit


class FakeSession:
    def __init__(self, branch=None):
        self._branch = branch
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def get(self, model, oid):
        return self._branch


def test_能从branch推出所属项目():
    """真调，不读源码 —— 导入路径写错时 except 会把它吞掉。"""
    pid, bid = uuid.uuid4(), uuid.uuid4()
    s = FakeSession(branch=SimpleNamespace(id=bid, project_id=pid))
    case = SimpleNamespace(id=uuid.uuid4(), title="x", branch_id=bid)
    got = asyncio.run(audit._resolve_project_id(s, case))
    assert got == pid, f"推不出项目（拿到 {got}）—— 多半是 Branch 导入路径写错被 except 吞了"


def test_对象自带project_id就直接用():
    pid = uuid.uuid4()
    got = asyncio.run(audit._resolve_project_id(FakeSession(),
                                                SimpleNamespace(id=uuid.uuid4(), project_id=pid)))
    assert got == pid


def test_推不出就返回None不瞎猜():
    got = asyncio.run(audit._resolve_project_id(FakeSession(), SimpleNamespace(id=uuid.uuid4())))
    assert got is None


def test_Branch确实在project模块里():
    """钉住那次踩空：Branch 不在 app.models.branch。"""
    from app.models.project import Branch
    assert hasattr(Branch, "project_id")


def test_写日志时用上下文里的操作人():
    uid = uuid.uuid4()
    audit.set_audit_context(user_id=uid, trace_id="t")
    s = FakeSession()
    asyncio.run(audit.write_audit_log(session=s, action="update", target_type="case"))
    assert s.added and s.added[0].user_id == uid


def test_MCP入口设了审计上下文():
    """挂在 on_call_tool 上 = 所有 tb_* 一次性覆盖。少了它，CC 的操作全是匿名。"""
    import inspect
    src = inspect.getsource(
        __import__("app.mcp.middleware", fromlist=["x"]).ToolScopeMiddleware.on_call_tool)
    assert "set_audit_context" in src, "MCP 调用没设审计上下文 —— 操作人会一直是「-」"
    assert "current_caller_user_id" in src, "没取 Key 身份"
