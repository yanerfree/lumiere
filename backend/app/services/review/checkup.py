"""模块体检：**这个模块整体怎么样、还缺什么**（review-spec §8）。

跟逐条审核是两件事：一条一条看只知道"这条不行"；看模块才知道
"这一整片都犯同一个错"和"这个模块压根没测到的地方"。

## 三块输出，来源完全不同

| 块 | 怎么来的 | 为什么这么定 |
|---|---|---|
| **共性问题** | 把逐条 findings 按 kind 归类，**纯汇总，不问 LLM** | 它就是个 group by。塞模型调用既慢又不稳，而"同一份报告两次打开长得不一样"比不聚合更糟 |
| **覆盖缺口** | 额外一次模型调用 | 「这个模块没测到什么」只有读过全部标题才答得上，代码判不了 |
| **覆盖分布** | `intake_gate.check_coverage`，**纯代码数个数** | P0 占比、六类操作各几条、缺哪一类 —— 这种问题模型答得又慢又飘，`re` 一遍就完。它原来在入库门禁里，可回推一条一条来、手里永远没有"一批"，2026-08-25 挪到这里 |

缺口和分布看的是同一件事的两面：缺口靠模型看**内容**（"页面上有这个操作、用例
里一条都没覆盖"），分布靠代码数**个数**（"18/22 都是创建类，一条删除都没有"）。
模型会漏掉后者 —— 它读 60 条标题时不会去算比例，而比例恰恰是最刺眼的信号。

## 覆盖缺口是**情报，不是闸门**

不参与任何一条用例过不过。`cc-platform-loop-spec.md` 附节原来的结论是
"模块级遗漏判不了，该由人看"；现在敢让它出缺口，是因为多了
**CC 在页面上探到的实际可操作项**做对账（`observed_actions`），
不再是纯凭用例标题猜。但定位没变 —— 它只负责告诉你"还该补什么"。

## 不占队列、不用环境

它只读用例库，不碰被测系统。所以 CC 写完一批用例可以随手问一句
"这个模块还缺什么"，拿到清单接着补，不用等人催、也不用排在真跑后面。
"""
from __future__ import annotations

import json
import logging
import re
import uuid

from sqlalchemy import select

from app.models.case import Case, CaseFolder
from app.services.ai import llm_client
from app.services.review.gap_merge import merge as merge_gaps

logger = logging.getLogger(__name__)

MAX_TITLES = 60         # 再多一次读不完，模型也开始编

_SYSTEM = """你在给一个测试模块做覆盖体检。给你这个模块**现有全部用例**的标题和步骤要点，
可能还有测试人员在页面上探到的**实际可操作项**。

你只做一件事：指出**这个模块还缺哪些该测没测的场景**。

硬要求：
1. **每条缺口必须能对上这个模块的具体功能**，不能是"缺少安全测试""建议补充异常场景"
   这类放到哪个项目都成立的话 —— 那种话说了等于没说。
2. 给了「实际可操作项」的话，**优先对着它找**：页面上有这个操作、用例里一条都没覆盖，
   这是最硬的缺口。
3. 已经有用例覆盖的别再列。拿不准的宁可不列。
4. 最多 8 条，按"不补会漏掉真 bug"的程度排序。

只输出 JSON：
```json
{"coverageGaps": ["...", "..."]}
```"""


def _norm_kind(kind: str) -> str:
    return re.sub(r"[^a-z_]", "", (kind or "").lower())


# 判据 kind → 人话。**没有这张表这一页就没法读**：一列
# `endpoint_not_used_by_page` 谁也不知道该改什么，而这一块存在的理由
# 恰恰是"改一处能修一片"——说不清是哪一处就白搭。
_KIND_LABEL = {
    "tautology_assertion": "断言恒真（写了等于没写）",
    "missing_baseline": "改之前没取基线",
    "no_readback": "改完没读回来确认",
    "control_plane_only": "只打了控制面，没验真生效",
    "status_only_assertion": "只断状态码，没断内容",
    "vague_expectation": "预期是「显示正常」这类模糊话",
    "control_group_in_one": "对照组挤在一条用例里",
    "endpoint_not_used_by_page": "验的端点页面根本不调",
    "traffic_not_covered": "页面真发的写请求没人验",
    "script_no_action": "脚本里一个页面动作都没有",
    "script_fewer_actions": "脚本动作数远少于步骤数",
    "ui_script_hard_error": "UI 脚本必挂",
    "i18n_key_not_in_dict": "文案键不在词典里",
    "step_action_not_in_script": "步骤写的操作脚本里没有",
    "no_assertion_for_expectations": "写了预期却一个断言都没有",
    "expectation_not_asserted": "某几条预期没有对应检查",
    "verify_step_without_assertion": "「验证」步骤没带断言",
    "script_cannot_run": "真跑挂在脚本自己身上",
    "residue_not_cleaned": "跑完没清理造的数据",
    "cleanup_failed": "清理发起了但没删掉",
    "no_traffic_captured": "跑了但没录到流量（没得对账）",
    "expectation_copied_from_impl": "预期照着实现抄",
    "assertion_cannot_fail": "这条断言不可能红",
    "no_real_verification": "通篇没有验证该功能的动作",
}
_DIM_LABEL = {
    "scenario_sanity": "场景合理性", "verification_depth": "验证点到位",
    "api_necessity": "接口必要性", "ui_correctness": "UI 脚本正确性",
    "self_coverage": "本条覆盖完整性", "discipline": "可执行与纪律",
}


async def common_issues(session, case_ids: list) -> list[dict]:
    """共性问题：**纯汇总，不问 LLM**（见模块头的表）。

    只数 blocker 和 major —— minor 全塞进来的话这一块会有二十几行，
    而它存在的理由是"改一处能修一片"，二十几行里挑不出那一处。
    """
    if not case_ids:
        return []
    rows = (await session.execute(
        select(Case.case_code, Case.review_reason)
        .where(Case.id.in_([uuid.UUID(str(c)) for c in case_ids])))).all()
    buckets: dict[str, dict] = {}
    for code, reason in rows:
        for f in ((reason or {}).get("findings") or []):
            if f.get("severity") not in ("blocker", "major"):
                continue
            # **模型产出的 finding 没有 kind，只有 dimension** —— 全归到一个
            # `other` 桶的话，这一页最大的一格写着「5× other」，等于没说。
            # 活体验证第一次跑出来就是这样。没 kind 就按维度分桶。
            k = _norm_kind(f.get("kind"))
            dim = str(f.get("dimension") or "")
            if k:
                label = _KIND_LABEL.get(k, k)
            elif dim:
                k, label = f"dim:{dim}", f"{_DIM_LABEL.get(dim, dim)}（模型判的）"
            else:
                k, label = "other", "其他"
            b = buckets.setdefault(k, {"kind": k, "label": label, "count": 0, "cases": [],
                                       "severity": f.get("severity"),
                                       "sample": str(f.get("detail")
                                                      or f.get("problem") or "")[:220]})
            b["count"] += 1
            if code and code not in b["cases"] and len(b["cases"]) < 8:
                b["cases"].append(code)
            if f.get("severity") == "blocker":
                b["severity"] = "blocker"
    return sorted(buckets.values(), key=lambda x: (-x["count"], x["kind"]))[:10]


async def coverage_gaps(session, cases: list, ai_config,
                        observed_actions: list | None = None) -> tuple[list, int]:
    """覆盖缺口：一次模型调用。返回 (归并后的缺口, 归并前总条数)。"""
    if not cases or ai_config is None:
        return [], 0
    lines = []
    for c in cases[:MAX_TITLES]:
        steps = "；".join(str(s.get("action") or "")[:40]
                          for s in (c.steps or [])[:4] if isinstance(s, dict))
        lines.append(f"- {c.case_code} {c.title}" + (f"（步骤：{steps}）" if steps else ""))

    user = "现有用例：\n" + "\n".join(lines)
    if observed_actions:
        user += ("\n\n页面上实际探到的可操作项：\n"
                 + "\n".join(f"- {str(a)[:80]}" for a in observed_actions[:40]))
    if len(cases) > MAX_TITLES:
        user += f"\n\n（这个模块共 {len(cases)} 条，上面只列了前 {MAX_TITLES} 条）"

    try:
        resp = await llm_client.complete(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
            config=ai_config, max_tokens=1200, temperature=0)
    except Exception:  # noqa: BLE001
        logger.exception("模块体检 LLM 调用失败")
        return [], 0

    m = re.search(r"```json\s*(\{.*?\})\s*```", resp.content or "", re.S)
    raw = m.group(1) if m else (resp.content or "")
    try:
        gaps = (json.loads(raw) or {}).get("coverageGaps") or []
    except Exception:  # noqa: BLE001
        return [], 0

    # 归并按**话题**，不按字面 —— LLM 每轮措辞都不一样，
    # 同一件事（越权）会被拆成三条各 1×，而这一列存在的理由就是那个 count。
    merged, total = merge_gaps([(str(g)[:300], None) for g in gaps], top=8)
    return merged, total


async def run(session, branch_id, *, folder_id=None, module: str | None = None,
              observed_actions: list | None = None, ai_config=None) -> dict:
    """做一次体检。folder_id 和 module 给一个就行。"""
    bid = uuid.UUID(str(branch_id))
    folder = None
    if folder_id:
        folder = await session.get(CaseFolder, uuid.UUID(str(folder_id)))
    elif module:
        folder = (await session.execute(
            select(CaseFolder).where(CaseFolder.branch_id == bid,
                                     CaseFolder.name == module))).scalars().first()
    if folder is None:
        return {"error": "找不到这个模块"}

    # **要连子模块一起算**（§8「这个模块全部用例」）。只查直属的话，
    # 「订阅管理」这种把用例全放在子模块（审批配置/本租户订阅/跨租户订阅）里的
    # 会被算成 0 条 —— 活体验证第一次跑就是这个结果，报告上一片空白，
    # 看起来像"这个模块很干净"，实际是 16 条一条都没看。
    folder_ids = [folder.id] + [
        r for r in (await session.execute(
            select(CaseFolder.id).where(CaseFolder.branch_id == bid,
                                        CaseFolder.path.like(f"{folder.path}/%")))).scalars().all()
    ]
    cases = (await session.execute(
        select(Case).where(Case.branch_id == bid, Case.folder_id.in_(folder_ids),
                           Case.deleted_at.is_(None))
        .order_by(Case.case_code))).scalars().all()
    if not cases:
        return {"module": folder.name, "total": 0, "commonIssues": [],
                "coverageGaps": [], "coverageSkew": {"total": 0, "notes": []},
                "note": "这个模块还没有用例"}

    if ai_config is None:
        from app.models.project import Branch
        from app.services.ai_config_resolver import resolve_ai_config
        pid = (await session.execute(
            select(Branch.project_id).where(Branch.id == bid))).scalars().first()
        ai_config = await resolve_ai_config(pid, session, capability="tb-quality-review")

    issues = await common_issues(session, [c.id for c in cases])
    gaps, gaps_total = await coverage_gaps(session, cases, ai_config, observed_actions)

    # 第三块：**分布**。跟共性问题一样是纯代码算的（同一份报告两次打开必须一样），
    # 但问的是另一件事 —— 共性问题看"这些用例各自写得对不对"，分布看
    # "这些用例合起来偏不偏"。判据在 intake_gate.check_coverage，那里写着
    # 它为什么从入库门禁挪过来。
    from app.services.intake_gate import check_coverage
    skew = check_coverage([
        {"title": c.title, "priority": c.priority,
         "expected_result": c.expected_result or ""} for c in cases
    ])

    reviewed = sum(1 for c in cases if c.review_status in ("approved", "rejected"))
    return {
        "module": folder.name,
        "total": len(cases),
        "reviewed": reviewed,
        "commonIssues": issues,
        "coverageGaps": gaps,
        "coverageGapsTotal": gaps_total,
        "coverageSkew": skew,
        # 缺口是**情报不是闸门**，这句话要跟着结果走 —— 不写的话
        # 迟早有人拿它当"这个模块没通过"的依据。
        "usage": ("覆盖缺口是建议清单，**不参与任何一条用例过不过**。"
                  "共性问题是逐条 findings 的汇总（改一处能修一片），"
                  "只数 blocker 和 major。"
                  "覆盖分布（coverageSkew）是纯代码算的：P0 占比、六类操作"
                  "（创建/查询/修改/删除/异常/权限）各几条、缺哪一类 —— "
                  "缺口靠模型看内容，分布靠代码数个数，两个都不是门禁。"
                  + ("" if observed_actions else
                     " 传 observed_actions（你在页面上探到的可操作项）能让缺口准得多 —— "
                     "不传的话它只能凭用例标题猜。")),
    }
