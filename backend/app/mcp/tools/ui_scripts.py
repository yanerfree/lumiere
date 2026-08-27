"""UI 脚本 MCP 工具 — 生成/执行/查询 Playwright 测试脚本"""
from __future__ import annotations

import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case
from app.models.environment import EnvironmentVariable
from app.models.script import Script, ScriptRun
from app.services import script_service


async def generate_ui_script(
    case_id: str,
    env_id: str | None = None,
    session: AsyncSession = None,
) -> dict:
    """AI 生成 Playwright 脚本并保存"""
    from app.services.ai.ui_script_gen_service import generate_ui_script as _gen
    result = await _gen(case_id=case_id, session=session, env_id=env_id)
    await session.commit()
    return {
        "status": "ok",
        "script_id": result["script_id"],
        "version": result["version"],
        "content_preview": result["content"][:500],
        "message": f"已生成 Playwright 脚本 v{result['version']}",
    }


async def run_ui_script(
    case_id: str,
    env_id: str,
    session: AsyncSession = None,
    run_mode: str = "debug",
) -> dict:
    """执行用例的 UI 脚本并返回结果。

    run_mode 默认 debug —— 这个工具的语义就是"聚焦调试单条"，不进通过率口径；
    批量回归由 run_ui_scripts_batch 传 regression。"""
    cid = uuid.UUID(case_id)
    script = await script_service.get_active_script(session, cid, "ui")
    if not script:
        return {"status": "error",
                "message": "没有可执行的 UI 脚本 —— 先用 lum_sync_ui_script 把你本地"
                           "写好并跑通的脚本回推上来。（平台侧生成已封存，"
                           "**没有 lum_generate_ui_script 这个工具**。）"}

    # 全局变量 + 环境变量（同名以环境为准）。见 variable_service.build_run_env ——
    # 四条执行路径原来各写一份 select，全局变量一条都没被注入过。
    from app.services.variable_service import build_run_env
    env_vars = await build_run_env(session, uuid.UUID(env_id) if env_id else None)

    # 注入场景变量：`SV_名字` 和裸名 `名字` 都注册 —— 和接口场景那边同一套规则。
    # 只注 SV_ 前缀的话，CC 照着「UI/接口共用同一份」写 os.getenv("PROJ_NAME")
    # 会静默拿到空串。
    from app.services.scenario_variable_service import (
        add_bare_names, resolve_scenario_variables,
    )
    add_bare_names(env_vars, await resolve_scenario_variables(
        session, cid, global_lookup=env_vars))

    # 注入鉴权 token TEST_TOKEN（S1.3）——脚本鉴权造数/清理用，避免 401
    if env_id:
        try:
            from app.services.token_service import get_target_token
            _case = await session.get(Case, cid)
            _pc = (_case.preconditions or "") if _case else ""
            _role = "TENANT" if any(k in _pc for k in ("租户", "tenant", "已授权")) else "ADMIN"
            _tok = await get_target_token(session, env_id, _role)
            if _tok:
                env_vars["TEST_TOKEN"] = _tok
        except Exception:
            pass

    file_name = script.file_name or "test_ui.py"
    content = script.content

    # 项目级共享资源（自动化数据）：脚本真的引用了才探。规范说"跑前平台探一次并注入
    # ${资源名}"，但那句原来只对接口场景成立 —— UI 脚本这边共享资源一个都进不来，
    # 于是「UI 脚本里的 projectId 放哪层」在规范里无解。见 inject_project_resources。
    from app.services.scenario_variable_service import inject_project_resources
    injected_res = await inject_project_resources(session, cid, env_vars, content or "")

    # 环境变量默认值：按 os.getenv 里的**键**替换，不要求左边同名 ——
    # `PROJECT_ID = os.getenv("SV_projectId", "")` 这种写法原来一个都替换不到
    # （平台跑时真环境变量在进程里，运行时照样取得到，所以一直没暴露）。
    from app.services.ui_text_render import bake_env_defaults as _bake
    content, _ = _bake(content, env_vars)

    # 选择器登记表：`${SEL:键}` 换成登记的那条。**先选择器、再文案** ——
    # 登记表里的值本身可以带文案占位（`text=${services.action.more|更多}`），
    # 顺序反了那一层就换不掉。
    from app.services.ui_selector_render import render_for_case as _render_sel
    content, _sel_stat, _sel_tbl = await _render_sel(session, cid, content)

    # 文案词典：脚本里的 ${键|中文} 和 TEXT 都靠它换语种。**这条路以前没注入**，
    # 于是取不到文案、选择器拿键去匹配，红在「element not found」上。
    from app.services.i18n_harvest_service import load_locale_table_for_case
    from app.services.ui_text_render import locale_of, render as render_text
    i18n = await load_locale_table_for_case(session, cid)
    content, text_stat = render_text(content, i18n, locale_of(env_vars))

    # 占位没解析出来就**不开跑**（executor 里也有同一道拦截；这里提前拦是为了把
    # 该登记哪几个键直接说清楚，也不留一条没意义的执行记录）。
    # 为什么必须拦死而不是"让它红在找不到元素上"：那只对正例成立。
    # 「不应出现」这类负例会**假绿** —— 未替换的占位匹配不到任何元素，
    # "不该存在"当然成立。实测（CC 活体回推 v4）：正例红了、同一趟里两条负例全绿。
    from app.services.ui_text_render import unresolved as _unresolved_text
    left = _unresolved_text(content)
    if left:
        return {
            "status": "error",
            "error_summary": f"{len(left)} 处文案占位没解析出来，拒绝执行",
            "textPlaceholdersUnresolved": left,
            "why": ("跑了也没意义：正例会红在「找不到元素」上，而「不应出现」那类断言会"
                    "**假绿**（未替换的占位匹配不到任何元素，'不该存在'当然成立）。"),
            "fix": ("两条任选一条：① lum_upsert_i18n_terms(project_id, "
                    "items=[{key, zh, en}]) 把这几个键登记上；"
                    "② 占位里补中文原文写成 ${键|中文原文}（英文环境下会退回中文，"
                    "不挂但测的是中文那一版）。"),
        }

    # 选择器占位同理，一个字都不放过 —— 理由和上面逐字相同（负例会假绿）。
    from app.services.ui_selector_render import (
        unresolved as _unresolved_sel, unresolved_hint as _sel_hint,
    )
    sel_left = _unresolved_sel(content)
    if sel_left:
        return {
            "status": "error",
            "error_summary": f"{len(sel_left)} 处选择器占位没解析出来，拒绝执行",
            "selectorsUnresolved": sel_left,
            "why": ("跑了也没意义：正例会红在「找不到元素」上，而「不应出现」那类断言会"
                    "**假绿**（未替换的占位匹配不到任何元素，'不该存在'当然成立）。"),
            "fix": _sel_hint(content, _sel_tbl),
        }

    sandbox_dir = tempfile.mkdtemp(prefix="lum_ui_")
    try:
        script_path = Path(sandbox_dir) / file_name
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(content, encoding="utf-8")

        from app.engine.executor import execute_single_case
        result = await anyio.to_thread.run_sync(
            lambda: execute_single_case(
                sandbox_dir=sandbox_dir,
                script_ref_file=file_name,
                script_ref_func=script.func_name,
                env_vars=env_vars,
                timeout=120,
                i18n=i18n,
            )
        )
    finally:
        shutil.rmtree(sandbox_dir, ignore_errors=True)

    # MCP 无登录上下文：executed_by 由 record_run 内部兜底取一个真实 active 用户，
    # 否则会命中 script_runs.executed_by 外键约束，导致「脚本明明跑通了却存不下结果」。
    from app.services import script_run_service
    run_row = await script_run_service.record_run(
        session,
        case_id=cid, script_id=script.id, script_type="ui",
        result=result, executed_by=None, run_mode=run_mode,
    )

    case = await session.get(Case, cid)
    script_run_service.apply_case_status(case, "ui", result.get("status"), run_mode)

    await session.commit()

    # **步骤必须回出来。** 此前只回 status + stdout_preview，而 stdout 前 1000 字
    # 全是 pytest 的启动横幅（platform/rootdir/plugins…），真正有用的那行
    # 「1 passed」和每一步验了什么都不在里面。于是跑挂之后只知道"挂了"，
    # 看不出挂在哪一步 —— 而 steps 本来就在 result 里带着，是这几行给扔了。
    steps = result.get("steps") or []
    out = {
        "status": result.get("status", "error"),
        "duration_ms": result.get("duration_ms"),
        "error_summary": result.get("error_summary"),
        "stepCount": len(steps),
        # 探到并注进来的项目级共享资源。**注了什么要说出来** —— 不说的话
        # "脚本里 os.getenv 拿到了值"这件事没有出处，下次换环境挂了也看不出是它。
        **({"resourcesInjected": injected_res} if injected_res else {}),
        # 文案占位有没有全部换掉 —— 没换掉的会以字面量 ${...} 进选择器，
        # 必然"找不到元素"。不说出来，人会去查前端。
        **({"textPlaceholdersUnresolved": text_stat["missing"]} if text_stat["missing"] else {}),
        **({"textFellBackToChinese": text_stat["fellBack"]} if text_stat["fellBack"] else {}),
        # seq 用枚举补 —— parse_step_json 的输出里没有这个键，
        # 取不到就全是 null，人没法说"第几步挂了"。
        "steps": [{
            "seq": i,
            "phase": s.get("step_phase") or s.get("phase"),
            "action": s.get("step_name") or s.get("action"),
            "status": s.get("status"),
            "durationMs": s.get("duration_ms"),
            **({"error": str(s.get("error_summary") or s.get("error"))[:300]}
               if s.get("error_summary") or s.get("error") else {}),
        } for i, s in enumerate(steps, 1)],
        "screenshots_count": len(result.get("screenshots") or []),
        "case_status": case.ui_status if case else None,
        # **runId 必须回**：跑完要归因（lum_submit_analysis 要它）。
        # 不回的话 CC 得再调一次 lum_get_ui_script_result 去找 —— 活体验证时
        # 归因那一步就是因为拿不到 run_id 静默跳过的。
        "runId": str(run_row.id) if run_row is not None else None,
    }
    # 挂了就把失败那几步单独拎出来 —— 十几步里找那一行红的很费眼。
    bad = [s for s in out["steps"] if s.get("status") == "failed"]
    if bad:
        out["failedSteps"] = bad[:5]
    # 红了就把这次对应的**跟进单**一起给出来：下一步该动哪张单，不用再查一遍
    if out["status"] != "passed" and run_row is not None:
        try:
            from sqlalchemy import select as _sel

            from app.models.failure_ticket import OPEN_STATUSES, FailureTicket
            t = (await session.execute(
                _sel(FailureTicket).where(
                    FailureTicket.case_id == cid,
                    FailureTicket.status.in_(OPEN_STATUSES),
                ).order_by(FailureTicket.created_at.desc()).limit(1))).scalars().first()
            if t is not None:
                out["ticket"] = {"id": str(t.id), "status": t.status,
                                 "phenomenon": t.phenomenon, "occurrences": t.occurrences,
                                 "recurrence": t.recurrence,
                                 "next": "拿证据判原因 → lum_submit_analysis(run_id=上面那个 runId)"}
        except Exception:  # noqa: BLE001
            pass
    if not steps:
        out["stdout_preview"] = (result.get("stdout") or "")[-1500:]
        out["note"] = ("这次没解析到步骤。脚本用普通 Playwright 写法时平台会自动埋点"
                       "（断言和 goto/click/fill 各算一步）；一步都没有说明埋点没装上，"
                       "看 stdout_preview。")
    return out


async def run_ui_scripts_batch(
    case_ids: str,
    env_id: str,
    session: AsyncSession = None,
) -> dict:
    """批量执行多个用例的 UI 脚本（AI-free，逐个跑真实 Playwright），返回聚合结果。
    case_ids: 逗号分隔的用例 UUID 列表。用于减少人工、回归批量跑。

    **卡在产品 bug 的用例（有 open 的 bug_refs）直接跳过**，不进通过率口径：
    重跑一条已知因产品 bug 而红的用例，除了把维度状态打回 debugging、
    刷一条红记录之外没有任何信息量。跳掉的会在 `skippedBlockedByBug` 里逐条列出 ——
    静默跳过比跑一遍更糟，人会以为它跑绿了。
    bug 标成 fixed 之后就不再跳（那正是"该重跑一遍"的意思）。
    """
    ids = [x.strip() for x in (case_ids or "").split(",") if x.strip()]
    results = []
    passed = failed = skipped = 0
    blocked_by_bug: list[dict] = []
    for cid in ids:
        case = await session.get(Case, uuid.UUID(cid)) if session else None
        if case is not None and case.blocked_by_bug:
            open_refs = [r.get("ref") for r in (case.bug_refs or [])
                         if r.get("status", "open") == "open"]
            blocked_by_bug.append({"case_id": cid, "caseCode": case.case_code,
                                   "openBugs": open_refs})
            results.append({"case_id": cid, "status": "skipped",
                            "error_summary": f"卡在产品 bug：{'、'.join(open_refs)}",
                            "duration_ms": None})
            skipped += 1
            continue
        try:
            # 批量 = 回归，进通过率口径；失败允许把维度状态打回 debugging
            r = await run_ui_script(case_id=cid, env_id=env_id, session=session, run_mode="regression")
            st = r.get("status", "error")
        except Exception as e:
            st = "error"
            r = {"status": "error", "error_summary": str(e)[:200], "duration_ms": None}
        results.append({
            "case_id": cid,
            "status": st,
            "duration_ms": r.get("duration_ms"),
            "error_summary": r.get("error_summary"),
        })
        if st == "passed":
            passed += 1
        elif st == "skipped":
            skipped += 1
        else:
            failed += 1
    # 通过率的分母要扣掉跳过的 —— 拿"卡在产品 bug 的条数"去拉低通过率，
    # 等于把产品的问题记在测试头上，报告一看就是"回归又掉了"。
    ran = len(ids) - len(blocked_by_bug)
    out = {
        "total": len(ids),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "pass_rate": round(passed / ran * 100, 1) if ran else 0,
        "results": results,
    }
    if blocked_by_bug:
        out["skippedBlockedByBug"] = blocked_by_bug
        out["note"] = (f"{len(blocked_by_bug)} 条卡在产品 bug，本轮没跑（不计入通过率）。"
                       "bug 修好了就用 lum_update_case(bug_refs=[{...,'status':'fixed'}]) "
                       "标一下，下轮会跑；跑绿后平台自动摘掉关联。")
    return out


async def get_ui_script_result(
    case_id: str,
    script_type: str | None = None,
    run_id: str | None = None,
    session: AsyncSession = None,
) -> dict:
    """最近一次执行的**证据包** —— UI 和接口场景都收。

    原来这里硬过滤 `script_type == "ui"`，于是**接口场景的失败拿不到证据包**：
    没有 run_id、没有 error_summary、没有现象初判。而 `lum_submit_analysis` 必须带
    run_id、evidence 还要能对上这次执行 —— 等于接口那半边的归因链是断的。
    偏偏 `lum_get_failed_scenarios` 对 api 计划照样把人指到这里来（它按
    report_scenario_id 取 run，不分维度），所以这个断口是走得到的。

    接口执行没有截图和浏览器流量（那是浏览器才有的），但 error_summary、
    stdout 的逐步轨迹、failure_phenomenon 都落了库（见 api_test_runner 里的
    record_run）—— 够 evidence 的 error_summary / stdout / phenomenon 三种指针用。

    `script_type` 不传 = 取这条用例**最近的一次**执行，不管哪一维。

    **要归因就把 `run_id` 传上**（`lum_get_failed_scenarios` 给的那个）。不传只能拿到
    「最近一次」，而这条用例可能已经被复跑过 —— 活体自测就撞到了：
    TC-DYGL-00013 有 6 次接口执行，最近一次是 passed，于是证据包里
    error_summary/stdout 全空，什么指针都写不出来，而 `lum_submit_analysis`
    又明确拒收 passed 的执行。**报告里指着失败那一次，这里却给最新那一次** ——
    中间这个错位不补上，复跑过的用例就永远归不了因。
    """
    cid = uuid.UUID(case_id)

    st = (script_type or "").strip().lower() or None
    if st and st not in ("ui", "api"):
        return {"error": f"script_type 只支持 ui / api，收到 {script_type}"}

    if run_id:
        try:
            rid = uuid.UUID(run_id)
        except (ValueError, AttributeError):
            return {"error": f"run_id 不是合法 UUID: {run_id}"}
        run = (await session.execute(
            select(ScriptRun).where(ScriptRun.id == rid))).scalar_one_or_none()
        if run is None:
            return {"error": f"找不到这次执行: {run_id}"}
        # 张冠李戴要拦住 —— 拿 A 用例的证据去给 B 用例归因，evidence 校验那边
        # 反而会通过（它只核"这次执行里有没有这条请求"），错得很难看出来
        if str(run.case_id) != str(cid):
            return {"error": f"这次执行不属于用例 {case_id}，它属于 {run.case_id}"}
    else:
        cond = [ScriptRun.case_id == cid]
        if st:
            cond.append(ScriptRun.script_type == st)
        run = (await session.execute(
            select(ScriptRun).where(*cond)
            .order_by(ScriptRun.created_at.desc()).limit(1))).scalar_one_or_none()

    # 活跃脚本按**这次执行的那一维**取，没跑过才退回 ui ——
    # 固定取 ui 的话，接口场景会报 has_script=false，看着像"脚本没了"。
    script = await script_service.get_active_script(
        session, cid, run.script_type if run is not None else (st or "ui"))

    if not run:
        return {
            "has_script": script is not None,
            "script_version": script.version if script else None,
            "script_source": script.source if script else None,
            "last_run": None,
        }

    from app.services import run_evidence_service
    evidence = run_evidence_service.build(run)

    return {
        "has_script": script is not None,
        "script_version": script.version if script else None,
        "script_source": script.source if script else None,
        "last_run": {
            "run_id": str(run.id),
            # 哪一维跑的 —— 接口执行没有截图/流量，不说清会被当成"证据丢了"
            "script_type": run.script_type,
            "status": run.status,
            "run_mode": run.run_mode,
            "attempt": run.attempt,
            "duration_ms": run.duration_ms,
            "error_summary": run.error_summary,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            # 平台的初判：只判「是什么」（现象），「为什么」（归因）归你
            "failure_phenomenon": run.failure_phenomenon,
            # 这次是通过的就说一句 —— 通过的执行没有失败证据，
            # 而 lum_submit_analysis 会直接拒收它，早点说清省一趟
            "note": ("这次执行是**通过**的，没有失败证据可归因。要看失败那次，"
                     "把 lum_get_failed_scenarios 给的 runId 传进来。"
                     if (run.status or "") == "passed" else None),
            **evidence,
        },
    }


async def render_ui_script(
    case_id: str,
    lang: str = "zh",
    env_id: str | None = None,
    include_credentials: bool = False,
    session: AsyncSession = None,
) -> dict:
    """把用例的 UI 脚本渲染成**一个能直接 pytest 跑的文件** —— 给本地跑用。

    库里存的是"带占位的原文"（文案 `${键|中文}`、取值 `os.getenv(...)`），
    平台执行时才把三样东西补齐。本地拿到的如果只是原文，就跑不通 ——
    所以这里一次把三样都烧进去：

      ① 文案占位 → 当前语种那句话（词典按项目取）
      ② `NAME = os.getenv("NAME", "默认")` 的默认值 → 该环境的真值
      ③ 被测系统自己的语种开关 → 在同一个文件里加一个 context fixture 种 localStorage
         （少这一条最坑：脚本渲染成英文了、系统还在说中文，必红）

    凭据默认**不烧进去**（`ADMIN_PASSWORD` 这类）：同族工具一直对凭证脱敏，
    这里不该开后门。所以默认返回里会给一行 `exportEnv`，把那几个变量 export 了再跑；
    确实要一个自包含文件就传 `include_credentials=true`（凭据会出现在返回内容里，仅本机用）。

    参数: case_id(用例UUID), lang(zh|en，默认 zh), env_id(强烈建议——不传就只渲染文案),
    include_credentials(默认 false)
    """
    import re

    from app.services.i18n_harvest_service import load_locale_table_for_case
    from app.services.ui_text_render import locale_of, render

    cid = uuid.UUID(case_id)
    script = await script_service.get_active_script(session, cid, "ui")
    if not script:
        return {"error": "这条用例还没有 UI 脚本"}

    # 选择器先替，再替文案（登记表的值里可以带文案占位）。少了这一步，
    # 本地渲出来的文件里还留着 `${SEL:键}` 字面量 —— 跑起来正例红在
    # 「找不到元素」上，而"不应出现"那类断言假绿。
    from app.services.ui_selector_render import render_for_case as _render_sel
    _content0, _sel_stat, _sel_tbl = await _render_sel(session, cid, script.content)

    table = await load_locale_table_for_case(session, cid)
    locale = locale_of({"TEST_LANGUAGE": lang})
    content, stat = render(_content0, table, locale)

    # ── ② 环境变量：把 os.getenv 那行的默认值换成真值（和平台执行时同一套替换）──
    ev: dict = {}
    if env_id:
        from app.services.variable_service import build_run_env
        ev = await build_run_env(session, uuid.UUID(env_id))
        from app.services.scenario_variable_service import (
            add_bare_names, resolve_scenario_variables,
        )
        add_bare_names(ev, await resolve_scenario_variables(session, cid, global_lookup=ev))
        ev = {k: v for k, v in ev.items() if k != "__I18N__"}

    from app.mcp.tools.sync import _SECRET_RE
    from app.services.ui_text_render import bake_env_defaults
    skip = set() if include_credentials else {k for k in (ev or {}) if _SECRET_RE.search(k)}
    content, baked = bake_env_defaults(content, ev or {}, skip=skip)
    need_export = sorted(k for k in skip
                         if re.search(rf'os\.getenv\(\s*["\']{re.escape(k)}["\']', content))

    # ── ③ 被测系统的语种开关：写进同一个文件（模块里定义的 fixture 会覆盖插件的）──
    lang_key = (ev or {}).get("UI_LANG_STORAGE_KEY") or ""
    if lang_key:
        lang_val = ((ev or {}).get("UI_LANG_STORAGE_VALUE") or "{locale}") \
            .replace("{locale}", locale).replace("{lang}", locale.split("-")[0])
        if "import pytest" not in content:
            content = "import pytest\n" + content
        content += (
            "\n\n# 平台执行时由沙箱 conftest 注入；本地跑要有这一段，"
            "否则被测系统还是原来那个语种（脚本换成英文了、系统还说中文 → 必红）。\n"
            "@pytest.fixture\n"
            "def context(context):\n"
            f"    context.add_init_script(\"try{{localStorage.setItem({lang_key!r}, {lang_val!r})}}"
            "catch(e){}\")\n"
            "    return context\n"
        )

    out = {
        "caseId": case_id,
        "lang": lang,
        "locale": locale,
        "fileName": script.file_name or "test_ui.py",
        "content": content,
        "bakedVariables": sorted(baked),
        "textResolved": sorted(set(stat["resolved"])),
        "textFellBackToChinese": sorted(set(stat["fellBack"])),
        # 没换掉的占位会以字面量进选择器，本地一样跑不通 —— 先去登记词条
        "textUnresolved": sorted(set(stat["missing"])),
        # 选择器占位 ${SEL:键}。gap = 登记了但**被测前端还没给抓手**，
        # 那条现在渲染不出东西 —— 本地也跑不了，该去前端补 data-testid 提 MR，
        # 不是在脚本里换个脆选择器绕过去。
        "selectorsResolved": sorted(set(_sel_stat["resolved"])),
        "selectorsUnresolved": sorted(set(_sel_stat["missing"]) | set(_sel_stat["gap"])),
        "langSwitchInjected": bool(lang_key),
    }
    if need_export:
        out["exportEnv"] = " ".join(f"{k}=<{k}>" for k in sorted(need_export))
        out["usage"] = ("content 存成 fileName，把 exportEnv 里那几个凭据 export 了直接 pytest 跑。"
                        "要一个不用 export 的自包含文件就传 include_credentials=true。"
                        + ("textUnresolved 非空先 lum_upsert_i18n_terms 登记，或在占位里补 |中文原文。"
                           if stat["missing"] else ""))
    else:
        out["usage"] = ("content 存成 fileName，直接 pytest 跑，不用再配任何东西。"
                        + ("textUnresolved 非空先 lum_upsert_i18n_terms 登记。"
                           if stat["missing"] else ""))
    if not env_id:
        out["usage"] = "只渲染了文案 —— 传 env_id 才会烧进环境变量和语种开关。" + out["usage"]
    return out
