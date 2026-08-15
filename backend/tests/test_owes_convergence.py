"""断点续跑的收敛性（C2）——这个循环必须收敛。

dogfood 实测踩到的坑：`_owes` 原来用 `!= executable` 当判据，而正常流程是
    回推 → debugging → 平台跑通 → pending_review → **人审** → executable
`executable` 只有人工在页面上才会推进，所以等人审的用例会被 CC 一遍遍捡回来
重做 —— **循环永远不收敛**。UI 明明已经跑通了，`owes` 还挂着。

正确判据是「CC 还有活要干」：`pending_review` / `executable` 都轮到人了，CC 该放手。
手工步骤更特殊 —— 它是内容不是执行物，没有"跑通"这回事，按有没有写判。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.mcp.tools.test_cases import _owes


def case(**kw):
    base = dict(steps=[{"seq": 1}], target_level="full",
                manual_status="not_started", api_status="not_started", ui_status="not_started")
    base.update(kw)
    return SimpleNamespace(**base)


def test_written_steps_clear_manual():
    """写了步骤，manual 就不欠了 —— manual_status 是人工字段，别拿它当判据。"""
    assert "manual" not in _owes(case(steps=[{"seq": 1}], manual_status="not_started"))


def test_no_steps_owes_manual():
    assert "manual" in _owes(case(steps=[]))
    assert "manual" in _owes(case(steps=None))


def test_pending_review_is_not_owed():
    """平台跑通后是 pending_review —— 轮到人了，CC 不该再捡回来。这条是收敛的关键。"""
    o = _owes(case(ui_status="pending_review", api_status="pending_review"))
    assert "ui" not in o and "api" not in o, o


def test_executable_is_not_owed():
    assert _owes(case(ui_status="executable", api_status="executable")) == []


def test_debugging_still_owed():
    """debugging = CC 正做到一半，该继续。"""
    assert "ui" in _owes(case(ui_status="debugging", api_status="executable"))


def test_只有五个态_没有needs_fix():
    """原来还有 needs_fix「待修改」。它和 debugging 表达的是同一件事
    （这一维现在有问题、不能进回归），多一个态只是让人纠结该选哪个 ——
    2026-08 去掉了，有问题直接改「调试中」或「草稿」。库里当时一条都没有。
    """
    from app.mcp.tools import test_cases
    assert "needs_fix" not in test_cases._CC_TODO, "needs_fix 又回来了"
    assert set(test_cases._CC_TODO) == {"not_started", "draft", "debugging"}


def test_target_level_limits_scope():
    """spec_api 的用例 UI 没做也不算欠 —— 它的目标本来就不含 UI。"""
    o = _owes(case(target_level="spec_api", api_status="executable", ui_status="not_started"))
    assert o == [], o
    o2 = _owes(case(target_level="spec", api_status="not_started", ui_status="not_started"))
    assert o2 == [], o2


def test_full_loop_converges():
    """走一遍完整流程，最终必须收敛到空。"""
    c = case(steps=[], target_level="full")
    assert set(_owes(c)) == {"manual", "api", "ui"}
    c.steps = [{"seq": 1}]                       # 写了步骤
    assert set(_owes(c)) == {"api", "ui"}
    c.api_status = "pending_review"              # 接口跑通，等人审
    assert _owes(c) == ["ui"]
    c.ui_status = "pending_review"               # UI 跑通，等人审
    assert _owes(c) == []                        # ← 收敛
