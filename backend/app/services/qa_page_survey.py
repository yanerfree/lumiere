"""页面枚举的**两趟对比 + 落库**。

爬取本身在 `app/engine/surveys/qa_page_survey_crawl.py`（只读五层守着，见 AD-7）。
这里回答的是下一问：**这一趟跟上一趟比，页面上多了什么、少了什么。**

这个模块的全部难点是一句话：

> **「这次没走到那个页面」和「这个功能没了」，在产物上长得一模一样。**

分不开的后果不是少报，是**凭空多报**：对账那边会拿 `removed` 去报「这个操作没人测了」，
人跑去查一个根本不存在的缺口，查两次之后就再也不信这份结论了。
所以规则一律往「没验证」偏：**账本里但凡有理由怀疑没看全，那一页的 item 只进
`unknown`，绝不进 `removed`/`added`。**

判定是纯函数（零 IO、零模型），落库在最后一节。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.qa_page_survey import QaPageSurvey, QaPageSurveyItem

# ── 纯函数：哪些页面这一趟不可比 ──────────────────────────────────────────


def _page_of(entry) -> str:
    """账本里一条「这页没成」记的是什么页。

    `pagesFailed` 存的是 `{"path": ..., "error": ...}` **结构化的一条**，不是
    `f"{path}: {err}"` 那种拼好的串 —— 拼了这里就得反解析，而路径里本来就可能带
    `": "`，解析一歪那一页就不算失败页，它的 item 立刻被报成 `removed`。
    真出现旧的字符串条目就整条当路径用（**不猜、不切**），宁可少认一页失败，
    也不要切出半截路径去匹配。
    """
    if isinstance(entry, dict):
        return str(entry.get("path") or "")
    return str(entry or "")


def undiffable_pages(ledger: dict | None) -> set[str]:
    """这一趟**没看清**的页面：打不开的 + 一个控件都没抓到的。

    空状态页（`pagesEmptyState`）为什么也算没看清：列表页在没数据时按钮成片消失，
    那是「这个环境这会儿没数据」，不是「这些功能删了」。
    """
    led = ledger or {}
    out = {_page_of(e) for e in led.get("pagesFailed") or []}
    out |= {_page_of(e) for e in led.get("pagesEmptyState") or []}
    return {p for p in out if p}


def is_wholly_unreliable(ledger: dict | None) -> bool:
    """整趟都不可比 —— 有分片直接死了。

    分片死掉时**账本里没有页面清单**（`crawl_role` 整个抛了，一页都没记），
    于是 `undiffable_pages` 是空的，而 items 也是空的 —— 逐页那条规则在这里
    完全失效，上一趟的每一行都会被算成 `removed`。这是最响的一种假缺口：
    一次网络抖动能报出「这个域的功能全没了」。
    """
    return bool((ledger or {}).get("shardsFailed"))


# ── 纯函数：两趟 diff ────────────────────────────────────────────────────


def diff_items(before, after, *, before_ledger: dict | None = None,
               after_ledger: dict | None = None) -> dict:
    """按 `key` 对齐两趟，返回 `{added, removed, unknown, stable}`。

    - 只在**旧**那趟里 → 这一次没看见它。这一趟没看清那页（或整趟不可比）就是
      `unknown`，否则才是 `removed`。
    - 只在**新**那趟里 → 上一次没看见它。**上一趟**没看清那页就是 `unknown`，
      否则才是 `added`（上次没看到不等于当时没有）。
    - 两边都有 → `stable`，只计数。

    `unknown` 的每条带 `reason`，因为「没看清」有两种，处理方式不同：
    页面打不开要去修爬取，整趟分片死了要去看环境。

    **顺序无关**：同一个构建跑两趟，哪怕 DOM 顺序变了，`added`/`removed` 也必须是空的
    （S6.4 的验收判据）—— 锚点是 testid/id/文案，不是序号。
    """
    b = {r["key"]: r for r in before or []}
    a = {r["key"]: r for r in after or []}
    bad_after, bad_before = undiffable_pages(after_ledger), undiffable_pages(before_ledger)
    blind_after = is_wholly_unreliable(after_ledger)
    blind_before = is_wholly_unreliable(before_ledger)

    added, removed, unknown = [], [], []
    for key, row in b.items():
        if key in a:
            continue
        if blind_after:
            unknown.append({**row, "reason": "整趟有分片没跑成，这一次根本没看"})
        elif row.get("page_path") in bad_after:
            unknown.append({**row, "reason": "这一趟没看清这个页面"})
        else:
            removed.append(row)
    for key, row in a.items():
        if key in b:
            continue
        if blind_before:
            unknown.append({**row, "reason": "上一趟有分片没跑成，当时根本没看"})
        elif row.get("page_path") in bad_before:
            unknown.append({**row, "reason": "上一趟没看清这个页面"})
        else:
            added.append(row)
    return {"added": added, "removed": removed, "unknown": unknown,
            "stable": len(b.keys() & a.keys())}


# ── 落库 ────────────────────────────────────────────────────────────────


async def _first_seen_map(session, project_id: uuid.UUID, keys: list[str]) -> dict:
    """这些 key 以前是在哪一趟第一次见到的。没见过的不在字典里。"""
    if not keys:
        return {}
    rows = (await session.execute(
        select(QaPageSurveyItem.key, QaPageSurveyItem.first_seen_survey_id,
               QaPageSurveyItem.survey_id)
        .where(QaPageSurveyItem.project_id == project_id,
               QaPageSurveyItem.key.in_(keys)))).all()
    out: dict = {}
    for key, first_seen, survey_id in rows:
        out.setdefault(key, first_seen or survey_id)
    return out


async def save_survey(session, *, project_id: uuid.UUID, env_id=None,
                      env_name: str = "", build_fingerprint: str = "",
                      route_table_hash: str = "", roles: list | None = None,
                      status: str, ledger: dict | None = None,
                      items: list | None = None,
                      error: str | None = None) -> QaPageSurvey:
    """把一趟爬取落库。**不 commit**，由调用方决定事务边界。

    **写入不许 `on_conflict_do_nothing/do_update`**（AD-6，两处写死）：
    `(survey_id, key)` 撞了意味着锚点推断塌了（整页退化成文案锚点、两个按钮同名），
    那时候 diff 会变成噪声源。让它**在写入时就炸**，比在 diff 结果里表现成
    「新增 40 项」好查得多 —— 后者没人查得出源头，只会被当成前端改版。
    """
    survey = QaPageSurvey(
        project_id=project_id, env_id=env_id, env_name=env_name or "",
        build_fingerprint=build_fingerprint or "", route_table_hash=route_table_hash or "",
        roles=list(roles or []), status=status, ledger=ledger or {},
        finished_at=datetime.now(timezone.utc), error=error)
    session.add(survey)
    await session.flush()

    rows = items or []
    seen = await _first_seen_map(session, project_id, [r["key"] for r in rows])
    for r in rows:
        session.add(QaPageSurveyItem(
            survey_id=survey.id, project_id=project_id, key=r["key"],
            page_path=r.get("page_path") or "", page_title=r.get("page_title") or "",
            anchor=r.get("anchor") or "", anchor_kind=r.get("anchor_kind") or "",
            label=r.get("label") or "", control_type=r.get("control_type") or "",
            state=r.get("state") or "present",
            roles_visible=r.get("roles_visible") or [], endpoints=r.get("endpoints") or [],
            first_seen_survey_id=seen.get(r["key"], survey.id),
            last_seen_survey_id=survey.id))
    await session.flush()
    return survey
