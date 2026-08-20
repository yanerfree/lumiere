"""评审证据 —— **平台先把能证明的摆出来，再让 LLM 判判断题**。

上一版评审只喂给 LLM 一行行 `[P1] 标题（N 步）`，它从没看见过步骤、断言、脚本、
执行结果，于是把统计数字编得有鼻子有眼（说 50 条没预期结果，实际 5 条）。
人照那个报告去改用例，比没有报告更糟。

所以口径反过来：
  · **能用代码判的一律不问 LLM** —— 恒真断言、断言表达力、写完没读回、只打控制面、
    对照组塞一条、悬空变量、环境卫生、UI 脚本硬伤、文案硬编码、三维欠哪一维。
    这些我这几轮已经攒了一堆检查器，全部复用，结论是**事实**。
  · LLM 只判**需要判断力**的：场景本身合不合理、验证点够不够、有没有漏掉该测的、
    预期是不是照实现抄的、UI 脚本验的是不是该验的东西。
    而且要求它**引用事实**，不许自己数数。

一条用例的证据包括：步骤原文、接口场景全部步骤+断言原文、UI 脚本正文、
最近几次执行结果、同模块其他用例的标题（判重复和遗漏要看邻居）、接口树。
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_test import ApiTestScenario, ApiTestStep
from app.models.case import Case, CaseFolder
from app.models.script import Script, ScriptRun

MAX_SCRIPT_CHARS = 6000      # UI 脚本正文超过这个长度就截断 —— 评审看形状不看长度；
                             # 喂太长会把网关打到限流、掉到 CLI 慢通道（实测一次评审从 30s 拖到几分钟）
MAX_NEIGHBORS = 25           # 同模块邻居标题：判重复够用了


async def _folder_path(session: AsyncSession, folder_id) -> str | None:
    if not folder_id:
        return None
    return (await session.execute(
        select(CaseFolder.path).where(CaseFolder.id == folder_id)
    )).scalar_one_or_none()


def _steps_text(steps: Any) -> list[dict]:
    out = []
    for i, s in enumerate(steps or []):
        if isinstance(s, dict):
            out.append({"seq": s.get("seq") or i + 1,
                        "action": (s.get("action") or "")[:400],
                        "expected": (s.get("expected") or "")[:400]})
    return out


async def _api_scenario(session: AsyncSession, case_id) -> dict | None:
    sc = (await session.execute(
        select(ApiTestScenario).where(ApiTestScenario.source_case_id == case_id)
        .order_by(ApiTestScenario.created_at)
    )).scalars().first()
    if sc is None:
        return None
    steps = (await session.execute(
        select(ApiTestStep).where(ApiTestStep.scenario_id == sc.id)
        .order_by(ApiTestStep.sort_order)
    )).scalars().all()
    return {
        "code": sc.code, "title": sc.title, "status": sc.status,
        "steps": [{
            "seq": st.sort_order, "name": st.name, "method": st.method, "url": st.url,
            # headers 要给 —— 多角色和对照组只能从 Authorization 上看出来。
            # 值本身不敏感（都是 ${变量}），真凭据从不落在这里。
            "headers": st.headers, "body": st.body,
            "assertions": st.assertions, "extract": st.variables_extract,
            "waitMs": st.wait_ms, "retryTimeoutMs": st.retry_timeout_ms,
        } for st in steps],
    }


async def _ui_script(session: AsyncSession, case_id) -> dict | None:
    sp = (await session.execute(
        select(Script).where(Script.case_id == case_id, Script.script_type == "ui",
                             Script.status == "active")
    )).scalars().first()
    if sp is None:
        return None
    content = sp.content or ""
    return {"fileName": sp.file_name, "language": sp.language,
            "chars": len(content), "truncated": len(content) > MAX_SCRIPT_CHARS,
            "content": content[:MAX_SCRIPT_CHARS]}


async def _recent_runs(session: AsyncSession, case_id, limit: int = 3) -> list[dict]:
    runs = (await session.execute(
        select(ScriptRun).where(ScriptRun.case_id == case_id)
        .order_by(ScriptRun.created_at.desc()).limit(limit)
    )).scalars().all()
    return [{"type": r.script_type, "status": r.status, "mode": r.run_mode,
             "durationMs": r.duration_ms,
             "error": (r.error_summary or "")[:300],
             "phenomenon": getattr(r, "failure_phenomenon", None),
             "at": r.created_at.isoformat() if r.created_at else None} for r in runs]


async def _neighbors(session: AsyncSession, case: Case) -> list[dict]:
    """同模块的其他用例。**判重复和判遗漏都得看邻居** ——
    单看一条永远看不出"这个模块少了状态切回来那条"。"""
    stmt = select(Case.case_code, Case.title, Case.target_level, Case.priority).where(
        Case.branch_id == case.branch_id, Case.deleted_at.is_(None), Case.id != case.id)
    if case.folder_id:
        stmt = stmt.where(Case.folder_id == case.folder_id)
    rows = (await session.execute(stmt.limit(MAX_NEIGHBORS))).all()
    return [{"caseCode": c, "title": t, "targetLevel": lv, "priority": p}
            for c, t, lv, p in rows]


# 模糊预期是**代码判得死的**，别交给 LLM。
# 评测实测：一条「项目管理页面各项操作均能正常工作 / 功能正常，无报错」的垃圾用例，
# 三轮全部 approved（均分 80，刚好卡线）—— 因为它没有接口场景也没有脚本，
# 机器事实是空的，全靠 LLM 打分，而它给了 80。
# intake_gate 里本来就有这套词表，但只查标题；预期结果和每步的 expected 才是重灾区。
_VAGUE_FIELDS = ("expectedResult", "steps.expected")


def _vague_findings(case: Case) -> list[dict]:
    from app.services.intake_gate import _VAGUE

    hits: list[str] = []
    m = _VAGUE.search(case.expected_result or "")
    if m:
        hits.append(f"预期结果里的「{m.group(0)}」")
    for i, st in enumerate(case.steps or []):
        if not isinstance(st, dict):
            continue
        m2 = _VAGUE.search(str(st.get("expected") or ""))
        if m2:
            hits.append(f"步骤 {st.get('seq') or i + 1} 的预期「{m2.group(0)}」")
    if not hits:
        return []
    return [{"kind": "vague_expectation", "severity": "blocker", "where": "步骤/预期",
             "detail": f"这些预期验不出对错：{'；'.join(hits[:4])}。"
                       f"「功能正常/无报错/显示正常」这类词跑起来永远是绿的 —— "
                       f"要写清具体看到什么（哪个文案、哪个字段、哪个状态）。"}]


_PLACEHOLDER = __import__("re").compile(r"\$\{([^}|]+)\|([^}]*)\}")
_CJK = __import__("re").compile(r"[\u4e00-\u9fff]")


async def _unresolvable_placeholders(session: AsyncSession, case: Case,
                                     script: dict | None) -> list[dict]:
    """脚本里的文案占位符 `${键|中文}`，**键在这个项目的词典里查不到**。

    判据改过一次，改的理由值得记下来：
    原来判的是"键是不是中文"（`${登录|登 录}` 就报）。错在两头 ——
      · 词典的键**本项目**用的是点分命名空间（common.confirm、menu.cases），
        但模型注释里写着"中文原文即自然键"，两种拼法 load_locale_table 都装；
        所以"是不是中文"跟"能不能命中"根本不是一回事。
      · 真正的后果只有一个：**查不到就退回竖线后面的中文**，于是英文环境下
        测的还是中文那一版，而中文环境跑起来全绿 —— 没人看得出来。
    所以改成查词典。查得到就没问题，键写成什么样都行。

    **只在这个项目真的有多语种词条时才报**（判据规范 ③）：
    只测中文的项目，占位符命不中也无所谓 —— 报它纯属噪音。
    """
    if not script or not script.get("content"):
        return []
    keys = [k.strip() for k, _ in _PLACEHOLDER.findall(script["content"])]
    if not keys:
        return []
    from app.models.project import Branch
    from app.services.i18n_harvest_service import load_locale_table
    pid = (await session.execute(
        select(Branch.project_id).where(Branch.id == case.branch_id)
    )).scalars().first()
    table = await load_locale_table(session, pid) if pid else {}
    langs = {lang for row in table.values() for lang in (row or {})}
    if len({l.split("-")[0] for l in langs}) < 2:
        return []                       # 这个项目还没做多语种，报了也没意义
    missing = [k for k in dict.fromkeys(keys) if k not in table]
    if not missing:
        return []
    return [{"kind": "i18n_key_not_in_dict", "severity": "major", "where": "ui",
             "detail": f"这些文案占位符的键在项目词典里查不到：{'、'.join(missing[:5])}。"
                       f"查不到就退回竖线后面的中文 —— 英文环境下测的还是中文那一版，"
                       f"而中文环境跑起来全绿，谁都看不出来。"
                       f"要么用词典里已有的键，要么先 tb_upsert_i18n_terms 把它登记上。"}]


def machine_findings(case: Case, scenario: dict | None, script: dict | None) -> list[dict]:
    """跑一遍**确定性检查器**，产出事实。LLM 不许推翻它们，只能引用。

    这些检查器都是前几轮从真实事故里长出来的，各自都有测试封样：
    恒真断言、not_exists 缺基准、写完没读回、只打控制面、对照组塞一条、
    UI 脚本硬伤（sync_playwright / 写死地址 / 文案硬编码 / 清 storage 换角色）。
    """
    from app.mcp.tools.sync import (_missing_path_baseline, _nondiscriminating,
                                    _scan_ui_script)
    from app.services.scenario_shape import check_shape

    out: list[dict] = list(_vague_findings(case))
    steps = (scenario or {}).get("steps") or []
    if steps:
        norm = [{"name": s.get("name"), "method": s.get("method"), "url": s.get("url"),
                 "headers": s.get("headers"), "body": s.get("body"),
                 "assertions": s.get("assertions")} for s in steps]
        for w in _nondiscriminating(norm):
            # **blocker**：恒真断言的定义就是"系统怎么坏它都不会红" —— 那就是假绿本身，
            # 跟「只打控制面」同一档。原来标 major，于是要凑够两处才打回。
            out.append({"kind": "tautology_assertion", "severity": "blocker", "where": "api",
                        "detail": w["value"]})
        for w in _missing_path_baseline(norm):
            out.append({"kind": "missing_baseline", "severity": "minor", "where": "api",
                        "detail": w["value"]})
        for w in check_shape(norm, case.title or "", has_ui_script=bool(script)):
            # control_plane_only 从 blocker 降成 major（判据规范 ①④）：
            # 反例是**纯控制面系统**——平台自己就是：「建用例 → 列表能查到」，
            # 那个"生效"本来就发生在同一个域里，没有数据面可打。
            # 而这条的触发靠步骤名里的"生效/可调通"，是在猜意图，猜错就冤枉人。
            sev = {"no_readback": "major", "control_plane_only": "major",
                   # 对照组塞一条：**minor**。合法写法存在 —— 权限矩阵类用例
                   # 「两种角色各看到应有范围」一条里验两个角色是正常的。
                   # 它的风险（前半段改过的开关让后半段结论失假）只在"前半段真改了
                   # 开关"时成立，而这一点平台判不出来，所以只提示、不参与打回。
                   "control_group_in_one": "minor"}.get(w["kind"], "minor")
            out.append({"kind": w["kind"], "severity": sev, "where": "api",
                        "detail": w["value"]})
        # 断言强度：一条只断状态码的读操作，几乎什么都验不出来。
        # **但断的是错误码就不算弱** —— 「删除后详情应 404」「越权应 403」本身就是
        # 完整的验证，那种响应体里没有可断的东西。实测这条误伤过一条写得很完整的用例
        # （它因此从 approved 掉到 65 分），所以要排掉。
        def _only_ok_status(st) -> bool:
            asserts = st.get("assertions") or []
            if not asserts or any((a or {}).get("type") != "status" for a in asserts):
                return False
            for a in asserts:
                v = a.get("value", a.get("expected"))
                vals = v if isinstance(v, list) else [v]
                if any(isinstance(x, int) and x >= 400 for x in vals):
                    return False          # 断错误码 = 已经在验一件事
            return True

        weak = [s.get("name") for s in steps
                if _only_ok_status(s)
                and (s.get("method") or "GET").upper() == "GET"
                and not str(s.get("name") or "").startswith(("制备", "清理"))]
        if weak:
            # **minor + 给忽略出口**（判据规范 ③）：合法写法是"这一步验的就是
            # 能不能调通"（网关放行、健康检查、下游可达），那时 200 就是结论，
            # 逼它断响应体是加冗余。平台分不出这两种意图，所以只提示。
            out.append({"kind": "status_only_assertion", "severity": "minor", "where": "api",
                        "detail": f"这些读操作只断了状态码，没断响应内容："
                                  f"{'、'.join(str(x)[:20] for x in weak[:6])}。"
                                  f"如果要验的是**数据对不对**，200 不够；"
                                  f"**如果验的就是「能不能调通」**（放行、可达、健康检查），"
                                  f"200 就是结论，忽略这条。"})
    if script and script.get("content"):
        errs, warns = _scan_ui_script(script["content"], script.get("language") or "python")
        for e in errs:
            out.append({"kind": "ui_script_hard_error", "severity": "blocker", "where": "ui",
                        "detail": e[:400]})
        for w in warns:
            out.append({"kind": "ui_script_warning", "severity": "minor", "where": "ui",
                        "detail": str(w)[:400]})
    return out


def owed_dimensions(case: Case, scenario: dict | None, script: dict | None) -> list[str]:
    """按 target_level 判**还欠哪几维**。评审不该因为"UI 还没写"就扣 UI 分 ——
    那是进度不是质量；但承诺要做 UI 却没写，是**交付缺口**，要说出来。"""
    owes = []
    lv = case.target_level or "spec"
    if not (case.steps or []):
        owes.append("manual")
    if lv in ("spec_api", "full") and not scenario:
        owes.append("api")
    if lv == "full" and not script:
        owes.append("ui")
    return owes


async def collect(session: AsyncSession, case_id: uuid.UUID) -> dict | None:
    """把一条用例的评审证据凑齐。"""
    case = (await session.execute(select(Case).where(Case.id == case_id))).scalars().first()
    if case is None:
        return None
    scenario = await _api_scenario(session, case.id)
    script = await _ui_script(session, case.id)
    return {
        "case": {
            "id": str(case.id), "caseCode": case.case_code, "title": case.title,
            "type": case.type, "priority": case.priority,
            "targetLevel": case.target_level,
            "targetLevelReason": case.target_level_reason,
            "module": await _folder_path(session, case.folder_id),
            "preconditions": case.preconditions,
            "steps": _steps_text(case.steps),
            "expectedResult": case.expected_result,
            "expectedConfirmedNote": case.expected_confirmed_note,
            "expectedConfirmedActor": case.expected_confirmed_actor,
            "manualStatus": case.manual_status, "uiStatus": case.ui_status,
            "apiStatus": case.api_status,
            "bugRefs": case.bug_refs, "tags": case.tags,
            "blockedExternal": case.blocked_external,
        },
        "apiScenario": scenario,
        "uiScript": script,
        "recentRuns": await _recent_runs(session, case.id),
        "neighbors": await _neighbors(session, case),
        "machineFindings": (machine_findings(case, scenario, script)
                            + await _unresolvable_placeholders(session, case, script)),
        "owes": owed_dimensions(case, scenario, script),
    }
