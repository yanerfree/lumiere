"""这个域的**功能地图**：每点一步落四样（规则 / 状态 / 动作面 / 结构），
再把「看到了多少」和「走通了多少」**分成两个数**。

需求在 `docs/qa-domain-live-verification-plan.md` §14 + §15。两句原话各否掉一件事：

> 「探索测试……当然不是只看反应啊，你要看业务逻辑呀」  → 否掉「点完只记有没有反应」
> 「业务不只是增删改查」                                → 否掉「一条 建→改→删 就算探完」
> 「可以不全部测试到位，但是必须把页面上所有的功能都看到」→ 广度必须满、深度可打折

**这里零 IO、零模型、零业务名词。** 和 `qa_directed_chain` 一个立场：
判据必须换个产品、换个语言照样成立，所以：

· 动作面**不靠动作词清单去认**（清单必然漏，而且换产品就烂）。
  页面这一半只问「能点的地方在哪、属于哪一行/哪一层、灰没灰」；
  脚本那一半的动作词由 `qa_business_actions.action_verb` 从**路径末段**数出来。
· 两边对账**用端点当连接键，不用名字**。理由见 `pair_actions` 的注释 ——
  拿「submit ↔ 提交」这种表去连，等于给每个产品/每种语言各维护一张表，
  而漏一条的表现是**凭空多一条缺口**，不是少一条。
· 认不出来的一律落 `unknown` 并**留原文**，不猜。
"""
from __future__ import annotations

from app.services.qa_business_actions import (
    CRUD_BY_METHOD,
    READ_METHODS,
    action_verb,
)
from app.services.qa_survey_guard import word_hit

# ── §14.4 提示就是规则 ───────────────────────────────────────────────────
#
# 点下去没成，页面给的回答**分三类，三类都不算失败**（§14.4）：
# 约束 / 这一步归别人 / 这个状态下不许这么干。
# 只有「点了、没反应、也没给提示」才是断点。
#
# ⚠ 这几个词是**语言资产，不是产品资产**：「不能为空」「已存在」是中文 UI 的
# 通用说法，换个产品照样是这几句；换个语言就加一组词，不用改判据。
# 认不出的落 `unknown` 且**原文照留** —— 压成 pass/fail 之后，
# 下一趟没人知道当初触发的是哪条约束（§14.1 那条 ⚠）。
HINT_KINDS: dict[str, dict] = {
    "constraint": {"label": "约束",
                   "why": "这个功能的规则：必填 / 格式 / 唯一 / 上限。"
                          "**它是业务逻辑的一部分**，不是我们填错了"},
    "permission": {"label": "这一步归别人",
                   "why": "当前角色点不动。接 §13.5：403 是路标 —— "
                          "它说明这一环在业务上属于另一个角色"},
    "state_edge": {"label": "状态机的一条边",
                   "why": "这个状态下不许这么干。**这是状态机上的一条边**，"
                          "记下来比记「点失败了」值钱得多"},
    "unknown": {"label": "认不出来",
                "why": "页面给了话但我们归不了类。**原文照留** —— "
                       "归不了类不等于没信息，人一眼就看得出来"},
}

_CONSTRAINT_WORDS = ("不能为空", "必填", "请输入", "请选择", "格式不对", "格式错误",
                     "格式不正确", "已存在", "已被占用", "重复", "不合法", "无效",
                     "超过", "不得超过", "长度", "至少", "最多", "范围",
                     "required", "invalid", "already exists", "duplicate",
                     "too long", "too short", "must be")
_PERMISSION_WORDS = ("无权限", "没有权限", "权限不足", "禁止访问", "不允许访问",
                     "未授权", "forbidden", "unauthorized", "permission denied",
                     "access denied")
_STATE_WORDS = ("当前状态", "状态不允许", "不允许此操作", "该状态", "此状态",
                "已被审批", "已完成", "已结束", "不可修改", "不可编辑", "不可删除",
                "not allowed in", "invalid state", "cannot be modified")


def classify_hint(text: str, status=None) -> str:
    """一句提示归成哪一类。**先看状态码再看文案** —— 403 是硬事实，
    文案是软证据；反过来的话「无权限」这三个字出现在一条 200 的业务提示里
    （"你没权限改这一项，请联系管理员"）就会被记成一条假的权限边界。
    """
    if isinstance(status, int) and status in (401, 403):
        return "permission"
    s = (text or "").strip()
    if not s:
        return ""            # 空 = 页面什么都没说，那是 G4 的料，不是一类提示
    # 匹配规则借 `word_hit`（ASCII 认词边界、中文认子串）—— **不许再写一套**：
    # 裸 `in` 会让 "must be" 命中 "must been"、"invalid" 命中 "invalidate"，
    # 而多认一句提示的表现是把一条断点（G4）洗成一条"规则"，最不该错的方向。
    low = s.lower()
    for word in _PERMISSION_WORDS:
        if word_hit(low, word):
            return "permission"
    for word in _STATE_WORDS:
        if word_hit(low, word):
            return "state_edge"
    for word in _CONSTRAINT_WORDS:
        if word_hit(low, word):
            return "constraint"
    return "unknown"


def read_rules(fields, hints) -> dict:
    """§14.1 的「规则」那一格：必填标记 + 提示**原文**。

    `fields` 是 `_COLLECT_JS` 抓的字段行；`hints` 是
    `[{"text": 原文, "status": 状态码或 None, "where": 哪一步}]`。

    **原文一个字都不许压。** 提示原文就是规则本身，压成 pass/fail 之后
    这条约束下一趟就查不回来了。
    """
    required = [(f.get("label") or "").strip() for f in (fields or [])
                if f.get("required")]
    rows: list[dict] = []
    counts = {k: 0 for k in HINT_KINDS}
    for h in hints or []:
        text = (h.get("text") or "").strip()
        if not text:
            continue
        kind = classify_hint(text, h.get("status")) or "unknown"
        counts[kind] = counts.get(kind, 0) + 1
        rows.append({"kind": kind, "text": text[:300],
                     "status": h.get("status"), "where": h.get("where") or ""})
    return {"requiredFields": [r for r in required if r],
            # 有必填标记这件事本身要能看出来：一个都没有**不等于没有必填**，
            # 而是这个产品用样式类标必填（antd 就是），我们读不到。
            "requiredMarksSeen": bool(required),
            "hints": rows, "hintKinds": counts}


# ── §14.2 动作面 ─────────────────────────────────────────────────────────
#
# 「能点的地方」不止表格上方那排按钮。**欠的是归类，不是抓取**（§14.2）——
# 所以这里只做归类，抓取还在 `_COLLECT_JS` 那边。
WHERE_KINDS: dict[str, str] = {
    "page": "页面上（表格上方那排、筛选区）",
    "row": "行内动作列 / 行末「更多」下拉",
    "batch": "勾选之后才冒出来的批量条",
    "layer": "弹层里（含次级按钮）",
    "detail": "详情页上",
    "tab": "详情页某个页签里",
}


def snapshot_actions(items, *, where: str, state: str = "") -> list[dict]:
    """把一次枚举的结果，落成一批「动作面」行。

    `where` 由**调用方**给（它才知道自己刚在哪个范围里枚举的：行内 / 层内 /
    详情页）—— 让这里去反推 `where` 就得认产品的 DOM 结构，
    那是换个 UI 库就烂掉的判据。

    `state` 是**当时**我们那一行的状态文本。带着它，同一个按钮在两种状态下
    的灰/亮差异就能被看出来 —— 那正是状态机的一条边（见 `merge_surface`）。
    """
    if where not in WHERE_KINDS:
        raise ValueError(f"不是动作面的位置：{where}")
    out: list[dict] = []
    for raw in items or []:
        label = (raw.get("label") or "").strip()
        role = (raw.get("role") or "").strip()
        if not label:
            continue
        out.append({
            "where": where, "label": label, "role": role,
            "anchor": raw.get("testid") or raw.get("id") or label,
            # `disabled` 缺键 ⇒ 当**没读到**，不当"是亮的"：读不到和亮着
            # 在页面上长得一样，而前者会让「入口禁用」这条规则整个消失。
            "enabled": (None if raw.get("disabled") is None
                        else not raw.get("disabled")),
            "state": state,
        })
    return out


def merge_surface(snapshots) -> dict:
    """把多次快照合成**这个域的动作清单**（§14.5 那份「功能地图」）。

    去重键是 `(where, label)`：同一个「删除」在行内和批量条上是**两个动作**
    （谁能点、点完影响几条都不同），合成一条会把批量那条藏起来。

    两个只有合起来才看得出来的信号：

    · `stateEdges` —— 同一个按钮在 A 状态下是亮的、B 状态下是灰的。
      **这是状态机的一条边**，而且是页面自己说出来的，不用猜。
    · `enabledUnknown` —— 灰没灰压根读不到的那些。0 也渲染：
      读不到和"全是亮的"混在一起，会让「入口禁用」这一类规则整个消失。
    """
    rows: dict[tuple, dict] = {}
    for snap in snapshots or []:
        for a in snap or []:
            key = (a["where"], a["label"])
            row = rows.setdefault(key, {
                "where": a["where"], "label": a["label"], "role": a["role"],
                "anchor": a["anchor"], "seen": 0,
                "enabledIn": [], "disabledIn": [], "enabledUnknown": 0,
            })
            row["seen"] += 1
            st = a.get("state") or ""
            if a.get("enabled") is True:
                if st not in row["enabledIn"]:
                    row["enabledIn"].append(st)
            elif a.get("enabled") is False:
                if st not in row["disabledIn"]:
                    row["disabledIn"].append(st)
            else:
                row["enabledUnknown"] += 1
    actions = sorted(rows.values(), key=lambda r: (r["where"], r["label"]))
    edges = [{"label": r["label"], "where": r["where"],
              "enabledIn": r["enabledIn"], "disabledIn": r["disabledIn"]}
             for r in actions if r["enabledIn"] and r["disabledIn"]]
    by_where = {k: 0 for k in WHERE_KINDS}
    for r in actions:
        by_where[r["where"]] = by_where.get(r["where"], 0) + 1
    return {
        "actions": actions,
        # **每个位置都渲染，0 也渲染**：`batch` 常年 0 说明我们一次都没勾过行，
        # 那是欠账（§15.2 那一层"不勾选就不出现"的功能），不是"这个产品没有批量"。
        "actionsByWhere": by_where,
        "actionsTotal": len(actions),
        "stateEdges": edges,
        "enabledUnknown": sum(r["enabledUnknown"] for r in actions),
    }


def _verb_of(method: str, path: str, readable) -> str:
    """`(method, path)` → 动作词，**只留"动作面"上的那些**。

    去掉的只有一档：`kind == "crud"` 且方法是读的 —— 那是列表页和详情页
    自己的 GET（`/x`、`/x/{}`），任何页面打开就会发，拿它去连两边等于
    把「他有没有打开过这个页面」当成「他测过这个动作」。
    `subread`（`GET /x/export` 那种）**留着** —— 页面上「导出」是个真按钮，
    §14.2 原话：丢了就等于"这个域没有导出功能"。

    ⚠ 代价说清：带部署前缀的列表读（`GET /api/orders`）在 `action_verb` 那边
    算 `subread`（末段不是 id、又不止一段），于是也会留下来。**不去修它** ——
    两边走的是同一个函数，它在脚本那一半和页面那一半会同时留下，
    连得上、不产缺口；反过来在这儿多加一条"像列表就丢掉"的判据，
    丢的会是真的导出按钮，而那种错不报错。
    """
    v = action_verb(method, path, readable)
    verb = v.get("verb") or ""
    if not verb:
        return ""
    if v.get("kind") == "crud" and (method or "").upper() in READ_METHODS:
        return ""
    return verb


# 读的那个中性译名（今天是 `read`）。**从 `CRUD_BY_METHOD` 里取，别写字面量** ——
# 那边改了名字，这边写死的话不会报错，只会静默多报一条"他没测 read"。
_READ_VERBS = frozenset(CRUD_BY_METHOD[m] for m in READ_METHODS
                        if m in CRUD_BY_METHOD)


def script_verbs_of(business_actions: dict | None) -> dict:
    """`gaps["businessActions"]` → `{动作词: [端点]}`，喂给 `pair_actions`。

    对账那边已经按域把动作面算好了（`verb_inventory`），这儿只是拍平 ——
    **不重算一遍**：两份算法哪天分叉，表现是页面上凭空多出一批缺口。

    三档都收（`actions` / `crud` / `subreads`），只丢掉 `read` 那一个词 ——
    列表和详情自己的 GET，页面那一半在 `_verb_of` 里也是丢掉的。
    两边丢的必须是同一档，否则少的那边会变成一条假缺口。
    """
    out: dict[str, list[str]] = {}
    for buckets in (business_actions or {}).values():
        for bucket in ("actions", "crud", "subreads"):
            for verb, eps in ((buckets or {}).get(bucket) or {}).items():
                if not verb or verb in _READ_VERBS:
                    continue
                cur = out.setdefault(verb, [])
                for ep in eps or []:
                    if ep not in cur:
                        cur.append(ep)
    return out


def attach_control_endpoints(surface: dict, items) -> dict:
    """把**控件级 P 边**那一列（item 的 `endpoints`）接到功能地图的动作行上。

    这是 §14.2「页面那一半」的最后一段路：动作面是链路枚举出来的，
    「这个按钮发了什么」却记在 item 那一列上，两边**同一个 anchor**、
    却从没接起来过 —— 不接的话 `pageVerbs` 恒为空，于是
    `paired: False` 永远成立，页面上会一直挂着「一条都没连上」。

    连接键是 **anchor**（`testid` / `id` / 兜底文案，两边同一个原始控件产出）。
    `page_path` **不进键**：功能地图是**这个域**的地图，同一个「删除」在
    列表页和详情页上是同一个动作词，而对账只按动作词进行 ——
    掺进页面路径只会让「详情页那个删除」凭空多出一条缺口。

    两种「没有边」都**一个字都不写**（`endpoints` 留着不设）：

    · `None` = 那一行**没点过**。写空列表等于替它宣布「点了没发请求」。
    · `[]`   = 点过、确实没发请求。那是 G4 那一列的料，不是动作端点，
      写进来会让这个按钮被当成"连上了、只是没有端点"。
    """
    idx: dict[str, list[dict]] = {}
    for it in items or []:
        eps = it.get("endpoints")
        if not eps:                     # None（没点过）和 [] 都不写
            continue
        anchor = (it.get("anchor") or it.get("label") or "").strip()
        if not anchor:
            continue
        cur = idx.setdefault(anchor, [])
        for ep in eps:
            method = (ep.get("method") or "").upper()
            path = ep.get("path") or ""
            if not path:
                continue
            row = {"method": method, "path": path}
            if row not in cur:
                cur.append(row)
    for a in (surface or {}).get("actions") or []:
        got = idx.get((a.get("anchor") or "").strip())
        if got:
            a["endpoints"] = list(got)
    return surface


def pair_actions(surface: dict, hits, *, readable_paths=None,
                 script_verbs: dict | None = None) -> dict:
    """§14.2 的两边一拼。**连接键是端点，不是名字。**

    为什么不拿名字连：那要一张「submit ↔ 提交」的对照表，
    等于给每个产品、每种语言各维护一张 —— 而漏一条的表现是
    **凭空多报一条缺口**（页面上明明有那个按钮），不是少报一条。
    端点这条键是两边都有的硬事实：控件级 P 边给「这个按钮发了什么」
    （2026-09-04 才有的那一列），Q 边给「脚本打过什么」。

    出两类发现（口径照 §14.2）：

    · `verbsNotOnPage` —— 脚本打过这个动作端点，**页面上没有哪个按钮发过它**。
      他可能测的是页面走不到的路（也可能是我们没走到那一层，
      所以这条**只是发现，不是结论** —— §13.3）。
    · `actionsUntested` —— 页面上这个动作发过的端点，**脚本一次都没打过**。
      就是 G3 那一类，但这次**带动作名**。
    """
    # `readable_paths` 一路**照原样传下去**，`None` 不许变成空集合：
    # 空集合读作「查过了，这些路径都不能 GET」，于是每条深路径都被判成动作
    # （`qa_business_actions` 模块头第 2 条写的就是这个坑）。
    readable = readable_paths
    verbs: dict[str, list[str]] = dict(script_verbs or {})
    for h in hits or []:
        method = (h.get("method") or "").upper()
        path = h.get("path") or ""
        verb = _verb_of(method, path, readable) if path else ""
        if not verb:
            continue
        verbs.setdefault(verb, [])
        key = f"{method} {path}"
        if key not in verbs[verb]:
            verbs[verb].append(key)

    page_verbs: dict[str, list[dict]] = {}
    for a in (surface or {}).get("actions") or []:
        for ep in a.get("endpoints") or []:
            method = (ep.get("method") or "").upper()
            path = ep.get("path") or ""
            verb = _verb_of(method, path, readable) if path else ""
            if not verb:
                continue
            page_verbs.setdefault(verb, [])
            page_verbs[verb].append({"label": a["label"], "where": a["where"],
                                     "method": method, "path": path})

    not_on_page = [{"verb": v, "calls": calls}
                   for v, calls in sorted(verbs.items())
                   if v not in page_verbs]
    untested = [{"verb": v, "controls": ctrls}
                for v, ctrls in sorted(page_verbs.items())
                if v not in verbs]
    return {
        "scriptVerbs": sorted(verbs),
        "pageVerbs": sorted(page_verbs),
        "verbsNotOnPage": not_on_page,
        "actionsUntested": untested,
        # **两边都空不是"对齐了"。** 一个动作端点都没连上，多半是控件级
        # 那一列还没落下来（`endpoints` 全是 NULL）—— 那时这两个清单
        # 恒为空，读起来像"页面和脚本完全一致"。
        "paired": bool(verbs and page_verbs),
    }


# ── §14.3 状态是**数出来**的 ─────────────────────────────────────────────

# 状态列的判据：**低基数 + 短 + 不是数字/日期**。
# 不去认「待审核」「已通过」那些词 —— 认词表的判据换个域就烂，
# 而"同一列里反复出现的少数几个短词"这件事在任何列表上都成立。
_STATE_MAX_LEN = 12
_STATE_MAX_DISTINCT = 8


def _looks_like_state(text: str) -> bool:
    s = (text or "").strip()
    if not s or len(s) > _STATE_MAX_LEN:
        return False
    if s.replace(".", "").replace("-", "").replace(":", "").isdigit():
        return False
    return True


def state_candidates(cell_texts, *, min_rows: int = 2) -> dict:
    """从列表里**数出**这个对象有几种状态（§14.3）。

    `cell_texts` 是每一行的单元格文本清单：`[[第一行的几格], [第二行…], …]`。
    判据是「同一列里反复出现的少数几个短词」—— 不认识那些词的含义，
    换域换产品照样成立。

    ⚠ 结果叫 **candidates（候选）不叫 states**：这是统计判据，
    不是事实。真正被确认的状态是**我们那一行变过的那些**
    （`state_path`）—— 变了的那一格，几乎不可能是别的东西。
    """
    rows = [r for r in (cell_texts or []) if r]
    if len(rows) < min_rows:
        return {"candidates": [], "columns": 0, "rows": len(rows),
                "why": "行太少，数不出低基数列（至少要 %d 行）" % min_rows}
    width = max(len(r) for r in rows)
    cands: list[dict] = []
    for col in range(width):
        vals = [(r[col] or "").strip() for r in rows if col < len(r)]
        vals = [v for v in vals if v]
        if len(vals) < min_rows:
            continue
        distinct = sorted(set(vals))
        if len(distinct) > _STATE_MAX_DISTINCT or len(distinct) >= len(vals):
            # 每行都不一样 ⇒ 那是名称/时间，不是状态。
            continue
        if not all(_looks_like_state(v) for v in distinct):
            continue
        cands.append({"column": col, "values": distinct,
                      "rows": len(vals)})
    return {"candidates": cands, "columns": width, "rows": len(rows), "why": ""}


def state_path(observed) -> dict:
    """我们那一条数据**走过**的状态序列。

    `observed` 是按时间顺序记下来的状态文本（每步一条，可以有空串 = 没读到）。
    连续相同的合成一格 —— 「读了三次都是待审」是一格，不是三格。

    `edgesWalked` = 走通了的边；候选里**没出现过的值 = 没走到的分支**
    （§14.3）。后者是广度缺口的料，不是"这个状态不存在"。
    """
    seq: list[str] = []
    for s in observed or []:
        s = (s or "").strip()
        if not s:
            continue
        if not seq or seq[-1] != s:
            seq.append(s)
    edges = [{"from": seq[i], "to": seq[i + 1]} for i in range(len(seq) - 1)]
    return {"path": seq, "edgesWalked": edges,
            "statesSeen": sorted(set(seq))}


def states_not_walked(candidates: dict, path: dict) -> list[str]:
    """候选状态里，我们那一条**一次都没到过**的那些。

    它是「没走到的分支」，**不是**「这些状态不存在」，也**不是**
    「他没测这些状态」—— 那两句得看对方脚本，不在这个函数的职权范围。
    """
    seen = set((path or {}).get("statesSeen") or [])
    out: list[str] = []
    for c in (candidates or {}).get("candidates") or []:
        for v in c.get("values") or []:
            if v not in seen and v not in out:
                out.append(v)
    return out


# ── §14.1 结构：详情页里多出来的区块 ─────────────────────────────────────

def read_structure(before, after) -> dict:
    """§14.1 的「结构」那一格：**建完之后**详情页里多出来的区块。

    `before` / `after` 是两次枚举里的**区块标题**（页签名、卡片标题、
    小节标题）。差集就是「建了一条数据才出现的东西」——
    那是 §15.2 那条因果的度量：不打通业务，这几层结构上就看不到。

    这里只报差集，**一个字都不解释它是什么**（审批记录？操作日志？关联列表？）——
    解释要认产品名词，而差集本身已经足够让人去看。
    """
    b = {(x or "").strip() for x in (before or []) if (x or "").strip()}
    a = {(x or "").strip() for x in (after or []) if (x or "").strip()}
    return {"appeared": sorted(a - b), "disappeared": sorted(b - a),
            "before": len(b), "after": len(a)}


# ── §15 广度 / 深度：**两个数，不许加权** ────────────────────────────────

UNSEEN_KINDS: dict[str, dict] = {
    "unreached": {"label": "没走到", "ours": True,
                  "why": "这一层要某个前置才出现（没建数据 / 状态没到 / "
                         "没勾选），而前置没做到。**算我们的欠账** —— "
                         "把前置补上就能看到，进广度缺口"},
    "blocked": {"label": "够不到", "ours": False,
                "why": "前置做到了，但手上所有角色都点不动（403 / 看不见）。"
                       "不算欠账，记成「这一步归我们够不到的角色」"},
    "seen_not_run": {"label": "看到了、故意没点", "ours": False,
                     "why": "危险动作 / 深度打折。不算欠账 —— **广度已经满了**"},
}


def new_unseen_book() -> dict:
    """三种「没看到」各一本。**必须分开记**（§15.3 那条 ⚠）：
    `unreached` 是遗漏、`seen_not_run` 是取舍，混成一个「未测」之后
    遗漏就再也报不出来了。
    """
    return {k: [] for k in UNSEEN_KINDS}


def note_unseen(book: dict, kind: str, *, where: str = "", label: str = "",
                why: str = "") -> dict:
    """记一条「没看到」。`kind` 打错一个字直接抛 —— 安静多出一本账，
    那本账谁都不会去看。
    """
    if kind not in UNSEEN_KINDS:
        raise ValueError(f"不是「没看到」的类型：{kind}")
    book.setdefault(kind, []).append(
        {"where": where, "label": label, "why": why})
    return book


def breadth_depth(surface: dict, unseen: dict, chain_summary: dict) -> dict:
    """§15.1：**两个数，分两列，不许加权。**

    加权之后「看全了但只走通一条」和「只看了一半但都走通了」会拿到同一个分，
    而这两种情况欠的账完全不同 —— 一个要补前置去看，一个要往深里走。
    所以这个函数**故意不返回任何一个合并分**，也别在页面上算一个出来。

    · 广度（看到了没有）**必须满**：判据是 `unreached` 为空。
      「看到」的定义是三件事齐（§15.1）：枚举到了 + 知道属于哪一行/哪一层 +
      知道谁能点 —— 前两件靠 `where`，第三件靠 `enabled` 读到没读到。
    · 深度（走通了没有）**允许不满**：主链至少走通一条到底。
      没走的**显式记成"没走"**（`seen_not_run`），省略掉之后
      它和"走过、没问题"在报告上长得一模一样。
    """
    s = surface or {}
    book = unseen or {}
    cs = chain_summary or {}
    unreached = list(book.get("unreached") or [])
    actions = s.get("actions") or []
    # 「谁能点」这一件：灰没灰读不到 ⇒ 这一条**不算看全**。
    # 不这么算的话，读不到 `disabled` 的那半边会白拿一个满分广度。
    role_unknown = len([a for a in actions if not a["enabledIn"]
                        and not a["disabledIn"]])
    breadth = {
        "actionsSeen": s.get("actionsTotal", 0),
        "byWhere": s.get("actionsByWhere") or {},
        # **满不满只由 `unreached` 决定**，和看到多少个无关：
        # 看到 100 个但漏了详情页那一层，广度就是不满。
        # 但**一个都没看到不算满** —— 一趟都没跑起来时 `unreached` 天然是空的，
        # 那时 `not unreached` 会给出「广度满」这句最响的假话。
        "full": bool(actions) and not unreached,
        "unreached": unreached,
        "roleUnknown": role_unknown,
        "declaredNotSeen": {k: len(v or []) for k, v in book.items()},
    }
    depth = {
        "chainsAttempted": cs.get("chainsAttempted", 0),
        "chainsCompleted": cs.get("chainsCompleted", 0),
        "statesWalked": len((s.get("statePath") or {}).get("edgesWalked") or []),
        "stateEdges": len(s.get("stateEdges") or []),
        # 深度**不要求满**，但要求「没走的说清楚」。
        "notRun": len(book.get("seen_not_run") or []),
        "blocked": len(book.get("blocked") or []),
        "mainChainDone": bool(cs.get("chainsCompleted")),
    }
    return {"breadth": breadth, "depth": depth}


def map_declarations(pairing: dict, unseen: dict, surface: dict) -> list[str]:
    """这一趟的功能地图**没看到什么 / 哪些结论不能下**。声明是一等公民。"""
    out: list[str] = []
    p = pairing or {}
    book = unseen or {}
    s = surface or {}
    if not p.get("paired"):
        out.append("动作面和脚本**一条都没连上** —— 连接键是「这个按钮发了哪条"
                   "端点」，而这一趟控件级那一列是空的（点得太少 / 边归不了属）。"
                   "所以「页面上有他没测的动作」这句话这一趟**下不了**，"
                   "两个清单是空的不等于对齐了")
    if not (s.get("actionsByWhere") or {}).get("batch"):
        out.append("批量条这一层**一次都没看到** —— 它要勾选一行才出现（§15.2）。"
                   "这是广度欠账，不是「这个产品没有批量操作」")
    if not (s.get("actionsByWhere") or {}).get("detail"):
        out.append("详情页那一层**一次都没看到** —— 它要先建出一条数据才进得去。"
                   "详情页里的页签和按钮天然一个都没数到，"
                   "别读成「这个域只有列表页」")
    if s.get("enabledUnknown"):
        out.append("%d 处控件的「灰没灰」读不到 —— 「入口禁用」这一类业务规则"
                   "在这些控件上**判不了**，别当成它们都是可点的"
                   % s["enabledUnknown"])
    if book.get("unreached"):
        out.append("%d 处功能因为前置没做到而**没看到**（没建数据 / 状态没到 / "
                   "没勾选）—— 这是广度缺口，**算我们的欠账**，"
                   "和「故意没点」那一本是两回事" % len(book["unreached"]))
    return out


# ── 把一条链的四样折成一份地图 ───────────────────────────────────────────

def absorb_reading(chain: dict, *, step: str, where: str, read=None,
                   items=None) -> dict:
    """把「点完这一步页面变成什么样」记进链的账本。

    `read` 是 `_READ_JS` 的原始产出（提示 / 区块标题 / 每行单元格 / 我们那一行），
    `items` 是这一步枚举到的控件，`where` 说这批控件**属于哪一行 / 哪一层**。

    这里只**收**，一个判断都不做 —— 折算在 `chain_map`，
    这样同一份原始记录换个判据可以重算，不用再跑一趟页面。
    """
    read = read or {}
    # **探过这一层**这件事本身要记账，和"探到了几个"分开
    # （见 `new_chain` 里 `probed` 那段注释）。
    if where not in chain.setdefault("probed", []):
        chain["probed"].append(where)
    row_cells = [c for c in (read.get("ourRow") or []) if (c or "").strip()]
    # 我们那一行的状态：候选列里落在这一行的那一格。**读不到就记空串**，
    # 别跳过 —— 跳过之后 `states` 短一格，而「没读到」和「状态没变」
    # 在序列上长得一模一样。
    chain["states"].append(pick_row_state(read.get("cells") or [], row_cells))
    for h in read.get("hints") or []:
        text = (h or "").strip()
        if text:
            chain["hints"].append({"text": text[:300], "status": None,
                                   "where": step})
    chain["sections"].append({"step": step,
                              "titles": list(read.get("sections") or [])})
    if read.get("cells"):
        chain["cells"] = list(read["cells"])      # 最后一次为准：行最全
    if items:
        state = chain["states"][-1] if chain["states"] else ""
        chain["surface"].append(snapshot_actions(items, where=where,
                                                 state=state))
    return chain


def pick_row_state(cells, our_row) -> str:
    """我们那一行的**状态**那一格是哪一格。

    判据：先在整张表上数出低基数列（`state_candidates`），
    再取我们那一行同一列的值。**不认识那些词** —— 换个域照样成立。
    数不出来（行太少 / 没有低基数列）就返回空串，不猜。
    """
    if not our_row:
        return ""
    cand = state_candidates(cells)
    for c in cand.get("candidates") or []:
        col = c.get("column")
        if isinstance(col, int) and 0 <= col < len(our_row):
            val = (our_row[col] or "").strip()
            if val in (c.get("values") or []):
                return val
    return ""


def chain_map(chain: dict) -> dict:
    """一条链 → 一份**功能地图**（§14.5 那两份产出的原料）。

    四样各占一格，**一格都不许省**：规则 / 状态 / 动作面 / 结构。
    省掉的那一格在页面上和"这个产品没有这东西"长得一样。
    """
    ch = chain or {}
    surface = merge_surface(ch.get("surface") or [])
    path = state_path(ch.get("states") or [])
    cand = state_candidates(ch.get("cells") or [])
    secs = ch.get("sections") or []
    first = (secs[0].get("titles") if secs else []) or []
    last = (secs[-1].get("titles") if secs else []) or []
    surface["statePath"] = path
    return {
        "page": ch.get("page", ""),
        "rules": read_rules([], ch.get("hints") or []),
        "probed": list(ch.get("probed") or []),
        "state": {"candidates": cand, "path": path,
                  "notWalked": states_not_walked(cand, path)},
        "surface": surface,
        "structure": read_structure(first, last),
    }


def map_meta() -> dict:
    """三张对照表跟着地图一起发给页面（同 `chain_meta` 一个理由：
    键在这个文件里，名字就得跟着键走，别在前端另抄一份）。

    `unseen` 那张表里的 `ours` 尤其不能丢 —— 它是「算不算我们的欠账」，
    三本账在页面上混成一个「未测」正是 §15.3 那条 ⚠ 拦的事。
    """
    return {
        "wheres": dict(WHERE_KINDS),
        "unseen": {k: dict(v) for k, v in UNSEEN_KINDS.items()},
        "hintKinds": {k: dict(v) for k, v in HINT_KINDS.items()},
    }


def summarize_maps(chains, *, hits=None, readable_paths=None) -> dict:
    """一趟里所有链的地图合起来 —— 加上和脚本那边的对账、三种「没看到」、
    和**两个分开的数**。

    ⚠ 返回里**没有**、也别在页面上算一个「综合完成度」：
    广度和深度加权之后，「看全了但只走通一条」和「只看了一半但都走通了」
    会拿到同一个分，而这两种欠的账完全不同（§15.1）。
    """
    from app.services.qa_directed_chain import summarize_chains

    maps = [chain_map(c) for c in (chains or [])]
    surface = merge_surface([s for c in (chains or [])
                             for s in (c.get("surface") or [])])
    # 状态序列**按链分开**再合并：每条链是一条独立的路径，首尾拼起来会造出
    # 一条谁都没走过的边（A 链末尾 → B 链开头）。
    surface["statePath"] = {
        "path": [],
        "edgesWalked": [e for m in maps
                        for e in m["state"]["path"]["edgesWalked"]],
        "statesSeen": sorted({s for m in maps
                              for s in m["state"]["path"]["statesSeen"]}),
    }
    surface["stateEdges"] = [e for m in maps
                             for e in m["surface"]["stateEdges"]]
    pairing = pair_actions(surface, hits or [], readable_paths=readable_paths)
    book = new_unseen_book()
    for m in maps:
        # 前置没做到而看不到的那几层。**这是我们的欠账**（§15.3）。
        probed = m.get("probed") or []
        # ⚠ 判据是「**探过没有**」，不是「数到几个」：探过、一个都没有，
        # 那是这个产品的事实；压根没探，才是我们的欠账。
        for layer, why in (
                ("detail", "没进到详情页那一层（没建出数据 / 没有详情入口）"),
                ("batch", "一次都没勾选过行，批量条根本没机会出现"),
                ("tab", "没进到详情页的页签那一层")):
            if layer not in probed:
                note_unseen(book, "unreached", where=layer, label=m["page"],
                            why=why)
        for v in m["state"]["notWalked"]:
            note_unseen(book, "seen_not_run", where="page", label=v,
                        why="列表上有这个状态，我们那一条没走到 —— 深度打折")
    nums = breadth_depth(surface, book, summarize_chains(chains or []))
    return {"maps": maps, "surface": surface, "pairing": pairing,
            "unseen": book, "breadth": nums["breadth"], "depth": nums["depth"],
            "declarations": map_declarations(pairing, book, surface)}
