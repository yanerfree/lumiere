"""单条用例的 AI 评审 —— 目标是**替掉人工那道「待审」**。

原来的评审只看到 `[P1] 标题（N 步）`，给出的是"缺少安全测试场景"这类放到哪个项目
都成立的话。用户的评价是"我看了不适用"。重做的三条口径：

1. **对象是一条用例的全部产物**：步骤、接口场景每一步的 URL/headers/断言/提取物、
   UI 脚本正文、最近几次执行结果、同模块邻居。不是标题列表。
2. **平台先出事实，LLM 只判判断题**（见 evidence.machine_findings）。
   代码能判的（恒真断言、只打控制面、写完没读回、UI 脚本硬伤…）一律不问 LLM；
   LLM 判的是"这个场景合不合理""该验的验了吗""漏了什么"。
3. **判定规则写死在代码里，不交给 LLM**：有 blocker 就是不过，分数线也在代码里。
   让 LLM 自己说"我给它 approved"，等于把闸门交给一个会被说服的东西。

六个维度和权重（评审对象决定权重，不适用的维度按比例摊掉）：
    场景合理性 20 / 验证点到位 25 / 接口必要性 10 /
    UI 脚本正确性 15 / 覆盖遗漏 20 / 可执行与纪律 10
「验证点到位」最重，因为这轮返工全部出在这一维：
只断控制面状态字段就以为验完了、写完不读回、断言恒真。
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case
from app.services.ai import llm_client
from app.services.review import evidence

logger = logging.getLogger(__name__)

PASS_SCORE = 80          # 体检分参考线（**不参与判定**，见 score_and_verdict 的说明）
MAJOR_LIMIT = 2          # 两个 major 就打回（单个 major 压顶之后往往还能过线）

# **模型能标 blocker，但必须点名是哪一类。** 不设这道闸的话，"能更强"的意见迟早
# 也会被标成致命 —— 而闸门一旦变成"什么都打回"，人就开始整体无视它（和上一版
# 评审说"缺少安全测试场景"是同一种失效：说了等于没说）。
# 这几类都是**确定的假绿来源**，且只有模型判得出来（平台判据覆盖不到）：
LLM_BLOCKER_KINDS = {
    "expectation_copied_from_impl",   # 预期照着实现抄 —— 把 bug 固化成"预期"
    "assertion_cannot_fail",          # 这条断言无论系统怎么坏都不会红
    "no_real_verification",           # 通篇没有验证该功能的动作（只走了流程）
    "script_cannot_run",              # 脚本必挂（语法/API 用错/依赖不存在的东西）
}
SEVERITIES = ("blocker", "major", "minor")

DIMENSIONS = {
    "scenario_sanity": {"label": "场景合理性", "weight": 20, "applies": "always"},
    "verification_depth": {"label": "验证点到位", "weight": 25, "applies": "always"},
    "api_necessity": {"label": "接口必要性", "weight": 10, "applies": "api"},
    "ui_correctness": {"label": "UI 脚本正确性", "weight": 15, "applies": "ui"},
    # **只判这一条自己承诺的东西验全了没有**，不判"这个模块还缺哪些场景"。
    # 实测第一版就是后者：一条写得很完整的用例，因为它所在模块只有它自己，
    # 被判"该模块对越权/幂等毫无覆盖"扣到 55 分，加权 74 分打回 —— 冤枉。
    # 模块缺什么写进 coverageGaps（情报，进模块报告），不进这一条的分。
    "self_coverage": {"label": "本条覆盖完整性", "weight": 20, "applies": "always"},
    "discipline": {"label": "可执行与纪律", "weight": 10, "applies": "always"},
}

_SYSTEM = """你是这个测试平台的评审员，替代人工那道「待审」。你要判的是**这一条用例值不值得进回归**。

你看到的东西分两类，别搞混：
· **事实**（machineFindings / owes / 执行记录）——平台用代码判出来的，**一律为真**。
  你的任务是把它们**归到维度里、说清后果**，不是复核它们对不对，更不许推翻。
· **待判断的**——场景本身合不合理、该验的验了没有、漏了什么、预期是不是照实现抄的。
  这些才是你要出结论的地方。

铁律：
1. **不要输出任何统计数字**（多少条、百分比、覆盖率）。平台会算，你数会数错。
2. 每条 finding 必须**指到具体位置**：步骤名、断言、脚本里的那一行/那个选择器。
   指不到位置的意见一律不要写 —— 那种话对每条用例都成立，等于没说。
3. 严重程度只有三档，按"放它进回归会怎样"判。
   **标 blocker 必须同时给 `kind`**，且只能从这四类里选（点不出类别的，最重只能标 major）：
   · `expectation_copied_from_impl` —— 预期照着实现抄，把 bug 固化成"预期"
   · `assertion_cannot_fail` —— 这条断言无论系统怎么坏都不会红
   · `no_real_verification` —— 通篇没有验证该功能的动作，只走了一遍流程
   · `script_cannot_run` —— 脚本必挂（语法错、API 用错、依赖不存在的东西）
   三档的含义：
   · blocker —— 放进去就是**假绿**或者根本跑不了：断言恒真、只断控制面状态就当生效、
     预期照着实现抄（把 bug 固化成预期）、UI 脚本必挂。
   · major   —— 能跑，但**验不出该验的**：写完不读回、只断状态码、漏了关键反向场景。
   · minor   —— 可读性、命名、冗余步骤、可以更强但不致命。
4. **别把"还没做"当质量问题**：owes 里列出的维度是进度。只有"承诺要做却没做"才提，
   而且归到 coverage_gap，severity 最多 major。
5. **self_coverage 只判这一条自己的承诺验全了没有** —— 标题和 expectedResult 里
   说到的每一件事，步骤/断言里是不是都验了。典型缺口：
   预期写了"列表和详情都查不到"，脚本只验了详情；
   预期写了"恢复后重新生效"，脚本只验了禁用那一半。
   ⚠ **不要因为这个模块缺别的场景而扣这一条的分**。模块级的缺口（越权、幂等、
   边界、状态切回来、异步收敛、删除残留）写进 `coverageGaps` —— 那是给人看的情报，
   不影响这一条过不过。已经有邻居覆盖的不要重复提。
6. **`reflections` 是作者自己写的"这条在验什么"**（回推时四问的答案）。用法：
   · 它说"第 8 步验编号不变"，你就去看第 8 步的断言 ——
     **说的和断言对不上，是最硬的证据**，标 blocker + kind=no_real_verification。
   · 它说某类场景"不适用"，理由站得住就别再当遗漏提。
   · **`reflections` 为空** = **自证不全**：作者没说这条在验什么。不是零分，
     但 self_coverage 最高给 70，并列一条 major「没答回推四问，这条在验什么只能靠猜」。
7. 只输出一个 JSON 对象，用 ```json 包裹。

JSON 形状（dimensions 里只出现适用的维度，分数 0-100 整数）：
{
  "dimensions": {
    "scenario_sanity": {"score": 85, "comment": "一句话"},
    "verification_depth": {"score": 60, "comment": "一句话"},
    "self_coverage": {"score": 90, "comment": "一句话"}
  },
  "findings": [
    {"dimension": "verification_depth", "severity": "blocker",
     "kind": "no_real_verification",
     "where": "步骤 6「审批通过后应生效」",
     "problem": "只断了控制面 status=approved",
     "fix": "补一步拿该应用凭据打网关，审批前必须 401、审批后 200"}
  ],
  "coverageGaps": ["模块级缺口：禁用后重新启用是否恢复调用（邻居里没有，不扣这一条的分）"],
  "summary": "两句话之内说清这条用例的问题"
}"""


def _applicable(ev: dict) -> dict:
    """哪些维度适用于这条用例。**不适用的维度权重摊给其他维度** ——
    给一条 target_level=spec 的用例扣「UI 脚本正确性」的分，是在惩罚它没承诺的事。
    """
    has_api = bool(ev.get("apiScenario"))
    has_ui = bool(ev.get("uiScript"))
    out = {}
    for key, meta in DIMENSIONS.items():
        if meta["applies"] == "api" and not has_api:
            continue
        if meta["applies"] == "ui" and not has_ui:
            continue
        out[key] = meta
    total_w = sum(m["weight"] for m in out.values()) or 1
    # normWeight **不四舍五入**：round 到 4 位之后几个维度加起来是 0.9999，
    # 加权总分会系统性偏低一点点，刚好卡在分数线上时能把 80 变成 79。
    # 显示用的整数百分比在下面单独 round。
    return {k: {**m, "normWeight": m["weight"] / total_w} for k, m in out.items()}


def _prompt(ev: dict, applicable: dict) -> list[dict]:
    case = ev["case"]
    dim_lines = "\n".join(
        f"- {k}（{m['label']}，权重 {round(m['normWeight'] * 100)}%）" for k, m in applicable.items())
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"""## 这条用例适用的维度（只评这些）
{dim_lines}

## 用例本体
{json.dumps(case, ensure_ascii=False, indent=2)}

## 接口场景（每一步的 URL / headers / 断言 / 提取物）
{json.dumps(ev.get("apiScenario"), ensure_ascii=False, indent=2)}

## UI 脚本
{json.dumps(ev.get("uiScript"), ensure_ascii=False, indent=2)}

## 平台判出来的事实（一律为真，你负责归类和说后果，不要复核）
（`run_first` 开着时这里会多出**执行式审核**的结论：这次真跑的结果、
页面真实发的请求 vs 接口场景用的端点。那几条是最硬的证据，别轻描淡写。）
{json.dumps(ev.get("machineFindings"), ensure_ascii=False, indent=2)}

## 按承诺还欠哪几维（进度，不是质量问题）
{json.dumps(ev.get("owes"), ensure_ascii=False)}

## 最近执行记录
{json.dumps(ev.get("recentRuns"), ensure_ascii=False, indent=2)}

## 同模块已有用例（判重复和遗漏要看这些）
{json.dumps(ev.get("neighbors"), ensure_ascii=False, indent=2)}

按上面的 JSON 形状输出评审结论。"""},
    ]


_JSON = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)


def _parse(text: str) -> dict | None:
    m = _JSON.search(text or "")
    raw = m.group(1) if m else None
    if raw is None:
        i, j = (text or "").find("{"), (text or "").rfind("}")
        raw = text[i:j + 1] if i >= 0 and j > i else None
    if not raw:
        return None
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else None
    except Exception:  # noqa: BLE001
        return None


def merge_findings(machine: list[dict], llm: list[dict]) -> list[dict]:
    """机器事实 + LLM 判断合成一份清单。

    机器那份**必须原样进结果**：LLM 可能漏掉它、也可能把 blocker 说成 minor，
    而这几条正是最贵的那几条（恒真断言、只打控制面）。
    去重按 kind/where 粗粒度做 —— 同一件事说两遍会让人以为有两个问题。
    """
    out = []
    seen_detail = set()
    for f in machine:
        key = (f.get("kind"), (f.get("detail") or "")[:40])
        seen_detail.add(key)
        out.append({"dimension": _kind_to_dim(f.get("kind")), "severity": f.get("severity", "major"),
                    "where": f.get("where") or "-", "problem": f.get("detail"),
                    # **kind 要留着**：前端要按类型筛、CC 要按类型判该怎么改，
                    # 丢了之后只能对着文本做子串匹配（活体验证时我自己就栽在这上面：
                    # 探针按 kind 过滤永远是空，看起来像"没报"，其实报了）
                    "kind": f.get("kind"), "fix": None, "source": "platform"})
    for f in (llm or []):
        if not isinstance(f, dict):
            continue
        sev = str(f.get("severity") or "minor").lower()
        if sev not in SEVERITIES:
            sev = "minor"
        kind = str(f.get("kind") or "").strip()
        if sev == "blocker" and kind not in LLM_BLOCKER_KINDS:
            # 点不出是哪一类的"致命"降成 major。它照样进 mustFix、照样参与
            # 「两处 major 就打回」，只是不再单独一票否决。
            sev = "major"
        prob = str(f.get("problem") or "")[:600]
        if not prob:
            continue
        # LLM 复述了机器那条 → 丢掉，保留机器那条（它的 severity 才是权威）
        if any(prob[:24] in (d or "") for _, d in seen_detail):
            continue
        out.append({"dimension": f.get("dimension") or "scenario_sanity", "severity": sev,
                    "where": str(f.get("where") or "-")[:200],
                    "problem": prob, "fix": str(f.get("fix") or "")[:600] or None,
                    "kind": kind or None, "source": "ai"})
    return out


def _kind_to_dim(kind: str | None) -> str:
    return {
        "tautology_assertion": "verification_depth",
        "missing_baseline": "verification_depth",
        "no_readback": "verification_depth",
        "control_plane_only": "verification_depth",
        "status_only_assertion": "verification_depth",
        "control_group_in_one": "scenario_sanity",
        "vague_expectation": "scenario_sanity",
        "endpoint_not_used_by_page": "verification_depth",
        "traffic_not_covered": "verification_depth",
        "script_no_action": "ui_correctness",
        "script_fewer_actions": "ui_correctness",
        "ui_script_hard_error": "ui_correctness",
        "i18n_key_not_in_dict": "ui_correctness",
        "ui_script_warning": "ui_correctness",
    }.get(kind or "", "discipline")


def score_and_verdict(dimensions: dict, findings: list[dict], applicable: dict) -> dict:
    """**判定在代码里，不问 LLM。**

    - 有 blocker → 一律不过。哪怕它给 95 分。
    - 加权分低于分数线 → 不过。
    - LLM 没给某个适用维度的分 → 按该维度上的 finding 严重程度兜一个，
      不是当满分（缺分数默认满分，等于漏评就白送）。
    """
    per_dim = {}
    for key, meta in applicable.items():
        raw = (dimensions or {}).get(key) or {}
        s = raw.get("score")
        if not isinstance(s, (int, float)):
            worst = _worst_severity([f for f in findings if f.get("dimension") == key])
            s = {"blocker": 40, "major": 65, "minor": 85, None: 80}[worst]
        s = max(0, min(100, int(round(s))))
        # **按该维度上最重的 finding 压顶**。不压的话会出现这种情况：
        # 平台明明判出一条 major（对照组塞一条），LLM 却给这一维 85 分，
        # 加权照样过线 —— 机器事实等于白判。评测里实测过（81 分过审）。
        worst = _worst_severity([f for f in findings if f.get("dimension") == key])
        cap = {"blocker": 45, "major": 70}.get(worst)
        if cap is not None:
            s = min(s, cap)
        per_dim[key] = {"label": meta["label"], "score": s,
                        "weight": round(meta["normWeight"] * 100),
                        "comment": str(raw.get("comment") or "")[:300] or None}
    total = round(sum(d["score"] * applicable[k]["normWeight"] for k, d in per_dim.items()))
    blockers = [f for f in findings if f.get("severity") == "blocker"]
    majors = [f for f in findings if f.get("severity") == "major"]
    # **过不过只看有没有实质问题，不看分数。**
    #
    # 分数是六个维度的加权，而每一维的分是模型给的 —— 同一条用例两次评审
    # 拿到 86 和 78 是常事（评测实测：一条写得规范的 UI 用例就这么被 78 分打回一次）。
    # 拿抖动的数当闸门有两个坏处：①同一条用例的结论不稳定，人就不信它了；
    # ②「78 分低于 80 分线」这句话没法照着改，而「这两条必须改」可以。
    # 分数留着做排序和体检，不参与判定。
    if blockers:
        verdict, reason = "rejected", f"有 {len(blockers)} 个致命问题（放进回归就是假绿或跑不了）"
    elif len(majors) >= MAJOR_LIMIT:
        # 两处"验不出该验的"叠在一起就该打回；一处不打回 ——
        # 否则任何"还能更强"的用例都过不了，人一样会开始无视这个结论。
        verdict, reason = "rejected", f"有 {len(majors)} 处重要问题（验不出该验的东西）"
    else:
        verdict, reason = "approved", (
            f"没有致命问题" + (f"，1 处重要问题已列出" if majors else "") + f"（体检分 {total}）")
    return {"total": total, "dimensions": per_dim, "verdict": verdict, "verdictReason": reason,
            "blockerCount": len(blockers),
            "majorCount": len([f for f in findings if f.get("severity") == "major"])}


def _worst_severity(findings: list[dict]) -> str | None:
    for sev in SEVERITIES:
        if any(f.get("severity") == sev for f in findings):
            return sev
    return None


async def _guess_env(session, case_id) -> str | None:
    """这条用例最近是在哪个环境跑通的 —— 拿它当审核试跑的环境。

    比让调用方每次都传更实际：审核入口在页面上是一个按钮，
    人不会先去想"该选哪个环境"。找不到就老实说没跑，不瞎跑。
    """
    from sqlalchemy import select

    from app.models.report import TestReport, TestReportScenario
    from app.models.script import ScriptRun
    try:
        row = (await session.execute(
            select(TestReport.environment_id)
            .join(TestReportScenario, TestReportScenario.report_id == TestReport.id)
            .join(ScriptRun, ScriptRun.report_scenario_id == TestReportScenario.id)
            .where(ScriptRun.case_id == case_id, TestReport.environment_id.isnot(None))
            .order_by(ScriptRun.created_at.desc()).limit(1))).scalars().first()
        if row:
            return str(row)
    except Exception:  # noqa: BLE001
        pass
    return None


async def _run_and_diff(session, case_id, ev: dict, env_id: str | None) -> None:
    """真跑一遍 + 拿这次的真实流量做四方对比，结论并进 machineFindings。

    UI 优先：UI 执行才有浏览器流量（`captured_requests`），而"页面到底调了哪个端点"
    只有它答得上。没有 UI 脚本才退回只跑接口场景。
    """
    from sqlalchemy import select

    from app.models.script import ScriptRun
    from app.services.review.traffic_diff import compare

    # **没有环境就别跑**。env_id 为空时跑出来的是 BASE_URL="" 的垃圾运行
    # （脚本导航到 "/login" 直接 Protocol error），而审核会把它报成"这条跑挂了" ——
    # 人会以为用例坏了。活体验证第一次就撞在这上面。
    if not env_id:
        env_id = await _guess_env(session, case_id)
    if not env_id:
        ev["freshRun"] = {"skipped": "没指定环境（envId），执行式审核跳过 —— "
                                    "空环境跑出来的失败是假的，不如不跑"}
        ev["machineFindings"] = list(ev.get("machineFindings") or []) + [{
            "kind": "review_run_skipped", "severity": "minor", "where": "-",
            "detail": "这次审核**没有真跑**（没给 envId，这条用例也没有历史执行可参考）。"
                      "「接口场景用的端点页面到底调不调」这类问题只有真跑才看得出来 —— "
                      "带上 envId 再审一次。"}]
        return

    ran = {}
    if ev.get("uiScript"):
        try:
            from app.mcp.tools.ui_scripts import run_ui_script
            r = await run_ui_script(case_id=str(case_id), env_id=env_id, session=session,
                                    run_mode="debug")     # 审核跑不进通过率口径
            ran = {"type": "ui", "status": r.get("status"),
                   "error": (r.get("error_summary") or "")[:200]}
        except Exception as e:  # noqa: BLE001
            ran = {"type": "ui", "error": str(e)[:200]}
    elif ev.get("apiScenario"):
        try:
            from app.mcp.tools.api_tests import run_api_test
            from app.models.api_test import ApiTestScenario
            sid = (await session.execute(
                select(ApiTestScenario.id).where(ApiTestScenario.source_case_id == case_id)
            )).scalars().first()
            if sid:
                res = await run_api_test(session, scenario_ids=str(sid), env_id=env_id)
                ran = {"type": "api", "passed": res.get("passed"), "failed": res.get("failed"),
                       "failedSteps": [x.get("step") for x in (res.get("results") or [])
                                       if x.get("status") not in ("pass", "passed", "skipped")][:6]}
        except Exception as e:  # noqa: BLE001
            ran = {"type": "api", "error": str(e)[:200]}
    ev["freshRun"] = ran or {"note": "这条既没有 UI 脚本也没有接口场景，没得跑"}

    # 取这次执行录到的浏览器流量
    run = (await session.execute(
        select(ScriptRun).where(ScriptRun.case_id == case_id)
        .order_by(ScriptRun.created_at.desc()).limit(1))).scalars().first()
    captured = (run.captured_requests or []) if run is not None else []
    ev["trafficSeen"] = len(captured)
    logger.info("执行式审核：case=%s 抓到 %d 条请求", case_id, len(captured))
    if captured:
        facts = compare(captured,
                        (ev.get("apiScenario") or {}).get("steps") or [],
                        (ev.get("case") or {}).get("steps") or [],
                        (ev.get("uiScript") or {}).get("content"))
        if facts:
            ev["machineFindings"] = list(ev.get("machineFindings") or []) + facts


async def review_case(session: AsyncSession, case_id: uuid.UUID, *, ai_config=None,
                      persist: bool = True, run_first: bool = False,
                      env_id: str | None = None) -> dict:
    """评审一条用例。`run_first=True` 会**先真跑一遍**再评。

    为什么要能先跑：断言到底咬不咬得住，静态看不出来 —— 一条"改完读回来还是 200"的
    断言长得完全正常，只有跑过、并且对着变异跑过才知道它是恒真的。
    跑的是 debug 模式，不进通过率口径。
    """
    ev = await evidence.collect(session, case_id)
    if ev is None:
        return {"error": f"用例 {case_id} 不存在"}

    if run_first:
        # **执行式审核**：不真跑就发现不了"接口调错端点""步骤没落实"这两类 ——
        # 两个端点都合法、都返回 200 时，静态审核只会说"写得挺完整"。
        # 用户的原话：不能只停留在查看，而不真实执行。
        await _run_and_diff(session, case_id, ev, env_id)

    applicable = _applicable(ev)
    try:
        resp = await llm_client.complete(_prompt(ev, applicable), config=ai_config,
                                         max_tokens=2500, temperature=0)
    except Exception as e:  # noqa: BLE001
        logger.exception("评审 LLM 调用失败")
        return {"error": f"AI 评审失败：{str(e)[:200]}"}

    parsed = _parse(resp.content) or {}
    findings = merge_findings(ev.get("machineFindings") or [], parsed.get("findings") or [])
    scored = score_and_verdict(parsed.get("dimensions") or {}, findings, applicable)

    result = {
        "caseId": str(case_id), "caseCode": ev["case"]["caseCode"],
        "title": ev["case"]["title"],
        **scored,
        "findings": findings,
        "coverageGaps": [str(g)[:300] for g in (parsed.get("coverageGaps") or [])][:8],
        "summary": str(parsed.get("summary") or "")[:600],
        "owes": ev.get("owes"),
        "reviewedAt": datetime.now(timezone.utc).isoformat(),
        "model": getattr(ai_config, "model", None),
        "ranBeforeReview": ev.get("freshRun"),
        # 这次比对看了多少条真实请求。**要露出来** —— 是 0 的话
        # "没发现端点问题"只说明没得比，不说明端点是对的
        "trafficSeen": ev.get("trafficSeen"),
    }

    if persist:
        case = (await session.execute(select(Case).where(Case.id == case_id))).scalars().first()
        if case is not None:
            case.review_status = scored["verdict"]
            case.quality_score = {"total": scored["total"],
                                  "dimensions": {k: v["score"] for k, v in scored["dimensions"].items()},
                                  "reviewedAt": result["reviewedAt"], "by": "ai",
                                  "model": result["model"]}
            case.review_reason = {
                "category": "ai_review",
                "text": scored["verdictReason"],
                "summary": result["summary"],
                # 结论要能复核：把 findings 存下来，人点开看得到"凭什么不过"
                "findings": findings[:20],
                "coverageGaps": result["coverageGaps"],
            }
            # 记一轮 —— 审核以前只有"当前值"，没有过程。有了它，
            # 「AI 打回 → CC 整改 → 再审 → 通过」这条链在页面上看得见。
            from app.services.review import rounds
            await rounds.record(session, case_id, "ai_review",
                                verdict=scored["verdict"], total=scored["total"],
                                dimensions={k: v["score"] for k, v in scored["dimensions"].items()},
                                findings=findings[:20], coverage_gaps=result["coverageGaps"],
                                summary=result["summary"], actor="ai", model=result["model"])
            await session.commit()
    return result
