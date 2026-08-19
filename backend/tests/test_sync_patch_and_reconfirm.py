"""两条"改一点却要重发一整份"的路：接口场景增量更新、落款轻量重确认。

来自外部 CC 用完 21 条用例后的反馈：
- 「改 3 个断言要重发 27 步，改 1 处要重发 26 步。费 token 是小事，**重发时容易手误
  引入新问题**才是大事。」→ mode='patch'，按 step name 只改点名的那几步。
- 「我只是把「订阅 id 不变」补进预期措辞，也要把几百字的依据整段重填，这轮重填了
  12 条。」→ tb_update_case(reconfirm=true)，措辞润色时沿用原落款。

重填几百字这件事的真正代价不是打字：**重填出来的不是新确认**，人不会真的重读一遍，
"预期已确认"就退化成走过场。
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from app.mcp.tools import sync, test_cases
from app.mcp.tools.sync import _merge_patch


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _Session:
    """够 _merge_patch 用的假 session：第一次查场景，第二次查步骤。"""

    def __init__(self, scenario, steps):
        self._queue = [[scenario] if scenario else [], steps]
        self.added = []

    async def execute(self, _stmt):
        return _Res(self._queue.pop(0) if self._queue else [])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


def _step(name, **kw):
    base = dict(name=name, method="GET", url="${BASE_URL}/x", headers=None, body=None,
                assertions=[{"type": "status", "value": 200}], variables_extract=None,
                enabled=True, group_name=None, wait_ms=0, retry_timeout_ms=0,
                retry_interval_ms=300)
    base.update(kw)
    return SimpleNamespace(**base)


_SCENARIO = SimpleNamespace(id="s1", title="订阅审批")
_OLD = [_step("登录"), _step("提交申请", method="POST"), _step("审批通过", method="POST"),
        _step("读回来确认")]


async def _merge(incoming, old=None):
    patched: list[str] = []
    out = await _merge_patch(_Session(_SCENARIO, old if old is not None else _OLD),
                             "b", "c", incoming, patched)
    return out, patched


@pytest.mark.asyncio
async def test_只改点名的那一步其余原样():
    out, patched = await _merge([{"name": "读回来确认",
                                  "assertions": [{"type": "status", "value": 404}]}])
    assert patched == ["读回来确认"]
    assert len(out) == 4, "其余三步必须还在 —— 少一步就是静默删了别人的活"
    assert out[3]["assertions"] == [{"type": "status", "value": 404}]
    assert out[1]["method"] == "POST", "没点名的步骤字段不能被动"


@pytest.mark.asyncio
async def test_只传的字段才覆盖():
    """patch 里没写 url，就得沿用原来的 —— 否则等于要求每次都写全，patch 白做。"""
    out, _ = await _merge([{"name": "登录", "retry_timeout_ms": 6000}])
    assert out[0]["url"] == "${BASE_URL}/x" and out[0]["retry_timeout_ms"] == 6000


@pytest.mark.asyncio
async def test_顺序不变():
    out, _ = await _merge([{"name": "审批通过", "wait_ms": 100}])
    assert [s["name"] for s in out] == ["登录", "提交申请", "审批通过", "读回来确认"]


@pytest.mark.asyncio
async def test_名字对不上整批拒绝():
    """静默漏改比报错糟得多：CC 以为改了，跑的还是老断言。"""
    out, _ = await _merge([{"name": "读回确认", "wait_ms": 1}])  # 少一个字
    assert isinstance(out, dict) and out.get("notFound") == ["读回确认"]
    assert "读回来确认" in out["existingNames"], "要把现有名字给它，否则只能猜"


@pytest.mark.asyncio
async def test_没有场景时说清楚该怎么办():
    patched: list[str] = []
    out = await _merge_patch(_Session(None, []), "b", "c", [{"name": "x"}], patched)
    assert isinstance(out, dict) and "replace" in out["error"]


@pytest.mark.asyncio
async def test_没写name的直接拒():
    out, _ = await _merge([{"method": "POST"}])
    assert isinstance(out, dict) and "name" in out["error"]


@pytest.mark.asyncio
async def test_patch里同名两条拒掉():
    out, _ = await _merge([{"name": "登录", "wait_ms": 1}, {"name": "登录", "wait_ms": 2}])
    assert isinstance(out, dict) and "同名" in out["error"]


@pytest.mark.asyncio
async def test_现有场景里有同名步骤时不猜():
    out, _ = await _merge([{"name": "登录", "wait_ms": 1}],
                          old=[_step("登录"), _step("登录"), _step("查一下")])
    assert isinstance(out, dict) and "replace" in out["error"]


def test_patch接在回推入口上且校验跑在合并之后():
    """合并必须发生在**校验之前** —— 否则 patch 进来的 ${var} 只对着这几步查，
    引用前面步骤提取物的会被误判成悬空引用，逼 CC 回去写死。
    """
    src = inspect.getsource(sync.sync_orchestrated_scenario)
    assert "_merge_patch" in src, "patch 没接进回推入口"
    assert src.index("_merge_patch") < src.index("dangling.append"), \
        "校验跑在合并之前，patch 的引用检查会误报悬空"


# ── 落款轻量重确认 ──────────────────────────────────────────────

def test_落款要在清空之前取():
    """改步骤/预期会把四个字段一起清掉。清完再取只剩 None，reconfirm 沿用不到东西。"""
    src = inspect.getsource(test_cases.update_case)
    assert src.index("prev_conf = (") < src.index("await case_service.update_case"), \
        "prev_conf 取晚了，reconfirm 永远沿用不到落款"


def test_没有原落款时不许凭空确认():
    """reconfirm 是"沿用"，不是"盖章"。本来没有依据的，盖上时间戳就成了假确认。"""
    src = inspect.getsource(test_cases.update_case)
    assert "elif reconfirm and prev_conf[0]:" in src
    assert "没有落款" in src, "沿用不到时要说出来，否则 CC 以为确认上了"


def test_工具描述里写了这两个新参数():
    """CC 只照工具描述调参 —— 描述里没有，参数等于不存在。"""
    from app.mcp import TOOL_CATALOG

    by_name = {t["name"]: t["description"] for t in TOOL_CATALOG}
    assert "reconfirm" in by_name["tb_update_case"]
    assert "措辞润色" in by_name["tb_update_case"]
    assert "patch" in by_name["tb_sync_orchestrated_scenario"]


# ── 重推不该把上一次运行的证据抹掉 ────────────────────────────────

def test_定义没变的步骤沿用上一次运行结果():
    """步骤行是**删了重建**的，于是 last_status / last_response 一并没了 ——
    而 tb_check_env_hygiene 判"上次跑到清理没有"靠的就是它。
    实测后果：CC 跑完再 patch 一次，那条链的运行痕迹归零，工具从此看不见残留，
    "报 0 条"于是变成一句空话。"""
    from app.mcp.tools.sync import _carried_evidence, _step_def_sig

    step = {"name": "清理：删除服务", "method": "DELETE", "url": "/api/v1/services/${sid}",
            "assertions": [{"type": "status", "value": [200, 204], "operator": "in"}]}
    carry = {"清理：删除服务": (_step_def_sig(step), "pass", {"statusCode": 204})}
    assert _carried_evidence(carry, step) == ("pass", {"statusCode": 204})


def test_定义变了就丢掉旧结果():
    """改了断言/url 的步骤，旧 last_response 已经不代表它了 —— 留着会让卫生检查
    拿过期的 id 去报残留，那比看不见更糟。"""
    from app.mcp.tools.sync import _carried_evidence, _step_def_sig

    old = {"name": "取列表", "method": "GET", "url": "/a",
           "assertions": [{"type": "status", "value": 200, "operator": "=="}]}
    carry = {"取列表": (_step_def_sig(old), "pass", {"statusCode": 200})}
    changed = {**old, "assertions": [{"type": "status", "value": 201, "operator": "=="}]}
    assert _carried_evidence(carry, changed) == (None, None)
    assert _carried_evidence(carry, {**old, "url": "/b"}) == (None, None)
    assert _carried_evidence(carry, {**old, "name": "取列表2"}) == (None, None)


def test_大小写和空格不算定义变了():
    """method 小写、url 前后带空格是同一个步骤 —— 否则每次重推都白丢证据。"""
    from app.mcp.tools.sync import _step_def_sig

    assert _step_def_sig({"method": "get", "url": " /a "}) == _step_def_sig({"method": "GET", "url": "/a"})


def test_快照必须取在删除之前():
    """顺序反了就什么都留不下 —— 这条只能按源码顺序判。"""
    src = inspect.getsource(sync.sync_orchestrated_scenario)
    snap = src.index("carry[_old.name]")
    dele = src.index("sa_delete(ApiTestStep)")
    assert snap < dele, "快照要在 delete 之前取"
    assert "last_status=prev_status" in src, "取了却没带回新行上"
