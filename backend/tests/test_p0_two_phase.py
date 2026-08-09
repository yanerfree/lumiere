"""P0 两阶段门禁封样。

这道门禁原来只有第一阶段：`tb_create_case` 拒绝 P0 声明 target_level=full。
实测（真 MCP 连接）发现两次调用就绕过去了 —— 先建 spec，再分别 sync 接口场景
和 UI 脚本，中间没有任何人确认过，三件套照样同源直出。
而 `has_confirmed_expected` 在唯一调用点写死 False，"人确认"这一步压根不存在。

所以这里钉的是**两阶段各自的那道闸**，以及"确认会失效"这条。
"""
from datetime import datetime, timezone

from app.services import intake_gate

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


# ── 第一阶段：建用例时不许声明 full ──

def test_P0直出三件套被拒():
    assert intake_gate.check_p0_two_phase("P0", "full", False)


def test_P0只出步骤放行():
    assert intake_gate.check_p0_two_phase("P0", "spec", False) == []


def test_非P0直出三件套不拦():
    """这道闸的成本只花在挂了就得停线的那一档上。"""
    for p in ("P1", "P2", "P3"):
        assert intake_gate.check_p0_two_phase(p, "full", False) == []


# ── 第二阶段：往已有 P0 上挂产物时看确认了没有 ──

def test_P0没确认预期不许挂接口场景():
    problems = intake_gate.check_p0_artifact("P0", None, "api", "TC-X-00001")
    assert problems
    assert "接口场景" in problems[0]


def test_P0没确认预期不许挂UI脚本():
    problems = intake_gate.check_p0_artifact("P0", None, "ui", "TC-X-00001")
    assert problems
    assert "UI 脚本" in problems[0]


def test_确认之后放行():
    assert intake_gate.check_p0_artifact("P0", NOW, "api") == []
    assert intake_gate.check_p0_artifact("P0", NOW, "ui") == []


def test_非P0不受第二阶段影响():
    """P1/P2 照旧直接挂，不然回推链会整个卡住。"""
    for p in ("P1", "P2", "P3", "", None):
        assert intake_gate.check_p0_artifact(p, None, "api") == []


def test_拦住时要说清怎么解():
    """被拦住又不知道怎么办，CC 会开始想办法绕（改优先级、换工具）。"""
    msg = intake_gate.check_p0_artifact("P0", None, "ui", "TC-X-00001")[0]
    assert "确认预期结果" in msg          # 去哪儿点
    assert "用例详情" in msg              # 在哪一页
    assert "自动失效" in msg              # 确认不是终身通行证


def test_用例编号写进提示里():
    """一次回推可能涉及好几条用例，不说是哪条等于让人自己猜。"""
    assert "TC-ABC-00007" in intake_gate.check_p0_artifact("P0", None, "api", "TC-ABC-00007")[0]
