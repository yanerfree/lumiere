"""P0 确认：从"平台拦人"改成"CC 带记录回推"的封样。

## 为什么改

原来是硬拦：P0 不许一次性出三件套 → CC 被拦住 → 人切到平台页面 → 找到那条用例 →
点「确认预期结果」→ CC 再回来挂。每条 P0 走一趟，是实打实的税。

两个理由让这道拦截站不住：

1. **支撑它的数据不对口。** 那个 80%（同源生成的断言退化成只看状态码）测的是
   **平台自己的 AI 生成器**，不是 CC。拿它去拦 CC，推断跨得太快。
2. **拦截本身也不硬。** 人在页面上点一下按钮，同样验证不了他真读了预期结果。
   两边都是形式，那就选便宜的那个。

所以：确认发生在人已经在的地方（CC 对话里），CC 把确认内容一起带上来，平台只存不拦。
风险是真的，所以信号留着 —— 没带确认记录就回一句提醒，进 warnings 不进 errors。
"""
from app.services import intake_gate


def test_P0三件套没带确认记录时给提醒():
    hints = intake_gate.p0_confirmation_hint("P0", "full", None)
    assert hints, "风险是真的，信号得留着"


def test_提醒里得说清该怎么做():
    """只说"有风险"没用 —— 得告诉 CC 去问人，以及怎么把确认带上来。"""
    msg = "\n".join(intake_gate.p0_confirmation_hint("P0", "full", ""))
    assert "确认" in msg
    assert "expected_confirmed_note" in msg
    assert "不拦" in msg, "得让 CC 知道这不是拦截，否则它会以为要改参数重试"


def test_带了确认记录就不再提醒():
    assert intake_gate.p0_confirmation_hint(
        "P0", "full", "已与张三确认：要验的是创建后列表里能查到该名字，不是只看 201"
    ) == []


def test_空白确认不算确认():
    """带个空串糊弄不行 —— 那和没带一样。"""
    assert intake_gate.p0_confirmation_hint("P0", "full", "   ")


def test_非P0不提醒():
    for p in ("P1", "P2", "P3"):
        assert intake_gate.p0_confirmation_hint(p, "full", None) == []


def test_只出步骤用例不提醒():
    """target_level=spec 本来就不涉及三件套一致性问题。"""
    assert intake_gate.p0_confirmation_hint("P0", "spec", None) == []
    assert intake_gate.p0_confirmation_hint("P0", "spec_api", None) == []


def test_拦截函数确实被删掉了():
    """留着旧函数会有人接着调，拦截就悄悄回来了。"""
    assert not hasattr(intake_gate, "check_p0_two_phase")
    assert not hasattr(intake_gate, "check_p0_artifact")


def test_改动预期结果会把确认记录整个清掉_四个字段一起():
    """只清 at/by 会留下**上一版**的确认内容：改完步骤后在平台点一次「确认」
    （那个接口只写 at/by），页面就把旧的 note 当成本次确认展示出来。
    这里盯的是"四个字段一起清"，不是"清了 at 就行"。"""
    import inspect

    from app.services import case_service

    src = inspect.getsource(case_service.update_case)
    body = src[src.index("_invalidate_confirmation"):]
    for f in ("expected_confirmed_at", "expected_confirmed_by",
              "expected_confirmed_actor", "expected_confirmed_note"):
        assert f"case.{f} = None" in body, f"作废时漏清 {f}"


def test_确认作废只在内容真变了时触发():
    """原样回写（前端全量 PUT 是常态）不该把确认打掉 —— 否则确认活不过一次保存。"""
    import inspect

    from app.services import case_service

    src = inspect.getsource(case_service.update_case)
    assert "if data.steps != case.steps:" in src
    assert "if data.expected_result != case.expected_result:" in src
