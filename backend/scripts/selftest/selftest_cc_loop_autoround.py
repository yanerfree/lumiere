"""活体自测 · CC 自动一轮的四处改动（2026-08-24）

对着**真库**跑，不造假数据、不 mock。四段各自独立，哪段挂了说哪段。

  ① 证据包两维都收 —— 接口场景的失败此前拿不到 run_id/现象，归因链是断的
  ② 抽检按哈希 —— 同一条用例反复提交，抽中与否必须每次一样
  ③ 待确认队列只列真在等人的 —— 自证放行的混进来，人就不看了
  ④ 一致率把抽检和人主动确认分开算 —— 后者有选择偏差

用法：
    cd backend && .venv/bin/python scripts/selftest/selftest_cc_loop_autoround.py

只读。不写任何一行库（唯一的例外是 ② 用 uuid 算哈希，压根不碰库）。
"""
from __future__ import annotations

import asyncio
import uuid

FAILS: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {label}" + (f" —— {detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


async def seg1_evidence(session) -> None:
    """① 接口场景的失败拿不拿得到证据包。"""
    from sqlalchemy import text

    from app.mcp.tools.ui_scripts import get_ui_script_result

    print("\n① 证据包：UI 和接口都收")
    # 找一条**只有接口执行、没有 UI 执行**的失败用例 —— 旧代码在这种上必然返回 None
    row = (await session.execute(text("""
        select r.case_id, c.case_code
        from script_runs r join cases c on c.id = r.case_id
        where r.script_type = 'api' and r.status <> 'passed'
          and not exists (select 1 from script_runs u
                          where u.case_id = r.case_id and u.script_type = 'ui')
        order by r.created_at desc limit 1"""))).first()
    if row is None:
        print("     （库里没有「只有接口执行且失败」的用例，这段跳过）")
        return
    case_id, code = row
    dist = list(await session.execute(text(
        "select script_type, count(*) from script_runs where case_id=:c group by 1"),
        {"c": str(case_id)}))
    ui_n = sum(n for t, n in dist if t == "ui")
    print(f"     样本 {code}，执行记录 {dist}")
    check(ui_n == 0, "样本确实没有 UI 执行",
          f"所以旧代码（过滤 script_type=='ui'）必然返回 last_run=None")

    out = await get_ui_script_result(case_id=str(case_id), session=session)
    lr = out.get("last_run") or {}
    check(bool(lr), "证据包拿到了 last_run")
    check(lr.get("script_type") == "api", "返回里说清了这次是哪一维", lr.get("script_type"))
    check(bool(lr.get("run_id")), "带 run_id（tb_submit_analysis 的必填入参）")

    # **归因要的是失败那一次，不是最近那一次。** 这条用例可能已经被复跑过 ——
    # 活体第一次跑就撞到：6 次接口执行、最近一次 passed，证据包里什么指针都写不出来，
    # 而 tb_submit_analysis 又拒收 passed 的执行。所以按 run_id 钉住那一次。
    fail_rid = (await session.execute(text(
        "select id from script_runs where case_id=:c and status<>'passed' "
        "order by created_at desc limit 1"), {"c": str(case_id)})).scalar_one()
    pinned = await get_ui_script_result(case_id=str(case_id), run_id=str(fail_rid),
                                       session=session)
    pl = pinned.get("last_run") or {}
    check(pl.get("run_id") == str(fail_rid), "按 run_id 能钉住失败那一次")
    check((pl.get("status") or "") != "passed", "钉住的是失败的执行", pl.get("status"))
    usable = [k for k in ("error_summary", "stdout") if pl.get(k)]
    check(bool(usable), "失败那次至少有一种 evidence 指针可写",
          "/".join(usable) or "一个都没有")
    if (lr.get("status") or "") == "passed":
        check(bool(lr.get("note")), "最近一次是 passed 时，返回里明说没有失败证据可归因")

    # 张冠李戴要拦住：拿别的用例的 run 去要证据
    other = (await session.execute(text(
        "select id from script_runs where case_id<>:c limit 1"),
        {"c": str(case_id)})).scalar_one_or_none()
    if other:
        cross = await get_ui_script_result(case_id=str(case_id), run_id=str(other),
                                          session=session)
        check("error" in cross, "别的用例的 run_id 被拒",
              str(cross.get("error"))[:50])

    # 显式指定另一维时不该串台
    only_ui = await get_ui_script_result(case_id=str(case_id), script_type="ui",
                                        session=session)
    check((only_ui.get("last_run") is None), "显式要 ui 时不把 api 的塞回来")
    bad = await get_ui_script_result(case_id=str(case_id), script_type="e2e",
                                    session=session)
    check("error" in bad, "非法 script_type 被拒", str(bad.get("error"))[:40])


def seg2_sampling() -> None:
    """② 抽检按哈希，不按随机。"""
    from app.services.analysis_service import SAMPLE_EVERY, sampled

    print("\n② 抽检：按哈希、可复现、比例不偏")
    ids = [uuid.uuid4() for _ in range(3000)]
    check(all(sampled(i) is sampled(i) for i in ids[:200]),
          "同一条用例反复问，结果不变")
    hits = sum(1 for i in ids if sampled(i))
    rate = hits / len(ids) * 100
    check(6.0 <= rate <= 14.0, f"抽中率 {rate:.1f}%（目标 {100/SAMPLE_EVERY:.0f}%）")
    # 字符串和 UUID 两种入参要一致 —— submit 传的是 run.case_id（UUID）
    one = ids[0]
    check(sampled(one) == sampled(str(one)), "UUID 和字符串入参结果一致")


async def seg3_pending(session) -> None:
    """③ 待确认队列只列真在等人的。"""
    from sqlalchemy import text

    from app.mcp.tools.analysis import list_pending_confirm
    from app.services.analysis_service import WAITING_ON_HUMAN

    print("\n③ 待确认队列：只列真在等人的")
    total = (await session.execute(text(
        "select count(*) from script_runs "
        "where cc_analysis is not null and confirmed_cause is null"))).scalar_one()
    default = await list_pending_confirm(session=session, limit=200)
    allof = await list_pending_confirm(session=session, limit=200,
                                       include_self_serve=True)
    print(f"     库里未确认的归因共 {total} 条")
    print(f"     默认列出 {default['total']} 条；include_self_serve=true 列出 {allof['total']} 条")
    check(allof["total"] <= total, "全量不超过库里未确认总数")
    check(default["total"] <= allof["total"], "默认是全量的子集")
    routes = {p.get("route") for p in default["pending"]}
    stray = routes - set(WAITING_ON_HUMAN) - {None}
    check(not stray, "默认结果里没有自证放行的", f"混进来的: {stray}" if stray else "")
    check(all("route" in p for p in default["pending"]), "每条都带 route，人能分辨要不要自己动")


async def seg4_stats(session) -> None:
    """④ 一致率把抽检和人主动确认分开算。"""
    from app.services.analysis_service import agreement_stats

    print("\n④ 一致率：抽检 vs 人主动确认分开")
    st = await agreement_stats(session)
    check("bySource" in st, "返回里有 bySource")
    srcs = {r["source"] for r in st.get("bySource", [])}
    check(srcs == {"sampled", "other"}, "两种来源都在", str(srcs))
    check(isinstance(st.get("byCause"), list), "byCause 仍是数组（过 camelCase 中间件不会被改键）")
    for r in st.get("bySource", []):
        if r["confirmed"] < 5:
            check(r["agreementRate"] is None,
                  f"{r['source']} 样本 {r['confirmed']}<5 时不给百分比")
    print(f"     当前：归因 {st['totalAnalyses']} 条，已确认 {st['confirmed']}，"
          f"待确认 {st['pending']}")


async def main() -> int:
    from app.deps.db import async_session_factory

    import app
    print("代码树:", app.__file__.replace("/app/__init__.py", ""))
    seg2_sampling()
    async with async_session_factory() as s:
        await seg1_evidence(s)
        await seg3_pending(s)
        await seg4_stats(s)
    print("\n" + ("全部通过" if not FAILS else f"有 {len(FAILS)} 条没过: {FAILS}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
