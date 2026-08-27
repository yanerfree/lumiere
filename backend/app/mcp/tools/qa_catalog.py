"""MCP 工具 —— 把 QA 域评审的结论**递给 QA 那边**。

## 为什么是"递"，不是"写回去"

QA 仓是别人维护的黑盒验收仓，平台对它**永远只读**（`docs/qa-repo-readonly-catalog.md` §1）。
所以"让 QA 拿到评审结果"这件事只有一种做法：我们把结论渲染成一份文本，
**他自己来拉**。放不放进他的仓库、放在哪、要不要提交，全是他那边的决定。

想反过来做（平台往他仓里提交一份 `ai-review.md`）会直接踩他的门禁：
`check-coverage.sh` 拿清单当判据来源，多一个文件、多一列，他那边就会红在一个
查不到原因的地方。

## 谁会调这个

QA 那边跑 Claude Code 改脚本时：先 `lum_get_qa_review` 拿到「哪条声明覆盖了其实没验到、
判据是脚本里哪一句、该改成什么」，再动手改自己的脚本。返回里的 `evidence` 是从脚本正文
原样抄的锚点，直接拿去 grep 就能定位。

**结论是建议，不是门禁。** 平台这边没有任何东西会因为这份结论变红或变绿。
"""
from __future__ import annotations

import uuid

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.qa_catalog_review import QaCatalogReview
from app.services import qa_catalog_review as qr


async def get_qa_review(
    session: AsyncSession,
    project_id: str,
    domain: str | None = None,
    review_id: str | None = None,
    format: str = "md",  # noqa: A002 — 对外参数名就叫 format
) -> dict:
    """拿 QA 域评审的结论。不传 domain = 列出每个域最近一次评了什么。

    - `domain='MCP'` → 那个域**最近一次已完成**的评审全文
    - `review_id=...` → 指定的那一次（复核历史结论时用，域名可能已经改过）
    - `format='md'` 给 Markdown 全文（贴 issue / 交给 AI 改脚本）；
      `format='json'` 给结构化的 scriptGaps / envMissing / nextUp（自己拼报表时用）
    """
    try:
        pid = uuid.UUID(str(project_id))
    except (ValueError, AttributeError, TypeError):
        return {"error": f"project_id 不是合法 UUID：{project_id}"}

    if review_id:
        try:
            r = await session.get(QaCatalogReview, uuid.UUID(str(review_id)))
        except (ValueError, AttributeError, TypeError):
            return {"error": f"review_id 不是合法 UUID：{review_id}"}
        if r is None or str(r.project_id) != str(pid):
            return {"error": "找不到这次评审（或它不属于这个项目）"}
        return _one(r, format)

    if not domain:
        rows = (await session.execute(
            select(QaCatalogReview).where(QaCatalogReview.project_id == pid)
            .order_by(desc(QaCatalogReview.created_at)).limit(200))).scalars().all()
        latest: dict[str, QaCatalogReview] = {}
        for r in rows:                      # 已按时间倒序，第一条就是最近的
            latest.setdefault(r.domain, r)
        if not latest:
            return {"reviews": [], "hint": "这个项目还没评过任何域。去平台「QA 对账」页点某个域的「AI 评审」。"}
        return {
            "reviews": [{
                "domain": r.domain,
                "domainName": r.domain_name or "",
                "status": r.status,
                # 结论只有三档：ok 都验到了 / risky 部分没验到 / bad 多数没验到
                "verdict": (r.result or {}).get("verdict") if r.result else None,
                "headline": qr.brief_of(r.result).get("headline") if r.result else None,
                "scenarioCount": r.scenario_count,
                "scriptCount": r.script_count,
                "commitSha": (r.commit_sha or "")[:10],
                "environmentName": r.environment_name or "",
                "createdAt": r.created_at.isoformat() if r.created_at else None,
            } for r in latest.values()],
            "hint": "要某个域的全文：再调一次这个工具，带上 domain。",
        }

    r = (await session.execute(
        select(QaCatalogReview).where(
            QaCatalogReview.project_id == pid,
            QaCatalogReview.domain == domain,
            QaCatalogReview.status == "done")
        .order_by(desc(QaCatalogReview.created_at)).limit(1))).scalars().first()
    if r is None:
        # 「在跑」和「从没评过」是两件事，分开说 —— 不然人会以为这个域评过了但结论是空的
        pending = (await session.execute(
            select(QaCatalogReview).where(
                QaCatalogReview.project_id == pid, QaCatalogReview.domain == domain,
                QaCatalogReview.status.in_(("queued", "running")))
            .order_by(desc(QaCatalogReview.created_at)).limit(1))).scalars().first()
        if pending is not None:
            return {"status": pending.status, "domain": domain,
                    "hint": "这个域正在评，几十秒后再来拿。"}
        return {"error": f"{domain} 这个域还没有已完成的评审"}
    return _one(r, format)


def _one(r: QaCatalogReview, fmt: str) -> dict:
    """一次评审的完整交付物。

    **元数据必须跟着结论一起走**：评的是哪个 commit、在哪个环境上评的、
    哪几份脚本进了模型。没有它们，接手的人没法判断这份结论还算不算数 ——
    脚本改过之后旧结论会指着一段已经不存在的代码。
    """
    if r.status != "done":
        return {"status": r.status, "domain": r.domain,
                "error": r.error, "hint": "这次评审没有可用的结论。"}
    res = r.result or {}
    out = {
        "domain": r.domain,
        "domainName": r.domain_name or "",
        "verdict": res.get("verdict"),
        "reviewedCommit": (r.commit_sha or "")[:10],
        "reviewedBranch": r.branch or "",
        "environmentName": r.environment_name or "",
        "reviewedScripts": res.get("reviewedScripts") or [],
        "createdAt": r.created_at.isoformat() if r.created_at else None,
        "readOnly": "平台只读了 QA 仓的清单与脚本正文，没有做任何写操作；这份结论是建议，不是门禁。",
    }
    if fmt == "json":
        out.update({
            "brief": qr.brief_of(res),
            "summary": res.get("summary") or "",
            "scriptGaps": res.get("scriptGaps") or [],
            "envMissing": res.get("envMissing") or [],
            "catalogGaps": res.get("catalogGaps") or [],
            "nextUp": res.get("nextUp") or [],
        })
    else:
        out["markdown"] = qr.to_markdown(r)
        out["filename"] = f"qa-review-{r.domain}-{(r.commit_sha or '')[:7]}.md"
    return out
