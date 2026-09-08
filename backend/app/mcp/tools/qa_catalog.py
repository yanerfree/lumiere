"""MCP 工具 —— 把 QA 域评审的结论**递给 QA 那边**。

## 为什么是"递"，不是"写回去"

QA 仓是别人维护的黑盒验收仓，平台对它**永远只读**（`docs/qa-repo-readonly-catalog.md` §1）。
所以"让 QA 拿到评审结果"这件事只有一种做法：我们把结论渲染成一份文本，
**他自己来拉**。放不放进他的仓库、放在哪、要不要提交，全是他那边的决定。

想反过来做（平台往他仓里提交一份 `ai-review.md`）会直接踩他的门禁：
`check-coverage.sh` 拿清单当判据来源，多一个文件、多一列，他那边就会红在一个
查不到原因的地方。

## 数据从哪来（2026-09 换过一次）

以前这份结论是**静态**评审（读脚本正文让模型判）。现在改从**活体页面枚举·三边对账**
（`qa_page_surveys` 那一趟真去点页面得到的账本）里切出来 —— 判据从「脚本里的一句话」
变成「**页面在调哪个端点 / 哪个按钮点了没反应**」，是真点出来的。切分逻辑在
`app/services/qa_survey_review.py`。

**所以取用方要改一个习惯**：以前拿 `evidence` 去 grep 脚本里的某一句；现在拿
`scriptGaps[].endpoint`（`GET /api/x` 这种）去脚本里搜**那个端点**，看自己的脚本
到底打没打过它。返回里**没有** `evidenceCheck` 这个键 —— 老契约里它的缺席本就有定义
（「所有判据都得自己验」），这里自己验 = 拿端点去 grep。

## 谁会调这个

QA 那边跑 Claude Code 改脚本时：先 `lum_get_qa_review` 拿到「哪个端点声明覆盖了其实
没验到、在哪个页面上观测到的」，再动手改自己的脚本。

**先决条件**：平台这边得先在「QA 对账」页点过一次「活体评审」（真跑一趟页面枚举）。
没跑过 → 这里会明说「还没跑过」，让 QA 那边先招呼平台跑一趟，而不是给一份空结论。

**结论是建议，不是门禁。** 平台这边没有任何东西会因为这份结论变红或变绿。
"""
from __future__ import annotations

import uuid

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.qa_page_survey import TERMINAL_STATUSES, QaPageSurvey
from app.services import qa_survey_review as sr


def _reconcile_of(survey: QaPageSurvey) -> dict | None:
    """这趟账本里的对账产物；`available=False`（爬崩了）当没有处理。"""
    rec = ((survey.ledger or {}).get("reconcile")) if survey else None
    if isinstance(rec, dict) and rec.get("available"):
        return rec
    return None


def _meta(survey: QaPageSurvey) -> dict:
    when = survey.finished_at or survey.started_at
    return {
        "surveyId": str(survey.id),
        "environmentName": survey.env_name or "",
        "buildFingerprint": survey.build_fingerprint or "",
        "status": survey.status,
        "createdAt": when.isoformat() if when else None,
    }


async def _latest_survey(session: AsyncSession, pid: uuid.UUID
                         ) -> tuple[QaPageSurvey | None, dict | None, bool]:
    """最近一趟**有可用对账**的活体枚举。

    返回 `(survey, reconcile, ran_at_all)`：
      · 找到有账本的 → `(s, rec, True)`
      · 跑过但最近这些趟对账都没算成（爬崩） → `(None, None, True)`
      · 从没跑过 → `(None, None, False)`

    「跑过但没算成」和「从没跑过」必须分开说 —— 前者让 QA 那边知道该重跑，
    后者让他知道该先招呼平台跑第一趟。
    """
    rows = (await session.execute(
        select(QaPageSurvey).where(
            QaPageSurvey.project_id == pid,
            QaPageSurvey.status.in_(TERMINAL_STATUSES))
        .order_by(desc(QaPageSurvey.started_at)).limit(25))).scalars().all()
    for s in rows:
        rec = _reconcile_of(s)
        if rec is not None:
            return s, rec, True
    return None, None, bool(rows)


async def get_qa_review(
    session: AsyncSession,
    project_id: str,
    domain: str | None = None,
    review_id: str | None = None,
    format: str = "md",  # noqa: A002 — 对外参数名就叫 format
) -> dict:
    """拿 QA 域评审的结论（源自最近一趟活体页面枚举）。

    - 不传 `domain` = 列出这一趟里每个域评了什么（verdict + 一句话 + 各类缺口数）
    - `domain='MCP'` → 那个域的全文（哪个端点没验到、在哪个页面上看见的）
    - `review_id=...` → 指定的某一趟枚举（复核历史结论时用，review_id 就是 surveyId）
    - `format='md'` 给 Markdown 全文（贴 issue / 交给 AI 改脚本）；
      `format='json'` 给结构化的 scriptGaps / catalogGaps / deadControls（自己拼报表时用）
    """
    try:
        pid = uuid.UUID(str(project_id))
    except (ValueError, AttributeError, TypeError):
        return {"error": f"project_id 不是合法 UUID：{project_id}"}

    # —— 定位这一趟 ——
    if review_id:
        try:
            survey = await session.get(QaPageSurvey, uuid.UUID(str(review_id)))
        except (ValueError, AttributeError, TypeError):
            return {"error": f"review_id 不是合法 UUID：{review_id}"}
        if survey is None or str(survey.project_id) != str(pid):
            return {"error": "找不到这一趟枚举（或它不属于这个项目）"}
        rec = _reconcile_of(survey)
        if rec is None:
            return {"status": survey.status, "surveyId": str(survey.id),
                    "error": "这一趟枚举没有可用的对账结果（多半是爬取没跑成）。"}
    else:
        survey, rec, ran = await _latest_survey(session, pid)
        if rec is None:
            if ran:
                return {"reviews": [],
                        "hint": "这个项目最近几趟活体枚举都没对成账（爬取没跑成）。"
                                "去平台「QA 对账」页点「活体评审」重跑一趟。"}
            return {"reviews": [],
                    "hint": "这个项目还没跑过活体页面枚举。"
                            "去平台「QA 对账」页点「活体评审」，选环境跑一趟。"}

    meta = _meta(survey)
    universe = sr.domain_universe(rec)

    # —— 单个域 ——
    if domain:
        if domain not in universe:
            return {"domain": domain, "surveyId": meta["surveyId"],
                    "error": f"{domain} 这个域在最近这趟活体枚举里没出现",
                    "knownDomains": universe}
        return sr.one(rec, meta, domain, format)

    # —— 列出所有域 ——
    return {
        "survey": meta,
        "reviews": [sr.domain_summary(rec, code) for code in universe],
        "hint": "要某个域的全文：再调一次这个工具，带上 domain。",
    }
