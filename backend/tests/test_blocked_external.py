"""「卡在外部条件上」—— 看板上分不出"没人写"和"写不了"。

外部 CC 第十一条：TC-DYGL-00015 因为环境变量还没加，场景被硬拒（拒得对），用例停在
`api_scenario_missing` —— 而这跟「我压根没写场景」在 lum_check_branch 里长得一模一样。
于是每轮都要人挨个去问一遍「这条为什么没做」。

一列文本就够，**刻意不做成状态枚举**：状态由执行事实推进（红线），
"等外部条件"不是一种进度，是一句归责说明；也**不免检任何阻塞** ——
免检就变成了万能挡箭牌，那比不做更糟。
"""
from __future__ import annotations

import inspect


def test_不是新状态而是一列说明():
    """做成 ui_status/api_status 的新枚举值会污染执行事实那条链（红线）。"""
    from app.models.case import Case

    col = Case.__table__.c["blocked_external"]
    assert col.nullable, "它是可选说明，不是必填状态"
    from app.mcp.tools import test_cases
    src = inspect.getsource(test_cases.update_case)
    # 状态字段一概不收，这条红线不许被这次改动顺手放开
    for f in ("ui_status=", "api_status=", "manual_status="):
        assert f not in src, f"update_case 开始收 {f} 了 —— 状态只能由执行事实推进"


def test_写空串就是撤掉():
    """条件到位了得能自己撤 —— 撤不掉的标签最后全是过期的。"""
    from app.mcp.tools import test_cases
    src = inspect.getsource(test_cases.update_case)
    assert "blocked_external.strip()[:500] or None" in src


def test_不免检阻塞():
    """免检就成了万能挡箭牌：贴一句"等外部"整条就没人管了。"""
    from app.mcp.tools import deliverable
    src = inspect.getsource(deliverable.check_deliverable)
    seg = src.split("blocked_external")[2] if src.count("blocked_external") > 2 else src
    assert "照旧算阻塞" in src
    # 它必须落在 notes（提示）里，不能落在 blockers/risks 里改变判定
    assert 'notes.append({"kind": "blocked_external"' in src


def test_分支看板上单独列出来():
    """一屏 api_scenario_missing 里必须分得出哪条是没人写、哪条是写不了。"""
    from app.mcp.tools import deliverable
    src = inspect.getsource(deliverable.check_branch)
    assert '"blockedExternal"' in src, "行里没带出来"
    assert "其中卡在外部条件" in src, "汇总里没分开数"


def test_前端也看得见():
    """人的看板是页面。只给 MCP 的话，人看到的还是一片"未开始"。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    schema = (root / "backend/app/schemas/case.py").read_text(encoding="utf-8")
    assert "blocked_external: str | None = None" in schema, "接口不返回，页面拿不到"
    jsx = (root / "frontend/src/pages/cases/CaseDetail.jsx").read_text(encoding="utf-8")
    assert "blockedExternal" in jsx and "等外部" in jsx


def test_工具描述里写了它():
    from app.mcp import TOOL_CATALOG

    d = {t["name"]: t["description"] for t in TOOL_CATALOG}["lum_update_case"]
    assert "blocked_external" in d and "写不了" in d


def test_迁移接在当前head后面():
    """两个 head 的库升不上去 —— 实测这类事故要人手工 merge。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    s = ScriptDirectory.from_config(Config("alembic.ini"))
    # 钉的是「只有一个 head」这条不变量，不是某个具体版本号 ——
    # 原来写死 ["zzg0blkext"]，之后每加一条迁移都要来改这行，
    # 而它想防的事（分叉出两个 head）跟版本号是谁没关系。
    assert len(s.get_heads()) == 1, s.get_heads()
    revs = {r.revision for r in s.walk_revisions()}
    assert "zzg0blkext" in revs, "blocked_external 那条迁移不在链上了"
