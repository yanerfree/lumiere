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
import hashlib
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


def test_写日志时带上操作来源():
    """来源跟操作人是两件事，得分别落库。"""
    audit.set_audit_context(user_id=uuid.uuid4(), trace_id="t",
                            actor_type="mcp", actor_label="小李的开发机")
    s = FakeSession()
    asyncio.run(audit.write_audit_log(session=s, action="update", target_type="case"))
    log = s.added[0]
    assert (log.actor_type, log.actor_label) == ("mcp", "小李的开发机"), \
        "来源没落库 —— 所有 CC 的日志又会长得一模一样"


def _prime(mw, monkeypatch, *, uid, key_name, project=None):
    """把一把假 Key 塞进中间件缓存，并让它以为当前请求带着这个 bearer。"""
    import time
    token = "tb_faketoken_for_test"
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    mw._CACHE[key_hash] = (None, uid, key_name, project, time.monotonic())
    monkeypatch.setattr(mw, "get_http_headers",
                        lambda include=None: {"authorization": f"Bearer {token}"})


def test_缓存命中时取到的是Key名不是时间戳(monkeypatch):
    """缓存元组每加一位，TTL 那一位的下标都得跟着挪。已经加过两次
    （Key 名、归属项目），下一次还会。

    写错了不会报错：`hit[N]` 从时间戳变成了 Key 名，`time.monotonic() - "小李的开发机"`
    抛 TypeError 被外层 except 吞掉 → 退化成每次查库，或者干脆全 None。
    """
    from app.mcp import middleware as mw
    uid = str(uuid.uuid4())
    _prime(mw, monkeypatch, uid=uid, key_name="小李的开发机", project="p-1")
    allowed, got_uid, got_name, got_proj = asyncio.run(mw._lookup_key())
    assert (got_uid, got_name, got_proj) == (uid, "小李的开发机", "p-1"), \
        f"缓存没读对（拿到 {got_uid!r}/{got_name!r}/{got_proj!r}）—— 多半是 TTL 下标没跟着挪"


def test_缓存再加一个字段也不会错位(monkeypatch):
    """★ 模拟"以后又给缓存元组加了一位"：塞一条比现在多一格的记录进去，
    命中逻辑必须照样读对。

    这是真行为测试，不是 grep 源码 —— 如果 TTL 用的是写死的下标，
    多出来的那一格会让它去减一个字符串，TypeError 被外层 except 吞掉，
    退化成每次查库（症状：改完范围要等 30s 才生效的假象消失，但库被打爆）。
    """
    import time

    from app.mcp import middleware as mw
    token = "tb_faketoken_sixfield"
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    uid = str(uuid.uuid4())
    # 比当前多一格：(allowed, uid, key_name, project, 【将来某个新字段】, 时间戳)
    mw._CACHE[key_hash] = (None, uid, "未来的Key", "proj-x", "将来加的字段", time.monotonic())
    monkeypatch.setattr(mw, "get_http_headers",
                        lambda include=None: {"authorization": f"Bearer {token}"})

    def boom():
        raise AssertionError("缓存该命中，不该去查库")
    monkeypatch.setattr("app.deps.db.async_session_factory", boom)

    allowed, got_uid, got_name, got_proj = asyncio.run(mw._lookup_key())
    assert (got_uid, got_name, got_proj) == (uid, "未来的Key", "proj-x")


def test_MCP调用把身份和来源都放进了审计上下文(monkeypatch):
    """真跑 on_call_tool，不读源码。

    挂在 on_call_tool 上 = 所有 lum_* 一次性覆盖。少了它，CC 的操作全是匿名；
    少了 actor_label，多台 CC 在日志里长得一模一样（Key 只能给自己建，归属人全一样）。
    """
    from app.mcp import middleware as mw
    uid = str(uuid.uuid4())
    _prime(mw, monkeypatch, uid=uid, key_name="uag-cc使用")
    audit.set_audit_context()  # 先清干净，避免读到上一条测试的残留

    captured = {}

    async def call_next(_ctx):
        # 工具执行发生在这里 —— 上下文必须在此刻已经就位
        captured.update(audit.get_audit_context())
        return "ok"

    ctx = SimpleNamespace(message=SimpleNamespace(name="lum_update_case", arguments={}))
    asyncio.run(mw.ToolScopeMiddleware().on_call_tool(ctx, call_next))

    assert str(captured.get("user_id")) == uid, "没取 Key 身份 —— 操作人会是「-」"
    assert captured.get("actor_type") == "mcp", "没记来源 —— 分不出是 CC 还是人"
    assert captured.get("actor_label") == "uag-cc使用", "没记 Key 名 —— 分不出是哪台 CC"
    assert captured.get("trace_id") == "mcp:lum_update_case"


class SQLCapturingSession:
    """真让 list_logs 去拼语句，把编译出来的 SQL 收下来 —— 不起 DB，也不读源码。"""

    def __init__(self):
        self.sqls = []

    async def execute(self, stmt):
        self.sqls.append(str(stmt.compile(compile_kwargs={"literal_binds": True})))

        class R:
            def scalar_one(self_inner):
                return 0

            def all(self_inner):
                return []

        return R()


def _where_sql(**kw):
    from app.services import audit_service
    s = SQLCapturingSession()
    asyncio.run(audit_service.list_logs(s, **kw))
    return " ".join(s.sqls)


def test_按来源筛人工用的是IS_NULL不是等值():
    """页面那条路从不写 actor_type，所以「页面操作」只能按 IS NULL 筛。

    写成 `== "human"` 不会报错，只会永远筛出 0 条 —— 静默的空列表最难发现。
    """
    sql = _where_sql(actor_type="human")
    assert "actor_type IS NULL" in sql, f"人工筛条件写错了，会永远 0 条：{sql[:300]}"
    assert "actor_type = 'human'" not in sql


def test_按来源筛CC是等值匹配():
    sql = _where_sql(actor_type="mcp")
    assert "actor_type = 'mcp'" in sql, sql[:300]


def test_不传来源就不加这个条件():
    """别把"不筛"实现成"筛 NULL" —— 那样默认视图会把所有 CC 操作藏起来。

    只看条件，不看 SELECT 列表（actor_type 本来就在选出来的列里）。
    """
    sql = _where_sql()
    assert "actor_type IS NULL" not in sql and "actor_type =" not in sql, \
        f"没传来源却加了条件：{sql[:300]}"
