"""维度显示与整体状态：**「不做」和「没做完」不能长得一样**。

来自一次真实的误读：列表页上 TC-FWGL-00005/00006 显示「UI·草稿」，被问
「为什么 UI 是草稿，是不是还没做完」。实际它们 target_level=spec_api ——
UI 那一维压根不在计划里，永远不会变成「完成」，人却会一直等它变。

同一行还有第二个矛盾：三件套全绿、审核写着「待审」，而最左边的「状态」列
写着「草稿」。人看列表第一眼看的就是状态列，它说草稿就等于说这条没做完。
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from app.services.script_run_service import sync_after_plan_change, sync_review_status

ROOT = Path(__file__).resolve().parents[2]


def _case(target="full", manual="completed", api="completed", ui="completed",
          review=None, lifecycle="draft"):
    return SimpleNamespace(target_level=target, manual_status=manual, api_status=api,
                           ui_status=ui, review_status=review, lifecycle_status=lifecycle)


def test_维度齐了整体状态自动变完成():
    c = _case()
    sync_review_status(c)
    assert c.lifecycle_status == "done", "三件套齐了状态列还写草稿 —— 同一行自相矛盾"
    assert c.review_status == "pending"


def test_只算target_level要求的维度():
    """spec_api 不做 UI —— UI 停在草稿不该拖住整体状态。"""
    c = _case(target="spec_api", ui="draft")
    sync_review_status(c)
    assert c.lifecycle_status == "done", "UI 不在计划里，却把整体状态拖在草稿"


def test_维度退回则整体状态跟着退回():
    c = _case(api="debugging", lifecycle="done", review="pending")
    sync_review_status(c)
    assert c.lifecycle_status == "draft"
    assert c.review_status is None


def test_废弃不许被自动推进覆盖():
    """「废弃」是人的决定，任何自动推进都不许碰。"""
    c = _case(lifecycle="deprecated")
    sync_review_status(c)
    assert c.lifecycle_status == "deprecated"


def test_人已审过的不被重跑抹掉():
    c = _case(review="approved", api="debugging")
    sync_review_status(c)
    assert c.review_status == "approved"


# ── 改覆盖计划 ────────────────────────────────────────────────────
# 2026-08-31 用户截图：一行里同时写着「UI·草稿」「状态·完成」「审核·通过」。
# 成因是 target_level 在 update_case **之后**单独赋值、没人重算派生状态 ——
# 一条 spec_api 的用例三维齐了自动「完成+待审」，后来把计划提到 full，
# 多出来的 UI 维摆在那儿没做，状态却还冻在「完成」上。


def test_计划提高后整体状态退回草稿():
    """spec_api 做完了 → 提到 full → UI 那一维还欠着，就不再是「完成」。"""
    c = _case(target="spec_api", ui="draft", lifecycle="done", review="pending")
    c.target_level = "full"
    sync_after_plan_change(c)
    assert c.lifecycle_status == "draft", "计划多了一维，状态列还写「完成」—— 同一行自相矛盾"
    assert c.review_status is None


def test_人审过的计划提高后状态也要退回_但审核结论不动():
    """**这条是那批脏数据的正解。**

    审核是人的判断、审的是当时那几维，不能被自动流程抹掉；但「状态」列说的是
    「做完没有」，计划多一维就是没做完 —— 两件事，不能一起冻住。
    """
    for verdict in ("approved", "rejected", "inconclusive"):
        c = _case(target="spec_api", ui="draft", lifecycle="done", review=verdict)
        c.target_level = "full"
        sync_after_plan_change(c)
        assert c.lifecycle_status == "draft", f"{verdict} 把整体状态一起冻住了"
        assert c.review_status == verdict, f"{verdict} 被自动流程改掉了"


def test_计划降低后整体状态跟着变完成():
    """反过来也要走：full 降到 spec_api，UI 不再算数，这条就齐了。"""
    c = _case(target="full", ui="draft", lifecycle="draft", review=None)
    c.target_level = "spec_api"
    sync_after_plan_change(c)
    assert c.lifecycle_status == "done"
    assert c.review_status == "pending"


def test_改计划也不许碰废弃():
    c = _case(target="spec_api", ui="draft", lifecycle="deprecated", review="approved")
    c.target_level = "full"
    sync_after_plan_change(c)
    assert c.lifecycle_status == "deprecated"


def test_改计划要走请求对象而不是事后赋值():
    """MCP 改计划**必须**把 target_level 传进 UpdateCaseRequest。

    事后单独赋 `case.target_level = ...` 有两处坏，而且都不报错：
      ① `@audit_log` 只认传进去的请求对象（`core/audit.py:_extract_changes`），
         绕开它 = 改计划这件事一个字都不记账。实测问「谁把计划提到 full 的」
         查不出来 —— 而 CC 是唯一在改它的人。项目规则：新加写操作必须记账。
      ② 重算得自己记着调，两套流程并存，漏一条就继续产矛盾行。
    走请求对象则记账和重算都归 update_case 一处管。
    """
    mcp = (ROOT / "backend/app/mcp/tools/test_cases.py").read_text(encoding="utf-8")
    i = mcp.index("UpdateCaseRequest(")
    j = mcp.index("update_case(session", i)
    assert "target_level=target_level" in mcp[i:j], \
        "lum_update_case 没把 target_level 传进请求对象 —— 改计划不记账"
    assert "case.target_level = " not in mcp, \
        "又在 update_case 之后单独赋计划了，绕开了记账"
    svc = (ROOT / "backend/app/services/case_service.py").read_text(encoding="utf-8")
    assert "sync_after_plan_change" in svc, "update_case 改完 target_level 没重算"


def test_更新接口收得下覆盖计划():
    """详情页的「计划·」下拉一直在传 targetLevel，而 UpdateCaseRequest 没这个字段 ——
    pydantic 默认 extra='ignore'，于是弹「保存成功」、库里一个字没变。"""
    src = (ROOT / "backend/app/schemas/case.py").read_text(encoding="utf-8")
    i = src.index("class UpdateCaseRequest")
    j = src.index("class BatchCaseRequest")
    assert "target_level" in src[i:j], "UpdateCaseRequest 收不下 target_level，详情页改了存不进去"


def test_建用例时计划要在建之前就位():
    """create_case 内部的 sync 跑在建的那一刻。target_level 建完再赋，
    那一刻它还是默认 spec —— 一条 full 的用例刚建出来就顶着「完成 + 待审」。"""
    src = (ROOT / "backend/app/mcp/tools/test_cases.py").read_text(encoding="utf-8")
    i = src.index("CreateCaseRequest(")
    j = src.index("create_case(session", i)
    assert "target_level=target_level" in src[i:j], \
        "lum_create_case 没把 target_level 带进 CreateCaseRequest"


# ── 显示层 ──────────────────────────────────────────────────────

def test_接口把target_level给前端():
    """没有它，列表页分不出「UI 草稿」是还没做还是本来就不做。"""
    src = (ROOT / "backend/app/schemas/case.py").read_text(encoding="utf-8")
    i = src.index("class CaseResponse")
    assert "target_level" in src[i:i + 2000], "CaseResponse 不带 target_level"


def test_两个页面都按计划翻译不做():
    for rel in ("frontend/src/pages/cases/CaseManagement.jsx",
                "frontend/src/pages/cases/CaseDetail.jsx"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "dimPlanned" in src and "NOT_PLANNED" in src, f"{rel} 没区分「不做」"
        assert re.search(r"label:\s*'无'", src), f"{rel} 没有「无」这个显示词"
        assert "dimBadge" in src, f"{rel} 定义了却没用上"


def test_详情页要显示覆盖计划():
    """CC 靠 target_level 决定做几维，人在页面上看不见它就只能靠猜。

    详情页叫「计划·」，列表页那一列叫「覆盖」（实际做到哪一步）—— 两个词
    必须分开：一个是打算做几维，一个是做到什么程度。用同一个词就又变成
    「覆盖和三件套重复了」那个问题。
    """
    src = (ROOT / "frontend/src/pages/cases/CaseDetail.jsx").read_text(encoding="utf-8")
    assert "计划·" in src, "详情页没显示覆盖计划"
    assert "setTargetLevel" in src, "覆盖计划不可改"


def test_类型只分场景和单接口且说清跟UI无关():
    """type 的存储值 e2e/api 一直是「端到端场景 / 单接口」的意思，但从没写清楚，
    于是被当成「做不做 UI」用 —— 实测 6 条全是场景，3 条被标成了 api。
    做几维是 target_level 的事：一条单接口用例也可能要验页面报错提示。"""
    src = (ROOT / "frontend/src/pages/cases/CaseManagement.jsx").read_text(encoding="utf-8")
    assert "'单接口'" in src and "'场景'" in src, "类型列没按场景/单接口显示"
    mcp = (ROOT / "backend/app/mcp/__init__.py").read_text(encoding="utf-8")
    assert "e2e=场景" in mcp, "工具说明没定义类型语义，CC 只能接着猜"
    assert "跟做不做 UI 无关" in mcp, "没说清类型和 target_level 的分工"


def test_列表两列不重名():
    """「覆盖」和「三件套」并排时看不出关系，被问「不觉得重复吗」。
    现在：类型（测什么形态）| 覆盖（三维各自到哪一步）。"""
    src = (ROOT / "frontend/src/pages/cases/CaseManagement.jsx").read_text(encoding="utf-8")
    assert "title: '三件套'" not in src, "还留着「三件套」"
    assert "title: '覆盖'" in src and "title: '类型'" in src


def test_人显式改的状态不被自动重算盖掉():
    """**人可以改状态，平台不拦。**

    自动重算是给"没人管"的情况兜底的，不是用来管人的。详情页有状态下拉，
    人要拍板说这条就是完成，那就是完成 —— 拦下来就又变成一个
    「弹保存成功、值没进去」，跟 target_level 那个漏字段是同一种病。

    盯的是**顺序**：显式赋值必须排在 `sync_after_plan_change` 之后。
    反过来的话重算会把人选的值冲掉，而且不报错、不留痕。
    """
    src = (ROOT / "backend/app/services/case_service.py").read_text(encoding="utf-8")
    i = src.index("sync_after_plan_change(case)")
    tail = src[i:i + 900]
    assert "data.lifecycle_status is not None" in tail, \
        "重算之后没把人显式设的 lifecycle_status 拨回来 —— 人选的值被平台冲掉了"
    assert "data.review_status is not None" in tail, \
        "同理，人显式设的 review_status 也会被冲掉"
