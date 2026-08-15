"""分支级验收 + target_level 理由。

**为什么要分支级**：`check_deliverable` 是按单条查的，而验收是"这一批做完了没有"。
逐条点几十次之后没人会真的点完，于是「做完了吗」实际上没人回答 —— 这正是之前
每一轮都要人肉逐条查库的原因。用户原话：「有一个总的状态来验收，不要一个个来」。

**为什么阻塞和脆弱点要分开**：原来只有 `owes` 一个信号（"欠 api"），而
"接口有一步真挂了"和"接口跑绿了但异步断言抢跑"在 owes 里长得一模一样，
要做的事却完全不同 —— 一个改断言，一个加 retry_timeout_ms。

**为什么要 target_level_reason**：只有 target_level 一个值时，人分不出
「判断过这条不需要 UI」和「没想、用了默认值」。实测被直接问过。
"""
from __future__ import annotations

import inspect

from app.mcp.tools import deliverable, test_cases


def test_有分支级验收入口():
    assert hasattr(deliverable, "check_branch"), "没有分支级验收"
    src = inspect.getsource(deliverable.check_branch)
    assert "check_deliverable" in src, "没复用单条判据 —— 两套判据必然对不上"


def test_汇总必须分开报阻塞和脆弱点():
    src = inspect.getsource(deliverable.check_branch)
    for k in ("可交付", "有阻塞", "有脆弱点", "待你审"):
        assert k in src, f"summary 里缺「{k}」"
    assert "firstBlocker" in src, "每行没说清卡在哪 —— 那还是得点进去看"
    assert "riskKinds" in src, "没报脆弱点种类"


def test_验收结论要说清审核不挡回归():
    src = inspect.getsource(deliverable._branch_verdict)
    assert "不挡回归" in src, "不说清的话人会以为不审就跑不了"


def test_只读不改状态():
    src = inspect.getsource(deliverable)
    for forbidden in ("session.commit", "session.add", "apply_case_status"):
        assert forbidden not in src, f"验收工具不该有 {forbidden}"


def test_不做某一维没给理由要提醒():
    """只提醒不硬拦 —— 真有确实不需要的，写一句话的成本就够了。"""
    src = inspect.getsource(test_cases.create_case)
    assert 'target_level != "full"' in src, '没判「不做全部维度」的情况'
    assert "target_level_reason" in src, "没提醒补理由"
    assert "_qualityWarnings" in src, "提醒没进回执，CC 看不到"
    # 不许硬拦
    assert 'return {"error"' not in src.split('target_level != "full"')[1][:400], \
'变成硬拦了 —— 说好只提醒'


def test_两个入口都能写理由():
    """建的时候能写，改的时候也要能改 —— 否则第一次没写就永远补不上。"""
    for fn in (test_cases.create_case, test_cases.update_case):
        assert "target_level_reason" in inspect.signature(fn).parameters, \
            f"{fn.__name__} 收不了 target_level_reason"


def test_理由存在用例上():
    from app.models.case import Case
    assert "target_level_reason" in {c.name for c in Case.__table__.columns}
