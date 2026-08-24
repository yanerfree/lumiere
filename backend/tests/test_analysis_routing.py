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
                                           SELF_SERVE, WAITING_ON_HUMAN,
                                           agreement_stats, route, sampled, submit)


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


def test_evidence两种形状都收():
    """活体撞出来的：自证要传对象（liveVerified/codeRefs/issue），
    而老校验只收数组（证据指针）—— 于是"自己处置不用等人"这条路压根走不通，
    每次都被"evidence 必须是非空数组"顶回来。
    """
    from types import SimpleNamespace

    from app.services.analysis_service import _validate_evidence
    run = SimpleNamespace(captured_requests=[1, 2, 3], screenshots=[],
                          error_summary="x", stdout="y", failure_phenomenon="p")
    assert _validate_evidence({"liveVerified": "重跑确认页面没这个链接"}, run) == []
    assert _validate_evidence([{"type": "error_summary", "ref": "x"}], run) == []
    assert _validate_evidence({}, run), "对象里什么都没有还是要拒"
    assert _validate_evidence([], run), "空数组还是要拒"


def test_对象里的items仍按指针校验():
    """混合形态：自证字段 + 证据指针。指针那部分不能因为套在对象里就免检。"""
    from types import SimpleNamespace

    from app.services.analysis_service import _validate_evidence
    run = SimpleNamespace(captured_requests=[], screenshots=[],
                          error_summary=None, stdout=None, failure_phenomenon=None)
    bad = _validate_evidence({"liveVerified": "a", "items": [{"type": "乱写", "ref": "x"}]}, run)
    assert bad, "items 里的类型写错了该报"


# ── 抽检：自动化会把体温计一起收走（2026-08-24）────────────────────

def test_抽检是按哈希的_同一条用例每次结果一样():
    """**反例：用随机。** 那样同一条用例反复提交归因，这次要等人、下次不用等，
    CC 从返回里分辨不出这是抽检还是判据变了，会当成平台行为不稳定。"""
    import uuid as _u
    for _ in range(20):
        cid = _u.uuid4()
        first = sampled(cid)
        assert all(sampled(cid) is first for _ in range(5)), f"{cid} 抽检结果会变"


def test_抽检比例大致是十分之一():
    """按哈希均匀抽 —— 偏得太多就代表不了总体，这个指标也就白留了。"""
    import uuid as _u
    hits = sum(1 for _ in range(4000) if sampled(_u.uuid4()))
    assert 250 <= hits <= 550, f"4000 条抽中 {hits} 条，偏离 10% 太远"


def test_抽中的仍然算自证_不能把CC拦下来():
    """抽检的语义是「人另外看一眼校准」，**不是「这条要等人」**。
    拦下来的话自证放行就白做了 —— 而自证正是这一轮要保住的东西。"""
    src = inspect.getsource(submit)
    assert 'where = "self_serve_sampled"' in src, "没在 submit 里挂抽检"
    assert 'self_serving = where in ("self_serve", "self_serve_sampled")' in src, \
        "抽中的必须仍然走自证那条分支，否则等于把 CC 拦下来了"


def test_真正在等人的只有两种():
    """`tb_list_pending_confirm` 默认列的就是这两种。自证放行的混进来的后果：
    队列里绝大多数不需要人动，人扫两眼就再也不看了。"""
    assert set(WAITING_ON_HUMAN) == {"needs_human", "self_serve_sampled"}


def test_抽检比例是写死的():
    """**反例：做成可配置。** 又一个能被调成 0 的开关 ——
    和 review-spec §3「检查项不做成可勾选」同一条纪律。"""
    src = inspect.getsource(__import__("app.services.analysis_service",
                                       fromlist=["x"]))
    assert "SAMPLE_EVERY = 10" in src
    assert "os.environ" not in src.split("SAMPLE_EVERY")[0][-500:], \
        "抽检比例不许从环境变量读"


def test_一致率把抽检和人主动确认分开算():
    """人主动去看的那批有选择偏差 —— 他挑的本来就是可疑的，算出来的一致率
    天然偏低。混在一起会误报成「CC 在系统性甩锅」，而那是要触发人工复核的告警。"""
    src = inspect.getsource(agreement_stats)
    assert "bySource" in src, "没把两种来源分开报"
    assert "self_serve_sampled" in src, "没按 route 区分抽检样本"
