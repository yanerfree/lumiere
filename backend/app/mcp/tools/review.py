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
        "usage": "rejected 的 blocker 一条都不许留着交上去。改完再调一次这个工具复核。",
    }
