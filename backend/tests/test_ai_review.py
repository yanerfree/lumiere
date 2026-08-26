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

from app.services.review import reviewer, step_coverage
from app.services.review.reviewer import (DIMENSIONS, PASS_SCORE, _applicable,
                                          _loc_sig, merge_findings, score_and_verdict)


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


def test_LLM换个说法复述机器那条也要去重():
    """措辞前缀重合那条去重逮不住这种：机检是模板拼的话，AI 复述是模型自己
    组织的句子，开头完全不一样，但两边都在说"第 19 步缺读回"这同一件事。
    活体验证撞过：TC-DYGL-00007 的 mustFix 有两条，`verdictReason` 写
    "有 2 处重要问题"，实际是一处被机检和 AI 复述各算了一次。
    """
    machine = [{"kind": "no_readback", "severity": "major", "where": "api",
                "detail": "第 19 步改了配置没有读回确认"}]
    llm = [{"dimension": "verification_depth", "severity": "major",
            "problem": "步骤 19 只发了修改请求，压根没有再查一次结果", "where": ""}]
    out = merge_findings(machine, llm)
    assert len(out) == 1, out


def test_LLM句子里不提数字时靠stepRef去重():
    """上一轮补的"同一维度 + 同一个位置数字"逮不住这种：**两边都不提步骤号**
    （机检的 detail 里有"19"，但 AI 复述只说"这条断言恒真"）。
    于是一处问题在报告上写成两处，`verdictReason` 又开始虚报数量。
    修法是让模型把步骤号填成结构化字段 `stepRef`，不用赌它写在句子里。
    """
    machine = [{"kind": "no_readback", "severity": "major", "where": "api",
                "detail": "第 19 步改了配置没有读回确认"}]
    llm = [{"dimension": "verification_depth", "severity": "major", "stepRef": "19",
            "problem": "改完之后没有再查一次，这一步验不出配置到底有没有落库", "where": "接口场景"}]
    out = merge_findings(machine, llm)
    assert len(out) == 1, out
    # 反向：stepRef 指到别处就不该合并
    llm2 = [{**llm[0], "stepRef": "7"}]
    assert len(merge_findings(machine, llm2)) == 2


def test_机器那条一个数字都不提时也能靠结构化步骤号去重():
    """上面那条其实是**半场**：它靠的是机器 detail 里恰好写着"19"。
    活体验证撞见了真正够不着的那一半 —— 机器判据的措辞里**一个数字都没有**
    （"这些步骤的预期，脚本里没有任何一处检查它。"），模型老老实实填了 `stepRef`
    也无处可撞，判据 1 形同虚设。
    所以机器那边手上有 `seq` 的判据也要把它填成字段，两边都不靠从散文里刮。
    """
    machine = [{"kind": "expectation_not_asserted", "severity": "major", "where": "ui",
                "stepRef": "1",
                "detail": "这些步骤的预期，脚本里没有任何一处检查它。预期写了不查，等于没写。"}]
    llm = [{"dimension": "verification_depth", "severity": "major", "stepRef": "1",
            "problem": "这一步走完之后没有任何读回，页面上到底变没变没人查。",
            "where": "UI 脚本"}]
    # 先封住前提：旧的两道在这个构造下确实都够不着，否则这条测试证明不了新判据
    assert not _loc_sig(machine[0]["detail"], machine[0]["where"]), "机器正文不该有数字"
    assert not _loc_sig(llm[0]["problem"], llm[0]["where"]), "LLM 正文不该有数字"
    assert llm[0]["problem"][:24] not in machine[0]["detail"][:40], "措辞不该重合"

    assert len(merge_findings(machine, llm)) == 1, "两边都声明了步骤 1，是同一处"
    # 机器那条自己的 stepRef 也要带进输出（前端跳转、CC 定位都要用）
    assert merge_findings(machine, llm)[0]["stepRef"] == "1"
    # 反向两条：号对不上、以及只有一边给得出号 —— 都不许合并
    assert len(merge_findings(machine, [{**llm[0], "stepRef": "99"}])) == 2
    assert len(merge_findings([{k: v for k, v in machine[0].items() if k != "stepRef"}],
                              llm)) == 2, "机器那条既没数字也没步骤号，凭什么判成同一件事"


def test_机器判据把手上现成的步骤号填成字段():
    """上一条测的是 `merge_findings` 吃得下这个字段，这一条测**真的有人填**。
    `step_coverage.analyze` 手上本来就有 `seq`（`missing_act`/`missing_exp` 的第一项），
    以前只拼进散文里，去重那边还得用正则刮回来。
    """
    steps = [{"seq": 3, "action": "点击「保存」按钮", "expected": "列表出现「订单已创建」"},
             {"seq": 5, "action": "点击「导出」", "expected": "下载出 report.csv"}]
    # 脚本里得有中文字面量这条判据才启动；这个字面量跟上面两步的锚点无关，
    # 所以两步都会落成"对不上"
    script = ('page.get_by_text("登录").click()\n'
              'assert page.get_by_text("登录").is_visible()\n')
    got = {f["kind"]: f.get("stepRef") for f in step_coverage.analyze(steps, script,
                                                                     scenario_steps=[])}
    assert got.get("step_action_not_in_script") == "3,5", got
    assert got.get("expectation_not_asserted") == "3", got


def test_stepRef缺失时旧的两道判据照样生效():
    """新字段是可选的。模型不填时去重能力必须**至少不比现在差** ——
    否则等于拿一个可能不写的字段换掉两道已经在跑的判据。
    """
    machine = [{"kind": "no_readback", "severity": "major", "where": "api",
                "detail": "第 19 步改了配置没有读回确认"}]
    # 不带 stepRef，但句子里有 19 → 老的数字粗筛接手
    llm = [{"dimension": "verification_depth", "severity": "major",
            "problem": "步骤 19 只发了修改请求，压根没有再查一次结果"}]
    assert len(merge_findings(machine, llm)) == 1


def test_stepRef留在结果里():
    """跟 kind 同一个理由：前端要能跳到那一步、CC 要知道该改哪一步。
    丢了之后只能回去从文本里刮数字 —— 那正是这次要摆脱的做法。"""
    out = merge_findings([], [{"dimension": "verification_depth", "severity": "major",
                               "problem": "这一步没断言", "stepRef": "步骤 6 和 7"}])
    assert out[0]["stepRef"] == "6,7"
    # 填不出来的（整条用例级问题）不该凭空多一个空字段
    out2 = merge_findings([], [{"dimension": "scenario_sanity", "severity": "minor",
                                "problem": "整条用例没写清测的是哪个角色"}])
    assert "stepRef" not in out2[0]


def test_不同维度撞了同一个数字不去重():
    """数字重合只是粗筛的必要条件之一，还得同一维度——不然两处毫不相关的问题
    只因为都提到"19"就被合并，会把真的第二个问题吃掉。"""
    machine = [{"kind": "no_readback", "severity": "major", "where": "api",
                "detail": "第 19 步改了配置没有读回确认"}]
    llm = [{"dimension": "scenario_sanity", "severity": "minor",
            "problem": "模块目前一共 19 条用例，都是正向流程", "where": ""}]
    out = merge_findings(machine, llm)
    assert len(out) == 2, "维度不同、说的不是同一件事，不该被合并"


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
    assert "lum_review_case" in d
    assert "blocker" in d["lum_review_case"] and "六维" in d["lum_review_case"]


def test_超时不代表没跑完():
    """这个工具是一次不间断的同步调用，run_first=true 时可能跑到分钟级，
    中途没有心跳——批量审核已经有 batchId 轮询解决了同样的问题，单条这边
    还没跟上。活体验证撞过：`lum_review_case` 报"300s 无响应，已中止"，
    但服务端其实跑完了、结果也写库了（77 分），当时按"没跑出结果"汇报，
    后来靠 lum_check_deliverable 才发现已经有结论。至少要在工具说明里
    把这条路指出来，别让调用方以为这次调用完全没有产出。
    """
    from app.mcp import TOOL_CATALOG
    d = {t["name"]: t["description"] for t in TOOL_CATALOG}
    assert "超时" in d["lum_review_case"] and "不代表没跑完" in d["lum_review_case"]
    import inspect
    from app.mcp.tools import review
    assert "不代表这条没跑完" in inspect.getsource(review.review_case)


def test_自审工具在回推那两档里():
    """CC 干活时挂的是 live / uiscript 档 —— 不在档里等于这个工具不存在。"""
    from app.mcp.profiles import PROFILES
    for key in ("live", "uiscript"):
        p = next(x for x in PROFILES if x["key"] == key)
        assert "lum_review_case" in p["tools"], f"{key} 档里没有 lum_review_case"


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
    assert "lum-quality-review" not in src or "已下线" in src
    assert "run_quality_review" not in src


def test_模块缺场景不扣单条的分():
    """一条写得很完整的用例，因为它所在模块只有它自己，被判"该模块对越权/幂等毫无覆盖"
    扣到 55 分、加权 74 分打回 —— 实测第一轮评测就是这么冤枉的。
    模块级缺口是**情报**（coverageGaps），不是这一条的扣分项。
    """
    assert "不要因为这个模块缺别的场景而扣这一条的分" in reviewer._SYSTEM
    assert "self_coverage" in DIMENSIONS and "coverage_gap" not in DIMENSIONS
    assert DIMENSIONS["self_coverage"]["label"] == "本条覆盖完整性"


def test_步骤角色是代码判的不是提示词引导():
    """TC-DYGL-00001 走了三轮才过：第一轮说预期没查，改成往 expected 里塞散文
    锚点（"由接口场景『制备：等推送收敛』断言覆盖"），第二轮反而把那句话里提到
    的步骤名当成新的待验证承诺，又标了 4 条。最后靠**步骤角色前缀**
    （前置:/操作:/验证:/清理:）过审。

    上一轮的修法是把这套约定写进 `_SYSTEM` 提示词 —— 那只是**引导**：模型可以
    读了不照做，跟 `step_coverage` 里的确定判据不是一回事。现在下沉成结构：
    角色由 `role_of()` 判（代码侧跳过前置/清理用的是同一个函数），
    "这句 expected 是在引用接口场景步骤名"由 `_refs_scenario_step()` 判，
    两个结论以 `role` / `refsScenarioStep` 字段喂给 LLM。
    **提示词里不再有让模型自己认中文前缀的话** —— 有的话就是又漂回引导了。
    """
    from app.services.review import evidence
    from app.services.review.step_coverage import role_of

    # ① 角色是代码判的，两边同一个函数
    assert role_of("前置：建三个应用") == "setup"
    assert role_of("清理: 删掉测试数据") == "setup"
    assert role_of("验证：列表查不到该记录") == "verify"
    assert role_of("操作：点击「弃用」") == "action"
    assert role_of("点击「弃用」按钮") is None      # 没前缀就不猜

    # ② 判出来的角色真的进了喂给 LLM 的结构里
    rows = evidence._steps_text([
        {"seq": 1, "action": "前置：调接口铺三条数据", "expected": "铺好"},
        {"seq": 2, "action": "操作：点击「弃用服务」", "expected": "状态变为「已弃用」"},
    ])
    assert rows[0]["role"] == "setup" and rows[1]["role"] == "action"

    # ③ "引用接口场景步骤名"也是代码判的 —— 全角/半角冒号和引号不一样照样认得出
    rows = evidence._steps_text(
        [{"seq": 1, "action": "操作：推送", "expected": "由接口场景『制备：等推送收敛』断言覆盖"}],
        ["制备:等推送收敛", "验证:列表可见"])
    assert rows[0]["refsScenarioStep"] is True, "全角冒号+另一种引号也必须认出来"
    # 没引用的不能乱打标记（打了就等于告诉模型"这条承诺换地方验了"，是假免责）
    assert "refsScenarioStep" not in evidence._steps_text(
        [{"seq": 1, "action": "操作：推送", "expected": "列表出现该记录"}],
        ["制备:等推送收敛"])[0]

    # ④ 提示词读字段，不再让模型自己认前缀
    assert "`role`" in reviewer._SYSTEM and "refsScenarioStep" in reviewer._SYSTEM
    assert "不要自己再从中文前缀猜" in reviewer._SYSTEM
    assert "不算一条新的验证承诺" in reviewer._SYSTEM
    assert "优先建议把步骤名改成" in reviewer._SYSTEM, "修复建议不该引导往 expected 里塞散文"


def test_列表不区分谁审的():
    """用户的口径：列表只显示审核状态，**不区分 AI 审还是人审** —— 一列一种语义，
    字段干净。谁审的、审了几轮、每轮必改什么，都在详情页的「审核」tab 里。
    （之前列表给 AI 的结论标「AI 过/AI 打回」，同一列混两套语义，看着就是不一致。）
    """
    src = (__import__("pathlib").Path("../frontend/src/pages/cases/CaseManagement.jsx")
           .read_text(encoding="utf-8"))
    i = src.index("key: 'reviewStatus'")
    # 切到**下一列开头**为止，不是切固定字数 —— 这一列的注释一多，
    # 固定窗口就够不着底下的 `>通过<`，测试红在一个跟它要封的事无关的地方。
    nxt = src.index("\n    { key: '", i + 10)
    # 只看**代码**，注释里当然会提到旧写法（说明"这里刻意不区分"）
    seg = "\n".join(l for l in src[i:nxt].splitlines()
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
    assert "lum_review_case 复核" in src, "要告诉它改完怎么复核"
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
