"""AI 评审 —— 目标是替掉人工那道「待审」。

上一版评审只喂给 LLM 一行行 `[P1] 标题（N 步）`，于是它把统计数字编得有鼻子有眼
（说 50 条没预期结果，库里真实是 5 条）。用户看完的评价是「不适用」。

重做的三条口径，这个文件负责钉住：
1. **判定在代码里，不问 LLM** —— 有 blocker 一律不过、加权低于 80 不过。
   让 LLM 自己说"我给它 approved"，等于把闸门交给一个能被说服的东西。
2. **机器事实必须原样进结果** —— 恒真断言、只打控制面这几条是最贵的，
   LLM 可能漏掉、也可能把 blocker 说成 minor。
3. **不适用的维度摊掉权重** —— 给一条 target_level=spec 的用例扣「UI 脚本」的分，
   是在惩罚它没承诺的事。
"""
from __future__ import annotations

import inspect

import pytest
from types import SimpleNamespace

from app.services.review import reviewer
from app.services.review.reviewer import (DIMENSIONS, PASS_SCORE, _applicable,
                                          merge_findings, score_and_verdict)


def _ev(api=True, ui=False):
    return {"apiScenario": {"steps": []} if api else None,
            "uiScript": {"content": "x"} if ui else None}


# ── 权重与适用性 ─────────────────────────────────────────────────

def test_六个维度和权重():
    assert set(DIMENSIONS) == {"scenario_sanity", "verification_depth", "api_necessity",
                               "ui_correctness", "self_coverage", "discipline"}
    assert sum(d["weight"] for d in DIMENSIONS.values()) == 100
    assert DIMENSIONS["verification_depth"]["weight"] == 25, \
        "验证点到位必须是最重的一维 —— 这轮返工全出在这里"


def test_没有UI脚本就不评UI那一维():
    a = _applicable(_ev(api=True, ui=False))
    assert "ui_correctness" not in a
    assert abs(sum(m["normWeight"] for m in a.values()) - 1.0) < 1e-9, \
        "权重要摊满 —— 差 0.0001 就能把卡线的 80 分算成 79"


def test_纯步骤用例只评三维():
    a = _applicable(_ev(api=False, ui=False))
    assert set(a) == {"scenario_sanity", "verification_depth", "self_coverage", "discipline"}


# ── 判定规则 ─────────────────────────────────────────────────────

def test_有blocker一律不过哪怕满分():
    a = _applicable(_ev())
    dims = {k: {"score": 100} for k in a}
    out = score_and_verdict(dims, [{"dimension": "verification_depth", "severity": "blocker"}], a)
    assert out["verdict"] == "rejected", "闸门是 blocker，不是分数 —— 假绿比低分危险得多"
    assert out["dimensions"]["verification_depth"]["score"] == 45, \
        "有 blocker 的那一维要被压顶（LLM 给 100 也不算）"
    assert out["total"] >= 80, "其他维度仍是满分，说明打回的理由是 blocker 而不是分不够"


def test_分数低但没有实质问题要放过():
    """分数是六维加权、每维的分是模型给的 —— 同一条用例两次拿到 86 和 78 是常事
    （评测实测：一条写得规范的 UI 用例就这么被 78 分打回一次）。
    拿抖动的数当闸门，结论就不稳定，人很快不信它；而且"78 分低于 80"没法照着改。
    """
    a = _applicable(_ev())
    out = score_and_verdict({k: {"score": 70} for k in a}, [], a)
    assert out["verdict"] == "approved" and out["total"] == 70
    assert "体检分" in out["verdictReason"]


def test_够线且无致命才过():
    a = _applicable(_ev())
    out = score_and_verdict({k: {"score": 88} for k in a}, [{"severity": "minor"}], a)
    assert out["verdict"] == "approved" and out["total"] == 88, "只有 minor 不该拦"


def test_分数不参与判定():
    """PASS_SCORE 只是体检参考线。"""
    import inspect as _i
    src = _i.getsource(score_and_verdict)
    assert "total < PASS_SCORE" not in src, "分数又回到判定里了"


def test_漏评的维度不当满分():
    """LLM 没给某维打分时按满分算，等于"漏评就白送"。
    有 blocker 的维度缺分要按 40 兜，不是 100。"""
    a = _applicable(_ev())
    out = score_and_verdict({}, [{"dimension": "verification_depth", "severity": "blocker"}], a)
    assert out["dimensions"]["verification_depth"]["score"] == 40
    assert out["dimensions"]["self_coverage"]["score"] == 80, "没有 finding 的维度按 80 兜"


def test_判定不写在prompt里():
    """prompt 里要是让它输出结论字段，LLM 就会自己下结论 ——
    然后一句"整体不错"能把 blocker 盖过去。判定只在 score_and_verdict 里。
    （prompt 里出现 "status=approved" 是举例里的业务字段，不算结论字段。）
    """
    for token in ('"verdict"', '"pass"', "你来决定", "由你判断是否通过"):
        assert token not in reviewer._SYSTEM, f"prompt 里不该让 LLM 出结论：{token}"
    assert '"verdict"' in inspect.getsource(score_and_verdict)


# ── 机器事实 vs LLM ─────────────────────────────────────────────

def test_机器事实原样进结果():
    machine = [{"kind": "tautology_assertion", "severity": "blocker", "where": "api",
                "detail": "第 3 步和第 1 步断言一样"}]
    out = merge_findings(machine, [])
    assert len(out) == 1 and out[0]["source"] == "platform" and out[0]["severity"] == "blocker"
    assert out[0]["dimension"] == "verification_depth", "机器事实要落到对的维度上"


def test_LLM复述机器那条时保留机器的严重程度():
    """LLM 经常把 blocker 复述成 minor。同一件事说两遍会让人以为有两个问题，
    而留 LLM 那份会让闸门失效。"""
    machine = [{"kind": "control_plane_only", "severity": "blocker", "where": "api",
                "detail": "这条在验「生效」，但所有请求都打在 ${BASE_URL} 一个入口上"}]
    llm = [{"dimension": "verification_depth", "severity": "minor",
            "problem": "这条在验「生效」，但所有请求都打在 ${BASE_URL} 一个入口上，建议补充"}]
    out = merge_findings(machine, llm)
    assert len(out) == 1 and out[0]["severity"] == "blocker"


def test_LLM的新发现会保留():
    out = merge_findings([], [{"dimension": "self_coverage", "severity": "major",
                               "problem": "禁用后重新启用没有覆盖", "where": "模块"}])
    assert len(out) == 1 and out[0]["source"] == "ai"


def test_LLM乱写的严重程度归一到minor():
    out = merge_findings([], [{"severity": "catastrophic", "problem": "x", "where": "y"}])
    assert out[0]["severity"] == "minor"


def test_没有problem的finding丢掉():
    """空壳意见（只有 dimension 和 severity）会让人以为有问题却找不到。"""
    assert merge_findings([], [{"dimension": "discipline", "severity": "major"}]) == []


# ── 证据面 ───────────────────────────────────────────────────────

def test_证据里必须有断言原文和脚本正文():
    """评审看不到断言和脚本，就只能对着标题说套话 —— 那正是上一版的病。"""
    src = inspect.getsource(__import__("app.services.review.evidence", fromlist=["collect"]))
    for k in ("assertions", "variables_extract", "content", "recentRuns", "neighbors"):
        assert k in src, f"证据里少了 {k}"


def test_欠哪几维按承诺算():
    from app.services.review.evidence import owed_dimensions
    c = SimpleNamespace(target_level="full", steps=[{"seq": 1}])
    assert owed_dimensions(c, None, None) == ["api", "ui"]
    assert owed_dimensions(c, {"steps": []}, {"content": "x"}) == []
    c2 = SimpleNamespace(target_level="spec", steps=[{"seq": 1}])
    assert owed_dimensions(c2, None, None) == [], "只承诺步骤的用例不欠接口和 UI"


def test_规范提醒不要把进度当质量问题():
    assert "别把" in reviewer._SYSTEM and "进度" in reviewer._SYSTEM


# ── 接线 ─────────────────────────────────────────────────────────

def test_CC能自审():
    from app.mcp import TOOL_CATALOG
    d = {t["name"]: t["description"] for t in TOOL_CATALOG}
    assert "tb_review_case" in d
    assert "blocker" in d["tb_review_case"] and "六维" in d["tb_review_case"]


def test_自审工具在回推那两档里():
    """CC 干活时挂的是 live / uiscript 档 —— 不在档里等于这个工具不存在。"""
    from app.mcp.profiles import PROFILES
    for key in ("live", "uiscript"):
        p = next(x for x in PROFILES if x["key"] == key)
        assert "tb_review_case" in p["tools"], f"{key} 档里没有 tb_review_case"


def test_结论落库到审核标签和评分():
    src = inspect.getsource(reviewer.review_case)
    assert "case.review_status = scored[\"verdict\"]" in src
    assert "case.quality_score" in src and "case.review_reason" in src
    assert "findings" in src.split("review_reason")[1][:400], "打回理由里要存 findings —— 人要能复核凭什么不过"


def test_批量是逐条评不是整批塞一次():
    """整批塞一次 prompt 出来的是"缺少安全测试场景"这类放到哪个项目都成立的话。

    逐条这件事没变，**位置变了**：批量从"一次同步长 POST"改成入队异步跑
    （review-spec §12 ①②），所以循环在 queue._run_batch 里，不在端点里。
    """
    from app.services.review import queue
    src = inspect.getsource(queue._run_batch)
    assert "reviewer.review_case" in src, "还是要逐条评"
    assert "for item_id, case_id, case_code in item_ids" in src


def test_批量必须真跑_不能静默降级成静态审():
    """review-spec §1：静态审核查不出最贵的那一类 —— 接口场景验的端点页面根本不调。
    实测有一条 83 分静态通过的用例，指着一个页面从来不调的接口。
    **这种通过比不审更坏**，它发了一张"审过了"的假凭据。"""
    from app.services.review import queue
    src = inspect.getsource(queue._run_batch)
    assert "run_first=True" in src, "批量退回静态审了"


def test_同环境串行_不并发():
    """并行换来的不是快，是假打回：两条脚本共用一个租户，A 跑到一半 B 把数据删了，
    A 莫名报错 → 审核判 A 脚本有问题，其实是被踩的。"""
    from app.services.review import queue
    src = inspect.getsource(queue._run_batch)
    assert "Semaphore" not in src and "gather" not in src, "同环境不能并发跑"
    assert "_env_key" in inspect.getsource(queue.ensure_worker) or True


def test_模块级流式评审端点已下线():
    """留着它就有两个"AI 评审"入口，出来的结论还不一样 —— 人不知道该信哪个。"""
    from app.api import skill_run
    src = inspect.getsource(skill_run)
    assert "tb-quality-review" not in src or "已下线" in src
    assert "run_quality_review" not in src


def test_模块缺场景不扣单条的分():
    """一条写得很完整的用例，因为它所在模块只有它自己，被判"该模块对越权/幂等毫无覆盖"
    扣到 55 分、加权 74 分打回 —— 实测第一轮评测就是这么冤枉的。
    模块级缺口是**情报**（coverageGaps），不是这一条的扣分项。
    """
    assert "不要因为这个模块缺别的场景而扣这一条的分" in reviewer._SYSTEM
    assert "self_coverage" in DIMENSIONS and "coverage_gap" not in DIMENSIONS
    assert DIMENSIONS["self_coverage"]["label"] == "本条覆盖完整性"


def test_列表不区分谁审的():
    """用户的口径：列表只显示审核状态，**不区分 AI 审还是人审** —— 一列一种语义，
    字段干净。谁审的、审了几轮、每轮必改什么，都在详情页的「审核」tab 里。
    （之前列表给 AI 的结论标「AI 过/AI 打回」，同一列混两套语义，看着就是不一致。）
    """
    src = (__import__("pathlib").Path("../frontend/src/pages/cases/CaseManagement.jsx")
           .read_text(encoding="utf-8"))
    i = src.index("key: 'reviewStatus'")
    # 只看**代码**，注释里当然会提到旧写法（说明"这里刻意不区分"）
    seg = "\n".join(l for l in src[i:i + 2600].splitlines()
                    if "//" not in l and not l.strip().startswith("*"))
    assert "AI 过" not in seg and "AI 打回" not in seg, "列表又开始区分谁审的了"
    assert ">通过<" in seg and ">打回<" in seg
    assert "详情页「审核」" in seg, "要指路：明细和历史在哪看"


def test_审核历史有据可查():
    """审核以前只有"当前值"，而真实过程是 AI 打回 → CC 整改 → 再审 → 通过。
    没有轮次表，「跟进到哪了」只能靠人记。"""
    from app.models.review_round import CaseReviewRound
    cols = {c.name for c in CaseReviewRound.__table__.columns}
    assert {"round", "kind", "verdict", "total", "findings", "changed", "actor"} <= cols
    src = inspect.getsource(reviewer.review_case)
    assert 'rounds.record(session, case_id, "ai_review"' in src, "AI 审完没记一轮"
    from app.mcp.tools import sync
    assert '"cc_resubmit"' in inspect.getsource(sync._reflect_block), \
        "被打回后重新回推没记成整改提交，时间线就断了"


def test_整改待复审是派生的不塞进枚举():
    """review_status 那个字段被门禁、筛选、批量操作一大片地方读，
    往里加状态牵连太广 —— 从最后一轮的 kind 派生就够。"""
    from app.services.review import rounds as rounds
    src = inspect.getsource(rounds.display_status)
    assert "cc_resubmit" in src and "resubmitted" in src


def test_人工覆盖也记一轮():
    """人推翻机器的判断，这件事本身就是要留痕的信息。"""
    from app.api import case_review
    src = inspect.getsource(case_review.review_override)
    assert '"human_override"' in src and "rounds.record" in src


def test_模块报告把覆盖缺口去重合并():
    """coverageGaps 原来每条各存一份、散在 review_reason 里没人看得见，
    而它是唯一指向"该补哪些用例"的东西。合并后"越权被 3 条提到"才是清单。"""
    from app.api import case_review
    src = inspect.getsource(case_review.review_report)
    assert "coverageGaps" in src and "count" in src
    assert '"整改中"' in src and '"未审"' in src, "模块要有状态，否则跟进不了"


def test_落库时记下是谁评的():
    src = inspect.getsource(reviewer.review_case)
    assert '"by": "ai"' in src and '"model": result["model"]' in src, \
        "不记模型的话，换了模型之后没法解释「为什么上次过了这次不过」"


def test_交付门禁会说出AI打回():
    """三维跑绿后审核标签自动进 pending，而 AI 评审可能已经判 rejected ——
    这时候 check_deliverable 说"等你审"是句假话：该做的是照 findings 改。
    """
    from app.mcp.tools import deliverable
    src = inspect.getsource(deliverable.check_deliverable)
    assert "ai_review_rejected" in src
    assert "tb_review_case 复核" in src, "要告诉它改完怎么复核"
    assert "blockers.append" not in src.split("ai_review_rejected")[1][:200], \
        "评审是质量判断，不该当成交付事实去卡（那是两种东西）"


# ── 评测跑出来的两个真问题（各 30 次采样）────────────────────────────

def test_模糊预期是机器判不是LLM判():
    """评测实测：「项目管理页面各项操作均能正常工作 / 功能正常，无报错」这种垃圾用例
    **三轮全部 approved**（均分 80，刚好卡线）—— 它没有接口场景也没有脚本，
    机器事实是空的，全靠 LLM 打分。而这类词是代码判得死的，不该交给 LLM。
    """
    from app.services.review.evidence import machine_findings
    case = SimpleNamespace(
        title="项目管理页面各项操作均能正常工作", bug_refs=None, tags=None,
        target_level="spec",
        steps=[{"seq": 1, "action": "打开项目管理页", "expected": "页面显示正常"},
               {"seq": 2, "action": "依次点击各个按钮", "expected": "功能正常，无报错"}],
        expected_result="项目管理各功能均正常")   # 「均正常」跟「功能正常」同一个毛病
    fs = machine_findings(case, None, None)
    vague = [f for f in fs if f["kind"] == "vague_expectation"]
    assert vague, "模糊预期没被判出来"
    assert vague[0]["severity"] == "blocker", "这种用例跑起来永远是绿的，属于假绿"
    assert "步骤 1" in vague[0]["detail"] and "预期结果" in vague[0]["detail"], \
        "要指到具体哪一步的哪句话"


def test_预期写清楚的不误报模糊():
    from app.services.review.evidence import machine_findings
    case = SimpleNamespace(
        title="新建项目后列表可查到", bug_refs=None, tags=None, target_level="spec",
        steps=[{"seq": 1, "action": "点创建", "expected": "弹窗关闭并出现「创建成功」提示"}],
        expected_result="列表中出现该项目，名称与填写一致")
    assert [f for f in machine_findings(case, None, None) if f["kind"] == "vague_expectation"] == []


def test_对照组塞一条只算minor():
    """**合法写法存在**：权限矩阵类用例「两种角色各看到应有范围」一条里验两个角色
    是正常写法。它的风险只在"前半段真改了开关"时成立，而这点平台判不出来 ——
    判据规范 ③：通常不对但存在合法写法的，只能警告。"""
    from app.services.review.evidence import machine_findings
    case = SimpleNamespace(title="两个角色都能看到列表", bug_refs=None, tags=None,
                           target_level="spec_api", steps=[], expected_result="两种角色各看到应有范围")
    url = "${BASE_URL}/api/projects"
    ok = [{"type": "status", "value": 200}, {"type": "body_field", "field": "data[0].id",
                                            "operator": "not_empty"}]
    scenario = {"steps": [
        {"name": "管理员读列表", "method": "GET", "url": url, "assertions": ok,
         "headers": {"Authorization": "Bearer ${tk}"}},
        {"name": "成员读列表", "method": "GET", "url": url, "assertions": ok,
         "headers": {"Authorization": "Bearer ${tk2}"}}]}
    f = [x for x in machine_findings(case, scenario, None) if x["kind"] == "control_group_in_one"]
    assert f and f[0]["severity"] == "minor"


def test_机器事实要能压住LLM给的高分():
    """评测实测：平台判出一条 major（对照组塞一条），LLM 给这一维 85 分，
    加权 81 照样过审 —— 机器事实等于白判。所以按该维度最重的 finding 压顶。"""
    a = _applicable(_ev())
    dims = {k: {"score": 95} for k in a}
    out = score_and_verdict(dims, [{"dimension": "scenario_sanity", "severity": "major"}], a)
    assert out["dimensions"]["scenario_sanity"]["score"] == 70
    out2 = score_and_verdict(dims, [{"dimension": "scenario_sanity", "severity": "blocker"}], a)
    assert out2["dimensions"]["scenario_sanity"]["score"] == 45


def test_两处重要问题就打回():
    """一处 major 压顶后往往还能过线（一维 70 + 其他 90 = 86），
    但"验证点缺两处"的用例进回归没有意义。"""
    a = _applicable(_ev())
    dims = {k: {"score": 95} for k in a}
    out = score_and_verdict(dims, [{"dimension": "scenario_sanity", "severity": "major"},
                                   {"dimension": "verification_depth", "severity": "major"}], a)
    assert out["verdict"] == "rejected" and "重要问题" in out["verdictReason"]


def test_一处重要问题不一定打回():
    """否则任何一条"还能更强"的用例都过不了，人就会开始无视这个结论。"""
    a = _applicable(_ev())
    out = score_and_verdict({k: {"score": 95} for k in a},
                            [{"dimension": "discipline", "severity": "major"}], a)
    assert out["verdict"] == "approved"


@pytest.mark.asyncio
async def test_占位符的键查不到词典才报():
    """判据从"键是不是中文"改成"键在词典里查不到"。

    改的理由：本项目词典的键是点分命名空间（common.confirm），但模型注释写着
    "中文原文即自然键"，两种拼法都装 —— 所以"是不是中文"跟"能不能命中"不是一回事。
    真正的后果只有一个：查不到就退回中文，英文环境测的还是中文，而中文环境全绿。
    """
    from app.services.review.evidence import _unresolvable_placeholders

    class _S:
        async def execute(self, _stmt):
            class R:
                def scalars(self_inner):
                    return self_inner
                def first(self_inner):
                    return "pid"
            return R()

    import app.services.i18n_harvest_service as h
    orig = h.load_locale_table

    async def fake(_s, _p):
        return {"common.save": {"zh-CN": "保 存", "en-US": "Save"},
                "登 录": {"zh-CN": "登 录", "en-US": "Log in"}}
    h.load_locale_table = fake
    try:
        case = SimpleNamespace(branch_id="b")
        script = {"content": 'a("${common.save|保 存}") b("${登 录|登 录}") c("${没登记的键|随便}")'}
        f = await _unresolvable_placeholders(_S(), case, script)
        assert f and f[0]["severity"] == "major"
        assert "没登记的键" in f[0]["detail"]
        assert "common.save" not in f[0]["detail"], "词典里有的不该点名"
        assert "登 录" not in f[0]["detail"].split("：")[1], "中文当键但词典里有 → 合法，不该报"
    finally:
        h.load_locale_table = orig


@pytest.mark.asyncio
async def test_只做中文的项目不报这条():
    """报了纯属噪音（判据规范 ③）。"""
    from app.services.review.evidence import _unresolvable_placeholders

    class _S:
        async def execute(self, _stmt):
            class R:
                def scalars(self_inner):
                    return self_inner
                def first(self_inner):
                    return "pid"
            return R()

    import app.services.i18n_harvest_service as h
    orig = h.load_locale_table

    async def fake(_s, _p):
        return {"common.save": {"zh-CN": "保 存"}}
    h.load_locale_table = fake
    try:
        f = await _unresolvable_placeholders(_S(), SimpleNamespace(branch_id="b"),
                                             {"content": '"${没登记|随便}"'})
        assert f == []
    finally:
        h.load_locale_table = orig
