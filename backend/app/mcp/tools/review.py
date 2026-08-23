"""CC 自审通道 —— 回推完自己先过一遍评审，别等人。

为什么给 CC 开这个口子：评审用的六个维度里，五个都是它自己能改的
（场景合不合理、验证点够不够、接口有没有多余、UI 脚本对不对、纪律）。
让它回推完自己评一次、按 findings 改完再评，人看到的就是已经过审的东西。
这也是"AI 评审替掉人工待审"的前半段 —— 后半段是平台在页面上按同一套判据出结论。
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession


async def review_case(
    session: AsyncSession,
    case_id: str,
    run_first: bool = False,
    env_id: str | None = None,
) -> dict:
    """按六个维度评审一条用例，回结论 + 每条 finding 指到具体位置。

    维度：场景合理性 / 验证点到位 / 接口必要性 / UI 脚本正确性 / 覆盖遗漏 / 可执行与纪律。
    不适用的维度自动摊掉权重（没写 UI 脚本就不评 UI 那一维）。

    **判定不由 AI 说**：有 blocker 一律不过、加权低于 80 不过 —— 规则写在平台代码里。
    blocker 的定义是"放进回归就是假绿或根本跑不了"：断言恒真、只断控制面状态就当生效、
    预期照着实现抄、UI 脚本必挂。

    `run_first=True` 会先真跑一遍这条的接口场景再评（debug 模式，不进通过率口径）。
    断言咬不咬得住静态看不出来 —— "改完读回来还是 200" 长得完全正常。

    结论会落库：审核标签（approved/rejected）、评分、findings。
    评完照着 findings 改，改完再评一次；rejected 的 blocker 一条都不许留着交上去。
    """
    from app.services.ai_config_resolver import resolve_ai_config
    from app.services.review import reviewer
    from app.models.case import Case
    from sqlalchemy import select

    cid = uuid.UUID(case_id)
    case = (await session.execute(select(Case).where(Case.id == cid))).scalars().first()
    if case is None:
        return {"error": f"用例 {case_id} 不存在"}

    # ── 三岔路口 ──────────────────────────────────────────
    # 这个工具是 CC 每轮对每条用例都会调的那一个，所以版本升级新增的两件事
    # 都合进来，不新开工具（不用让 CC 判断"这次该调哪个"）。
    from app.services import branch_diff_review

    # ① 有待决废弃请求 → 不审六维，改审「该不该废」。
    #    审一条正在申请废弃的用例的六维质量本身没有意义。
    if case.deprecate_status == "requested":
        return await branch_diff_review.review_deprecate(session, case, env_id=env_id)

    # ② 照抄堆（未被对账清单命中 + 内容与上一版逐字一致 + 上一版已审通过）
    #    → 四条件结算，不问 AI。清单命中的是「端点变了/字段变了/新增状态值」，
    #    没被命中就意味着它碰的接口和字段这一版全没动 —— 上一版的审核结论仍然成立，
    #    再审是拿同一份内容问同一个问题。
    hits = await branch_diff_review.hit_case_ids_of(session, case.branch_id)
    if hits is not None and case.review_status not in ("approved", "rejected"):
        why = await branch_diff_review.auto_approve_reason(session, case, hits)
        if why is not None and why[0]:
            await branch_diff_review.approve_as_system(session, case, why[1], why[2])
            await session.commit()
            return {
                "caseCode": case.case_code, "verdict": "approved",
                "decidedBy": "system", "照抄堆自动过审": True,
                "理由": why[1],
                "说明": ("这条没被对账清单命中、内容与上一版逐字一致、上一版已审通过，"
                         "所以不再走六维审。后续补交 changes 时若新命中，"
                         "这次自动过审会被撤回待审。"),
            }

    from app.models.project import Branch
    pid = (await session.execute(
        select(Branch.project_id).where(Branch.id == case.branch_id)
    )).scalars().first()
    cfg = await resolve_ai_config(pid, session, capability="tb-quality-review")
    if not cfg:
        return {"error": "这个项目还没配 AI 服务，评审跑不了"}
    out = await reviewer.review_case(session, cid, ai_config=cfg,
                                    persist=True, run_first=run_first, env_id=env_id)
    if out.get("error"):
        return out
    # 给 CC 的返回要短：它要的是"过没过 + 该改哪几条"，不是完整报告
    return {
        "caseCode": out["caseCode"], "verdict": out["verdict"], "total": out["total"],
        "verdictReason": out["verdictReason"],
        "dimensions": {k: v["score"] for k, v in out["dimensions"].items()},
        "mustFix": [f"[{f['severity']}] {f['where']}：{f['problem']}"
                    + (f" → {f['fix']}" if f.get("fix") else "")
                    for f in out["findings"] if f["severity"] in ("blocker", "major")][:10],
        "niceToFix": [f"{f['where']}：{f['problem']}" for f in out["findings"]
                      if f["severity"] == "minor"][:5],
        "coverageGaps": out["coverageGaps"],
        "summary": out["summary"],
        "ranBeforeReview": out.get("ranBeforeReview"),
        # 这次是**真跑过再评**还是静态看的，必须回给 CC —— 两者结论强度差一个量级
        # （实测同一条：静态 84 分通过、真跑 56 分打回）。不说的话它会拿一个
        # 静态 approved 当"这条过了"就交上去。
        "reviewMode": out.get("reviewMode"),
        "runAttribution": out.get("runAttribution"),
        "usage": ("rejected 的 blocker 一条都不许留着交上去。改完再调一次这个工具复核。"
                  + (" **这次是静态审的**（没真跑），"
                     "「接口场景验的端点页面到底调不调」这类问题它看不出来 —— "
                     "带 run_first=true 和 env_id 再审一次才算数。"
                     if out.get("reviewMode") != "run_first" else "")
                  + (" 这次结论是「无法审核」：既不算通过也不算打回，"
                     "把环境弄好再审一次。"
                     if out.get("verdict") == "inconclusive" else "")),
    }


async def module_checkup(
    session: AsyncSession,
    branch_id: str,
    module: str | None = None,
    folder_id: str | None = None,
    observed_actions: list | None = None,
) -> dict:
    """**这个模块还缺什么** —— 写完一批用例自己问一句，别等人催（review-spec §8）。

    回两块：
    · `commonIssues` 共性问题 —— 这个模块的用例反复犯的同一个错（改一处能修一片）。
      纯汇总，不问模型。
    · `coverageGaps` 覆盖缺口 —— 该测没测的场景。

    **`observed_actions` 值得多花一步去凑**：把你在页面上探到的可操作项
    （按钮、菜单项、状态流转）传进来，缺口就是拿它跟现有用例对账出来的 ——
    "页面上有这个操作、用例里一条都没覆盖"是最硬的缺口。不传的话它只能
    凭用例标题猜，出来的东西会泛。

    **缺口是建议清单，不是门禁** —— 不参与任何一条用例过不过。
    不占队列、不用环境、不碰被测系统，随时可以问。
    """
    from app.services.review import checkup

    out = await checkup.run(session, branch_id, folder_id=folder_id, module=module,
                            observed_actions=observed_actions)
    if out.get("error"):
        return out
    return out
