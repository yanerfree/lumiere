"""照抄堆自动过审 + 废弃审核。文档：docs/version-upgrade-branch-diff.md §5 §6

两件事共用一个入口 `tb_review_case`（§6：合进去，不新开工具），所以放在一起。

**照抄堆为什么可以不走 AI 六维审**：清单命中的是「端点变了 / 字段变了 / 新增了
状态值」。一条用例**没被命中**就意味着它碰的接口和字段新版本全没动、新增的东西
也不在它身上 —— 那上一版那次审核的结论在这一版上仍然成立，再审是拿同一份内容
问同一个问题。

三条防线：内容一变就作废（比指纹，机械判定，不听 CC 声明）；清单重算能撤销
（在 apply_endpoint_diff 里，包括已自动过审的）；条件 3、4 不能免（它们治的是
git diff 看不出来的行为变化 —— 「接口签名没变、底层行为变了」）。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.branch_diff_service import compute_fingerprint

logger = logging.getLogger(__name__)

AUTO_ACTOR = "system"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dims_for(target_level: str | None) -> list[str]:
    t = target_level or "spec"
    return ["manual"] + (["api"] if t in ("spec_api", "full") else []) \
        + (["ui"] if t == "full" else [])


# ── 照抄堆自动过审：四条件 ────────────────────────────────────

def _bite_ok(case, live_fp: str | None) -> tuple[bool, str]:
    """条件 4：断言咬得住。判据是 `tb_check_assertion_bite` **落库的**结论。

    跑绿不等于断言有效：方向写反的断言是绿的，恒真断言（动作前后都成立）也是绿的。
    数量和指纹都判不了这件事，只有「删掉原因、看结果是否消失」能判。
    """
    br = case.bite_result or {}
    summary = br.get("summary") or {}
    if not summary:
        return False, ("没有断言咬合结论 —— 跑一次 tb_check_assertion_bite("
                       "case_id, skip_steps='那个改状态的动作步名', env_id)")
    if br.get("fingerprint") and live_fp and br["fingerprint"] != live_fp:
        return False, "断言咬合结论是改动之前那一版的，已过期，重跑一次"
    if summary.get("stillGreen"):
        return False, (f"{summary['stillGreen']} 步在动作被跳掉时照样绿（恒真嫌疑）——"
                       "这种断言放进回归就是假绿，先改断言")
    if not summary.get("bites"):
        return False, "咬合检查没有任何一步变红，证明不了断言有效（换个跳法：跳改状态的动作步）"
    return True, ""


async def auto_approve_reason(
    session: AsyncSession, case, hit_case_ids: set,
) -> tuple[bool, str, bool] | None:
    """四条件逐条判。返回 (过了没, 说明, 是否 spec 特例)；命中清单的返回 None
    （那是要改堆，本来就该走 AI 审，不算"没够条件"）。"""
    from app.models.case import Case as _Case

    if case.id in hit_case_ids:
        return None

    # 条件 2：内容与源分支逐字一致
    if case.source_case_id is None:
        return False, "不是从别的分支复制来的（没有源用例），谈不上「与上一版逐字一致」", False
    if not case.content_fingerprint:
        return False, ("内容指纹已失效 —— 复制之后内容被改过（哪怕只改了标题）。"
                       "改过就走 AI 审：tb_review_case(case_id, run_first=true, env_id)"), False
    live_fp = await compute_fingerprint(session, case.id)
    if live_fp != case.content_fingerprint:
        return False, ("内容跟复制那一刻已经不一致（接口场景正文或 UI 脚本被改过）。"
                       "改过就走 AI 审：tb_review_case(case_id, run_first=true, env_id)"), False

    # 「上一版已审通过」—— 自动过审是拿上一版的审核结论续期，
    # 上一版没通过就没有结论可续。
    src = (await session.execute(
        select(_Case).where(_Case.id == case.source_case_id)
    )).scalar_one_or_none()
    if src is None:
        return False, "源用例已经不在了，没法证明「上一版审过」", False
    if src.review_status != "approved":
        return False, (f"源用例 {src.case_code} 上一版没有审核通过"
                       f"（review_status={src.review_status or '待提审'}）——"
                       "自动过审是拿上一版的结论续期，没有结论可续"), False

    dims = _dims_for(case.target_level)

    # 只承诺手工步骤的单列一档：条件 3、4 在**原理上**没法满足（手工步骤没有执行器）。
    # 原设计这里是个真空 —— §5 要求「跑绿」而 §7.2 又要求复制后强制回草稿，
    # 两条一叠，spec 级用例永远到不了「通过」。
    # **必须在理由里写明「未经执行验证」**：否则它在验收看板上跟真跑绿过的那些
    # 长得一模一样，而两者的证据强度差一个量级。
    if dims == ["manual"]:
        return True, (f"内容与源用例 {src.case_code} 逐字一致、源用例上一版已审通过、"
                      "未被本次对账清单命中。⚠ 只承诺手工步骤（target_level=spec），"
                      "**未经执行验证** —— 手工步骤没有执行器，「跑绿」和「断言咬合」"
                      "这两条在原理上不适用。"), True

    # 条件 3：新版本上跑绿。**按 target_level 该做的每一维都要 completed** ——
    # full 的用例 UI 那一维也不能免：纯 UI 改版（页面拆分、改名、入口挪走）在端点表上
    # 一个字都不会变，所以它**永远不会被清单命中**，条件 1 对它是白送的。
    # 唯一还能抓到 UI 变化的就是让 UI 脚本在新版本上真跑一遍。
    not_done = [d for d in dims if getattr(case, f"{d}_status", None) != "completed"]
    if not_done:
        return False, (f"这些维度还没跑绿：{'、'.join(not_done)}。"
                       "内容没变也必须在新版本上真跑一遍 ——「接口签名没变、"
                       "底层行为变了」只有这一跑抓得到。"), False

    # 条件 4：断言咬得住
    ok, detail = _bite_ok(case, live_fp)
    if not ok:
        return False, detail, False

    return True, (f"内容与源用例 {src.case_code} 逐字一致、源用例上一版已审通过、"
                  f"未被本次对账清单命中、新版本上 {'、'.join(dims)} 全部跑绿"
                  "且断言咬得住。"), False


async def hit_case_ids_of(session: AsyncSession, branch_id: uuid.UUID) -> set | None:
    """这个分支被对账清单命中的用例。**一次对账都没做过返回 None** ——
    跟"做过对账、零命中"是两件完全不同的事，不能都用空集合表示。"""
    from app.models.endpoint_diff import EndpointDiffBatch, EndpointDiffHit

    batches = (await session.execute(
        select(EndpointDiffBatch.id).where(EndpointDiffBatch.branch_id == branch_id)
    )).scalars().all()
    if not batches:
        return None
    return set((await session.execute(
        select(EndpointDiffHit.case_id).where(EndpointDiffHit.batch_id.in_(batches))
    )).scalars().all())


NO_BATCH_NOTE = ("这个分支还没对过账（一个对账批次都没有），所以「未被清单命中」"
                 "在原理上不成立 —— 一条都不自动过审。先 tb_list_branch_endpoints "
                 "+ tb_apply_endpoint_diff。")


REVOKE_TEXT = ("内容改过了，原自动过审失效 —— 自动过审的判据是「内容与上一版逐字一致」，"
               "内容一变这个判据就不成立。改过的内容必须走 AI 审："
               "tb_review_case(case_id, run_first=true, env_id)。")


def revoke_auto_approval(case, why: str = REVOKE_TEXT) -> bool:
    """撤回一条**自动过审**的通过。人审/AI 审的结论一个字不碰。

    返回有没有真撤（调用方要据此决定说不说话）。
    """
    if case.review_status != "approved":
        return False
    if (case.review_reason or {}).get("decidedBy") != AUTO_ACTOR:
        return False        # 人审或 AI 审的结论，不是我给的，我不能收回
    case.review_status = "pending"
    case.review_reason = {
        **(case.review_reason or {}),
        "category": "内容改过·自动过审失效",
        "text": why,
        "decidedBy": AUTO_ACTOR,
        "revokedFrom": "auto_approved",
        "revokedAt": _now_iso(),
    }
    return True


async def revoke_diverged(session: AsyncSession, branch_id, commit: bool = False) -> list[dict]:
    """把**内容已经跟复制那一刻不一致**的自动过审全部撤回。

    为什么要在这里重算而不是在每个写入口挂钩子：改内容的路不止一条 ——
    tb_update_case 改步骤/预期/标题、tb_sync_orchestrated_scenario 改接口场景正文、
    tb_sync_ui_script 改 UI 脚本。逐个挂钩子，漏一条就留一个**假绿**：
    那条用例顶着「已通过」，而它通过的依据（内容与上一版逐字一致）早就不成立了。

    重算指纹是**路径无关**的：不管谁改的、从哪改的，对不上就撤。
    """
    from app.models.case import Case

    rows = (await session.execute(
        select(Case).where(Case.branch_id == branch_id, Case.deleted_at.is_(None),
                           Case.review_status == "approved")
    )).scalars().all()
    revoked: list[dict] = []
    for case in rows:
        if (case.review_reason or {}).get("decidedBy") != AUTO_ACTOR:
            continue
        live = await compute_fingerprint(session, case.id)
        if case.content_fingerprint and live == case.content_fingerprint:
            continue
        if revoke_auto_approval(case):
            revoked.append({"用例编号": case.case_code, "caseId": str(case.id)})
    if commit and revoked:
        await session.commit()
    return revoked


async def evaluate_auto_approve(
    session: AsyncSession, branch_id: str, case_ids: list | None = None,
    commit: bool = True,
) -> dict:
    """结算照抄堆。**只写 review_status / lifecycle_status，别的一个字不碰。**

    没做过对账时一条都不放 —— 不加这条守卫，任何一个新分支复制完直接跑一遍
    就全绿全过审，整套对账等于没做。
    """
    from app.models.case import Case

    try:
        bid = uuid.UUID(branch_id) if isinstance(branch_id, str) else branch_id
    except (ValueError, AttributeError, TypeError):
        return {"error": f"branch_id 不是合法 UUID：{branch_id!r}"}

    hits = await hit_case_ids_of(session, bid)
    if hits is None:
        return {"结算": 0, "说明": NO_BATCH_NOTE}

    # **先撤回再结算。** 已经自动过审、但内容后来被改过的那些，通过的依据
    # 已经不成立了 —— 顺序反了的话它们会被上面 `review_status in (approved,...)`
    # 那条 continue 直接跳过，顶着「已通过」永远不再被看一眼。
    diverged = await revoke_diverged(session, bid)

    q = select(Case).where(Case.branch_id == bid, Case.deleted_at.is_(None))
    if case_ids:
        q = q.where(Case.id.in_(list(case_ids)))
    cases = (await session.execute(q)).scalars().all()

    approved: list[dict] = []
    held: list[dict] = []
    for case in cases:
        if case.review_status in ("approved", "rejected"):
            continue
        if case.deprecate_status == "requested":
            continue        # 正在申请废弃，别顺手给它盖个「通过」
        why = await auto_approve_reason(session, case, hits)
        if why is None:
            continue
        ok, detail, spec_only = why
        if not ok:
            held.append({"用例编号": case.case_code, "caseId": str(case.id), "还差": detail})
            continue
        await approve_as_system(session, case, detail, spec_only)
        approved.append({"用例编号": case.case_code, "caseId": str(case.id), "理由": detail})

    if commit:
        await session.commit()
    return {
        "结算": len(approved),
        "自动过审": approved or None,
        "内容改过·撤回的自动过审": diverged or None,
        "还没够条件的": held or None,
        "说明": ("自动过审的合法性全部来自「未被清单命中」。后续补交 changes 时"
                 "新命中的会被撤回待审 —— 包括已经自动过审的。"),
    }


async def approve_as_system(session: AsyncSession, case, text: str,
                            spec_only: bool = False) -> None:
    case.review_status = "approved"
    case.review_reason = {
        "category": "照抄堆·自动过审",
        "text": text,
        "reviewer": AUTO_ACTOR,
        "decidedBy": AUTO_ACTOR,
        "at": _now_iso(),
        "specOnly": spec_only or None,
    }
    if case.lifecycle_status != "deprecated":
        case.lifecycle_status = "done"
    await write_round(session, case, kind="auto", verdict="approved", summary=text)


async def write_round(session: AsyncSession, case, kind: str, verdict: str,
                      summary: str) -> None:
    """写一条评审轮次。

    自动过审和废弃审核**都要写** —— 不写的话审核历史上断一截：一条用例显示
    「已审通过」而历史里一轮都没有，人点开只看到空白，没法回答"凭什么通过的"。
    """
    from app.models.review_round import CaseReviewRound

    last = (await session.execute(
        select(sa_func.max(CaseReviewRound.round)).where(CaseReviewRound.case_id == case.id)
    )).scalar()
    session.add(CaseReviewRound(
        case_id=case.id, round=(last or 0) + 1, kind=kind,
        verdict=verdict, summary=summary, actor=AUTO_ACTOR,
    ))


# ── 废弃审核 ─────────────────────────────────────────────────
#
# **假废弃比假绿更毒。** 一条用例被误废，那块功能就再没人测了，而且**永远不报错** ——
# 没有任何信号会说"这里本来该有覆盖"。假绿至少还在回归池里刷红。
#
# 所以「我在页面上找不到」≠「这个功能没了」：入口挪到二级菜单、改名、拆成两个页面，
# 在 UI 上都长得像"没了"。探测必须正反两面都过，探不出来一律落人。

DEPRECATE_STATUSES = ("requested", "approved", "rejected")


def _evidence_gaps(evidence: dict | None) -> list[str]:
    """证据够不够。**平台硬校验，不听转述** —— 这里不严，整套废弃审核就是个橡皮章。

    要求两面：
      · 正面（老入口/老端点真的没了）：接口打过 → 404/410；或 UI 上走到那个位置 → 不在
      · 反面（功能没被搬到别处）：改名、挪菜单、拆页面都排除过

    **UI 那半边只能 CC 交。** 平台侧的 AI 审没有浏览器（平台侧 UI 通道 2026-08-08
    起封存，playwright-mcp 日常不起），所以「UI 上走到那个位置看在不在」这条
    平台自己做不到。CC 有浏览器，它交证据、平台校验形状 + 复核接口那半边。
    这不是放水：缺证据一律落人，而不是默认通过。
    """
    ev = evidence if isinstance(evidence, dict) else {}
    gaps: list[str] = []
    api_probe = ev.get("apiProbe") or []
    ui_probe = ev.get("uiProbe") or []
    searched = ev.get("searchedElsewhere") or []

    if not api_probe and not ui_probe:
        gaps.append("正面证据一条都没有：要么 apiProbe=[{url, method, status}]"
                    "（打老端点拿到 404/410），要么 uiProbe=[{page, 找了什么, 截图}]"
                    "（在页面上走到那个位置，它不在）")
    else:
        bad = [p for p in api_probe if isinstance(p, dict)
               and p.get("status") not in (404, 410, "404", "410")]
        if bad and not ui_probe:
            gaps.append(f"apiProbe 里有 {len(bad)} 条状态码不是 404/410 —— "
                        "端点还应答就不是「没了」，那是「变了」（走要改堆）")
    if not searched:
        gaps.append("缺反面证据 searchedElsewhere=[...]：功能有没有被搬到别处。"
                    "改名、挪菜单、拆页面在 UI 上都长得像「没了」，"
                    "而误废一条用例那块功能就再没人测、且永远不报错")
    return gaps


async def request_deprecate(
    session: AsyncSession, case_id: str, reason: str, evidence: dict | None = None,
) -> dict:
    """提请废弃一条用例。**独立工具，不塞进 tb_update_case** ——
    塞进去会被顺手带过（改标题时把用例一起废了），而且这里要硬校验证据。"""
    from app.models.case import Case

    try:
        cid = uuid.UUID(case_id)
    except (ValueError, AttributeError, TypeError):
        return {"error": f"case_id 不是合法 UUID：{case_id!r}"}

    case = (await session.execute(
        select(Case).where(Case.id == cid, Case.deleted_at.is_(None))
    )).scalar_one_or_none()
    if case is None:
        return {"error": "用例不存在"}
    if case.lifecycle_status == "deprecated":
        return {"error": "这条已经是废弃状态了", "caseCode": case.case_code}
    if case.deprecate_status == "requested":
        return {"error": "已经有一个待决的废弃请求了，别重复提",
                "caseCode": case.case_code,
                "原请求": case.deprecate_reason}
    if not (reason or "").strip():
        return {"error": "reason 必填：为什么认为这个场景在新版本上不存在了"}

    gaps = _evidence_gaps(evidence)
    if gaps:
        return {
            "error": "证据不够，没有提请。「我在页面上找不到」不等于「这个功能没了」",
            "还缺": gaps,
            "形状": {
                "reason": "一句话：这个场景在新版本上为什么不存在了",
                "evidence": {
                    "apiProbe": [{"url": "/subscriptions/provider", "method": "POST",
                                  "status": 404}],
                    "uiProbe": [{"page": "/subscriptions", "找了什么": "「新增服务商」入口",
                                 "结论": "不在", "截图": "路径或说明"}],
                    "searchedElsewhere": ["搜了全站菜单没有同义入口",
                                          "grep 前端路由表没有对应 path",
                                          "确认不是改名/拆页面/挪到二级菜单"],
                },
            },
        }

    case.deprecate_status = "requested"
    case.deprecate_reason = {
        "reason": reason.strip(),
        "evidence": evidence,
        "requestedBy": "cc",
        "requestedAt": _now_iso(),
    }
    await session.commit()
    return {
        "caseCode": case.case_code, "caseId": str(case.id),
        "deprecate_status": "requested",
        "说明": ("已挂上「待废审」。**用例状态一个字没动** —— lifecycle_status "
                 "要等批准才落 deprecated。"),
        "下一步": ("tb_review_case(case_id) —— 这条用例有待决废弃请求时它不审六维，"
                   "改审「该不该废」：平台自己复核接口那半边（真打老端点看是不是 "
                   "404/410），UI 那半边看你交的证据。探不出来落人，不会自己拍。"),
    }


async def _probe_api(session: AsyncSession, case, env_id: str | None) -> dict:
    """平台自己复核接口那半边：把这条用例引用的端点真打一遍，看还在不在。

    **这是平台唯一能自己做的探测。** UI 那半边（走到页面上看入口在不在）平台没有
    浏览器，只能看 CC 交的证据。
    """
    import httpx

    from app.models.api_test import ApiTestScenario, ApiTestStep

    base = None
    if env_id:
        try:
            from app.services import environment_service
            merged = await environment_service.get_merged_variables(session, uuid.UUID(env_id))
            kv = {i["key"]: i["value"] for i in merged}
            base = kv.get("BASE_URL") or kv.get("base_url")
        except Exception:  # noqa: BLE001
            base = None
    if not base:
        return {"探了没": False, "为什么": "没有 env_id 或环境里没有 BASE_URL，打不了真接口"}

    scenarios = (await session.execute(
        select(ApiTestScenario).where(ApiTestScenario.source_case_id == case.id)
    )).scalars().all()
    probes: list[dict] = []
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        for sc in scenarios:
            for st in (await session.execute(
                select(ApiTestStep).where(ApiTestStep.scenario_id == sc.id)
                .order_by(ApiTestStep.sort_order)
            )).scalars().all():
                url = (st.url or "")
                if "${" in url or "{{" in url:
                    # 带未解析变量的 url 打不了 —— 别拿一个拼错的地址当"404，没了"
                    probes.append({"步骤": st.name, "url": url, "结论": "跳过：url 里有未解析变量"})
                    continue
                full = url if url.startswith("http") else base.rstrip("/") + "/" + url.lstrip("/")
                try:
                    r = await client.request((st.method or "GET").upper(), full)
                    probes.append({"步骤": st.name, "method": (st.method or "GET").upper(),
                                   "url": full, "status": r.status_code,
                                   "结论": "端点没了" if r.status_code in (404, 410)
                                           else "端点还在（还应答）"})
                except Exception as exc:  # noqa: BLE001
                    probes.append({"步骤": st.name, "url": full,
                                   "结论": f"打不通：{type(exc).__name__} —— "
                                           "这不算「没了」，可能是环境不通"})
    return {"探了没": True, "结果": probes}


async def review_deprecate(
    session: AsyncSession, case, env_id: str | None = None,
) -> dict:
    """审「该不该废」。三态结论：确认没了 / 还在（驳回）/ **探不出来 → 落人**。

    AI 批准直接生效的依据：废弃可逆（撤销回草稿）+ 全程留痕 + 三道门槛
    （证据硬校验 / 平台自己复核接口 / 探不出来不许自己拍）。
    「一条一条确认」这个前提保住了，只是确认人可以是 AI。
    """
    ev = (case.deprecate_reason or {}).get("evidence") or {}
    api_probe_platform = await _probe_api(session, case, env_id)

    still_alive = [p for p in (api_probe_platform.get("结果") or [])
                   if p.get("结论") == "端点还在（还应答）"]
    if still_alive:
        case.deprecate_status = "rejected"
        case.deprecate_reason = {
            **(case.deprecate_reason or {}),
            "decision": "rejected", "decidedBy": "ai", "decidedAt": _now_iso(),
            "note": "平台复核：这些端点还在应答，功能没有消失",
            "platformProbe": api_probe_platform,
        }
        await write_round(session, case, kind="deprecate", verdict="rejected",
                          summary="驳回废弃：平台复核发现端点还在应答")
        await session.commit()
        return {
            "结论": "驳回 —— 这是要改，不是要废",
            "caseCode": case.case_code,
            "还在应答的端点": still_alive,
            "下一步": "回要改堆：读新版本的需求/代码把预期改对，别照着实测抄。",
        }

    inconclusive: list[str] = []
    if not api_probe_platform.get("探了没"):
        inconclusive.append(f"平台侧接口探测没做成：{api_probe_platform.get('为什么')}")
    if not (ev.get("apiProbe") or ev.get("uiProbe")):
        inconclusive.append("CC 没交正面证据")
    if not ev.get("searchedElsewhere"):
        inconclusive.append("CC 没交反面证据（功能有没有被搬到别处）")
    # UI 那半边平台探不了，只要这条用例承诺了 UI 就必须有 CC 的 UI 证据
    if (case.target_level == "full") and not ev.get("uiProbe"):
        inconclusive.append("这条承诺了 UI（target_level=full），但没有 uiProbe —— "
                            "平台没有浏览器，UI 那半边只能你交")

    if inconclusive:
        case.deprecate_reason = {
            **(case.deprecate_reason or {}),
            "note": "探不出来，落人：" + "；".join(inconclusive),
            "platformProbe": api_probe_platform,
        }
        await write_round(session, case, kind="deprecate", verdict="pending_human",
                          summary="探不出来，落人：" + "；".join(inconclusive))
        await session.commit()
        return {
            "结论": "探不出来 —— **落人，不自己拍**",
            "caseCode": case.case_code,
            "为什么": inconclusive,
            "怎么办": ("在用例列表/详情页的「待废审」上人工确认或驳回。"
                       "误废一条用例，那块功能就再没人测了，而且**永远不报错** ——"
                       "所以探不出来时宁可等人。"),
        }

    case.deprecate_status = "approved"
    case.lifecycle_status = "deprecated"
    case.deprecate_reason = {
        **(case.deprecate_reason or {}),
        "decision": "approved", "decidedBy": "ai", "decidedAt": _now_iso(),
        "note": "正反两面都过：平台复核老端点 404/410，CC 交了反面排查（不是改名/挪位置）",
        "platformProbe": api_probe_platform,
    }
    await write_round(session, case, kind="deprecate", verdict="approved",
                      summary="批准废弃：正反两面都过")
    await session.commit()
    return {
        "结论": "批准废弃，已生效",
        "caseCode": case.case_code,
        "留痕": "decidedBy=ai，理由和证据都在 deprecate_reason 里，可撤销回草稿",
        "副作用": ("这条从此不进待办队列、不进批量回归、不算进通过率分母。"
                   "tb_list_cases 显式传 lifecycle_status=deprecated 还查得到"
                   "（不然废了就再也找不着，撤销都撤不了）。"),
    }


async def decide_deprecate(
    session: AsyncSession, case_id: uuid.UUID, approve: bool, note: str | None,
    user_id: uuid.UUID | None = None, actor: str = "human",
) -> dict:
    """人确认/驳回废弃。列表页和详情页两个入口都走这里。**一条一条点，不做批量。**"""
    from app.models.case import Case

    case = (await session.execute(
        select(Case).where(Case.id == case_id, Case.deleted_at.is_(None))
    )).scalar_one_or_none()
    if case is None:
        return {"error": "用例不存在"}
    if case.deprecate_status != "requested":
        return {"error": f"这条没有待决的废弃请求（deprecate_status="
                         f"{case.deprecate_status or 'NULL'}）"}

    case.deprecate_status = "approved" if approve else "rejected"
    case.deprecate_reason = {
        **(case.deprecate_reason or {}),
        "decision": case.deprecate_status,
        "decidedBy": actor,
        "decidedById": str(user_id) if user_id else None,
        "decidedAt": _now_iso(),
        "note": note,
    }
    if approve:
        case.lifecycle_status = "deprecated"
    await write_round(session, case, kind="deprecate",
                      verdict=case.deprecate_status,
                      summary=f"人工{'批准' if approve else '驳回'}废弃"
                              + (f"：{note}" if note else ""))
    await session.flush()
    return {"caseCode": case.case_code, "deprecate_status": case.deprecate_status,
            "lifecycle_status": case.lifecycle_status}


async def undo_deprecate(session: AsyncSession, case_id: uuid.UUID) -> dict:
    """撤销废弃，回草稿。**废弃可逆是 AI 敢直接批准的前提之一**，所以这条必须有。"""
    from app.models.case import Case

    case = (await session.execute(
        select(Case).where(Case.id == case_id, Case.deleted_at.is_(None))
    )).scalar_one_or_none()
    if case is None:
        return {"error": "用例不存在"}
    if case.lifecycle_status != "deprecated" and case.deprecate_status is None:
        return {"error": "这条没被废弃，也没有废弃请求"}

    case.deprecate_status = None
    case.deprecate_reason = {
        **(case.deprecate_reason or {}),
        "undoneAt": _now_iso(), "undone": True,
    }
    case.lifecycle_status = "draft"
    case.review_status = None
    await write_round(session, case, kind="deprecate", verdict="undone",
                      summary="撤销废弃，回草稿")
    await session.flush()
    return {"caseCode": case.case_code, "lifecycle_status": "draft",
            "说明": "回草稿、审核标签清空 —— 它得重新走一遍验证和审核"}
