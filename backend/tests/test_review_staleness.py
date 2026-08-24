"""评审结论不该在场景/脚本被覆盖之后原样留着。

**事故现场**：TC-DYGL-00017 有一份 49 分打回的记录，写着「UI 脚本没有 def test_
入口」。脚本早被 `tb_sync_ui_script` 换过一版，取出当前内容一看那个入口就在里面。
原地复评直接 83 分 approved，那两条问题一个字没再出现——旧记录是对着已经不存在
的内容算的，却没有任何标记说这件事，险些被引导去重写一个本来就能跑的脚本。

判据：`content_signature` 摊平场景步骤（url/method/assertions/body）+ UI 脚本
版本号；`ai_review` 落库时存一份，`list_rounds` 读的时候拿当前签名重新算一遍
比对，标 `stale`。**只摊定义、不摊执行结果**——重新跑一遍不该让上一轮"过期"，
只有内容真的被改过才算。
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from app.models.api_test import ApiTestScenario, ApiTestStep
from app.models.review_round import CaseReviewRound
from app.models.script import Script
from app.services.review import rounds


class FakeSession:
    """execute() 按 select 的目标模型分发预置行，够 content_signature/list_rounds 用。

    单列和多列分开放（`by_model` / `multi`）：`content_signature` 查的是
    `select(ApiTestScenario.id)` 这种单列（取 scalar），`stale_map` 查的是
    `select(ApiTestScenario.id, .source_case_id)` 这种多列（取元组）——
    同一个模型两种形状，混在一个 dict 里会把元组当 scalar 喂进去。
    """

    def __init__(self, by_model=None, multi=None):
        self._by_model = by_model or {}
        self._multi = multi or {}

    async def execute(self, stmt):
        model = stmt.column_descriptions[0]["entity"]
        src = self._multi if len(stmt.column_descriptions) > 1 else self._by_model
        rows = src.get(model, [])
        return SimpleNamespace(all=lambda: rows, scalars=lambda: SimpleNamespace(
            first=lambda: (rows[0] if rows else None), all=lambda: rows))


def _step(method="GET", url="/api/x", assertions=None, body=None):
    return SimpleNamespace(sort_order=0, method=method, url=url,
                           assertions=assertions or [], body=body)


def _sig(scenario_present=True, steps=None, ui_version=1):
    by_model = {}
    if scenario_present:
        by_model[ApiTestScenario] = [uuid.uuid4()]
    by_model[ApiTestStep] = steps or [_step()]
    if ui_version is not None:
        by_model[Script] = [ui_version]
    return asyncio.run(rounds.content_signature(FakeSession(by_model), uuid.uuid4()))


def test_内容不变签名不变():
    assert _sig() == _sig()


def test_断言改了签名跟着变():
    a = _sig(steps=[_step(assertions=[{"field": "data.enabled", "operator": "=="}])])
    b = _sig(steps=[_step(assertions=[{"field": "data.enabled", "operator": "!="}])])
    assert a != b


def test_脚本版本号变了签名跟着变():
    """场景一字没动，只是 UI 脚本被 sync 覆盖出了新版本——这正是事故现场那种：
    问题出在 UI 脚本，接口场景没碰。"""
    assert _sig(ui_version=1) != _sig(ui_version=2)


def test_只是重新跑一遍不该改变签名():
    """`content_signature` 只摊场景定义，不摊执行结果 —— run_first 重跑
    不该把上一轮 approved 标成过期。"""
    import inspect
    src = inspect.getsource(rounds.content_signature)
    assert "st.last_status" not in src and "st.last_response" not in src


def test_列表按当前内容标过期():
    case_id = uuid.uuid4()
    same_sig = _sig()
    row_fresh = SimpleNamespace(
        round=2, kind="ai_review", verdict="approved", total=83, dimensions={}, findings=[],
        coverage_gaps=[], summary="", changed=None, actor="ai", model="m",
        review_mode="static", traffic_seen=None, content_hash=same_sig, created_at=None)
    row_stale = SimpleNamespace(
        round=1, kind="ai_review", verdict="rejected", total=49, dimensions={}, findings=[],
        coverage_gaps=[], summary="", changed=None, actor="ai", model="m",
        review_mode="static", traffic_seen=None, content_hash="deadbeef" * 4, created_at=None)
    row_legacy = SimpleNamespace(
        round=0, kind="ai_review", verdict="approved", total=80, dimensions={}, findings=[],
        coverage_gaps=[], summary="", changed=None, actor="ai", model="m",
        review_mode=None, traffic_seen=None, content_hash=None, created_at=None)

    by_model = {
        CaseReviewRound: [row_fresh, row_stale, row_legacy],
        ApiTestScenario: [uuid.uuid4()], ApiTestStep: [_step()], Script: [1],
    }
    out = asyncio.run(rounds.list_rounds(FakeSession(by_model), case_id))
    by_round = {r["round"]: r for r in out}
    assert by_round[2]["stale"] is False, "内容没变的最新一轮不该被标过期"
    assert by_round[1]["stale"] is True, "签名对不上当前内容，得说出来"
    assert by_round[0]["stale"] is None, "存量轮次没存过签名，不能瞎猜说它过期"


# ── 列表那一级：stale_map ────────────────────────────────────
#
# 详情页时间线标了过期，列表上那个"通过/打回"却照旧 —— 于是一条被
# tb_sync_ui_script 换过脚本的 approved 用例，在列表上仍然干干净净，
# 没人会想到去点开看那个结论是对着哪一版算的（原反馈 #1 的遗留部分）。

def _sc_step(sc_id, **kw):
    st = _step(**kw)
    st.scenario_id = sc_id
    return st


def _stale_map(hash_rows, steps=None, ui_version=1, case_id=None, extra_scenarios=0):
    """hash_rows: [(case_id, round, content_hash), ...]"""
    cid = case_id or uuid.uuid4()
    sc = uuid.uuid4()
    rows = steps if steps is not None else [_sc_step(sc)]
    session = FakeSession(
        # 单列：退回单条算法（`content_signature`）时走这几条
        by_model={ApiTestScenario: [sc], ApiTestStep: rows,
                  Script: [ui_version] if ui_version is not None else []},
        # 多列：批量那几条
        multi={CaseReviewRound: hash_rows,
               ApiTestScenario: ([(sc, cid)]
                                 + [(uuid.uuid4(), cid) for _ in range(extra_scenarios)]),
               Script: [(cid, ui_version)] if ui_version is not None else []})
    return cid, asyncio.run(rounds.stale_map(session, [cid]))


def test_批量算的签名跟单条算的一模一样():
    """**这是这一组里最要紧的一条**：两处各写一遍哈希公式的话，任何一处漂移
    都会让批量算出来的对不上落库那份，于是整库的 approved 全被标成过期 ——
    一个假警报比不报警更贵。"""
    cid = uuid.uuid4()
    cid, out = _stale_map([(cid, 3, _sig())], case_id=cid)
    assert out[cid] is False


def test_脚本被换过就在列表上标过期():
    cid = uuid.uuid4()
    cid, out = _stale_map([(cid, 3, _sig(ui_version=1))], ui_version=2, case_id=cid)
    assert out[cid] is True


def test_最新一轮没存签名就不收录():
    """人工在 AI 审之后又点过一次通过的话，那次改动到底发生在人点之前
    还是之后，库里没有依据 —— **不收录 ≠ 没过期**，是判不出来。"""
    cid = uuid.uuid4()
    cid, out = _stale_map([(cid, 1, _sig()), (cid, 2, None)], case_id=cid)
    assert cid not in out


def test_只看最新一轮不看历史轮次():
    cid = uuid.uuid4()
    cid, out = _stale_map([(cid, 1, "deadbeef" * 4), (cid, 2, _sig())], case_id=cid)
    assert out[cid] is False, "旧轮次早就过期了，但列表上显示的是最新那条结论"


def test_一条用例挂多个场景时退回单条算法():
    """单条那边是 `.first()` 取的（没有 order_by，顺序由库定），批量这边
    猜一个就可能凭空报一个过期。这种少数情况直接走单条那段代码。"""
    cid = uuid.uuid4()
    cid, out = _stale_map([(cid, 1, _sig())], case_id=cid, extra_scenarios=1)
    assert out[cid] is False


def test_没审过的一条都不查():
    assert asyncio.run(rounds.stale_map(FakeSession({}), [])) == {}
