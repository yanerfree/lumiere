"""命名规范 —— **让判据不用猜**。

用户指出的问题：现在的标题长这样
  「消费方租户管理员自己申请跨租户订阅时一级节点自动跳过，提供方直接批二级后订阅生效」
得读完整句才知道在测什么，而列表上只露标题。规范成两段：
  「租户管理员跨租户订阅-一级自动跳过、二级批完即生效」

步骤名同理，加角色前缀（前置/操作/验证/清理）。这一条的收益不只是好读：
`no_readback`、`single_entry_effect`、`_is_setup` 原来都在**猜**哪步是制备、
哪步在验生效 —— 靠词表猜，CC 换个说法就误判。有了前缀就是读一个字段。
"""
from __future__ import annotations

from app.services.intake_gate import check_title_shape
from app.services.scenario_shape import _is_setup, no_readback, step_role


def _s(name, url, method="GET"):
    return {"name": name, "url": url, "method": method,
            "assertions": [{"type": "status", "value": 200}]}


# ── 步骤角色前缀 ────────────────────────────────────────────────

def test_读前缀不用猜():
    assert step_role("前置: 管理员登录") == "setup"
    assert step_role("操作：审批通过") == "act"
    assert step_role("验证: 拿凭据打网关应 200") == "verify"
    assert step_role("清理：删掉本次建的服务") == "cleanup"
    assert step_role("随便写的一句") is None


def test_没写前缀才回退去猜():
    """老数据没有前缀，得兜住 —— 但兜底词表永远补不全，这正是要推前缀的理由。"""
    assert _is_setup("制备：建服务 A") is True
    assert _is_setup("前置: 登录") is True
    assert _is_setup("操作：审批通过") is False, "前缀说了是操作，就别再按词表猜成制备"


def test_前缀能纠正词表的误判():
    """兜底词表里有「取」，于是「取消订阅」这种**操作**会被猜成制备。
    写了前缀就不会 —— 这就是规范的直接收益。"""
    assert _is_setup("取消订阅") is True, "（兜底词表的已知误判）"
    assert _is_setup("操作：取消订阅") is False


def test_标了验证的步骤就算验过了():
    """有明确「验证:」的后续步骤，不用再去猜"他是不是用回读/下游在验"。"""
    assert no_readback([
        _s("操作：发布服务", "${BASE_URL}/api/v1/services/${id}/publish", "POST"),
        _s("验证：服务详情里状态为已发布", "${BASE_URL}/api/v1/services/${id}"),
    ]) == []


# ── 标题形状 ────────────────────────────────────────────────────

def test_长标题没分段要提示():
    w = check_title_shape("消费方租户管理员自己申请跨租户订阅时一级节点自动跳过，提供方直接批二级后订阅生效")
    assert w and "前段" in w[0]


def test_两段式不提示():
    assert check_title_shape("租户管理员跨租户订阅-一级自动跳过、二级批完即生效") == []


def test_短横不留空格也认():
    """规范就是短横不留空格 —— 列表里标题宽度有限，别浪费在空格上。"""
    assert check_title_shape("模块改名-子模块路径跟着改、用例编号不变") == []


def test_短标题不要求分段():
    """短标题本身就一眼可读，逼它分段是没必要的形式主义。"""
    assert check_title_shape("禁用服务后网关停止转发") == []


def test_只提示不硬拦():
    """判据规范 ③：合法写法存在（短标题、本来就没有"预期"可拼的标题）。"""
    import inspect
    from app.services import intake_gate
    src = inspect.getsource(intake_gate.check_one)
    assert "warns.extend(check_title_shape(title))" in src


def test_断应当被拒的步骤不要求读回():
    """活体自测撞出来的滥报：「验证: 同级重名被拒 409」被要求"写完要读回"。
    被拒就等于什么都没写，没有任何东西可以读回来。"""
    from app.services.scenario_shape import no_readback
    w = no_readback([{"name": "验证: 同级重名被拒 409", "method": "PATCH",
                      "url": "${BASE_URL}/api/folders/${id}",
                      "assertions": [{"type": "status", "operator": "==", "value": 409}]}])
    assert w == []


def test_断成功的写操作照旧要求验效果():
    from app.services.scenario_shape import no_readback
    w = no_readback([{"name": "操作: 改名", "method": "PATCH",
                      "url": "${BASE_URL}/api/folders/${id}",
                      "assertions": [{"type": "status", "operator": "==", "value": 200}]}])
    assert len(w) == 1
