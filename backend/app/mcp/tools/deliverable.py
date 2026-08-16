"""交付门禁 —— 把「这条用例能不能交付」变成一条命令。

**为什么要它。** 之前每一轮都是这个流程：CC 说"这两条可以交付"，然后人逐条去查库
才发现不行 —— 请求体被改坏、断言类型写错、异步断言裸奔、状态其实没到位。
人肉门禁的问题不是慢，是**不可复现**：查的人换了、心气松了，就漏过去。

所以判据要落成代码，让 CC 自己先跑、人在发布前也跑一遍。它只回答事实，不改任何状态。

判据分三类：
- `blockers`  —— 交不了。有一条就是不可交付。
- `risks`     —— 交得了但脆。典型是"跑绿了但纯靠跑赢时间窗"，换台机器就红。
- `notes`     —— 提示。越界测试点、弱断言这类需要人判断的，不拦。
"""
from __future__ import annotations

import json
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_test import ApiTestScenario, ApiTestStep
from app.models.case import Case
from app.models.script import Script, ScriptRun
from app.services.data_health import is_protocol_envelope

# 期望值写成字符串的布尔 —— 必然假红（平台故意不兜布尔，兜了会假绿）
_BOOL_STRINGS = {"true", "false", "True", "False"}
# 异步下发之后立刻断言「已生效」的两种形状
_ASYNC_FIELD_RE = re.compile(r"(push|sync)[-_]?status|data\.(status|phase|synced_count)", re.I)
_DATA_PLANE_RE = re.compile(r"\$\{(gatewayBase|gateway_base|dataPlane|GATEWAY_URL)\}", re.I)
# 请求体被驼峰化的痕迹（库里本该是蛇形；见 middleware._OPAQUE_KEYS 那次修复）
_CAMEL_KEY_RE = re.compile(r"^[a-z]+[a-z0-9]*[A-Z]")
# 「应产生/应新增/应记入」这类承诺，只用 body_contains 兑付就是弱断言
_PROMISE_RE = re.compile(r"应(产生|新增|记入|生成|保留|接管)|版本历史|操作日志")
# 改状态的动作词。这类写操作只断 200 = 没验它真的改成功了。
_STATE_CHANGE_RE = re.compile(
    r"禁用|启用|停用|发布|上线|下线|弃用|废弃|回滚|恢复|撤销|审批|授权|"
    r"修改|更新|保存|编辑|切换|变更|调整")
# 稳态语义：断的是「一直是这样」，不是「等它变成这样」
_STEADY_RE = re.compile(r"不中断|保持|不变|不应|别变|仍(应|然)|依旧|不下发|不新旧并存")


def _is_negative_assertion(step) -> bool:
    """这一步断的是「不存在 / 不变」吗？是的话不该催重试。

    两种判据，任一成立即可：
      · 断言里期望一个非 2xx 状态码 —— 那是在断「这个东西不存在」。
        重试会一直等到它变成 404，把「路由该清没清掉」等成绿。
      · 步骤名带稳态词（保持/不中断/不变/不应…）—— 断的是「一直是这样」，
        重试同样是反的。

    **只看状态码不够**：「弃用后存量调用不中断（应保持 200）」期望的是 200，
    却同样不能重试 —— 重试意味着允许它先断一下再恢复，而这条测的正是"不能断"。
    """
    for a in (step.assertions or []):
        if (a.get("type") or "") == "status":
            v = a.get("value", a.get("expected"))
            try:
                if int(v) >= 400:
                    return True
            except (TypeError, ValueError):
                pass
    return bool(_STEADY_RE.search(step.name or ""))
# 越界判定的阈值：步骤名有多少比例的二元组能在用例范围里找到，低于这个才报。
# 在真实那批 6 条上标定过：真阳性（用例压根没提「版本记录」「操作日志」）落在 12%，
# 边界误报（「推送应已收敛」「确认服务已转 active」，用例其实提过）落在 17~20%。
# **宁可漏报也不要滥报** —— notes 里出现假的，人就不看了，真的那条跟着被忽略。
_OUT_OF_SCOPE_RATIO = 0.15


async def check_deliverable(session: AsyncSession, case_id: str) -> dict:
    """这条用例现在能不能交付。只读，不改任何状态。"""
    cid = uuid.UUID(case_id)
    case = await session.get(Case, cid)
    if not case:
        return {"error": "用例不存在"}

    blockers: list[dict] = []
    risks: list[dict] = []
    notes: list[dict] = []

    target = case.target_level or "spec"
    owed_dims = ["manual"] + (["api"] if target in ("spec_api", "full") else []) \
        + (["ui"] if target == "full" else [])

    # ── 手工步骤：内容有没有 ──
    if not (case.steps or []):
        blockers.append({"kind": "manual_missing", "detail": "一条手工步骤都没有"})

    # ── 接口维度 ──
    scenario = None
    if "api" in owed_dims:
        scenario = (await session.execute(
            select(ApiTestScenario).where(ApiTestScenario.source_case_id == cid)
            .order_by(ApiTestScenario.created_at)
        )).scalars().first()
        if scenario is None:
            blockers.append({"kind": "api_scenario_missing",
                             "detail": f"target_level={target} 要求接口维度，但没有编排场景"})
        else:
            steps = (await session.execute(
                select(ApiTestStep).where(ApiTestStep.scenario_id == scenario.id)
                .order_by(ApiTestStep.sort_order)
            )).scalars().all()
            _audit_api_steps(scenario, steps, case, blockers, risks, notes)

    # ── UI 维度 ──
    if "ui" in owed_dims:
        script = (await session.execute(
            select(Script).where(Script.case_id == cid, Script.script_type == "ui",
                                 Script.status == "active")
        )).scalars().first()
        if script is None:
            blockers.append({"kind": "ui_script_missing",
                             "detail": f"target_level={target} 要求 UI 维度，但没有活跃脚本"})
        else:
            last = (await session.execute(
                select(ScriptRun).where(ScriptRun.case_id == cid, ScriptRun.script_type == "ui")
                .order_by(ScriptRun.created_at.desc())
            )).scalars().first()
            if last is None:
                blockers.append({"kind": "ui_never_run", "detail": "UI 脚本从没跑过"})
            elif last.status != "passed":
                blockers.append({"kind": "ui_last_failed",
                                 "detail": f"UI 脚本最近一次是 {last.status}"})
            _audit_ui_traffic(last, risks)

    # ── 状态：轮到谁了 ──
    # 维度只有三态：draft / debugging / completed。审核是**用例级**的单独标签
    # （空=待提审、pending=待审、approved/rejected=人拍板）。
    # 这里以前读的是 pending_review / not_started / executable —— 那几个态在
    # 三态改造时就删了，于是 waiting_human 恒为空、判词里还在说「待发布」，
    # 而「待发布」这个环节根本已经不存在。CC 照着这句话去找按钮会找不到。
    dim_status = {"manual": case.manual_status, "ui": case.ui_status, "api": case.api_status}
    waiting_human = case.review_status == "pending"
    not_ready = [d for d in owed_dims if dim_status.get(d) in ("draft", "debugging")]
    if not_ready and not blockers:
        # 没有硬阻塞却维度没到「完成」：多半是跑过但没经平台记账，或者压根没在
        # 平台上跑过。说清楚，别让人以为是内容问题。
        # **manual 不能排除掉** —— 停在调试中这条就进不了「待审」，不报出来
        # 人只会看到它一直不冒头，不知道卡在哪。
        #
        # 但也别说成「只能人在页面上改」（我上一版就是这么写的，错了）：
        # sync_manual_status 的规矩是**手工步骤写了就是 completed**（手工步骤没有
        # 执行器，写完就是做完）。所以改一下步骤重存就会自己回到完成。
        # 实测 TC-FWGL-00002 有 13 步却停在 debugging，是一次「整条用例保存」把
        # manual 和 api 一起写成 debugging 的连带产物，步骤本身自始至终没动过 ——
        # 不是谁判断了「步骤没写完」。说成"只能人改"会让人以为要去做一次真的判断。
        detail = (f"{'、'.join(not_ready)} 维度还在 "
                  f"{'、'.join(dim_status[d] or '空' for d in not_ready)}。"
                  f"接口/UI 在平台上跑一遍会自己往前走。")
        if "manual" in not_ready:
            detail += ("manual 的规矩是「手工步骤写了就算完成」——"
                       "有步骤却停在草稿/调试中，多半是被某次整条保存带偏了，"
                       "改一下步骤重存或在下拉里直接选「完成」即可。")
        risks.append({"kind": "status_behind", "detail": detail})

    # 预期结果里写了 UI 落点，但这条不做 UI 维度 → 那句话没人验
    if target == "spec_api" and re.search(r"详情页|列表页|页面|回显|界面", case.expected_result or ""):
        notes.append({"kind": "ui_wording_in_spec_api",
                      "detail": "预期结果里提到了页面/回显，但 target_level=spec_api 不做 UI 维度。"
                                "接口层若已断言对应字段就算覆盖了数据层，页面渲染那一层没人验 —— "
                                "确认这是有意的，或者把那句话改成接口口径。"})

    # 预期到底跟谁确认的。
    #
    # 这个标记的用途是**记录人确认过「这个场景要验什么」** —— 同源生成的三份产物
    # 容易互相一致而不正确（典型是把「创建成功」做成「返回 200」），所以要有个
    # 外部锚点。落款是 CC 自由填的（它转述对话里那句话，这是设计如此），
    # 但因此也可能填成自证：实测有一条落款写的是「实测（本轮探索）」——
    # 那是 CC 自己跑了一遍，不是任何人确认过。
    #
    # 平台判不出真假，也不该判。**把落款原样摆出来让人一眼看见**就够了：
    # 装作有确认，比没有确认更危险。
    _actor = (getattr(case, "expected_confirmed_actor", None) or "").strip()
    if not getattr(case, "expected_confirmed_at", None):
        notes.append({"kind": "expected_not_confirmed",
                      "detail": "「预期已确认」是空的 —— 没跟人对过这条要验什么。"
                                "改过步骤或预期会自动清掉这个标记，如果是那种情况，"
                                "把确认内容重新带上来（tb_update_case 的 "
                                "expected_confirmed_by / expected_confirmed_note）。"})
    elif not re.search(r"用户|产品|需求|评审|业务|客户|PM", _actor):
        notes.append({"kind": "expected_confirmed_by_self",
                      "detail": f"「预期已确认」的落款是「{_actor}」，看不出是跟人确认的。"
                                f"这个标记要记的是**人确认过这条要验什么** —— "
                                f"自己实测一遍不算：三份产物同源，互相一致但一起错的时候，"
                                f"只有外部确认能挡住。确认过就把对话里那句原话写进落款。"})

    # 少做了一维却没说为什么。
    # **建用例时只提醒不拦，实测 6 条全空** —— 提醒发生在写入那一刻，CC 当时
    # 正忙着别的，过了就没人再提。挪到这里：CC 每次自证交付都要看这份结论，
    # 缺了会一直挂着。仍然不拦（不是缺陷，是信息缺失）。
    if target != "full" and not (getattr(case, "target_level_reason", None) or "").strip():
        skipped = "UI" if target == "spec_api" else "接口和 UI"
        notes.append({"kind": "target_level_reason_missing",
                      "detail": f"target_level={target}，{skipped} 维度不做，但没写为什么。"
                                f"只有一个 target_level 值时，人分不出你是**判断过不需要**"
                                f"还是**没想就用了默认值** —— 后者半年后没人敢动它。"
                                f"用 tb_update_case 的 target_level_reason 补一句。"})

    deliverable = not blockers
    return {
        "caseCode": case.case_code,
        "title": case.title,
        "targetLevel": target,
        "lifecycleStatus": case.lifecycle_status,
        "dimStatus": {d: dim_status.get(d) for d in owed_dims},
        "deliverable": deliverable,
        "blockers": blockers,
        "risks": risks,
        "notes": notes,
        "waitingHuman": waiting_human,
        "verdict": _verdict(deliverable, blockers, risks, waiting_human, owed_dims, dim_status),
    }


def _audit_api_steps(scenario, steps, case, blockers, risks, notes) -> None:
    if not steps:
        blockers.append({"kind": "api_no_steps", "detail": f"{scenario.code} 一步都没有"})
        return

    ran = [s for s in steps if s.last_status in ("pass", "fail")]
    failed = [s for s in steps if s.last_status == "fail"]
    never = [s for s in steps if s.last_status is None]

    if not ran:
        blockers.append({"kind": "api_never_run",
                         "detail": f"{scenario.code} 的 {len(steps)} 步全都没有执行记录 —— "
                                   f"「写完了」和「跑通了」是两件事"})
    if failed:
        blockers.append({"kind": "api_steps_failed",
                         "detail": f"{scenario.code} 最近一次有 {len(failed)} 步失败",
                         "steps": [{"sortOrder": s.sort_order, "name": s.name} for s in failed[:5]]})
    if never and ran:
        risks.append({"kind": "api_partial_run",
                      "detail": f"{len(never)} 步没有执行记录（可能上次只跑了勾选的一部分），"
                                f"整条链没被完整验证过一次",
                      "steps": [{"sortOrder": s.sort_order, "name": s.name} for s in never[:5]]})

    for s in steps:
        # 断言期望值类型写错 —— 必然假红
        for a in (s.assertions or []):
            if not isinstance(a, dict):
                continue
            exp = a.get("expected") if a.get("expected") is not None else a.get("value")
            if isinstance(exp, str) and "${" not in exp and exp in _BOOL_STRINGS:
                blockers.append({"kind": "assertion_bool_as_string",
                                 "detail": f"第 {s.sort_order + 1} 步「{s.name}」断言 "
                                           f"{a.get('field')} 期望写成了字符串 \"{exp}\"，"
                                           f"应为 {exp.lower()}（不加引号）"})
        # 异步断言裸奔 —— 跑绿了也是侥幸。
        # **但否定/稳态断言要放过**：重试的语义是「等它变成期望值」，对
        # 「应 404」「应保持 200」恰恰是反的 —— 路由本该立刻且一直不存在，
        # 给它开 10 秒重试，等于把「路由没被清掉」这种真 bug 等到收敛后判绿。
        # 实测这条误报占了 4 报 3（CC 甚至在步骤名里写了「否定断言故不加重试」，
        # 而门禁还在催），照建议改反而有害。
        # 写操作一律不催重试 —— 重试是整步重发，POST 重发就多造一份数据。
        # 回推门禁（sync._needs_retry）和这里必须同一个口径，否则 CC 在两个地方
        # 收到相反的建议。实测这条误报占了 19/19：全是 申请/驳回/审批/撤销 这类
        # POST，data.status 是同步响应直接回传的，没有异步可等。
        _idempotent = (s.method or "GET").upper() in ("GET", "HEAD", "OPTIONS")
        if int(s.retry_timeout_ms or 0) == 0 and _idempotent \
                and not _is_negative_assertion(s):
            atext = json.dumps(s.assertions or [], ensure_ascii=False)
            if _DATA_PLANE_RE.search(s.url or "") or _ASYNC_FIELD_RE.search(s.url or "") \
                    or _ASYNC_FIELD_RE.search(atext):
                risks.append({"kind": "async_assertion_no_retry",
                              "detail": f"第 {s.sort_order + 1} 步「{s.name}」断的是异步下发的结果"
                                        f"却没开重试。现在是 {s.last_status or '未跑'} —— "
                                        f"绿也是靠跑赢时间窗，换台机器就红。"
                                        f"建议 retry_timeout_ms=10000。"})
        # 请求体被驼峰污染。协议信封（MCP/JSON-RPC）的驼峰是规范规定的，不报 ——
        # 实测 AT-0012 的 clientInfo/protocolVersion 被报过一次，而它 18/18 全绿。
        if isinstance(s.body, dict) and not is_protocol_envelope(s.body):
            camel = [k for k in _flat_keys(s.body) if _CAMEL_KEY_RE.match(k)]
            if camel:
                notes.append({"kind": "body_camel_keys",
                              "detail": f"第 {s.sort_order + 1} 步「{s.name}」请求体里有驼峰键 "
                                        f"{camel[:4]} —— 如果被测系统用蛇形命名，"
                                        f"这是被响应层驼峰化污染过的痕迹（历史 bug，已修根因）；"
                                        f"如果那个接口本来就用驼峰，忽略这条。"})
        # 「应产生/应新增」这类承诺只用 body_contains 兑付
        if _PROMISE_RE.search(s.name or ""):
            kinds = {a.get("type") for a in (s.assertions or []) if isinstance(a, dict)}
            if kinds and kinds <= {"status", "body_contains"}:
                notes.append({"kind": "weak_assertion",
                              "detail": f"第 {s.sort_order + 1} 步「{s.name}」承诺的是"
                                        f"「产生/新增/记入」，但只用了 {sorted(kinds)} 断言 —— "
                                        f"字符串出现在响应里不等于那件事真发生了。"
                                        f"补一条 body_field 断到具体字段上。"})

        # 写操作只断状态码 —— 「请求被接受」不等于「状态真的变了」。
        # 实测 105 步里有 20 步只断 status，其中「禁用服务」「重新启用服务」
        # 「回滚到上一版本」这类**改状态的写操作**只断了 200：接口哪怕什么都没做、
        # 只要返回 200 就判绿。这类步骤恰恰是用例的核心动作。
        # 登录/制备/清理不算 —— 它们的目的就是"别报错"，断状态码是对的。
        if (s.method or "").upper() in ("POST", "PUT", "PATCH", "DELETE") \
                and (s.group_name or "") not in ("前置", "制备", "清理") \
                and _STATE_CHANGE_RE.search(s.name or ""):
            kinds = {a.get("type") for a in (s.assertions or []) if isinstance(a, dict)}
            if kinds and kinds <= {"status"}:
                notes.append({"kind": "write_only_status_assert",
                              "detail": f"第 {s.sort_order + 1} 步「{s.name}」是改状态的写操作，"
                                        f"却只断了状态码 —— 接口什么都没做、只要回 200 也判绿。"
                                        f"补一条 body_field 断到变更后的字段上"
                                        f"（如 data.status / data.enabled），"
                                        f"或者在下一步查一次确认它真的变了。"})

    # 疑似越界测试点：这一步在讲一件用例本身**从没提过**的事。
    #
    # 两个坑我都踩过，写在这儿免得下次再来：
    # ① 分词不能用 `[一-龥]{2,}` —— 它贪婪匹配，把「发布应产生版本记录」整串当成
    #    一个词，于是在标题里当然找不到，一条正常步骤被报成越界。用二元组。
    # ② 范围不能只算标题+预期 —— 那两句话很短，覆盖不了一个完整流程。
    #    **手工步骤才是这条用例的完整描述**，必须算进去。加进去之后
    #    「确认服务已转 active 且为启用态」「发布应产生版本记录」这些正常步骤
    #    就不再误报，而真正越界的那两条（暴露级别、跨租户目录）仍然报得出来。
    #
    # 滥报的代价比漏报大：一屏十几条假的，人就不看了，真的那条跟着被忽略。
    scope = _case_scope_text(case)
    for s in steps:
        if (s.group_name or "") in ("前置", "制备", "清理"):
            continue
        ratio = _bigram_overlap(s.name or "", scope)
        if ratio is not None and ratio < _OUT_OF_SCOPE_RATIO:
            notes.append({"kind": "possible_out_of_scope",
                          "detail": f"第 {s.sort_order + 1} 步「{s.name}」讲的事情在用例标题、"
                                    f"预期结果和手工步骤里都没出现过（词重叠 {ratio:.0%}）—— "
                                    f"确认它属于这条用例，还是该拆出去"
                                    f"（合并的唯一代价是一挂全挂）。"
                                    f"**消除方式是拆出去或把标题/预期写全，不是删掉这一步** —— "
                                    f"删掉等于丢覆盖。实测 CC 打算删掉两条『应记入操作日志』，"
                                    f"理由是『别的用例已经覆盖』，而那两条断的 action "
                                    f"（改路由 / 回滚）没有任何其它用例验过。"})


def _audit_ui_traffic(last_run, risks) -> None:
    if last_run is None:
        return
    caps = last_run.captured_requests
    if not isinstance(caps, list):
        return
    for e in caps:
        if isinstance(e, dict) and e.get("truncated"):
            risks.append({"kind": "traffic_truncated",
                          "detail": f"这次执行的流量被截断了（留存 {e.get('kept')} 条，"
                                    f"实际 {e.get('totalSeen')} 条）—— 靠后的写操作可能不在证据里，"
                                    f"拿这份流量编排接口场景会漏掉关键请求。"})
            return


def _case_scope_text(case) -> str:
    """这条用例「在讲什么」的全文：标题 + 预期 + 手工步骤。

    手工步骤必须算进来 —— 标题和预期各一句话，覆盖不了一个完整流程，
    只拿那两句当范围会把大量正常步骤判成越界（实测一条用例误报 12 次）。
    """
    parts = [case.title or "", case.expected_result or ""]
    for st in (case.steps or []):
        if isinstance(st, dict):
            parts.append(str(st.get("action") or ""))
            parts.append(str(st.get("expected") or ""))
    return "".join(parts)


def _bigrams(text: str) -> set[str]:
    """中文二元组。整串当一个词是错的（贪婪匹配的坑），按相邻两字切。"""
    zh = re.sub(r"[^一-龥]", "", text or "")
    return {zh[i:i + 2] for i in range(len(zh) - 1)}


def _bigram_overlap(name: str, scope: str) -> float | None:
    """步骤名有多少比例的二元组能在用例范围里找到。太短就不判（返回 None）。"""
    a = _bigrams(name)
    if len(a) < 3:
        return None
    b = _bigrams(scope)
    return len(a & b) / len(a)


def _flat_keys(obj, out=None) -> list[str]:
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append(str(k))
            _flat_keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _flat_keys(v, out)
    return out


def _verdict(deliverable, blockers, risks, waiting_human, owed_dims, dim_status) -> str:
    if not deliverable:
        return (f"**不可交付**：{len(blockers)} 项硬阻塞。"
                + "；".join(b["detail"] for b in blockers[:3]))
    parts = ["**内容可交付**（该做的几维都有、都跑绿了）"]
    if risks:
        parts.append(f"但有 {len(risks)} 项脆弱点，不修的话回归里会偶发红")
    if waiting_human:
        # 审核**不挡回归** —— 人可以不审，直接建计划跑。这句话要说清楚，
        # 否则 CC 会以为还卡着一道人工闸口而停下来等。
        parts.append("已进「待审」，等人拍板；审核不挡回归，现在就能建计划跑")
    else:
        pending = [d for d in owed_dims if dim_status.get(d) != "completed"]
        if pending:
            parts.append(f"{'、'.join(pending)} 维度还没到「完成」，所以还没进「待审」")
    return "；".join(parts) + "。"


async def check_branch(session: AsyncSession, branch_id: str,
                       module: str | None = None) -> dict:
    """**整个分支一次验收** —— 不用逐条点。

    为什么要它：`check_deliverable` 是按单条查的，而验收是个"一批做完了没有"的问题。
    逐条点几十次之后没人会真的点完，于是「做完了吗」这件事实际上没人回答 ——
    这正是之前每一轮都要人肉逐条查库的原因。

    关键是**阻塞和脆弱点分开报**。原来只有 `owes` 一个信号（"欠 api"），
    而"接口有一步真挂了"和"接口跑绿了但异步断言抢跑"在 owes 里长得一模一样，
    要做的事却完全不同：一个改断言，一个加 retry_timeout_ms。
    """
    bid = uuid.UUID(branch_id)
    q = select(Case).where(Case.branch_id == bid, Case.deleted_at.is_(None))
    if module:
        from app.models.case import CaseFolder
        folders = (await session.execute(
            select(CaseFolder.id).where(CaseFolder.branch_id == bid,
                                        CaseFolder.name == module)
        )).scalars().all()
        if folders:
            q = q.where(Case.folder_id.in_(folders))
    cases = (await session.execute(q.order_by(Case.case_code))).scalars().all()
    if not cases:
        return {"error": "这个分支/模块下没有用例"}

    rows, deliverable, blocked, risky = [], 0, 0, 0
    waiting_review = 0
    for c in cases:
        r = await check_deliverable(session, str(c.id))
        if r.get("error"):
            continue
        ok = r["deliverable"]
        deliverable += 1 if ok else 0
        blocked += 0 if ok else 1
        risky += 1 if r["risks"] else 0
        waiting_review += 1 if c.review_status == "pending" else 0
        rows.append({
            "caseCode": r["caseCode"],
            "title": r["title"][:40],
            "targetLevel": r["targetLevel"],
            "deliverable": ok,
            # 一行一句话说清卡在哪 —— 不用点进去
            "blockers": [b["kind"] for b in r["blockers"]],
            "firstBlocker": (r["blockers"][0]["detail"] if r["blockers"] else None),
            "riskCount": len(r["risks"]),
            "riskKinds": sorted({x["kind"] for x in r["risks"]}),
            "noteCount": len(r["notes"]),
            "review": c.review_status or "待提审",
        })

    gaps = await _module_ui_gaps(session, cases)
    return {
        "total": len(rows),
        "summary": {
            "可交付": deliverable, "有阻塞": blocked,
            "有脆弱点": risky, "待你审": waiting_review,
        },
        "moduleGaps": gaps,
        "cases": rows,
        "verdict": _branch_verdict(len(rows), deliverable, blocked, risky, waiting_review, gaps),
        "usage": "blockers=交不了，必须先修；riskKinds=交得了但会偶发红；"
                 "review=pending 表示三维都完成了、等你审（不审也能建计划跑）。",
    }


async def _module_ui_gaps(session: AsyncSession, cases) -> list[dict]:
    """整个模块一条 UI 维度都没有 → 那个模块的页面裸奔。

    **这个空洞单条看不出来。** 逐条问「这条要不要做 UI」，每次的回答都合理：
    「判定点在数据面，页面上看不到」「UI 只能验按钮不存在」…… 六条各有各的道理，
    合起来就是整块界面没有任何自动化盯着 —— 审批页签没了、驳回按钮失效、
    状态标签不刷新，全都发现不了。实测订阅管理 6 条全是 spec_api，0 条做 UI。

    所以判据放在模块级：不看单条该不该做，只看**这个模块有没有人做**。
    报出来不拦 —— 有的模块（纯后台任务、纯数据处理）确实没有页面。
    """
    from app.models.case import CaseFolder

    by_folder: dict = {}
    for c in cases:
        by_folder.setdefault(c.folder_id, []).append(c)

    gaps = []
    for fid, group in by_folder.items():
        if fid is None or len(group) < _MODULE_MIN_CASES:
            continue
        if any((c.target_level or "spec") == "full" for c in group):
            continue
        folder = await session.get(CaseFolder, fid)
        gaps.append({
            "module": getattr(folder, "name", None) or "（未归类）",
            "caseCount": len(group),
            "detail": f"「{getattr(folder, 'name', '?')}」{len(group)} 条用例**没有一条做 UI 维度** —— "
                      f"这个模块的页面没有任何自动化盯着：审批入口消失、按钮失效、"
                      f"状态标签不刷新，都发现不了。单条看每次都有道理"
                      f"（「判定点在数据面」「UI 只能验按钮不存在」），合起来就是整块界面裸奔。"
                      f"挑一条页面路径最长的升成 target_level=full。",
        })
    return gaps


# 模块里少于这个数不报 —— 才两三条的模块可能只是刚开始写
_MODULE_MIN_CASES = 4


def _branch_verdict(total, ok, blocked, risky, waiting, gaps=None) -> str:
    parts = [f"{total} 条里 {ok} 条可交付"]
    if blocked:
        parts.append(f"{blocked} 条有硬阻塞（看 firstBlocker）")
    if risky:
        parts.append(f"{risky} 条有脆弱点 —— 不修的话回归里会偶发红")
    if waiting:
        parts.append(f"{waiting} 条等你审（**审核不挡回归**，不审也能建计划跑）")
    for g in (gaps or []):
        parts.append(f"⚠「{g['module']}」{g['caseCount']} 条没有一条做 UI 维度，整块界面没人盯")
    return "；".join(parts) + "。"
