"""S7.7 角色维度：产物层取并集 → 可见性矩阵 → 越权候选。

三件事写在一个模块里，因为它们共用同一个判定：**「没探到」不是「看不见」**。
拆开就会各写一份，迟早有一份把没探到算成看不见 —— 而那两种错的代价差一个量级：

- 「看不见」当成「没探到」 ⇒ 少一条候选，人工照旧能发现。
- 「没探到」当成「看不见」 ⇒ **凭空产出一条越权候选**，SEC 那边跑去查一个
  根本不存在的东西。查空两次，这份清单以后就没人看了。

浅扫只跑前 `SHALLOW_MAX_PAGES` 页（爬虫那边的硬上限），所以「这个角色在第 41 页
看不见任何东西」是**必然发生**的假象。它不是边角情况，是常态 —— 整个模块的
第三态就是为它存在的。

全是纯函数：零 IO、零模型。爬取在 `app/engine/surveys/qa_page_survey_crawl.py`。
"""
from __future__ import annotations

# 一格的三种取值。`UNPROBED` 是**独立第三态**，不是「暂时当看不见」。
VISIBLE = "visible"
INVISIBLE = "invisible"
UNPROBED = "unprobed"

# 越权候选的两个标记，**每条都带，不是页面上补一句**。
# 出口不止一个（页面、MCP、导出的文档），少带一个出口就会有人把候选当结论转出去。
BASIS = "由可见性矩阵推导，不是实测越权结果"
SEC_MARK = "SEC 域候选"

# 判成候选的两个门槛，**故意保守**（同 S7.1 的第 2 条：凭空造出来的缺口比漏掉的贵）。
# - 支撑：高权角色至少要多看见这么多个控件，才算得上「它本来就该是超集」。
# - 例外：超过这个数就不是「超集带几个例外」，那两个角色只是**菜单不同**，
#   硬报出来全是噪声。
_MIN_SUPPORT = 5
_MAX_EXCEPTIONS = 3


# ── 并集 ────────────────────────────────────────────────────────────────


def merge_shards(shards, *, main_role: str) -> list[dict]:
    """把各角色分片的账本行**并**成一份，`roles_visible` 是看得见它的角色全集。

    三条规矩，每一条都对应一种"看着没问题"的写法：

    1. **主爬角色自己也要进 `roles_visible`。** 只往里追加浅扫角色的话，
       一个只有主爬看得见的控件会落成 `roles_visible == []` ——
       和「一个角色都没探过它」在产物上一模一样，下游再也分不开。
    2. **只有浅扫角色看见的 key 要留下来。** 拿主爬那份当底、其余只做"标注"的话，
       这些行整个消失 —— 而「低权角色看得见、主爬（只读账号）看不见」
       恰恰是角色维度**唯一有价值的那个信号**。
    3. **同一个分片里重复的 key 不许合并。** 跨分片的同 key 才是并集要合的；
       在这里顺手去个重，「撞了」和「两个角色都看得见」就再也分不开 ——
       这一层看不见它们是不是同一页上并排的兄弟节点，分不清就不该替它做主。
       同页撞 key 该在**采集处**合（`qa_page_survey_crawl.dedupe_items`：
       那里看得见，而且合了会记 `anchorCollisions`）。真漏到这里的会撞
       `(survey_id, key)` 那条唯一约束 —— 那是最后一道，不是第一道。
    """
    ordered = ([s for s in shards or [] if (s.get("role") or "") == main_role]
               + [s for s in shards or [] if (s.get("role") or "") != main_role])
    rows: list[dict] = []
    by_key: dict[str, list[dict]] = {}
    for shard in ordered:
        role = (shard.get("role") or "").strip()
        fresh: set[str] = set()          # 本分片里已经开过行的 key
        for src in shard.get("items") or []:
            key = src.get("key") or ""
            if key in by_key and key not in fresh:
                for row in by_key[key]:
                    row["_roles"].add(role)
                    # 「点过」和「点了有反应」是**关于这个控件**的事实，不是关于
                    # 角色的。跨分片取并集 —— 不取的话，主爬那份（先进来的）
                    # 一个 `clicked=False` 就把别的角色刚点出来的证据盖掉了，
                    # 而这两件事在报告上都长成"G4 是空的"。
                    if src.get("clicked"):
                        row["clicked"] = True
                    if src.get("effect") and not row.get("effect"):
                        row["effect"] = src["effect"]
                continue
            row = dict(src)
            row["_roles"] = {r for r in src.get("roles_visible") or []} | (
                {role} if role else set())
            rows.append(row)
            by_key.setdefault(key, []).append(row)
            fresh.add(key)
    for row in rows:
        row["roles_visible"] = sorted(row.pop("_roles"))
    return rows


# ── 可见性矩阵 ──────────────────────────────────────────────────────────


def visibility_matrix(items, *, roles=None, probed_pages=None) -> dict:
    """`{key: {角色: visible/invisible/unprobed}}`。

    `probed_pages` = `{角色: 它这一趟真正走到的页面}`（爬虫账本的 `pagesProbed`）。
    **落到 `unprobed` 的三条路，全都不许算成「看不见」**：

    - 这个角色根本不在账本里 —— 没配凭证被跳过、登录没成、分片死了。
      三种都长成「它一个控件都没看见」，而那和「它被禁掉了所有功能」
      在数字上是同一个 0。
    - 这个角色的页面清单里没有这一页 —— 浅扫只跑前 40 页，**这是常态**。
    - 这一项没有页面路径，无从判断它那一页有没有被走到。

    `roles` 可以不传：账本里出现过的角色自动算进来。传了是为了让
    **一个控件都没看见的角色也出现在矩阵里** —— 不然它连同它那一列的
    「未探测」一起消失，报告上看不出这一趟少了个角色。
    """
    probed = {str(k): (None if v is None else {str(p) for p in v})
              for k, v in (probed_pages or {}).items()}
    observed = {str(r) for it in items or [] for r in (it.get("roles_visible") or [])}
    all_roles = sorted({str(r) for r in roles or [] if str(r).strip()} | observed)

    by_key: dict[str, dict] = {}
    for it in items or []:
        key = it.get("key") or ""
        ent = by_key.setdefault(key, {"pagePath": it.get("page_path") or "",
                                      "label": it.get("label") or "",
                                      "roles": {}})
        vis = {str(r) for r in it.get("roles_visible") or []}
        for role in all_roles:
            if role in vis:
                ent["roles"][role] = VISIBLE          # 看见了就是看见了，压过一切
                continue
            if ent["roles"].get(role) == VISIBLE:
                continue                              # 同 key 的另一行已经看见过
            pages = probed.get(role)
            page = ent["pagePath"]
            ent["roles"][role] = (
                INVISIBLE if (pages is not None and page and page in pages) else UNPROBED)

    counters = {VISIBLE: 0, INVISIBLE: 0, UNPROBED: 0}
    for ent in by_key.values():
        for state in ent["roles"].values():
            counters[state] = counters.get(state, 0) + 1
    # 一格都没探过的角色单独列出来：它在矩阵里是**整整一列的未探测**，
    # 只看总数看不出「少了个角色」和「到处都没探到」的区别。
    roles_unprobed = sorted(
        r for r in all_roles
        if by_key and all(ent["roles"].get(r) == UNPROBED for ent in by_key.values()))
    return {"byKey": by_key, "roles": all_roles, "counters": counters,
            "rolesUnprobed": roles_unprobed}


# ── 越权候选 ────────────────────────────────────────────────────────────


def overprivilege_candidates(matrix, *, min_support: int = _MIN_SUPPORT,
                             max_exceptions: int = _MAX_EXCEPTIONS) -> dict:
    """从矩阵里推出**越权候选**。注意是候选，不是结论。

    没有任何一份输入告诉我们「哪个角色本来该看见什么」—— 那是被测系统的权限设计，
    我们手上只有观测。所以判据只能是**观测到的包含关系被自己打破**：

    > 在两边都探过的格子上，`high` 看见的比 `low` 多出一大片（说明它本来就是超集），
    > 却恰好有一两个控件是 `low` 看得见而 `high` 看不见的。

    那一两个就是候选。它也完全可能是正常的角色专属菜单（审计角色专属页就是这样），
    所以每条都带 `BASIS`，并且**不进枚举产物** —— 混进 items 里就会被当成
    页面事实往下游传，而它是一个推断。

    `unprobed` 的格子**整格排除在比较之外**，不是当成"看不见"参与运算：
    浅扫的 40 页上限会让每个浅扫角色在后面的页上"什么都看不见"，
    照那么算，每一趟都能吐出成百条假候选。
    """
    by_key = matrix.get("byKey") or {}
    roles = list(matrix.get("roles") or [])
    cands: list[dict] = []
    pairs = 0
    for low in roles:
        for high in roles:
            if low == high:
                continue
            comparable = [k for k, e in by_key.items()
                          if e["roles"].get(low) != UNPROBED
                          and e["roles"].get(high) != UNPROBED]
            if not comparable:
                continue
            pairs += 1
            seen_low = {k for k in comparable if by_key[k]["roles"].get(low) == VISIBLE}
            seen_high = {k for k in comparable if by_key[k]["roles"].get(high) == VISIBLE}
            exceptions = sorted(seen_low - seen_high)
            support = seen_high - seen_low
            if not exceptions or len(exceptions) > max_exceptions or len(support) < min_support:
                continue
            for key in exceptions:
                ent = by_key[key]
                cands.append({"key": key, "pagePath": ent["pagePath"],
                              "label": ent["label"], "role": low, "supersetRole": high,
                              "support": len(support), "comparable": len(comparable),
                              "mark": SEC_MARK, "basis": BASIS})
    cands.sort(key=lambda c: (c["pagePath"], c["key"], c["role"], c["supersetRole"]))

    cnt = matrix.get("counters") or {}
    counters = {"candidates": len(cands), "pairsCompared": pairs,
                "cellsComparable": cnt.get(VISIBLE, 0) + cnt.get(INVISIBLE, 0),
                "cellsUnprobed": cnt.get(UNPROBED, 0)}
    return {"candidates": cands, "counters": counters, "basis": BASIS,
            "rolesUnprobed": list(matrix.get("rolesUnprobed") or []),
            "summary": _summary(counters, matrix.get("rolesUnprobed") or [])}


def _summary(counters: dict, roles_unprobed: list) -> str:
    """一句话。**永远不说「没有越权漏洞」** —— 那是最毒的一种假绿。

    这份东西看的是「哪些控件在页面上露给了谁」，它连一次越权请求都没发过。
    候选为 0 只说明**这一趟的观测里没撞见那个形状**，和"系统没有越权"之间
    隔着整个未探测的部分。所以计数（尤其是未探测那个）跟结论**焊在同一句话里**：
    单独一个「0 条候选」被截出去，就变成了一个绿勾。
    """
    head = ("本轮没有观测到越权候选" if not counters["candidates"]
            else f"越权候选 {counters['candidates']} 条")
    miss = (f"；这一趟一格都没探到的角色：{'/'.join(roles_unprobed)}"
            if roles_unprobed else "")
    return (f"{head}（可比 {counters['cellsComparable']} 格 / "
            f"未探测 {counters['cellsUnprobed']} 格，比了 {counters['pairsCompared']} 对角色）。"
            f"候选{BASIS}；未探测的格子不参与推导，"
            f"本轮观测覆盖不到的地方这份清单什么都没说{miss}。")
