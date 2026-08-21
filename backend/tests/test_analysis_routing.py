"""归因分流 —— 「不是所有的都要等人来确认」。

原来一律等人，理由是怕"跑不过就说是产品的锅"。但 CC 有代码、能活体复现，
大部分情况拦它没意义（用户原话：它需要活体验证并结合代码分析，是问题就按 skill 提单，
脚本问题改完回推，**只有不确定的和需求问题才要人**）。

所以改成**按证据齐不齐分流，不按类型分流**，并且用户拍了「硬性」：
产品缺陷要三样齐全才放行，缺一样落回等人确认。

甩锅为什么无收益：放行只让这条**回归不再刷红**，交付门禁照旧算「卡在产品缺陷」。
脚本类自证的闸门是「改完必须跑绿才关单」—— 跑不绿它自己关不掉。
"""
from __future__ import annotations

import inspect

from app.services.analysis_service import (CAUSES, DEFECT_EVIDENCE, NEEDS_HUMAN,
                                           SELF_SERVE, route)


def test_脚本自己错的不用等人():
    for cause in ("test_defect", "case_expired", "env_issue", "data_issue", "flaky"):
        where, missing = route({"cause": cause})
        assert where == "self_serve" and missing == [], cause


def test_需求问题和拿不准只有人能定():
    for cause in ("requirement_unclear", "unknown"):
        assert route({"cause": cause})[0] == "needs_human", cause
    assert "requirement_unclear" in CAUSES, "需求问题得有地方放 —— 原来只有四类，没有它"


def test_产品缺陷三样齐全才放行():
    ev = {"liveVerified": "重新调了 POST /services，仍然 500",
          "codeRefs": "app/api/services.py:88 没判空",
          "issue": "GH-123"}
    assert route({"cause": "product_defect", "evidence": ev}) == ("self_serve", [])


def test_产品缺陷缺一样就落回等人并说清缺什么():
    """硬性（用户拍的）：缺一样就不放行。而且要告诉它缺哪个，不然它只能猜。"""
    full = {"liveVerified": "x", "codeRefs": "y", "issue": "z"}
    for k in DEFECT_EVIDENCE:
        ev = {kk: vv for kk, vv in full.items() if kk != k}
        where, missing = route({"cause": "product_defect", "evidence": ev})
        assert where == "needs_human", f"缺 {k} 竟然放行了"
        assert any(k in m for m in missing), f"没说清缺的是 {k}"


def test_三样都缺时一次全列出来():
    where, missing = route({"cause": "product_defect", "evidence": {}})
    assert where == "needs_human" and len(missing) == 3


def test_evidence不是对象也不放行():
    assert route({"cause": "product_defect", "evidence": "我复现过了"})[0] == "needs_human"


def test_自证和等人两个集合不重叠():
    assert not (SELF_SERVE & NEEDS_HUMAN)
    assert "product_defect" not in SELF_SERVE, "产品缺陷得走证据检查，不能无条件自证"


# ── 状态流转接上了没有 ────────────────────────────────────────────

def test_归因会同步跟进单():
    """不同步的话单子永远停在「待分析」，中间几步是断的（上一版就是这样）。"""
    from app.services import analysis_service
    src = inspect.getsource(analysis_service.submit)
    assert "FailureTicket" in src
    assert 't.status = "fixing"' in src, "自证的要进「处置中」"
    assert 't.status = "known"' in src, "挂了单号的产品缺陷要进「已知问题」"
    assert 't.status = "analyzed"' in src, "等人确认的要进「等你确认」"


def test_人确认也同步跟进单():
    from app.services import analysis_service
    src = inspect.getsource(analysis_service.confirm)
    assert "FailureTicket" in src and "t.disposition = cause" in src
    assert '"known" if cause in ("product_defect", "requirement_unclear")' in src, \
        "产品缺陷/需求问题人拍板之后也不是「CC 去修」，该挂成已知问题"


def test_自证不给它免检的错觉():
    """返回里必须说清：产品缺陷放行只是不再刷红，交付门禁照旧算卡住。"""
    from app.services import analysis_service
    src = inspect.getsource(analysis_service.submit)
    assert "不是通过" in src and "回归不再刷红" in src
    assert "必须复跑跑绿" in src, "脚本类自证要说清闸门在哪"


def test_工具描述把规则写全了():
    """CC 只照描述调参 —— 描述里没有，规则等于不存在。"""
    from app.mcp import TOOL_CATALOG
    d = {t["name"]: t["description"] for t in TOOL_CATALOG}["tb_submit_analysis"]
    for k in ("liveVerified", "codeRefs", "issue", "requirement_unclear"):
        assert k in d
    assert "不是所有归因都要等人" in d and "甩锅没有收益" in d
