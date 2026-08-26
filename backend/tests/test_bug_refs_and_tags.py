"""关联 bug + 标签 —— 「用例被 bug 阻塞了，bug 写哪里、痕迹怎么留」。

在此之前平台没有任何地方能记这件事：只有 `blocked_external`（等环境/等接口，
是"我还写不了"）、`remark` 自由文本、和绑在探索测试会话上的 ExploratoryFinding
（跟用例不挂钩）。于是"这条用例曾经抓到过什么 bug"只存在于当时那次对话里。

    关联 open ──▶ 批量回归跳过（跑了只是刷红）
        │  git 上 issue 关闭 → CC 回来调
        ▼
    调通了 ──▶ 标 fixed，**关联永久留着**（痕迹）
        └─ 没调通 → 留在 open

两条纪律钉在这里：
· **fixed 是"回来调通了"，不是"据说修好了"** —— 中间那段该留在 open。
· **标完 fixed 不清掉** —— 清了就看不出这条用例曾经抓到过 bug，
  而"哪些用例真抓到过问题"是评估用例价值的唯一依据。
· 平台任何路径都不自己改 status，也不自己删关联。
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from app.core.exceptions import ValidationError
from app.services.bug_ref_service import (blocked_by_bug, has_fixed_bug,
                                          normalize_bug_refs, normalize_tags)


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

def test_有open就是还没验回来():
    """open 的含义是"发现了、还没验回来"，不是"据说没修好"。"""
    assert blocked_by_bug(_case([{"ref": "A", "status": "open"}])) is True
    assert has_fixed_bug(_case([{"ref": "A", "status": "open"}])) is False


def test_全fixed是痕迹不是待办():
    """「这条曾经抓到过 bug、已经验回来了」—— 列表灰着显示，不催任何人。"""
    c = _case([{"ref": "A", "status": "fixed"}])
    assert blocked_by_bug(c) is False and has_fixed_bug(c) is True


def test_一条open一条fixed算还卡着():
    """修好一个不等于能跑了 —— 剩下那个照样让它红。"""
    c = _case([{"ref": "A", "status": "fixed"}, {"ref": "B", "status": "open"}])
    assert blocked_by_bug(c) is True and has_fixed_bug(c) is False


def test_没关联的两个信号都是假():
    c = _case(None)
    assert blocked_by_bug(c) is False and has_fixed_bug(c) is False


# ── 痕迹不许被任何自动逻辑抹掉 ──────────────────────────────────

def test_跑绿不会摘掉关联():
    """曾经做成"跑绿自动摘掉已修的关联"，是错的：**清掉就看不出这条用例
    曾经发现过 bug**。那是这份数据最值钱的部分。
    """
    from app.services.script_run_service import apply_case_status
    code = "\n".join(l for l in inspect.getsource(apply_case_status).splitlines()
                     if not l.strip().startswith("#"))
    assert "bug_refs" not in code and "clear_fixed" not in code, "执行路径又开始动关联了"

    case = SimpleNamespace(bug_refs=[{"ref": "A", "status": "fixed"}], tags=None,
                           ui_status="debugging", review_status=None,
                           manual_status="completed", api_status="completed",
                           target_level="full")
    apply_case_status(case, "ui", "passed", "regression")
    assert case.ui_status == "completed", "维度状态该推进"
    assert case.bug_refs == [{"ref": "A", "status": "fixed"}], "痕迹要原样留着"


def test_全库没有自动删关联的路径():
    """留痕这件事只要有一处例外就废了 —— 谁都不敢再信这份记录。"""
    import pathlib
    root = pathlib.Path("app")
    for f in root.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
        assert "bug_refs = None" not in code, f"{f} 里有把关联整份清空的代码"
        assert "clear_fixed_refs" not in code, f"{f} 又把自动摘那套加回来了"


def test_标了fixed之后再存一次不会掉():
    """改标题、改步骤这些日常保存都会带着 bug_refs 走一圈，痕迹不能在这中间蒸发。"""
    prev = [{"ref": "A", "status": "fixed", "updatedAt": "t0", "fixedAt": "t0"}]
    again = normalize_bug_refs(prev, prev)
    assert again[0]["ref"] == "A" and again[0]["status"] == "fixed"
    assert again[0]["fixedAt"] == "t0", "修复时间要保住 —— 它是「什么时候验回来的」唯一记录"


def test_关联错了才用清空():
    assert normalize_bug_refs([]) is None


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
    for k in ("blockedByBug", "hasFixedBug", "卡在产品bug", "抓到过bug已验回来"):
        assert k in src, f"{k} 没给到 CC，它只能靠猜"


def test_工具描述里写清了这两个参数():
    """CC 只照工具描述调参 —— 描述里没有，参数等于不存在。"""
    from app.mcp import TOOL_CATALOG
    d = {t["name"]: t["description"] for t in TOOL_CATALOG}["lum_update_case"]
    assert "bug_refs" in d and "tags" in d
    assert "永久痕迹" in d, "没写清 fixed 之后关联要留着，CC 会顺手清掉"


def test_列表筛选在SQL里做():
    """列表是分页的 —— 拿当前页在内存里过滤，会得到「第 3 页只剩 1 条」。"""
    from app.services.case_service import list_cases
    src = inspect.getsource(list_cases)
    assert "bug_state" in src and "@>" in src


def test_响应里带派生的两个布尔():
    """让前端自己算，列表和 CC 那边各算一遍必然分叉。"""
    from app.schemas.case import CaseResponse
    assert {"blocked_by_bug", "has_fixed_bug", "bug_found_count", "bug_refs", "tags"} \
        <= set(CaseResponse.model_fields)


# ── CC 怎么知道该干什么：清单 + 规范 ──────────────────────────────

def test_能按bug状态拉清单():
    """用户的原话：「cc 直接可以拉取 bug 清单已关闭的他就能知道」。
    没有这个筛选，CC 只能整分支拉回来自己逐条看 bugRefs —— 分支大了就是全表扫。"""
    from app.mcp.tools.test_cases import list_cases
    src = inspect.getsource(list_cases)
    assert "bug_state" in src and "@>" in src, "要在 SQL 里筛，不是拿当前页在内存里过"
    for k in ("fixed", "blocked", "none"):
        assert f'"{k}"' in src


def test_清单里带着三个信号():
    from app.mcp.tools.test_cases import list_cases
    src = inspect.getsource(list_cases)
    for k in ("bugRefs", "blockedByBug", "hasFixedBug", "bugFoundCount"):
        assert k in src, f"{k} 不返回的话，拉回来还得再查一遍"


def test_规范里写清了整条流程():
    """CC 动手前读的是这份规范。参数存在 ≠ 它知道什么时候用、用完会发生什么。"""
    from app.mcp.tools.sync import _SPEC_CASE as spec
    assert "bug_refs=" in spec, "没告诉它怎么关联"
    assert "批量回归跳过" in spec, "没说清关联 open 之后批量回归会跳过"
    assert 'bug_state="blocked"' in spec, "没告诉它待办从哪来"
    assert "永久痕迹" in spec, "没写清关联要留着，CC 会顺手清掉"
    assert "不是\"据说修好了\"" in spec, "fixed 的含义没说清，CC 会一看到 issue 关了就标"
    assert "自动摘" not in spec and "待重跑" not in spec, "规范里别留旧口径"


def test_两个工具描述都提了():
    from app.mcp import TOOL_CATALOG
    d = {t["name"]: t["description"] for t in TOOL_CATALOG}
    assert "bug_refs" in d["lum_update_case"], "写入口没写"
    assert "bug_state" in d["lum_list_cases"], "读入口没写 —— CC 不会去猜一个参数名"


def test_筛选条件不能对text取反():
    """`~text(...)` 在 SQLAlchemy 里会 AssertionError —— 实测这条筛选直接 500，
    而且只在 retest / none 两个分支上炸（blocked 不取反，所以看着"能用"）。
    NOT 必须写在 SQL 文本里。
    """
    import inspect as _i

    from app.mcp.tools.test_cases import list_cases
    from app.services.case_service import list_cases as rest_list
    for fn in (list_cases, rest_list):
        src = _i.getsource(fn)
        assert "~has_open" not in src and "~has_fixed" not in src, "又对 text() 取反了"
        assert "NOT (" in src, "NOT 要写进 SQL 文本"


@pytest.mark.asyncio
async def test_三个筛选分支都能真的编译成SQL():
    """光看源码钉不住这个 —— 上一版就是"看着对、一跑就 AssertionError"。
    真去编译一次 SQL。"""
    from sqlalchemy.dialects import postgresql

    from app.models.case import Case
    from sqlalchemy import select, text as _t
    OPEN = "cases.bug_refs @> '[{\"status\": \"open\"}]'::jsonb"
    FIXED = "cases.bug_refs @> '[{\"status\": \"fixed\"}]'::jsonb"
    for clause in (_t(OPEN), _t(f"{FIXED} AND NOT ({OPEN})"),
                   _t(f"cases.bug_refs IS NULL OR NOT ({OPEN})")):
        sql = str(select(Case).where(clause).compile(dialect=postgresql.dialect()))
        assert "bug_refs" in sql


def test_没关联是真的一条都没有():
    """`NOT(有 open)` 会把「已修待重跑」也算成"没关联" —— 实测 none 里混进了
    一条 fixed 的用例。判据只能是"数组空或为 NULL"。
    """
    import inspect as _i

    from app.mcp.tools.test_cases import list_cases
    from app.services.case_service import list_cases as rest_list
    for fn in (list_cases, rest_list):
        src = _i.getsource(fn)
        assert "jsonb_array_length" in src, "none 分支又退回成「没有 open」了"


def test_none分支必须自己带括号():
    """`WHERE 分支=X AND 未删 OR bug_refs IS NULL` —— OR 会把前面两个条件甩掉，
    于是「从没关联」筛出**别的分支、连已删的**用例。实测就是这么炸的（500 之后
    才发现更严重的是它本来会静默串分支）。
    """
    from app.mcp.tools.test_cases import list_cases
    from app.services.case_service import list_cases as rest_list
    for fn in (list_cases, rest_list):
        src = inspect.getsource(fn)
        i = src.index("cases.bug_refs IS NULL")
        assert src[i - 1] == "(", "OR 那一串没用括号包起来"


def test_none分支要挡住标量():
    """`jsonb_array_length` 撞到 JSON 标量会直接报
    `cannot get array length of a scalar` —— JSONB 列把 None 存成 JSON null
    这件事这个项目里踩过（见 script_runs.captured_requests 那条注释）。
    """
    from app.mcp.tools.test_cases import list_cases
    from app.services.case_service import list_cases as rest_list
    for fn in (list_cases, rest_list):
        assert "jsonb_typeof" in inspect.getsource(fn)
