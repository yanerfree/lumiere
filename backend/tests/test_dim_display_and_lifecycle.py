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

from app.services.script_run_service import sync_review_status

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
        assert re.search(r"label:\s*'不适用'", src), f"{rel} 没有「不适用」这个显示词"
        assert "dimBadge" in src, f"{rel} 定义了却没用上"


def test_详情页要显示覆盖层级():
    """CC 靠 target_level 决定做几维，人在页面上看不见它就只能靠猜。"""
    src = (ROOT / "frontend/src/pages/cases/CaseDetail.jsx").read_text(encoding="utf-8")
    assert "覆盖·" in src, "详情页没显示覆盖层级"
    assert "setTargetLevel" in src, "覆盖层级不可改"
