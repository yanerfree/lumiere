"""环境卫生 —— 「探测脚本崩过几次，留下孤儿待办/服务/订阅，平台完全不知道」。

外部 CC 第十条反馈。平台扫不了被测系统，但**每条链自己就写着"我造了什么、怎么删"**，
步骤上还留着最后一次运行的响应，所以有两件事是能证明的：
① 造了东西却没有清理步骤 —— 每跑一次留一份
② 最后一次运行没跑到清理 —— 那次造的 id 就在创建步骤的响应里

为什么这不只是"脏"：列表里堆满同类数据之后，`data[0]` 指向别人、满页把本次那条挤到
第二页，断言开始时红时绿 —— 人会当成被测系统的缺陷去查。**垃圾会反过来毁掉断言。**
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.env_hygiene import (_creates, _extracted_ids, _is_cleanup, _verdict,
                                      check_env_hygiene)


def _st(name, method="GET", extract=None, last_status=None, body=None):
    return SimpleNamespace(name=name, method=method, variables_extract=extract,
                           last_status=last_status,
                           last_response={"body": body} if body is not None else None,
                           url="${BASE_URL}/api/v1/services/${serviceId}")


def test_认清理步骤():
    assert _is_cleanup(_st("清理：删除本次服务", "DELETE"))
    assert _is_cleanup(_st("收尾：删掉订阅", "POST")), "有些系统用 POST 收尾，按名字也要认"
    assert not _is_cleanup(_st("回查详情"))


def test_认造东西的步骤():
    assert _creates(_st("建服务", "POST", {"serviceId": "data.id"}))
    assert not _creates(_st("登录", "POST", {"token": "data.access_token"})), \
        "登录不算造东西 —— 它抽的不是 id"
    assert not _creates(_st("查列表", "GET", {"someId": "data[0].id"})), "读不算造"


def test_从最后一次响应里抽出残留id():
    st = _st("建服务", "POST", {"serviceId": "data.id"},
             last_status="pass", body={"data": {"id": "svc-9527"}})
    assert _extracted_ids(st) == [{"variable": "serviceId", "value": "svc-9527",
                                  "fromStep": "建服务"}]


def test_没跑过的步骤抽不出东西不瞎报():
    st = _st("建服务", "POST", {"serviceId": "data.id"})
    assert _extracted_ids(st)[0]["value"] is None


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, scenarios, steps_by_id):
        self.q = [scenarios] + [steps_by_id[s.id] for s in scenarios] + [[]]

    async def execute(self, _stmt):
        return _Res(self.q.pop(0) if self.q else [])


_PID = "11111111-1111-1111-1111-111111111111"


def _scenario(sid, code):
    return SimpleNamespace(id=sid, code=code, title=f"场景{code}")


@pytest.mark.asyncio
async def test_造了东西没清理步骤要报():
    sc = _scenario("s1", "TC-A-00001")
    steps = [_st("登录", "POST", {"token": "data.t"}, "pass"),
             _st("建服务", "POST", {"serviceId": "data.id"}, "pass",
                 {"data": {"id": "svc-1"}}),
             _st("回查详情", "GET", None, "pass")]
    r = await check_env_hygiene(_Session([sc], {"s1": steps}), _PID)
    assert len(r["noCleanupStep"]) == 1
    assert r["noCleanupStep"][0]["createsAt"] == ["建服务"]
    assert "每跑一次留一份" in r["verdict"]


@pytest.mark.asyncio
async def test_上次没跑到清理要把残留id列出来():
    sc = _scenario("s2", "TC-A-00002")
    steps = [_st("建服务", "POST", {"serviceId": "data.id"}, "pass", {"data": {"id": "svc-7"}}),
             _st("禁用服务", "POST", None, "fail"),
             _st("清理：删除本次服务", "DELETE", None, None)]
    r = await check_env_hygiene(_Session([sc], {"s2": steps}), _PID)
    row = r["lastRunLeftBehind"][0]
    assert row["suspectedLeftovers"][0]["value"] == "svc-7"
    assert row["deleteWith"][0]["method"] == "DELETE", "要告诉人拿什么请求去删"


@pytest.mark.asyncio
async def test_清理跑成了就不报():
    sc = _scenario("s3", "TC-A-00003")
    steps = [_st("建服务", "POST", {"serviceId": "data.id"}, "pass", {"data": {"id": "x"}}),
             _st("清理：删除本次服务", "DELETE", None, "pass")]
    r = await check_env_hygiene(_Session([sc], {"s3": steps}), _PID)
    assert r["lastRunLeftBehind"] == [] and r["noCleanupStep"] == []


@pytest.mark.asyncio
async def test_没跑过的场景不算残留():
    """从没跑过 ≠ 跑挂了。报它就是滥报，人看两条假的就不看这个工具了。"""
    sc = _scenario("s4", "TC-A-00004")
    steps = [_st("建服务", "POST", {"serviceId": "data.id"}),
             _st("清理：删除", "DELETE")]
    r = await check_env_hygiene(_Session([sc], {"s4": steps}), _PID)
    assert r["lastRunLeftBehind"] == []


@pytest.mark.asyncio
async def test_报0条时必须说清看不见什么():
    """「报 0 条」最容易被当成「环境是干净的」—— 而 UI 脚本造的、手工造的、
    更早那几次运行留下的，平台一概没有记录。"""
    sc = _scenario("s5", "TC-A-00005")
    r = await check_env_hygiene(_Session([sc], {"s5": [_st("查列表")]}), _PID)
    assert "报 0 条不等于环境是干净的" in r["scope"]
    assert "UI 脚本" in r["scope"] and "最后一次运行" in r["scope"]


def test_判词把后果说出来而不只是数数():
    v = _verdict([{"code": "TC-A-1"}], [])
    assert "data[0]" in v or "分页" in v, "不说后果，人不会去清"
