"""带步骤新建的用例，手工维度不该停在 draft。

活体跑回推链路时撞到的：MCP 建了一条带 3 步的用例，跑完接口全绿，
`tb_check_branch` 却一直挂着「有脆弱点：manual 维度还在 draft」，
判词还让人去"改一下步骤重存" —— 而那条用例从头到尾没错过。

根子在时序：`sync_manual_status` 跑在 flush **之前**，列的 server_default 还没落下来，
`manual_status` 是 None，于是 `in ("draft","debugging")` 两个分支都不命中。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services.case_service import sync_manual_status


def _case(status, steps):
    return SimpleNamespace(manual_status=status, steps=steps, ui_status="draft",
                           api_status="draft", review_status=None)


def test_新建时状态还是None也要推到completed():
    c = _case(None, [{"seq": 1, "action": "点保存", "expected": "列表出现该条"}])
    sync_manual_status(c)
    assert c.manual_status == "completed"


def test_没步骤时不乱推():
    c = _case(None, [])
    sync_manual_status(c)
    assert c.manual_status in (None, "draft")


def test_已有draft带步骤照旧推进():
    c = _case("draft", [{"seq": 1}])
    sync_manual_status(c)
    assert c.manual_status == "completed"


def test_步骤被清空要退回draft():
    c = _case("completed", [])
    sync_manual_status(c)
    assert c.manual_status == "draft"


def test_建用例路径上真的调了它():
    """漏了这一步，新建的用例状态就永远靠后面某次保存"顺手"修正。"""
    import inspect

    from app.services import case_service
    src = inspect.getsource(case_service._build_and_flush)
    assert "sync_manual_status(case)" in src
    assert src.index("sync_manual_status(case)") < src.index("session.add(case)")
