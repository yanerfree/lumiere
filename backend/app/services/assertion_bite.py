"""变异验证 —— 「这条断言到底会不会红」。

**为什么非做不可。** 外部 CC 写完 21 条用例后的第一条反馈，也是它自己说
"如果只能改一条就选这条"：21 条全绿，但其中有多少条真能抓到问题，**没人知道**。
一条方向写反的断言也是绿的；一条恒真的断言（动作前后都成立）也是绿的。
平台的「断言强度」只数数量，「断言指纹」只比前后变化，都回答不了这个问题。

回答它只有一个办法：**把动作拿掉，看验证步会不会红。**
- 红了 → 这条断言咬得住这个动作，它的绿是有价值的
- 还绿 → 它跟这个动作无关，之前那条绿等于没测（恒真／方向反／断在别处）

这就是变异测试的思路，只是变异对象不是被测代码，而是**这条链自己的因果**：
删掉原因，结果必须消失。链子本身就是可执行的，所以这件事平台做得到，
不用被测系统配合。

四个判据（分清楚很重要，不然会把"环境没配好"当成"断言有效"、把好断言冤枉成恒真）：
- `bites`         断言真的红了 → 有效
- `still_green`   照样绿 → **恒真嫌疑，要改**
- `inconclusive`  压根没发出请求（变量没解析：它引用了被跳过那步的提取物）
                  → 判不了。**不能算它有效** —— 它是被变量卡死的，不是被断言抓住的
- `out_of_window` 它在后面另一个动作之后，这次跳的管不到它 → 要验它就单独跳那个动作
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_test import ApiTestScenario, ApiTestStep
from app.services.api_test_runner import expected_of, failure_detail, run_scenario


@dataclass
class BiteRow:
    step: str
    verdict: str                     # bites / still_green / inconclusive
    detail: str = ""
    assertions: list = field(default_factory=list)


_REJECT_NAME_RE = re.compile(r"应被拒|被拒绝|不允许|应失败|应报错|拒掉|不该|禁止|无权|越权")


def _expects_rejection(st) -> bool:
    """这一步本身预期就是"被拒"吗（4xx/5xx，或名字明说）。

    这种动作的**预期效果是"什么都不变"** —— 把它跳掉，观察不到任何差别，
    变异验证在原理上就说不了话。活体自测时撞到：跳掉「同名再建应被拒」，
    「列表里有且只有一条」照样绿，工具报 still_green —— 那是**冤枉**，
    人照着"修"反而会把一条正确的唯一性断言改坏。
    """
    for a in (st.assertions or []):
        if not isinstance(a, dict) or (a.get("type") or "") != "status":
            continue
        v = expected_of(a)
        for one in (v if isinstance(v, list) else [v]):
            try:
                if int(one) >= 400:
                    return True
            except (TypeError, ValueError):
                pass
    return bool(_REJECT_NAME_RE.search(st.name or ""))


def _watch_window(all_steps, skip: set[str]) -> dict[str, str | None]:
    """哪些步骤的结果能**归因到这次跳过**：步骤名 → 拦在它前面的那个动作（None = 能归因）。

    两条排除：
      · 写操作不看 —— 它是动作，不是验证（`清理：删除服务` 当然还是 204）
      · 中间又插了别的动作之后的读，归不到这次跳过头上。实测跳掉「禁用服务」，
        后面「启用后打网关应恢复 200」照样绿 —— 那不是恒真，是因为它验的是「启用」，
        而启用真的跑了。把它算成 still_green 是**冤枉**：人照着改反而会把好断言改坏。
        这种单独归一类，并告诉它"要验这条就单独跳那个动作"。
    """
    first_skipped = min(i for i, s in enumerate(all_steps) if s.name in skip)
    watched: dict[str, str | None] = {}
    blocker: str | None = None
    for s in all_steps[first_skipped:]:
        if s.name in skip:
            continue
        if (s.method or "GET").upper() not in ("GET", "HEAD", "OPTIONS"):
            blocker = s.name
            continue
        watched[s.name] = blocker
    return watched


async def check_assertion_bite(
    session: AsyncSession,
    scenario_id: uuid.UUID,
    skip_step_names: list[str],
    base_env: dict | None = None,
    env_name: str | None = None,
) -> dict:
    """跳掉 skip_step_names 里那几步跑一遍，报告后面每一步的断言有没有变红。

    只读：`persist=False`，不写 last_status、不建报告、不动用例维度状态。
    """
    scenario = await session.get(ApiTestScenario, scenario_id)
    if not scenario:
        return {"error": "场景不存在"}

    all_steps = (await session.execute(
        select(ApiTestStep).where(ApiTestStep.scenario_id == scenario_id)
        .order_by(ApiTestStep.sort_order)
    )).scalars().all()
    if not all_steps:
        return {"error": "这条场景没有步骤"}

    names = [s.name for s in all_steps]
    unknown = [n for n in skip_step_names if n not in names]
    if unknown:
        return {"error": "这些 step name 在场景里找不到，没跳成任何东西就不该往下跑：",
                "notFound": unknown, "existingNames": names}
    if not skip_step_names:
        return {"error": "skip_step_names 必填：要跳掉的是**动作步**（审批/禁用/删除…）。"
                         "不跳任何东西跑出来就是一次普通执行，证明不了断言会不会红。"}

    skip = set(skip_step_names)
    rejects = [s.name for s in all_steps if s.name in skip and _expects_rejection(s)]
    if rejects and len(rejects) == len(skip):
        return {"error": f"「{'、'.join(rejects)}」本身预期就是被拒（4xx）——"
                         f"它的预期效果是**什么都不变**，跳掉它观察不到任何差别，"
                         f"变异验证在原理上说不了话（照跑只会把正确的断言冤枉成恒真）。",
                "whatToDoInstead": "这类断言要靠被测系统**真的收下**那次非法请求才会红，"
                                   "跳过模拟不出来。要么跳它前后那个**正面动作**"
                                   "（创建/审批/删除），要么就认下：这条的有效性只能靠"
                                   "「断的是不是稳定错误码 + 有没有断状态没被改坏」来判。",
                "skippedButNegative": rejects}

    kept = [s for s in all_steps if s.name not in skip]
    watched = _watch_window(all_steps, skip)
    if not any(v is None for v in watched.values()):
        # 两种情形，说清是哪一种 —— 都在真跑之前拦住，别白打一趟被测系统。
        blocker = next((v for v in watched.values() if v), None)
        if blocker:
            return {
                "error": f"跳的这步和验证步之间还夹着别的动作（第一个是「{blocker}」）——"
                         f"那些读验的是它，归不到这次跳过头上，所以这一跑证明不了任何事。"
                         f"改跳「{blocker}」，或者跳那个**紧接着就有读回来确认**的动作步。",
                "nextActionInBetween": blocker,
                "readsAfterIt": [k for k, v in watched.items() if v == blocker][:5],
            }
        return {"error": "被跳掉的步骤后面没有任何读步骤 —— 没有验证步可看。"
                         "要跳的是动作步，不是最后那几步。"}

    rows: list[BiteRow] = []
    async for ev in run_scenario(scenario, kept, session, base_env=base_env,
                                 env_name=env_name, persist=False):
        if ev.type != "step_result":
            continue
        name = ev.data.get("stepName")
        if name not in watched:
            continue
        err = ev.data.get("error")
        asserts = ev.data.get("assertions") or []
        if watched[name] is not None:
            rows.append(BiteRow(name, "out_of_window",
                                f"它在「{watched[name]}」之后 —— 验的是那个动作，"
                                f"这次跳的管不到它。要验它就单独跳「{watched[name]}」"))
        elif ev.data.get("status") == "pass":
            rows.append(BiteRow(name, "still_green",
                                "动作被跳掉了它还是绿的 —— 这条断言不验这个动作",
                                asserts))
        elif err and not any(not a.get("passed") for a in asserts if isinstance(a, dict)):
            # 没有任何断言失败，却挂了 → 压根没发出请求（多半引用了被跳步骤的提取物）
            rows.append(BiteRow(name, "inconclusive", str(err)[:200], asserts))
        else:
            rows.append(BiteRow(name, "bites", failure_detail(asserts, err)["why"][:200],
                                asserts))

    green = [r.step for r in rows if r.verdict == "still_green"]
    bite = [r.step for r in rows if r.verdict == "bites"]
    unknown_rows = [r.step for r in rows if r.verdict == "inconclusive"]
    outside = [r.step for r in rows if r.verdict == "out_of_window"]
    return {
        "scenario": scenario.title,
        "skipped": sorted(skip),
        "steps": [{"step": r.step, "verdict": r.verdict, "detail": r.detail} for r in rows],
        "summary": {"bites": len(bite), "stillGreen": len(green),
                    "inconclusive": len(unknown_rows), "outOfWindow": len(outside)},
        "verdict": _verdict(bite, green, unknown_rows)
                   + (f" 另有 {len(outside)} 步归不到这次跳过头上（它们在后面的动作之后），"
                      f"要验它们就分别跳那个动作。" if outside else ""),
    }


def _verdict(bite: list[str], green: list[str], unknown: list[str]) -> str:
    if green:
        return (f"⚠ {len(green)} 步在动作被跳掉时照样绿：{'、'.join(green[:5])}。"
                f"它们的绿不代表这个动作是对的 —— 改成断动作真正改变的那个东西"
                f"（状态字段变成什么、列表里多/少了哪条）。")
    if not bite and unknown:
        return (f"判不了：{len(unknown)} 步压根没发出请求（它们引用了被跳步骤的提取物）。"
                f"换个跳法 —— 跳那个**改状态**的动作步，别跳产出 id 的创建步。")
    return (f"✅ {len(bite)} 步都红了，这些断言咬得住这个动作"
            + (f"；另有 {len(unknown)} 步判不了（变量依赖被跳步骤）" if unknown else "")
            + "。")
