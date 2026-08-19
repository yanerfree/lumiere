"""变异验证 —— 「这条断言到底会不会红」。

外部 CC 那 21 条用例全绿，但有多少条真能抓到问题**没人知道**。数量、指纹都判不了；
唯一能判的办法是把动作拿掉再跑：该红的必须红。

真跑过两条（stoa 环境，只读运行、不留痕）：
- TC-FWGL-00003 跳「禁用服务」→ 3 步该红的全红，4 步归到后面的「重新启用」名下
- TC-FWGL-00005 跳「修改路由路径」→ 5 步红，**1 步照样绿**：
  「改动应重新推送并收敛」（push-status 断 success），而制备阶段它就已经是 success ——
  推送压根没重跑，这条也是绿的。跟回推入口那条静态告警指向同一条断言，两个机制对上了。
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

from app.services.assertion_bite import _verdict, _watch_window


def _s(name, method="GET"):
    return SimpleNamespace(name=name, method=method)


# 真实那条的形状（TC-FWGL-00003）
_SCENARIO = [
    _s("登录", "POST"), _s("制备：建服务", "POST"), _s("基准：应可调通"),
    _s("禁用服务", "POST"),
    _s("回查详情：已禁用"), _s("列表页应一致"), _s("禁用后打网关应 404"),
    _s("重新启用服务", "POST"),
    _s("回查详情：已启用"), _s("启用后打网关应恢复 200"),
    _s("清理：删除服务", "DELETE"),
]


def test_只看被跳动作后面紧跟的那几步():
    w = _watch_window(_SCENARIO, {"禁用服务"})
    assert [k for k, v in w.items() if v is None] == [
        "回查详情：已禁用", "列表页应一致", "禁用后打网关应 404"]


def test_后面另一个动作之后的读不算恒真():
    """跳掉「禁用服务」，「启用后打网关应恢复 200」照样绿 —— 那不是恒真，
    是因为它验的是「启用」而启用真的跑了。算成 still_green 是冤枉，
    人照着改会把好断言改坏。"""
    w = _watch_window(_SCENARIO, {"禁用服务"})
    assert w["回查详情：已启用"] == "重新启用服务"
    assert w["启用后打网关应恢复 200"] == "重新启用服务"


def test_写操作不参与判定():
    """`清理：删除服务` 当然还是 204 —— 它是动作，不是验证。"""
    w = _watch_window(_SCENARIO, {"禁用服务"})
    assert "清理：删除服务" not in w and "重新启用服务" not in w


def test_被跳步骤之前的制备不看():
    w = _watch_window(_SCENARIO, {"禁用服务"})
    assert "基准：应可调通" not in w and "登录" not in w


def test_跳后面那个动作时窗口跟着挪():
    w = _watch_window(_SCENARIO, {"重新启用服务"})
    assert [k for k, v in w.items() if v is None] == [
        "回查详情：已启用", "启用后打网关应恢复 200"]


def test_判词在还绿时必须说怎么改():
    v = _verdict(["a"], ["改动应重新推送并收敛"], [])
    assert "照样绿" in v and "改成断动作真正改变的那个东西" in v


def test_全红才算通过():
    assert _verdict(["a", "b"], [], []).startswith("✅")


def test_全部判不了时不许说通过():
    """引用了被跳步骤提取物的步骤是被变量卡死的，不是被断言抓住的 ——
    把它算成"有效"就是又造一个假绿。"""
    v = _verdict([], [], ["x", "y"])
    assert "判不了" in v and "✅" not in v


# ── 只读：这是一次诊断，不是一次回归 ──────────────────────────────

def test_变异运行不许写库():
    """写进去会把用例的接口维度、步骤状态、执行历史全带成"这条挂了"。
    实测跑完两次，两条场景的 last_status 仍然全是 pass、api_status 仍是 completed。"""
    from app.services import api_test_runner

    src = inspect.getsource(api_test_runner.run_scenario)
    assert "persist: bool = True" in src
    assert "if persist:\n                    step.last_status" in src, "persist=False 仍在写步骤状态"
    assert "if persist:\n            await session.commit()" in src, "persist=False 仍在 commit"
    bite = inspect.getsource(__import__("app.services.assertion_bite", fromlist=["x"]))
    assert "persist=False" in bite, "变异验证没用只读模式"


def test_不跳任何步骤要拒绝():
    """不跳东西跑出来就是一次普通执行，证明不了任何事 —— 别让它以为验过了。"""
    src = inspect.getsource(
        __import__("app.services.assertion_bite", fromlist=["x"]).check_assertion_bite)
    assert "skip_step_names 必填" in src


def test_工具注册了并进了live档():
    from app.mcp import TOOL_CATALOG
    from app.mcp.profiles import PROFILES

    cat = {t["name"]: t["description"] for t in TOOL_CATALOG}
    assert "tb_check_assertion_bite" in cat
    d = cat["tb_check_assertion_bite"]
    assert "别跳产出 id 的创建步" in d, "不说清跳什么，CC 会跳创建步然后全部判不了"
    assert "请求是真发的" in d, "没说清它会在被测系统里造数据"
    live = next(p for p in PROFILES if p["key"] == "live")
    assert "tb_check_assertion_bite" in live["tools"]


def test_规范里把它接到断言纪律上():
    """「先让它红一次」如果没有工具可用，就只是一句口号。"""
    from app.mcp.tools.sync import _SPEC_API_SCENARIO as spec
    assert "tb_check_assertion_bite" in spec


def test_夹着别的动作时说清该跳哪个():
    """活体跑回推链路时撞到的：跳「建服务」，后面的读全在「同名再建」「清理」之后 ——
    原来只回一句"后面没有紧跟任何读步骤"，而明明有 4 个读，人会以为工具坏了。
    现在点名夹在中间的那个动作，并直接给出该改跳哪一步。**在真跑之前就拦住**，
    别白打一趟被测系统。
    """
    import inspect

    from app.services import assertion_bite
    src = inspect.getsource(assertion_bite.check_assertion_bite)
    assert "nextActionInBetween" in src
    assert "改跳" in src, "只说不行不给出路，等于让人猜"


def test_跳清理步会留残留这件事说出来了():
    """跳的正是清理步时，那一趟造的数据不会被删，而且变异运行不留痕 ——
    tb_check_env_hygiene 也看不见它。不写这句，谁跑谁在被测系统里攒垃圾。"""
    from app.mcp import TOOL_CATALOG

    d = {t["name"]: t["description"] for t in TOOL_CATALOG}["tb_check_assertion_bite"]
    assert "残留归你自己收" in d and "看不见" in d


# ── 否定动作：变异验证在原理上说不了话 ────────────────────────────

def test_跳否定动作要当场拒绝而不是冤枉断言():
    """活体自测撞到的：跳掉「同名再建应被拒（409）」，「列表里有且只有一条」照样绿。
    那不是恒真 —— 这个动作的**预期效果就是"什么都不变"**，跳掉当然没差别。
    报成 still_green 会让人去"修"一条正确的唯一性断言，比不报更糟。
    """
    from app.services.assertion_bite import _expects_rejection

    class S:
        def __init__(self, name, assertions=None):
            self.name, self.assertions = name, assertions or []

    assert _expects_rejection(S("同名再建", [{"type": "status", "value": 409}]))
    assert _expects_rejection(S("越权读取应 403", [{"type": "status", "value": [401, 403]}]))
    assert _expects_rejection(S("同名再建应被拒", [{"type": "status", "value": 200}])), \
        "状态码看不出来时按名字认"
    assert not _expects_rejection(S("建服务", [{"type": "status", "value": 201}]))


def test_拒绝时要给出替代做法():
    """只说"不行"，CC 会换个随便的步骤再跳一次，白打一趟被测系统。"""
    import inspect

    from app.services import assertion_bite
    src = inspect.getsource(assertion_bite.check_assertion_bite)
    assert "whatToDoInstead" in src and "正面动作" in src
    assert "_expects_rejection" in src, "判据没接进入口"
