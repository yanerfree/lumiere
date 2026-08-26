"""活体自测 · CC 自己入队批量审核（lum_review_batch，2026-08-24）

**为什么要有这个工具**：`lum_review_case` 是直调、一条也不排队。CC 推一批时
逐条调 = 并发真跑打同一个环境，而队列要防的两件事一件都吃不到：
同环境串行（防假打回）、连续环境失败熔断。

**这个脚本的血溅范围是刻意压住的**：
  · 自己建一个一次性分支 + 两条用例，**故意不挂脚本、不挂接口场景** ——
    于是 reviewer 的 `_run_and_diff` 只会记一句"没得跑"，**全程不碰被测系统**
  · 跑完把建的东西全删掉（分支/用例/批次/轮次，以及"为了过环境校验临时补的
    BASE_URL"）
  · 代价是每条用例一次 LLM 调用（两条，很短）

用法：
    cd backend && .venv/bin/python scripts/selftest/selftest_review_batch_queue.py
"""
from __future__ import annotations

import asyncio
import uuid

# 靶子项目：要求「已有带 BASE_URL 的环境」（否则得临时造，那是额外的血溅面）。
# 用例照旧建在**自己新开的一次性分支**里，跑完整支删掉，碰不到它原有的用例。
PROJECT_NAME = "测试平台"
FAILS: list[str] = []
MADE: dict = {"branch": None, "cases": [], "batches": [], "env_var": None}


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {label}" + (f" —— {detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


async def setup(s) -> tuple:
    """建一次性数据。**用 ORM 不用手写 SQL** —— 第一版手写 insert 猜错了列名
    （branches 没有 is_active），而猜错列名这件事和被测代码一点关系都没有。"""
    from sqlalchemy import select

    from app.mcp.tools.review import review_batch
    from app.models.case import Case
    from app.models.environment import Environment, EnvironmentVariable
    from app.models.project import Branch, Project

    pid = (await s.execute(
        select(Project.id).where(Project.name == PROJECT_NAME))).scalar_one()

    br = Branch(project_id=pid, name=f"selftest-autoround-{uuid.uuid4().hex[:6]}")
    s.add(br)
    await s.commit()
    MADE["branch"] = br.id

    # ① 一条用例都没有时要明确报错，不是静默建个空批次
    r = await review_batch(session=s, branch_id=str(br.id))
    check("error" in r, "没用例时明确报错，不是静默建个空批次", str(r.get("error"))[:40])

    # ② 补一个带 BASE_URL 的环境（新项目的默认环境**故意**不带变量，见 project_defaults）
    env = (await s.execute(
        select(Environment).where(Environment.project_id == pid)
        .order_by(Environment.sort_order).limit(1))).scalars().first()
    if env is None:
        raise SystemExit("这个项目连环境都没有，换个靶子项目")
    has = (await s.execute(
        select(EnvironmentVariable.id)
        .where(EnvironmentVariable.environment_id == env.id,
               EnvironmentVariable.key == "BASE_URL"))).scalars().first()
    if has is None:
        # 指向一个没人监听的端口 —— 反正这两条没脚本没场景，跑不到网络那一层
        v = EnvironmentVariable(environment_id=env.id, key="BASE_URL",
                                value="http://127.0.0.1:9")
        s.add(v)
        await s.commit()
        MADE["env_var"] = v.id
        print("     （临时给环境补了个 BASE_URL，跑完删掉）")

    # ③ 两条一次性用例：**不挂脚本、不挂接口场景** → reviewer 不会执行任何东西
    for i in (1, 2):
        c = Case(branch_id=br.id, case_code=f"TC-ZSELF-{i:05d}",
                 title=f"一次性自测用例 {i}（跑完就删）", type="api",
                 priority="P2", steps=[], source="manual")
        s.add(c)
        await s.flush()
        MADE["cases"].append(c.id)
    await s.commit()
    return pid, br.id


async def run_checks(s, bid) -> None:
    from sqlalchemy import text

    from app.mcp.tools.review import review_batch, review_batch_status

    print("\n入队")
    r = await review_batch(session=s, branch_id=str(bid))
    check("error" not in r, "入队成功", str(r.get("error") or "")[:60])
    if "error" in r:
        return
    batch_id = r["batchId"]
    MADE["batches"].append(uuid.UUID(batch_id))
    check(r["total"] == 2, "两条都排上了", f"total={r['total']}")
    check(r["kind"] == "module_full", "类型按「一条没勾」推成模块全量", r["kind"])
    check(bool(r.get("environment")), "环境跟着批次落库了", r.get("environment"))

    row = (await s.execute(text(
        "select actor, actor_kind, status from review_batches where id=:i"),
        {"i": batch_id})).first()
    check(row[1] == "cc", "落库的 actor_kind 是 cc（这样人发起的才排得到前面）", str(row))

    # 同一条再排一遍 → 该被合并，不能跑两遍（两次真跑结论可能不一样）
    r2 = await review_batch(session=s, branch_id=str(bid))
    if "error" not in r2:
        MADE["batches"].append(uuid.UUID(r2["batchId"]))
        check(bool(r2.get("merged")), "重复入队被合并，不重复跑",
              f"merged={r2.get('merged')}")

    # 人发起的要排在 CC 前面 —— 用 _claim_next 的同一个 ORDER BY 只读复核
    order = list(await s.execute(text("""
        select actor_kind from review_batches
        where status='queued'
        order by (actor_kind <> 'human'), created_at""")))
    if len(order) >= 2 and {o[0] for o in order} >= {"human", "cc"}:
        first_cc = next(i for i, o in enumerate(order) if o[0] == "cc")
        last_human = max(i for i, o in enumerate(order) if o[0] == "human")
        check(last_human < first_cc, "排队顺序里人全在 CC 之前")
    else:
        print("     （当前队列里没有同时存在 human 和 cc 的排队项，顺序这条跳过）")

    print("\n等这批跑完（worker 在同一个事件循环里）")
    last = None
    max_running = 0          # ← 这才是「同环境串行」的直接证据
    seen_order: list[str] = []
    # AI 网关 429 时每条要退避重试 3 轮（30s 起），所以窗口给足
    for _ in range(200):
        st = await review_batch_status(session=s, batch_id=batch_id)
        running = [i for i in (st.get("items") or []) if i["status"] == "running"]
        max_running = max(max_running, len(running))
        if running and running[0]["caseCode"] not in seen_order:
            seen_order.append(running[0]["caseCode"])
        cur = (st.get("status"), st.get("done"), st.get("current"))
        if cur != last:
            print(f"     status={cur[0]} done={cur[1]}/{st.get('total')} 当前={cur[2]}")
            last = cur
        if st.get("finished"):
            break
        await asyncio.sleep(3)

    st = await review_batch_status(session=s, batch_id=batch_id)
    items = st.get("items") or []
    for it in items:
        print(f"     {it['caseCode']}: status={it['status']} verdict={it['verdict']} "
              f"runState={it['runState']} err={(it.get('error') or '')[:50]}")

    # ★ 主判据：**同一时刻最多一条在跑**。这就是逐条调 lum_review_case 拿不到的东西。
    check(max_running <= 1, "同环境串行：同一时刻只有一条在跑",
          f"观察到最多 {max_running} 条同时 running；顺序 {seen_order}")
    check(len(items) == 2, "逐条结果都在")
    # 这两条没脚本没场景 → 不可能是 approved（review-spec §9：没真跑成功不得通过）
    check(all(i["verdict"] != "approved" for i in items),
          "没脚本没场景的不会被判通过（没真跑成功不得 approved）")

    # 终态判定：网关限流是**环境状态**，不算这个功能的缺陷，分开报
    throttled = any("429" in (i.get("error") or "") or
                    "rate_limit" in (i.get("error") or "") for i in items)
    if st.get("finished"):
        check(True, "批次跑到了终态", str(st.get("status")))
        check(st.get("done") == st.get("total"), "每条都处理过了",
              f"{st.get('done')}/{st.get('total')}")
    elif throttled:
        print("  ⚠️  AI 网关正在 429 限流，这批没跑到终态 —— "
              "**队列机制（串行/合并/落库）已经验到了**，终态这条留给限流缓解后复跑")
    else:
        check(False, "批次跑到了终态", str(st.get("status")))


async def cleanup(s) -> None:
    from sqlalchemy import text

    print("\n清理")
    # setup 半路挂掉时事务是坏的，不先回滚清理语句一条都执行不了 ——
    # 第一版就这么留了个空分支在库里。
    await s.rollback()
    # 顺手收掉历次跑挂留下的残骸，别越攒越多
    await s.execute(text(
        "delete from cases where case_code like 'TC-ZSELF-%'"))
    await s.execute(text(
        "delete from branches where name like 'selftest-autoround-%' "
        "and id not in (select distinct branch_id from cases)"))
    await s.execute(text(
        "delete from branches where name like 'selftest-noenv-%' "
        "and id not in (select distinct branch_id from cases)"))
    for b in MADE["batches"]:
        await s.execute(text("delete from review_batch_items where batch_id=:i"), {"i": b})
        await s.execute(text("delete from review_batches where id=:i"), {"i": b})
    for c in MADE["cases"]:
        await s.execute(text("delete from case_review_rounds where case_id=:i"), {"i": c})
        await s.execute(text("delete from cases where id=:i"), {"i": c})
    if MADE["env_var"]:
        await s.execute(text("delete from environment_variables where id=:i"),
                        {"i": MADE["env_var"]})
    if MADE["branch"]:
        await s.execute(text("delete from branches where id=:i"), {"i": MADE["branch"]})
    await s.commit()
    left = (await s.execute(text(
        "select count(*) from cases where case_code like 'TC-ZSELF-%'"))).scalar_one()
    check(left == 0, "自测数据清干净了", f"还剩 {left} 条")


async def main() -> int:
    from app.deps.db import async_session_factory

    import app
    print("代码树:", app.__file__.replace("/app/__init__.py", ""))
    async with async_session_factory() as s:
        try:
            _pid, bid = await setup(s)
            await run_checks(s, bid)
        finally:
            try:
                await cleanup(s)
            except Exception as e:  # noqa: BLE001
                print("  ❌ 清理失败，需要手工收尾:", str(e)[:200])
                FAILS.append("清理")
    print("\n" + ("全部通过" if not FAILS else f"有 {len(FAILS)} 条没过: {FAILS}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
