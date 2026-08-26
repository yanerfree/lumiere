"""场景形态提示 —— 减少「验了控制面就以为验完了」这类返工。

来自网关订阅审批那批的真实返工：
  ·「转 approved」当成生效验完了 —— 真正的判据是拿该应用凭据去调需要认证的服务，
    审批前必须调不通；CC 自己的复盘写的是「我只断到状态，没落到数据面」
  · POST 断了响应就完事，没有 GET 回读
  · 对照组（本租户 / 跨租户）塞进同一条，标题一眼看不出在验什么

**全部只提示、一条都不拦**（用户拍的口径）：这几条判据都要从自然语言里猜意图，
猜错就是滥报，而人看两条假的就再也不看这个提示了。
"""
from __future__ import annotations

import json

from app.services.scenario_shape import (check_shape, control_group_in_one,
                                         no_readback, single_entry_effect)


def _s(name, url, method="GET", assertions=None, headers=None):
    return {"name": name, "url": url, "method": method,
            "assertions": assertions or [{"type": "status", "value": 200}],
            "headers": headers}


# ── 写完要读回 ───────────────────────────────────────────────────

def test_写完没读回要提示():
    w = no_readback([_s("创建 Provider", "${BASE_URL}/api/v1/providers", "POST")])
    assert len(w) == 1 and "读回来" in w[0]["value"]


def test_读回了就不提示():
    w = no_readback([_s("创建 Provider", "${BASE_URL}/api/v1/providers", "POST"),
                     _s("读详情确认落库", "${BASE_URL}/api/v1/providers/${id}")])
    assert w == []


def test_列表回读也算():
    """按名称查列表确认能查到，跟读详情是同一件事。"""
    w = no_readback([_s("创建 Provider", "${BASE_URL}/api/v1/providers", "POST"),
                     _s("列表按名称能查到", "${BASE_URL}/api/v1/providers?search=${name}")])
    assert w == []


def test_制备步骤不要求读回():
    """制备阶段建完就用，不读回是正常的 —— 每条都提示会淹掉真正的问题。"""
    assert no_readback([_s("制备：建服务 A", "${BASE_URL}/api/v1/services", "POST")]) == []


def test_读回必须在写之后():
    """先 GET 再 POST 不算读回 —— 那是取基准。"""
    w = no_readback([_s("基准：先查一遍", "${BASE_URL}/api/v1/providers/${id}"),
                     _s("改配置", "${BASE_URL}/api/v1/providers/${id}", "PUT")])
    assert len(w) == 1


# ── 生效判据 ─────────────────────────────────────────────────────

def test_只打控制面要提示():
    steps = [_s("审批通过", "${BASE_URL}/api/v1/subs/${id}/approve", "POST"),
             _s("状态应变为 approved 即生效", "${BASE_URL}/api/v1/subs/${id}")]
    w = single_entry_effect(steps, "审批通过后订阅生效")
    assert len(w) == 1
    assert "控制面的状态字段变了不等于真生效" in w[0]["value"]
    assert "共享数据" in w[0]["value"], "要告诉它数据面入口该写到哪，否则下轮还得再摸一遍"


def test_跨到另一个入口就不提示():
    """打了网关（数据面）就是真验了 —— 平台不关心那个变量叫什么名字。"""
    steps = [_s("审批通过", "${BASE_URL}/api/v1/subs/${id}/approve", "POST"),
             _s("审批后应可调通", "${gatewayBase}/svc/echo")]
    assert single_entry_effect(steps, "审批通过后订阅生效") == []


def test_没声称验生效的不提示():
    """普通的增删改查场景本来就只在控制面上，报它就是滥报。"""
    steps = [_s("创建", "${BASE_URL}/api/v1/providers", "POST"),
             _s("读详情", "${BASE_URL}/api/v1/providers/${id}")]
    assert single_entry_effect(steps, "新建 Provider 后 API Key 仅显示脱敏值") == []


def test_制备里出现生效字样不算():
    assert single_entry_effect(
        [_s("制备：等服务 A 推送生效", "${BASE_URL}/api/v1/services/${id}/push-status")],
        "新建服务后能查到") == []


# ── 对照组 ───────────────────────────────────────────────────────

def test_同一请求换身份断言相同要提示拆两条():
    url = "${BASE_URL}/api/v1/subscriptions"
    ok = [{"type": "status", "value": 201}]
    w = control_group_in_one([
        _s("本租户申请订阅", url, "POST", ok, {"Authorization": "Bearer ${tokenA}"}),
        _s("跨租户申请订阅", url, "POST", ok, {"Authorization": "Bearer ${tokenB}"}),
    ])
    assert len(w) == 1 and "拆成两条用例互为对照" in w[0]["value"]
    assert "开关" in w[0]["value"], "要说清挤在一条里的真实代价，否则只是风格建议"


def test_同一身份做两次不算对照组():
    """一级审批、二级审批都是同一个人调同一个接口 —— 那是流程，不是对照。"""
    url = "${BASE_URL}/api/v1/subs/${id}/approve"
    ok = [{"type": "status", "value": 200}]
    h = {"Authorization": "Bearer ${token}"}
    assert control_group_in_one([_s("一级通过", url, "POST", ok, h),
                                _s("二级通过", url, "POST", ok, h)]) == []


def test_断言不同不算对照组():
    """同一个人换个参数打同一个接口、断不同的结果，是边界覆盖。"""
    url = "${BASE_URL}/api/v1/subs"
    assert control_group_in_one([
        _s("正常申请应 201", url, "POST", [{"type": "status", "value": 201}],
           {"Authorization": "Bearer ${tokenA}"}),
        _s("重复申请应 409", url, "POST", [{"type": "status", "value": 409}],
           {"Authorization": "Bearer ${tokenB}"}),
    ]) == []


# ── 「一条验两件事」刻意不做检查 ──────────────────────────────────

def test_一条验两件事不做检查():
    """试过，判不了。这两个真实标题是同一个形状，一个该拆一个不该拆：
      「平台关闭审批开关后申请免审批直接生效，开关恢复后申请重新回到待审批」← 该拆
      「禁用服务后网关停止转发，重新启用后恢复调用」                    ← 一件事两阶段
    上一版按逗号/连接词判，第二个直接误报。分不清对错的提示比没有提示更糟 ——
    这条只写进规范，靠人和 CC 自己判。
    """
    a = "平台关闭本租户审批开关后订阅申请免审批直接生效，开关恢复后申请重新回到待审批"
    b = "禁用服务后网关停止转发，重新启用后恢复调用"
    for t in (a, b):
        assert check_shape([_s("改配置", "${BASE_URL}/x", "PUT"),
                            _s("读回确认", "${BASE_URL}/x")], t) == [] or True
    from app.services import scenario_shape
    assert not hasattr(scenario_shape, "two_things_in_one"), "又把猜不准的那条加回来了"
    from app.mcp.tools.sync import _SPEC_SCENARIO_SHAPE as spec
    assert "一条场景只验一件事" in spec, "检查不做，规范里必须写着"


# ── 接线 ─────────────────────────────────────────────────────────

def test_全部是软警告一条都不拦():
    """用户拍的口径：只提示不拦。判据靠猜，硬拦一次就再没人信这个提示。"""
    import inspect

    from app.mcp.tools import sync
    src = inspect.getsource(sync.sync_orchestrated_scenario)
    assert "check_shape" in src, "没接进回推入口"
    i = src.index("check_shape")
    seg = src[i - 200:i + 200]
    assert "warnings.extend" in seg and "return {" not in seg.split("check_shape")[1][:80]


def test_规范里前置了这四条():
    """回推后才提示已经算晚了 —— 判据要写在 CC 动手前读的那份规范里。"""
    from app.mcp.tools.sync import _SPEC_SCENARIO_SHAPE as spec
    assert "一条场景只验一件事" in spec
    assert "对照组拆成两条" in spec
    assert "写完必须读回来" in spec
    assert "不是控制面的状态字段" in spec
    assert "lum_upsert_automation_resource" in spec, \
        "数据面入口每个项目不一样，平台不硬编码 —— 得告诉 CC 摸清了写进共享数据"


def test_规范能单独按kind取():
    import asyncio

    from app.mcp.tools.sync import get_sync_spec
    out = asyncio.run(get_sync_spec("scenario_shape"))
    blob = json.dumps(out, ensure_ascii=False)
    assert "一条场景只验一件事" in blob and out.get("kind") == "scenario_shape"


# ── 评审证据侧：断言强度判据 ──────────────────────────────────────

def test_断错误码的读操作不算弱断言():
    """「删除后详情应 404」「越权应 403」只断状态码是**完整的**验证 ——
    那种响应体里没有可断的东西。实测这条误伤过一条写得很完整的用例：
    它因此从 approved 掉到 65 分被打回，而它根本没问题。
    """
    from types import SimpleNamespace

    from app.services.review.evidence import machine_findings
    case = SimpleNamespace(title="删除后查不到", bug_refs=None, tags=None, steps=[],
                           target_level="spec_api", expected_result="删除后详情和列表都查不到")
    scenario = {"steps": [
        {"name": "删除项目", "method": "DELETE", "url": "${BASE_URL}/api/projects/${id}",
         "assertions": [{"type": "status", "value": [200, 204], "operator": "in"}]},
        {"name": "删除后详情应 404", "method": "GET", "url": "${BASE_URL}/api/projects/${id}",
         "assertions": [{"type": "status", "operator": "==", "value": 404}]},
    ]}
    kinds = [f["kind"] for f in machine_findings(case, scenario, None)]
    assert "status_only_assertion" not in kinds, kinds


def test_只断200的读操作仍然算弱():
    from types import SimpleNamespace

    from app.services.review.evidence import machine_findings
    case = SimpleNamespace(title="搜索", bug_refs=None, tags=None, steps=[],
                           target_level="spec_api", expected_result="结果只含匹配关键词的用例")
    scenario = {"steps": [
        {"name": "按关键词搜索", "method": "GET", "url": "${BASE_URL}/api/cases?keyword=a",
         "assertions": [{"type": "status", "operator": "==", "value": 200}]},
    ]}
    assert "status_only_assertion" in [f["kind"] for f in machine_findings(case, scenario, None)]


# ── 「写完没读回」的判据修正（用户指出的滥报）────────────────────────

def test_下游调通就不要求回读():
    """用户的原话：「发布服务，调用服务通了就可以了呀，那你非要让别人 get 一下
    这样就被打回了，很不合理」。
    对 —— 下游调通比回读更强：回读只证明控制面记下了，调通才证明真生效。
    """
    w = no_readback([
        _s("发布服务", "${BASE_URL}/api/v1/services/${id}/publish", "POST"),
        _s("打这个服务应可调通", "${gatewayBase}/svc/echo"),
    ])
    assert w == [], w


def test_挂了UI脚本也不要求接口回读():
    """页面上验可见结果比接口回读更接近用户。"""
    w = no_readback([_s("创建项目", "${BASE_URL}/api/projects", "POST")], has_ui_script=True)
    assert w == []


def test_写完真的什么都没验才提示():
    w = no_readback([_s("创建项目", "${BASE_URL}/api/projects", "POST")])
    assert len(w) == 1
    assert "下游调通比回读更强" in w[0]["value"], "要说清两者有其一就够，别让它两样都写"


def test_同入口的后续请求不算下游():
    """同一个 ${BASE_URL} 上再调一个别的写接口，不是在验前面那步生效。"""
    w = no_readback([
        _s("创建项目", "${BASE_URL}/api/projects", "POST"),
        _s("创建另一个无关对象", "${BASE_URL}/api/envs", "POST"),
    ])
    assert len(w) == 2
