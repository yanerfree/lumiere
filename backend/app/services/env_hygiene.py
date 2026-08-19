"""环境卫生 —— 「探测脚本崩过几次，留下孤儿待办、孤儿服务、孤儿订阅，平台完全不知道」。

外部 CC 的第十条反馈。它只能自己写清理脚本按前缀扫，而**平台其实知道一半**：
每条链子自己就写着"我造了什么、打算怎么删"，步骤上还留着最后一次运行的响应。

所以这里回答两个能证明的问题，不假装能扫被测系统：

① **结构性的**：这条链造了东西却没有清理步骤 → 每跑一次留一个。
   这不只是脏，它会**反过来毁掉断言** —— 列表里堆满同类数据后，`data[0]` 指向别人、
   满页把本次那条挤到第二页，于是断言时红时绿，人以为是被测系统的问题。
② **上一次运行的**：清理步骤没跑成（链子中途挂了）→ 那次造的东西还在，
   id 就在创建步骤的 last_response 里，删它的请求就是那条清理步骤本身。

⚠ 平台**只留最后一次运行**的响应，更早的残留看不见 —— 输出里必须把这句话带上，
不然"报了 0 条"会被当成"环境是干净的"。
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_test import ApiTestScenario, ApiTestStep
from app.models.case import Case
from app.models.project import Branch
from app.services.api_test_runner import _extract_value

# 清理步骤：DELETE，或者名字里明说是清理/删除的（有些系统用 POST /archive 收尾）
_CLEANUP_NAME_RE = re.compile(r"清理|清除|删除|删掉|销毁|回收|收尾")
_ID_LIKE_RE = re.compile(r"id$|Id$|_id$", re.I)


def _is_cleanup(st: ApiTestStep) -> bool:
    return (st.method or "").upper() == "DELETE" or bool(_CLEANUP_NAME_RE.search(st.name or ""))


def _creates(st: ApiTestStep) -> bool:
    """这一步造了东西吗：写操作 + 抽了个 id 出来。"""
    if (st.method or "").upper() not in ("POST", "PUT"):
        return False
    return any(_ID_LIKE_RE.search(k) for k in (st.variables_extract or {}))


def _extracted_ids(st: ApiTestStep) -> list[dict]:
    """从最后一次运行的响应里把 id 抽出来（用步骤自己声明的 JSONPath）。"""
    body = ((st.last_response or {}).get("body")) if isinstance(st.last_response, dict) else None
    out = []
    for name, path in (st.variables_extract or {}).items():
        if not _ID_LIKE_RE.search(name):
            continue
        val = _extract_value(body, str(path)) if body is not None else None
        out.append({"variable": name, "value": val, "fromStep": st.name})
    return out


def _status_code(st: ApiTestStep) -> int | None:
    """这一步最后一次运行**真的**回了什么状态码。判 fail 不代表请求没成功。"""
    lr = st.last_response if isinstance(st.last_response, dict) else None
    code = (lr or {}).get("statusCode", (lr or {}).get("status_code"))
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


async def check_env_hygiene(session: AsyncSession, project_id: str,
                            branch_id: str | None = None) -> dict:
    pid = uuid.UUID(project_id)
    q = select(ApiTestScenario).where(ApiTestScenario.project_id == pid)
    if branch_id:
        q = q.where(ApiTestScenario.branch_id == uuid.UUID(branch_id))
    scenarios = (await session.execute(q.order_by(ApiTestScenario.code))).scalars().all()
    if not scenarios:
        return {"error": "这个项目/分支下没有接口场景"}

    no_cleanup: list[dict] = []
    dirty_runs: list[dict] = []
    for sc in scenarios:
        steps = (await session.execute(
            select(ApiTestStep).where(ApiTestStep.scenario_id == sc.id)
            .order_by(ApiTestStep.sort_order)
        )).scalars().all()
        if not steps:
            continue
        creators = [s for s in steps if _creates(s)]
        cleaners = [s for s in steps if _is_cleanup(s)]
        if not creators:
            continue

        if not cleaners:
            no_cleanup.append({
                "code": sc.code, "scenario": sc.title,
                "createsAt": [s.name for s in creators],
                "why": "造了东西但整条链没有清理步骤 —— 每跑一次留一份。"
                       "在末尾加一步按 id 删掉（自建自删，跑一百遍都干净）。",
            })
            continue

        # 上一次运行有没有跑到清理
        ran = [s for s in steps if s.last_status]
        if not ran:
            continue
        bad_clean = [s for s in cleaners if s.last_status not in ("pass", "skip")]
        if not bad_clean:
            continue
        leftovers = [x for s in creators for x in _extracted_ids(s) if x["value"] is not None]
        # **"步骤判 fail" ≠ "请求没成功"。** 清理请求真的回了 204、只是某条断言判失败时，
        # 只看 last_status 就会报出一堆并不存在的"残留" —— 实测（CC）报了 3 条，
        # 那 3 个 id 去查全是 404，早删干净了，根因是状态码断言的键名 bug 把 204 判成了 fail。
        # 响应码本来就躺在 last_response 里，带上它，"删没删成"就不用猜。
        codes = {s.name: _status_code(s) for s in cleaners}
        deleted = [c for c in codes.values() if c is not None and (200 <= c < 300 or c == 404)]
        really_bad = [s for s in bad_clean
                      if _status_code(s) is None or not (200 <= _status_code(s) < 300
                                                         or _status_code(s) == 404)]
        dirty_runs.append({
            "code": sc.code, "scenario": sc.title,
            "cleanupStatus": {s.name: s.last_status for s in cleaners},
            "cleanupStatusCode": codes,
            "suspectedLeftovers": leftovers if really_bad else [],
            "deleteWith": [{"method": s.method, "url": s.url} for s in cleaners],
            "why": ("最后一次运行没跑成清理步骤 —— 那次造的东西大概还在。"
                    "上面的 id 取自创建步骤最后一次运行的响应，删它的请求就是清理步骤本身。"
                    if really_bad else
                    f"清理步骤被判 fail，但**请求其实成功了**"
                    f"（{'、'.join(str(c) for c in deleted)}）—— 判 fail 是断言的事，"
                    f"不是没删掉。所以这条大概**没有残留**，先去看那几条断言为什么不过。"),
            "requestsSucceeded": not really_bad,
        })

    # 用例编号 → 标题，方便人对上是谁的活
    codes = {sc.code for sc in scenarios}
    titles = {}
    if codes:
        rows = (await session.execute(
            select(Case.case_code, Case.title).join(Branch, Case.branch_id == Branch.id)
            .where(Branch.project_id == pid, Case.case_code.in_(codes))
        )).all()
        titles = dict(rows)
    for row in no_cleanup + dirty_runs:
        row["case"] = titles.get(row["code"])

    return {
        "scanned": len(scenarios),
        "noCleanupStep": no_cleanup,
        "lastRunLeftBehind": dirty_runs,
        "scope": "只看接口场景，且只看得见**最后一次运行**的响应 —— "
                 "更早的残留、UI 脚本造的数据、手工造的数据平台都没有记录。"
                 "报 0 条不等于环境是干净的。",
        "verdict": _verdict(no_cleanup, dirty_runs),
    }


def _verdict(no_cleanup: list[dict], dirty: list[dict]) -> str:
    parts = []
    if no_cleanup:
        parts.append(f"{len(no_cleanup)} 条链造了东西没有清理步骤（每跑一次留一份，"
                     f"堆多了会让 data[0]、满页分页那类断言时红时绿）："
                     + "、".join(x["code"] for x in no_cleanup[:6]))
    real = [x for x in dirty if not x.get("requestsSucceeded")]
    fake = [x for x in dirty if x.get("requestsSucceeded")]
    if real:
        parts.append(f"{len(real)} 条链最后一次没跑到清理，留下的 id 已列出："
                     + "、".join(x["code"] for x in real[:6]))
    if fake:
        parts.append(f"{len(fake)} 条链的清理步骤被判 fail 但**请求其实成功了**"
                     f"（2xx/404），大概没有残留，该查的是那几条断言："
                     + "、".join(x["code"] for x in fake[:6]))
    return "；".join(parts) or "没发现结构性漏清理，最后一次运行也都跑到了清理步骤。"
