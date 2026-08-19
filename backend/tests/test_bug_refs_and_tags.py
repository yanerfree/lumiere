"""关联 bug + 标签 —— 「用例被 bug 阻塞了，bug 写哪里、什么时候能继续」。

在此之前平台没有任何地方能记这件事：只有 `blocked_external`（等环境/等接口上线，
是"我还写不了"）、`remark` 自由文本、和绑在探索测试会话上的 ExploratoryFinding
（跟用例不挂钩）。于是"这条为什么一直红"只能靠人记着。

这里钉住的是那条状态线，中间不允许平台去判 bug 死活：

    open ──人/CC 标 fixed──▶ fixed（列表「待重跑」）──跑绿──▶ 自动摘掉关联
                                                    └─跑红──▶ 关联留着

两头都要钉：**跑绿要自动摘**（靠 CC 记得回来清，忘一次就永远挂着一条假阻塞），
**open 绝不自动变 fixed**（那是判定 bug 死活，平台没有依据）。
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from app.core.exceptions import ValidationError
from app.services.bug_ref_service import (blocked_by_bug, clear_fixed_refs,
                                          normalize_bug_refs, normalize_tags,
                                          retest_pending)


def _case(refs=None, **kw):
    return SimpleNamespace(bug_refs=refs, tags=None, **kw)


# ── 入库校验 ─────────────────────────────────────────────────────

def test_只给单号也收():
    """最常见的写法就是丢一个单号进来，逼人写完整对象只会让人写在 remark 里。"""
    out = normalize_bug_refs(["UAG-123"])
    assert out == [{"ref": "UAG-123", "status": "open", "updatedAt": out[0]["updatedAt"]}]


def test_默认是阻塞中():
    """不写 status 默认 open —— 关联一个 bug 的语义就是「它卡着」。"""
    assert normalize_bug_refs([{"ref": "x"}])[0]["status"] == "open"


def test_乱写的状态硬拒():
    """收下 'closed'、'done' 这类近义词，后面所有判断全部失准。"""
    with pytest.raises(ValidationError):
        normalize_bug_refs([{"ref": "x", "status": "closed"}])


def test_没有ref的硬拒():
    with pytest.raises(ValidationError):
        normalize_bug_refs([{"status": "open", "note": "忘了写单号"}])


def test_url要是真链接():
    with pytest.raises(ValidationError):
        normalize_bug_refs([{"ref": "x", "url": "本地记了一下"}])


def test_同一个单号写两遍后写的算():
    out = normalize_bug_refs([{"ref": "A", "status": "open"}, {"ref": "A", "status": "fixed"}])
    assert len(out) == 1 and out[0]["status"] == "fixed"


def test_状态没变就不刷时间戳():
    """否则改个标题就把「什么时候标的 fixed」冲掉了 —— 而那正是判断
    「这条重跑过没有」的唯一线索。"""
    prev = normalize_bug_refs([{"ref": "A", "status": "fixed"}])
    again = normalize_bug_refs([{"ref": "A", "status": "fixed"}], prev)
    assert again[0]["updatedAt"] == prev[0]["updatedAt"]
    assert again[0]["fixedAt"] == prev[0]["fixedAt"]


def test_状态变了要刷时间戳():
    prev = normalize_bug_refs([{"ref": "A", "status": "open"}])
    now = normalize_bug_refs([{"ref": "A", "status": "fixed"}], prev)
    assert now[0]["updatedAt"] != prev[0]["updatedAt"] and now[0]["fixedAt"]


def test_传空数组等于清空():
    """「不再卡着」要有一个明确的表达方式，否则只能留着一条假阻塞。"""
    assert normalize_bug_refs([]) is None


def test_标签去重去空保序():
    assert normalize_tags(["冒烟", " 冒烟 ", "", "需要真数据"]) == ["冒烟", "需要真数据"]


def test_标签太长硬拒():
    with pytest.raises(ValidationError):
        normalize_tags(["长" * 33])


# ── 两个信号 ─────────────────────────────────────────────────────

def test_有open就是卡着():
    assert blocked_by_bug(_case([{"ref": "A", "status": "open"}])) is True
    assert retest_pending(_case([{"ref": "A", "status": "open"}])) is False, \
        "还卡着就不该催人重跑"


def test_全fixed就是可以继续了():
    """这就是「怎么知道这条用例可以继续」的答案。"""
    c = _case([{"ref": "A", "status": "fixed"}])
    assert blocked_by_bug(c) is False and retest_pending(c) is True


def test_一条open一条fixed算还卡着():
    c = _case([{"ref": "A", "status": "fixed"}, {"ref": "B", "status": "open"}])
    assert blocked_by_bug(c) is True and retest_pending(c) is False


def test_没关联的两个信号都是假():
    c = _case(None)
    assert blocked_by_bug(c) is False and retest_pending(c) is False


# ── 跑绿自动摘 ───────────────────────────────────────────────────

def test_跑绿摘掉已修的():
    c = _case([{"ref": "A", "status": "fixed"}])
    assert clear_fixed_refs(c) == 1 and c.bug_refs is None
    assert retest_pending(c) is False, "摘完「待重跑」提示要跟着消失"


def test_跑绿不动还卡着的():
    """一条修好了、另一条没修 —— 摘掉修好的那条，剩下的照旧卡着。"""
    c = _case([{"ref": "A", "status": "fixed"}, {"ref": "B", "status": "open"}])
    assert clear_fixed_refs(c) == 1
    assert [r["ref"] for r in c.bug_refs] == ["B"] and blocked_by_bug(c) is True


def test_没有已修的不白写一次():
    c = _case([{"ref": "B", "status": "open"}])
    assert clear_fixed_refs(c) == 0 and c.bug_refs == [{"ref": "B", "status": "open"}]


def test_跑绿这件事真的接在执行上():
    """只有函数没人调，等于「待重跑」永远挂着。"""
    from app.services.script_run_service import apply_case_status
    src = inspect.getsource(apply_case_status)
    assert "clear_fixed_refs" in src
    assert src.index("clear_fixed_refs") < src.index("elif run_mode"), \
        "摘关联要在 passed 分支里，跑红时绝不能摘"


def test_平台不会把open改成fixed():
    """判定 bug 死活是人的事，平台只认传进来的那个值。

    真要出错会怎么错：某处"顺手"把跑绿的用例的 open 关联标成 fixed ——
    于是"跑绿了所以 bug 修好了"，而现实里常是脚本绕过了那个 bug。
    """
    out = normalize_bug_refs([{"ref": "A", "status": "open"}],
                             [{"ref": "A", "status": "fixed", "updatedAt": "x"}])
    assert out[0]["status"] == "open", "库里是 fixed、传进来 open，必须听传进来的"
    c = _case([{"ref": "A", "status": "open"}])
    clear_fixed_refs(c)
    assert c.bug_refs[0]["status"] == "open", "摘关联只许删，不许改状态"


# ── 两个消费者 ───────────────────────────────────────────────────

def test_批量回归跳过卡bug的():
    """重跑一条已知因产品 bug 而红的用例，除了把维度打回 debugging、
    刷一条红记录之外没有任何信息量。"""
    from app.mcp.tools.ui_scripts import run_ui_scripts_batch
    src = inspect.getsource(run_ui_scripts_batch)
    assert "blocked_by_bug" in src
    assert "skippedBlockedByBug" in src, "静默跳过比跑一遍更糟 —— 人会以为它跑绿了"
    assert "ran = len(ids) - len(blocked_by_bug)" in src, \
        "通过率分母要扣掉跳过的，否则产品的问题记在测试头上"


def test_check_branch_把两个信号给CC():
    from app.mcp.tools.deliverable import check_branch
    src = inspect.getsource(check_branch)
    for k in ("blockedByBug", "retestPending", "卡在产品bug", "待重跑"):
        assert k in src, f"{k} 没给到 CC，它只能靠猜"


def test_工具描述里写清了这两个参数():
    """CC 只照工具描述调参 —— 描述里没有，参数等于不存在。"""
    from app.mcp import TOOL_CATALOG
    d = {t["name"]: t["description"] for t in TOOL_CATALOG}["tb_update_case"]
    assert "bug_refs" in d and "tags" in d
    assert "待重跑" in d, "没写清 fixed 之后会发生什么，CC 不会去标"


def test_列表筛选在SQL里做():
    """列表是分页的 —— 拿当前页在内存里过滤，会得到「第 3 页只剩 1 条」。"""
    from app.services.case_service import list_cases
    src = inspect.getsource(list_cases)
    assert "bug_state" in src and "@>" in src


def test_响应里带派生的两个布尔():
    """让前端自己算，列表和 CC 那边各算一遍必然分叉。"""
    from app.schemas.case import CaseResponse
    assert {"blocked_by_bug", "retest_pending", "bug_refs", "tags"} <= set(CaseResponse.model_fields)
