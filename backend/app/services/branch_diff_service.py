"""版本升级·分支对账（端点反查）。文档：docs/version-upgrade-branch-diff.md

**这个模块只读用例、只写清单和标签。** 它没有任何路径能改 `steps` / 断言 /
`review_status` / 三维状态 —— 红线 1。改用例还是走 `lum_update_case` /
`lum_sync_orchestrated_scenario`，一条条过原有门禁。

为什么这条红线值一条命：这批用例是上一版审过的成果，一个"自动帮你改"的工具
改坏了没人看得出来 —— 它改的正是断言，而断言坏了的表现就是**变绿**。

唯一的两处例外（都不是"改用例内容"）：
  · 命中清单的用例，预期落款打回「待重新确认」—— 需求变了、步骤没变的那种漏网
  · 命中清单的用例，如果之前是**自动过审**的，撤回待审（自动过审的全部合法性
    来自"未命中"，命中了就得作废）
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# CC 能报的变更类型。
#   removed        端点没了            → 该废候选（**不自动废**，要走 lum_request_deprecate）
#   field_changed  响应/请求字段变了   → 要改
#   new_state      新增了状态值/分支   → 要改
#   renamed        端点改名/挪位置     → 要改（**不是废** —— 红线 3：改名在 UI 上长得像"没了"）
#   added          v2.0 新端点         → 谁都不命中 → 「待补用例」
#
# `added` 是原设计漏掉的第四堆。三堆分法（照抄/要改/该废）全是"命中老用例"型，
# 新端点不命中任何老用例 → 不进清单 → v2.0 新功能**零覆盖且零信号**：
# 没有任何东西会说"这里本来该有覆盖"。所以它必须有地方落。
CHANGE_KINDS = ("removed", "field_changed", "new_state", "renamed", "added")

# ── url 归一化 ───────────────────────────────────────────────
# 对账是拿"CC 从 git diff 里读到的 url"跟"用例步骤里存的 url"对。这两边长得很不一样：
#   git diff 里： /subscriptions/{id}/approve
#   步骤里：      {{BASE_URL}}/api/v1/subscriptions/${subId}/approve?force=true
# 不归一化就一条都对不上，而**对不上的后果是假绿**（用例被判成"照抄堆"自动过审）。

_VAR_TOKEN = re.compile(r"\{\{[^{}]*\}\}|\$\{[^{}]*\}|\$[A-Za-z_][A-Za-z0-9_]*")
_UUID_SEG = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                       r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_BRACE_SEG = re.compile(r"^[:{][^/]*\}?$")   # {id} / :id
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://[^/]*(?P<rest>/.*)?$")
WILDCARD = "{}"


def normalize_path(url: str | None) -> str:
    """把一个 url 压成可比的**路径模板**：`/subscriptions/{}/approve`

    做四件事：剥 scheme+host、剥 query/fragment、把变量占位和 id 段压成 `{}`、
    去掉末尾斜杠。

    **第一段的变量单独处理**：`{{BASE_URL}}/api/x` 里那个占位展开是
    `http://host:port`（含 scheme 和 host），压成 `{}` 会凭空多出一段，
    于是 `/{}/api/x` 跟 `/api/x` 对不上。所以出现在**任何字面段之前**的占位
    整段丢掉，后面位置上的占位才压成 `{}`。
    """
    u = (url or "").strip()
    m = _SCHEME.match(u)
    if m:
        u = m.group("rest") or "/"
    u = u.split("#", 1)[0].split("?", 1)[0]

    out: list[str] = []
    seen_literal = False
    for raw in u.split("/"):
        if not raw:
            continue
        stripped = _VAR_TOKEN.sub("", raw).strip()
        is_pure_var = (raw != stripped) and stripped == ""
        if is_pure_var:
            if not seen_literal:
                continue          # base url 那一段，整段丢掉
            out.append(WILDCARD)
            continue
        if _BRACE_SEG.match(raw) or _UUID_SEG.match(raw) or raw.isdigit():
            out.append(WILDCARD)
            continue
        if raw != stripped:
            # 段里混了变量（`user-${id}.json`）—— 整段当通配，别猜
            out.append(WILDCARD)
            continue
        out.append(raw)
        seen_literal = True
    return "/" + "/".join(out)


def _seg_eq(a: str, b: str) -> bool:
    return a == b or a == WILDCARD or b == WILDCARD


def paths_match(a: str, b: str) -> bool:
    """两条归一化路径算不算同一个端点。**段边界后缀匹配**，`{}` 当单段通配。

    为什么允许后缀：一边可能带部署前缀（`/api/v1/...`）另一边没带，这在
    「git diff 里的路由声明」对「步骤里的完整 url」上是常态。

    **为什么故意偏向多命中**：两个方向的错代价差几个数量级 ——
      · 多命中 → 这条用例多过一次 AI 审（贵一点，但结论仍然对）
      · 漏命中 → 这条用例进照抄堆、自动过审、**没人再看它一眼**（假绿）
    所以宁可多命中。唯一的限制是单段不许是纯通配（`/{}` 会命中一切）。
    """
    sa = tuple(x for x in a.split("/") if x)
    sb = tuple(x for x in b.split("/") if x)
    if not sa or not sb:
        return False
    short, long_ = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    if len(short) == 1 and short[0] == WILDCARD:
        return False
    tail = long_[-len(short):]
    return all(_seg_eq(x, y) for x, y in zip(tail, short))


# ── 内容指纹 ─────────────────────────────────────────────────

async def compute_fingerprint(session: AsyncSession, case_id: uuid.UUID) -> str:
    """一条用例**三份产物**的内容指纹（sha256 前 32 位十六进制）。

    盖住手工步骤/预期、接口场景正文、UI 脚本正文。
    **只盖手工步骤是不够的** —— CC 改了接口断言指纹照旧，那道防线等于没有，
    而断言恰恰是最危险的改动点。

    刻意**不**盖的：三维状态、review_status、执行历史、`updated_at`、
    `target_level`、预期落款 —— 那些不是"内容"，跑一次就变，盖进去指纹永远对不上，
    自动过审就永远不生效（等于白做）。
    """
    from app.models.api_test import ApiTestScenario, ApiTestStep
    from app.models.case import Case
    from app.models.script import Script

    case = (await session.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if case is None:
        return ""

    payload: dict = {
        "spec": {
            "title": case.title,
            "priority": case.priority,
            "preconditions": case.preconditions,
            "steps": case.steps,
            "expected": case.expected_result,
        },
        "api": [],
        "ui": [],
    }

    scenarios = (await session.execute(
        select(ApiTestScenario).where(ApiTestScenario.source_case_id == case_id)
        .order_by(ApiTestScenario.code)
    )).scalars().all()
    for sc in scenarios:
        steps = (await session.execute(
            select(ApiTestStep).where(ApiTestStep.scenario_id == sc.id)
            .order_by(ApiTestStep.sort_order)
        )).scalars().all()
        payload["api"].append({
            "title": sc.title,
            "steps": [{
                "name": st.name, "method": st.method, "url": st.url,
                "headers": st.headers, "body": st.body, "assertions": st.assertions,
                "extract": st.variables_extract, "enabled": st.enabled,
                "pre": st.pre_script, "post": st.post_script,
            } for st in steps],
        })

    for sp in (await session.execute(
        select(Script).where(Script.case_id == case_id, Script.status == "active")
        .order_by(Script.script_type)
    )).scalars().all():
        payload["ui"].append({"type": sp.script_type, "content": sp.content})

    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


# ── 端点反查 ─────────────────────────────────────────────────

def _assert_digest(assertions) -> tuple[list, list]:
    """从一步的断言里抽出（期望状态码, 字段路径清单）。"""
    from app.services.api_test_runner import expected_of, field_of

    statuses: list = []
    fields: list = []
    for i, a in enumerate(assertions or []):
        if not isinstance(a, dict):
            continue
        atype = a.get("type")
        if atype == "status":
            exp = expected_of(a)
            statuses.extend(exp if isinstance(exp, list) else [exp])
            continue
        try:
            fp = field_of(a, a.get("operator") or "")
        except Exception:  # noqa: BLE001 — 断言形状是 CC 写的，别让一条坏数据打死整次反查
            fp = a.get("field")
        fields.append({
            "断言序号": i, "类型": atype, "字段路径": fp,
            "操作符": a.get("operator"), "期望": expected_of(a) if atype != "status" else None,
        })
    return statuses, fields


async def list_branch_endpoints(session: AsyncSession, branch_id: str) -> dict:
    """这个分支的用例**依赖了哪些端点、哪些字段**（反查的平台那一半）。

    平台只有这一半 —— 另一半「v2.0 到底改了什么」在你本机的 git 里。
    影响清单 = 两半求交集，所以平台单独产不出清单，别等它自己算出来。

    返回里 `覆盖不到的` 是**必读**的一节：手工步骤和 UI 脚本里没有结构化的
    method/url（一个是 JSONB 文本，一个是 Playwright 正文），所以这套反查
    **探不到它们**。纯 UI 改版（页面拆分、改名、入口挪走）在这份端点表上
    一个字都不会变 —— 那批用例得你自己拿 v2.0 的前端改动去比。
    """
    from app.models.api_test import ApiTestScenario, ApiTestStep
    from app.models.case import Case
    from app.models.script import Script

    try:
        bid = uuid.UUID(branch_id)
    except (ValueError, AttributeError, TypeError):
        return {"error": f"branch_id 不是合法 UUID：{branch_id!r}"}

    cases = {c.id: c for c in (await session.execute(
        select(Case).where(Case.branch_id == bid, Case.deleted_at.is_(None))
    )).scalars().all()}
    if not cases:
        return {"error": "这个分支下没有用例"}

    scenarios = (await session.execute(
        select(ApiTestScenario).where(ApiTestScenario.branch_id == bid)
    )).scalars().all()

    # 归一化路径+method → 用它的那些地方
    buckets: dict[tuple[str, str], dict] = {}
    step_total = 0
    cases_with_api: set[uuid.UUID] = set()
    for sc in scenarios:
        case = cases.get(sc.source_case_id)
        if case is None:
            continue
        cases_with_api.add(case.id)
        steps = (await session.execute(
            select(ApiTestStep).where(ApiTestStep.scenario_id == sc.id)
            .order_by(ApiTestStep.sort_order)
        )).scalars().all()
        for st in steps:
            step_total += 1
            method = (st.method or "GET").upper()
            path = normalize_path(st.url)
            statuses, fields = _assert_digest(st.assertions)
            slot = buckets.setdefault((method, path), {
                "method": method, "路径模板": path, "原始url": set(), "被谁用": [],
            })
            slot["原始url"].add(st.url or "")
            slot["被谁用"].append({
                "用例编号": case.case_code, "caseId": str(case.id),
                "用例标题": case.title,
                "scenarioId": str(sc.id), "场景编号": sc.code,
                "步骤名": st.name, "第几步": st.sort_order,
                "期望状态码": statuses, "断言字段": fields,
                "这步没启用": None if st.enabled else True,
            })

    endpoints = []
    for slot in sorted(buckets.values(), key=lambda s: (s["路径模板"], s["method"])):
        slot["原始url"] = sorted(x for x in slot["原始url"] if x)
        endpoints.append(slot)

    # —— 反查覆盖不到的那些。**这一节必须显式说出来** ——
    # 不说的话，这批用例在下一步 lum_apply_endpoint_diff 里"一条都没命中"，
    # 而"没命中"会被当成"接口没动、可以照抄"，直接自动过审。
    ui_cases = {
        r[0] for r in (await session.execute(
            select(Script.case_id).where(
                Script.case_id.in_(list(cases)), Script.status == "active",
                Script.script_type == "ui",
            )
        )).all()
    }
    no_api = [c for cid, c in cases.items() if cid not in cases_with_api]
    return {
        "branchId": branch_id,
        "总计": {"端点": len(endpoints), "步骤": step_total,
                 "有接口场景的用例": len(cases_with_api), "分支下用例": len(cases)},
        "endpoints": endpoints,
        "覆盖不到的": {
            "说明": ("下面这些用例在这份端点表里没有任何结构化端点，所以 "
                     "lum_apply_endpoint_diff 对它们**一条都不会命中** —— 而「没命中」"
                     "会被当成「接口没动、可以照抄」。纯 UI 改版和纯手工流程的变化"
                     "只能你拿 v2.0 的前端/需求改动自己比。"),
            "只有手工步骤的用例": [
                {"用例编号": c.case_code, "caseId": str(c.id), "标题": c.title,
                 "target_level": c.target_level}
                for c in no_api if c.id not in ui_cases
            ],
            "有UI脚本但反查探不到端点的用例": [
                {"用例编号": cases[cid].case_code, "caseId": str(cid),
                 "标题": cases[cid].title}
                for cid in sorted(ui_cases, key=lambda x: cases[x].case_code)
            ],
        },
        "下一步": ("本机 git diff <v1>..<v2> 看改了哪些 router / schema，跟上面求交集，"
                   "然后 lum_apply_endpoint_diff(branch_id, changes=[...])。"
                   f"kind 取值：{'、'.join(CHANGE_KINDS)}。"
                   "**v2.0 新加的端点也要报**（kind=added）—— 它不命中任何老用例，"
                   "但它是「该补用例」那一堆，不报就没人知道新功能没覆盖。"),
    }


# ── 对账：求交集、落清单 ──────────────────────────────────────

def _validate_changes(changes) -> tuple[list[dict], list[str]]:
    """校验 CC 报上来的 changes。**坏数据必须报错不能静默跳过** ——
    静默跳过一条 removed，那条用例就进照抄堆自动过审了。"""
    ok: list[dict] = []
    errs: list[str] = []
    if not isinstance(changes, list) or not changes:
        return [], ["changes 必须是非空数组，形如 "
                    "[{url:'/x/{id}', method:'POST', kind:'field_changed', detail:'响应去掉 quota'}]"]
    for i, ch in enumerate(changes):
        if not isinstance(ch, dict):
            errs.append(f"第 {i+1} 条不是对象")
            continue
        kind = (ch.get("kind") or "").strip()
        url = (ch.get("url") or "").strip()
        method = (ch.get("method") or "").strip().upper()
        if kind not in CHANGE_KINDS:
            errs.append(f"第 {i+1} 条 kind={kind!r} 不认识，只能是：{'、'.join(CHANGE_KINDS)}")
            continue
        if not url:
            errs.append(f"第 {i+1} 条缺 url")
            continue
        if not method:
            errs.append(f"第 {i+1} 条缺 method（同一个 url 不同 method 是不同端点，"
                        "GET 没动 POST 动了这种最常见）")
            continue
        if kind in ("field_changed", "new_state", "renamed") and not (ch.get("detail") or "").strip():
            errs.append(f"第 {i+1} 条（{kind}）缺 detail —— 「字段变了」不写变成什么，"
                        "拿到清单的人还得重新去读一遍 diff，这条清单就等于没落")
            continue
        ok.append({"url": url, "method": method, "kind": kind,
                   "detail": (ch.get("detail") or "").strip() or None})
    return ok, errs


async def apply_endpoint_diff(
    session: AsyncSession,
    branch_id: str,
    changes: list | None = None,
    from_ref: str | None = None,
    to_ref: str | None = None,
    actor: str | None = None,
) -> dict:
    """拿 CC 报的 v2.0 变更跟平台的端点表求交集，落清单。**一个用例都不改。**

    落完之后：命中的进「要改」堆（`removed` 的进「该废候选」），没命中的进「照抄」堆，
    `added` 的进「待补用例」堆。清单进 `lum_next_duty` 队列，CC 每轮问一句就知道干到哪。

    两处**会写**的地方（都不是改用例内容）：
      · 命中的用例，预期落款打回「待重新确认」—— 需求变了、步骤没变那种漏网
      · 命中的用例如果是**自动过审**过的，撤回待审 —— 自动过审的全部合法性来自
        "未命中"，命中了就得作废

    可以**多次调**（补交漏报的）：命中是累积的，重复报同一条不会重复落。
    """
    from app.models.api_test import ApiTestScenario, ApiTestStep
    from app.models.case import Case
    from app.models.endpoint_diff import EndpointDiffBatch, EndpointDiffHit

    try:
        bid = uuid.UUID(branch_id)
    except (ValueError, AttributeError, TypeError):
        return {"error": f"branch_id 不是合法 UUID：{branch_id!r}"}

    good, errs = _validate_changes(changes)
    if errs:
        return {"error": "changes 有问题，一条都没落库（怕静默漏掉 removed 那种）",
                "问题": errs, "已收下的": len(good)}

    cases = {c.id: c for c in (await session.execute(
        select(Case).where(Case.branch_id == bid, Case.deleted_at.is_(None))
    )).scalars().all()}
    if not cases:
        return {"error": "这个分支下没有用例"}

    # 建索引：(method, 归一化路径) → [(case, scenario, step)]
    index: list[tuple] = []
    for sc in (await session.execute(
        select(ApiTestScenario).where(ApiTestScenario.branch_id == bid)
    )).scalars().all():
        case = cases.get(sc.source_case_id)
        if case is None:
            continue
        for st in (await session.execute(
            select(ApiTestStep).where(ApiTestStep.scenario_id == sc.id)
            .order_by(ApiTestStep.sort_order)
        )).scalars().all():
            index.append((case, sc, st, (st.method or "GET").upper(), normalize_path(st.url)))

    # 已经落过的命中，用来去重（补交时重复报同一条不该重复落）
    seen_keys = {
        (str(h.case_id), h.step_name or "", h.assertion_index, h.kind, h.method or "", h.url or "")
        for h in (await session.execute(
            select(EndpointDiffHit).join(
                EndpointDiffBatch, EndpointDiffHit.batch_id == EndpointDiffBatch.id
            ).where(EndpointDiffBatch.branch_id == bid)
        )).scalars().all()
    }

    batch = EndpointDiffBatch(
        branch_id=bid, changes=good, from_ref=from_ref, to_ref=to_ref,
        actor=actor or "cc",
    )
    session.add(batch)
    await session.flush()

    pending_new: list[dict] = []
    new_hits: list[EndpointDiffHit] = []
    hit_case_ids: set[uuid.UUID] = set()
    unmatched_changes: list[dict] = []

    for ch in good:
        if ch["kind"] == "added":
            pending_new.append(ch)
            continue
        cpath = normalize_path(ch["url"])
        matched = False
        for case, sc, st, method, path in index:
            if method != ch["method"] or not paths_match(path, cpath):
                continue
            matched = True
            hit_case_ids.add(case.id)
            key = (str(case.id), st.name or "", None, ch["kind"], ch["method"], ch["url"])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            hit = EndpointDiffHit(
                batch_id=batch.id, case_id=case.id, scenario_id=sc.id,
                step_name=st.name, assertion_index=None, kind=ch["kind"],
                method=ch["method"], url=ch["url"], detail=ch["detail"],
            )
            session.add(hit)
            new_hits.append(hit)
        if not matched:
            # **没命中不等于没事。** 一条 removed 谁都不用，是好消息（没人依赖它）；
            # 但也可能是 url 写法差太多没对上，而那种情况下受影响的用例会被
            # 判进照抄堆自动过审 —— 所以必须回出来让人看一眼。
            unmatched_changes.append(ch)

    # 命中的：落款打回「待重新确认」+ 撤销自动过审
    reset_stamps: list[dict] = []
    revoked: list[dict] = []
    for cid in hit_case_ids:
        case = cases[cid]
        if case.expected_confirmed_note or case.expected_confirmed_at:
            reset_stamps.append({"用例编号": case.case_code,
                                 "原依据": (case.expected_confirmed_note or "")[:200]})
            case.expected_confirmed_at = None
            case.expected_confirmed_by = None
            case.expected_confirmed_actor = None
            case.expected_confirmed_note = None
        if (case.review_status == "approved"
                and ((case.review_reason or {}).get("decidedBy") == "system")):
            case.review_status = "pending"
            case.review_reason = {
                **(case.review_reason or {}),
                "category": "对账补充后命中",
                "text": "对账补充后命中，原自动过审失效 —— 自动过审的全部合法性来自"
                        "「未被清单命中」，命中了就得作废，重新走 AI 审。",
                "decidedBy": "system",
                "revokedFrom": "auto_approved",
            }
            revoked.append({"用例编号": case.case_code, "caseId": str(cid)})

    deprecate_candidates = sorted({
        cases[h.case_id].case_code for h in new_hits if h.kind == "removed"
    })
    revise_codes = sorted({cases[cid].case_code for cid in hit_case_ids})
    reuse_codes = sorted({c.case_code for cid, c in cases.items() if cid not in hit_case_ids})

    batch.pending_new = pending_new
    batch.stats = {
        "要改": len(revise_codes), "照抄": len(reuse_codes),
        "该废候选": len(deprecate_candidates), "待补用例": len(pending_new),
        "本次新落命中": len(new_hits), "没对上的变更": len(unmatched_changes),
        "落款被打回": reset_stamps, "撤销的自动过审": revoked,
    }
    await session.commit()

    return {
        "batchId": str(batch.id),
        "对的哪两版": {"from": from_ref, "to": to_ref},
        "三堆": {
            "要改": {"条数": len(revise_codes), "用例": revise_codes},
            "照抄": {"条数": len(reuse_codes), "用例": reuse_codes,
                     "说明": "内容没变也必须在新版本上真跑一遍 —— 「接口签名没变、"
                             "底层行为变了」只有这一跑抓得到。跑绿+断言咬得住才会自动过审。"},
            "该废候选": {"条数": len(deprecate_candidates), "用例": deprecate_candidates,
                         "说明": "端点没了。**别自己废** —— 走 lum_request_deprecate 交证据，"
                                 "「我在页面上找不到」不等于「这个功能没了」（改名、挪菜单、"
                                 "拆页面在 UI 上都长得像没了）。"},
        },
        "待补用例": {
            "条数": len(pending_new), "端点": pending_new,
            "说明": "v2.0 新端点，不命中任何老用例。这一堆不做的话新功能零覆盖，"
                    "而且**永远不会报错** —— 没有任何信号说这里本来该有覆盖。",
        } if pending_new else None,
        "落款被打回": reset_stamps or None,
        "撤销的自动过审": revoked or None,
        "没对上的变更": {
            "条数": len(unmatched_changes), "变更": unmatched_changes,
            "说明": "这些变更在分支里找不到任何用例引用。可能是真没人依赖（好消息），"
                    "也可能是 url 写法差太多没对上 —— 后者会让受影响的用例被判进"
                    "照抄堆自动过审，所以自己核一眼：拿 lum_list_branch_endpoints 的"
                    "「路径模板」跟你报的 url 比。",
        } if unmatched_changes else None,
        "下一步": "lum_next_duty(branch_id) 取「待处理接口变动」，一条条改。",
    }
