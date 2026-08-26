"""版本升级·分支对账的封样。文档：docs/version-upgrade-branch-diff.md

盯的是四件**错了就是假绿/假废**的事：

1. url 归一化和匹配 —— 对不上就漏命中，漏命中的用例进照抄堆自动过审（假绿）
2. 内容指纹覆盖三份产物 —— 只盖手工步骤的话，改了接口断言指纹照旧（假绿）
3. 自动过审四条件 —— 每一条都得真挡住，尤其"没对过账就不放"
4. 废弃证据硬校验 —— 不严，整套废弃审核就是个橡皮章（假废）
"""
import pytest

from app.services.branch_diff_service import (
    CHANGE_KINDS, _validate_changes, normalize_path, paths_match,
)
from app.services.branch_diff_review import _bite_ok, _evidence_gaps, _dims_for


# ── url 归一化 ───────────────────────────────────────────────

@pytest.mark.parametrize("raw,want", [
    # 最常见的那一对：git diff 里的路由声明 vs 步骤里存的完整 url
    ("/subscriptions/{id}/approve", "/subscriptions/{}/approve"),
    ("{{BASE_URL}}/api/v1/subscriptions/${subId}/approve?force=true",
     "/api/v1/subscriptions/{}/approve"),
    # 第一段的变量是 base url（展开含 scheme+host），整段丢掉而不是压成通配 ——
    # 压成通配会凭空多一段，于是 /{}/api/x 跟 /api/x 永远对不上
    ("{{BASE_URL}}/api/x", "/api/x"),
    ("${BASE_URL}/api/x", "/api/x"),
    ("http://127.0.0.1:8756/api/v1/cases", "/api/v1/cases"),
    ("https://host/api/v1/cases/", "/api/v1/cases"),
    # id 段：数字、UUID、{}、:id 都压成通配
    ("/cases/123/steps", "/cases/{}/steps"),
    ("/cases/3f2504e0-4f89-11d3-9a0c-0305e82c3301", "/cases/{}"),
    ("/cases/:caseId/steps", "/cases/{}/steps"),
    # query 和 fragment 剥掉
    ("/cases?page=1&size=20", "/cases"),
    ("/cases#top", "/cases"),
    # 段里混了变量 —— 整段当通配，别猜
    ("/files/report-${runId}.json", "/files/{}"),
    # 空的和只有斜杠的不炸
    ("", "/"),
    ("/", "/"),
    (None, "/"),
])
def test_归一化(raw, want):
    assert normalize_path(raw) == want


@pytest.mark.parametrize("a,b", [
    # 一边带部署前缀一边没带 —— 这是 git diff 对步骤 url 的常态
    ("/api/v1/subscriptions/{}/approve", "/subscriptions/{}/approve"),
    ("/api/v1/cases", "/cases"),
    # 通配对**字面段**：/approvals/{id} 要能命中 /approvals/pending
    # （不能只验通配对通配 —— 那两边归一化后本来就一模一样，恒真）
    ("/approvals/{}", "/approvals/pending"),
    ("/cases/{}/steps", "/cases/abc/steps"),
    # 完全相同
    ("/cases", "/cases"),
])
def test_匹配上(a, b):
    assert paths_match(a, b)
    assert paths_match(b, a), "匹配必须对称，否则谁在左边成了结果的一部分"


@pytest.mark.parametrize("a,b", [
    ("/cases", "/plans"),
    ("/api/v1/cases", "/api/v1/plans"),
    # 后缀匹配不许跨段：/ases 不是 /cases 的后缀段
    ("/cases", "/subcases"),
    # 单段纯通配不许命中一切 —— /{} 会匹配掉整个分支
    ("/{}", "/cases"),
    ("/{}", "/api/v1/cases"),
])
def test_匹配不上(a, b):
    assert not paths_match(a, b)


def test_单段通配不命中一切():
    """`/{}`（比如 CC 报了个 `/{id}`）如果被当成能匹配任何路径，
    一条变更就会把整个分支的用例全部拖进要改堆 —— 清单等于没有信息。"""
    for other in ("/cases", "/api/v1/plans/9/run", "/x"):
        assert not paths_match("/{}", other)


def test_偏向多命中的方向():
    """匹配故意宽 —— 两个方向的错代价差一个量级：
    多命中只是多过一次 AI 审，漏命中是这条用例进照抄堆自动过审、没人再看一眼。"""
    # 步骤里带前缀 + 真实 id，变更报的是路由声明
    step = normalize_path("{{BASE_URL}}/api/v1/subscriptions/42/approve?x=1")
    change = normalize_path("/subscriptions/{id}/approve")
    assert paths_match(step, change)


# ── changes 校验 ────────────────────────────────────────────

def test_坏数据不许静默跳过():
    """静默跳过一条 removed，那条用例就进照抄堆自动过审了 —— 所以整批拒绝。"""
    ok, errs = _validate_changes([{"url": "/x", "method": "POST", "kind": "typo"}])
    assert not ok and errs

    ok, errs = _validate_changes([{"url": "/x", "kind": "removed"}])
    assert not ok and any("method" in e for e in errs), \
        "同一个 url 不同 method 是不同端点，缺 method 必须报错"

    ok, errs = _validate_changes([{"url": "", "method": "GET", "kind": "removed"}])
    assert not ok and errs


def test_字段变了必须说变成什么():
    """detail 为空的 field_changed 等于没落清单 —— 拿到它的人还得重读一遍 diff。"""
    ok, errs = _validate_changes(
        [{"url": "/x", "method": "POST", "kind": "field_changed"}])
    assert not ok and any("detail" in e for e in errs)
    # removed 不要求 detail（"没了"本身就说完了）
    ok, errs = _validate_changes([{"url": "/x", "method": "POST", "kind": "removed"}])
    assert ok and not errs


def test_空的changes报错而不是当成零变更():
    """当成"零变更"会让整个分支判进照抄堆全部自动过审。"""
    for bad in (None, [], "not a list"):
        ok, errs = _validate_changes(bad)
        assert not ok and errs


def test_added是合法kind():
    """新端点必须报得上来 —— 它不命中任何老用例，不报就零覆盖且永远不报错。"""
    assert "added" in CHANGE_KINDS
    ok, errs = _validate_changes([{"url": "/new", "method": "POST", "kind": "added"}])
    assert ok and not errs


def test_method大小写归一():
    ok, _ = _validate_changes([{"url": "/x", "method": "post", "kind": "removed"}])
    assert ok[0]["method"] == "POST"


# ── 自动过审的条件 ──────────────────────────────────────────

def test_维度按承诺算():
    assert _dims_for("spec") == ["manual"]
    assert _dims_for("spec_api") == ["manual", "api"]
    assert _dims_for("full") == ["manual", "api", "ui"]
    assert _dims_for(None) == ["manual"], "没写就是 spec"


class _FakeCase:
    def __init__(self, **kw):
        self.bite_result = kw.get("bite_result")


def test_没做过咬合检查不算断言有效():
    ok, why = _bite_ok(_FakeCase(bite_result=None), "fp1")
    assert not ok and "lum_check_assertion_bite" in why


def test_咬合结论过期不算():
    """内容改过之后，上一版的咬合结论说明不了这一版的断言 —— 必须重跑。"""
    case = _FakeCase(bite_result={"fingerprint": "old", "summary": {"bites": 3, "stillGreen": 0}})
    ok, why = _bite_ok(case, "new")
    assert not ok and "过期" in why


def test_有恒真嫌疑的断言不许自动过审():
    """动作被跳掉还照样绿 = 恒真嫌疑，放进回归就是假绿。"""
    case = _FakeCase(bite_result={"fingerprint": "fp", "summary": {"bites": 1, "stillGreen": 2}})
    ok, why = _bite_ok(case, "fp")
    assert not ok and "恒真" in why


def test_一步都没红证明不了什么():
    case = _FakeCase(bite_result={"fingerprint": "fp", "summary": {"bites": 0, "stillGreen": 0}})
    ok, _ = _bite_ok(case, "fp")
    assert not ok


def test_咬得住才过():
    case = _FakeCase(bite_result={"fingerprint": "fp", "summary": {"bites": 3, "stillGreen": 0}})
    ok, why = _bite_ok(case, "fp")
    assert ok and why == ""


def test_四条件判定的返回必须是三元组():
    """`return False, ("很长的" "字符串", False)` 会把第三个值括进字符串组，
    变成 (str, False) 当第二个元素 —— **返回的是二元组**，调用方
    `ok, detail, spec_only = why` 当场 ValueError。

    活体跑第一步就炸在这上面（三处都这么写的）。纯函数单测覆盖不到它：
    _bite_ok / _evidence_gaps 都测了，而这个错在 auto_approve_reason 里，
    它要 session。所以直接对源码钉住形状：那三个 return 的第三个值必须在括号外。
    """
    import ast
    import inspect
    import textwrap

    from app.services import branch_diff_review

    src = textwrap.dedent(inspect.getsource(branch_diff_review.auto_approve_reason))
    fn = ast.parse(src).body[0]
    for node in ast.walk(fn):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        # 允许 `return None`（命中清单那条路）
        if isinstance(node.value, ast.Constant) and node.value.value is None:
            continue
        assert isinstance(node.value, ast.Tuple), \
            f"第 {node.lineno} 行的 return 不是元组"
        assert len(node.value.elts) == 3, (
            f"第 {node.lineno} 行的 return 只有 {len(node.value.elts)} 个值 —— "
            "多半是把第三个值括进了字符串组：`(\"a\" \"b\", False)` 是一个元组，"
            "不是两个返回值。把标志位挪到括号外面。"
        )


def test_两条回归路径都要推维度状态():
    """§7.1 那个洞：计划执行路径只调 record_run（记执行、进通过率、出报告），
    从不调 apply_case_status —— 于是走计划跑哪怕全绿，api_status/ui_status
    一动不动，用例永远进不了「待审」：你会看到一份 100% 通过的报告和一批
    还是草稿的用例。

    这是漏不是设计：apply_case_status 的 docstring 自己写着「只有 regression
    （**计划**/批量回归）失败才是真信号」，它就是按"计划会调我"写的。
    同一平台里 adhoc 批量调了，计划没调。

    钉住两条路都调 —— 真跑一次计划要有被测系统和真脚本，而这个洞的形状
    恰好是"某个调用不存在"，结构封样正好治它。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app/engine/tasks"
    for name in ("execution.py", "adhoc_execution.py"):
        src = (root / name).read_text(encoding="utf-8")
        assert "apply_case_status" in src, (
            f"{name} 没有调 apply_case_status —— 这条路跑绿也不会推维度状态，"
            "用例永远进不了待审（100% 通过的报告 + 一批草稿用例）"
        )


def test_废弃的用例四处都排除():
    """§7.3：待办队列 / 交付门禁 / 批量回归 / 建计划 都要排除 deprecated。

    这个洞不补，废弃审核做出来也没用：废掉一条用例，它照样进待办、
    照样进回归、照样算进通过率分母。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app"
    for rel, what in [
        ("mcp/tools/duty.py", "待办队列"),
        ("mcp/tools/deliverable.py", "交付门禁"),
        ("mcp/tools/test_cases.py", "lum_list_cases"),
        ("services/case_service.py", "用例列表接口"),
        ("engine/tasks/adhoc_execution.py", "批量回归"),
        ("services/plan_service.py", "建计划"),
    ]:
        src = (root / rel).read_text(encoding="utf-8")
        assert "deprecated" in src, f"{what}（{rel}）没有排除 deprecated"


# ── 废弃证据 ────────────────────────────────────────────────

def test_没证据不许提请废弃():
    """「我在页面上找不到」不等于「这个功能没了」。"""
    gaps = _evidence_gaps(None)
    assert gaps and len(gaps) == 2, "正面和反面都缺，要两条都报"


def test_缺反面证据不许过():
    """改名、挪菜单、拆页面在 UI 上都长得像"没了" —— 反面排查是硬要求。"""
    gaps = _evidence_gaps({"apiProbe": [{"url": "/x", "method": "POST", "status": 404}]})
    assert any("searchedElsewhere" in g for g in gaps)


def test_端点还应答就不是没了():
    gaps = _evidence_gaps({
        "apiProbe": [{"url": "/x", "method": "POST", "status": 200}],
        "searchedElsewhere": ["搜了全站菜单"],
    })
    assert any("404/410" in g for g in gaps), "200 是「变了」不是「没了」，该走要改堆"


def test_正反两面齐了才受理():
    gaps = _evidence_gaps({
        "apiProbe": [{"url": "/x", "method": "POST", "status": 404}],
        "searchedElsewhere": ["搜了全站菜单没有同义入口", "grep 前端路由表没有对应 path"],
    })
    assert gaps == []


def test_UI证据也算正面():
    """平台没有浏览器，UI 那半边只能 CC 交 —— 交了就该认。"""
    gaps = _evidence_gaps({
        "uiProbe": [{"page": "/subs", "找了什么": "新增入口", "结论": "不在"}],
        "searchedElsewhere": ["确认不是改名/拆页面"],
    })
    assert gaps == []
