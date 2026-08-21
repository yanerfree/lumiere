"""审核的三道新闸：**没真跑不得通过**、**步骤↔脚本要对得上**、**跑完要清干净**。

对应 review-spec 的 §9 / §3 第 2、3 条 / §3 第 8 项。这个文件的作用是钉住
「什么情况下它不该报」——判据的价值全在反例上：

- 一条判据只会报不会放，人两周内就学会整体无视它（上一版评审说
  「缺少安全测试场景」就是这么废掉的）。
- 「无法审核」如果被当成打回，被测系统一挂整批用例全被判死刑，
  第二天没人敢信这套审核。

所以下面每组都有一条**"应该沉默"**的用例，它们比"应该报"的那几条更重要。
"""
from __future__ import annotations

from app.services.review import residue, run_outcome, step_coverage
from app.services.review.reviewer import score_and_verdict

_APPLICABLE = {"verification_depth": {"label": "验证点到位", "weight": 25, "normWeight": 1.0}}


def _kinds(findings):
    return [f["kind"] for f in findings]


# ── §9 归因：跑挂了怪谁 ──────────────────────────────────────────

def test_环境挂了算无法审核_不算用例的错():
    o = run_outcome.classify({"type": "ui", "error": "net::ERR_CONNECTION_REFUSED"})
    assert o["kind"] == run_outcome.ENV_DOWN
    assert o["kind"] in run_outcome.INCONCLUSIVE_KINDS
    # 环境类**不发 finding** —— 发了人会以为是用例写坏了
    assert run_outcome.to_finding(o) is None


def test_脚本自己跑不起来是blocker():
    o = run_outcome.classify({"type": "ui", "error": 'locator.click: waiting for selector "#x"'})
    assert o["kind"] == run_outcome.SCRIPT_BUG
    assert run_outcome.to_finding(o)["severity"] == "blocker"


def test_断言没过默认算被测系统的问题_不打回用例():
    """**这条是 §9 的核心**：默认成"用例的错"正好制造那个最坏后果 ——
    被测系统真有 bug 时用例被判死刑、bug 没人管。"""
    o = run_outcome.classify({"type": "api", "failed": 2, "failedSteps": ["验证:订阅生效"]})
    assert o["kind"] == run_outcome.SYSTEM_BUG
    f = run_outcome.to_finding(o)
    assert f["severity"] not in ("blocker", "major"), "被测系统的 bug 不该压垮用例"


def test_跑通了不发任何finding():
    o = run_outcome.classify({"type": "ui", "status": "passed"})
    assert o["kind"] == run_outcome.OK
    assert run_outcome.to_finding(o) is None


# ── §9 结论：没真跑成功就不可能 approved ─────────────────────────

def test_没跑成的一律不能是approved():
    for kind in run_outcome.INCONCLUSIVE_KINDS:
        out = score_and_verdict({"verification_depth": {"score": 95}}, [], _APPLICABLE,
                                run_state={"kind": kind, "reason": "x"})
        assert out["verdict"] == "inconclusive", f"{kind} 居然给过了"
        assert out["total"] >= 90, "无法审核不该顺手把分也扣了 —— 那是两件事"


def test_无法审核不等于打回():
    """既不算通过也不算打回。判成 rejected 的话，CC 会去改一条没毛病的用例。"""
    out = score_and_verdict({}, [], _APPLICABLE, run_state={"kind": run_outcome.NO_ENV})
    assert out["verdict"] == "inconclusive" != "rejected"


def test_有blocker时仍然是打回_不被无法审核盖掉():
    """恒真断言这种事实**跑不跑都成立**，不能因为这次没跑成就降级成"无法审核"，
    否则「没环境」会变成一块挡箭牌。"""
    out = score_and_verdict({}, [{"severity": "blocker", "kind": "tautology_assertion"}],
                            _APPLICABLE, run_state={"kind": run_outcome.NO_ENV})
    assert out["verdict"] == "rejected"


def test_跑通了照常给通过():
    out = score_and_verdict({"verification_depth": {"score": 90}}, [], _APPLICABLE,
                            run_state={"kind": run_outcome.OK})
    assert out["verdict"] == "approved"


def test_没传run_state时行为不变():
    """静态审核路径（不真跑）不该被这道闸误伤 —— 它本来就没声称跑过。"""
    out = score_and_verdict({"verification_depth": {"score": 90}}, [], _APPLICABLE)
    assert out["verdict"] == "approved"


# ── §3 第 2、3 条：步骤↔脚本对账 ─────────────────────────────────

_STEPS = [{"seq": 1, "action": "点击「弃用服务」", "expected": "状态徽标变为「已弃用」"},
          {"seq": 2, "action": "点击「下线服务」", "expected": "返回 404"}]


def test_步骤里的动作脚本里找不到就是blocker():
    script = 'page.get_by_role("button", name="弃用服务").click()\nexpect(page).to_have_title("x")'
    ks = _kinds(step_coverage.analyze(_STEPS, script))
    assert "step_action_not_in_script" in ks      # 步骤 2「下线服务」脚本里没有


def test_预期一条断言都没有是blocker():
    ks = _kinds(step_coverage.analyze(_STEPS, 'page.click("#a")\npage.click("#b")'))
    assert "no_assertion_for_expectations" in ks


def test_脚本全用testid时不报_不能逼人写中文字面量():
    """**最重要的反例**：key/testid 驱动的脚本是完全合法、而且更好的写法。
    锚点对不上是判据的局限，不是脚本的错 —— 报了就是在逼人改成写死中文。"""
    script = ('page.get_by_test_id("btn-deprecate").click()\n'
              'page.get_by_test_id("btn-offline").click()\n'
              'expect(page.get_by_test_id("badge")).to_be_visible()')
    assert step_coverage.analyze(_STEPS, script) == []


def test_前置和清理步骤不要求页面动作():
    steps = [{"seq": 1, "action": "前置：造一条「测试服务」", "expected": ""}]
    assert step_coverage.analyze(steps, 'expect(page).to_have_url("/x")') == []


def test_没脚本时不下结论():
    """「承诺要做 UI 却没脚本」是另一条判据的活，这里重复报等于同一件事罚两次。"""
    assert step_coverage.analyze(_STEPS, "") == []
    assert step_coverage.analyze(_STEPS, None) == []


def test_验证角色的接口步骤没断言是blocker():
    sc = [{"name": "验证:订阅已生效", "assertions": []}]
    assert "verify_step_without_assertion" in _kinds(step_coverage.analyze([], "x", sc))


def test_验证步骤有断言就不报():
    sc = [{"name": "验证:订阅已生效", "assertions": [{"path": "$.status"}]}]
    assert "verify_step_without_assertion" not in _kinds(step_coverage.analyze([], "x", sc))


# ── §3 第 8 项：跑完的残留 ───────────────────────────────────────

_CREATE = {"method": "POST", "url": "http://h/api/v1/services", "status": 201}


def test_造了没删要报():
    assert _kinds(residue.analyze([_CREATE])) == ["residue_not_cleaned"]


def test_各种形状的id都能配上删除():
    """**踩过的坑**：只认 UUID 和纯数字的话，`/services/abc-123` 配不上
    `POST /services`，"造了也删了"照样被报成残留 —— 一条假打回。"""
    for ident in ("3f2504e0-4f89-11d3-9a0c-0305e82c3301", "abc-123", "4211", "a1b2c3d4e5f6a7b8"):
        traf = [_CREATE, {"method": "DELETE", "url": f"http://h/api/v1/services/{ident}",
                          "status": 204}]
        assert residue.analyze(traf) == [], f"{ident} 没被认成 id 段"


def test_软删也算清理():
    traf = [_CREATE, {"method": "PUT", "url": "http://h/api/v1/services/abc-123/archive",
                      "status": 200}]
    assert residue.analyze(traf) == []


def test_版本段不能被当成id():
    """`v1` 带数字但不是 id。误伤它会把 `/api/v1/services` 砍成 `/api`，
    所有集合糊成一个，残留判定整个失真。"""
    traf = [{"method": "POST", "url": "http://h/api/v1/x", "status": 201},
            {"method": "DELETE", "url": "http://h/api/v1/x/99", "status": 204}]
    assert residue.analyze(traf) == []


def test_删不掉是系统的问题_不是用例的():
    traf = [_CREATE, {"method": "DELETE", "url": "http://h/api/v1/services/abc-123",
                      "status": 409}]
    ks = _kinds(residue.analyze(traf))
    assert ks == ["cleanup_failed"]
    sev = [f["severity"] for f in residue.analyze(traf)]
    assert "blocker" not in sev and "major" not in sev


def test_登录和实例动作不算造数据():
    assert residue.analyze([{"method": "POST", "url": "http://h/api/v1/auth/login",
                             "status": 200}]) == []
    assert residue.analyze([{"method": "POST", "url": "http://h/api/v1/services/abc-123/publish",
                             "status": 200}]) == []


def test_没抓到流量就不下结论():
    assert residue.analyze([]) == []
    assert residue.analyze(None) == []
