"""QA 仓的公共选择器表 → 活体命中报告（批 1 的第一件产出）。

他自己的文件头上写着这么一句：

    ⚠️ 建仓时**没有可访问的 UAG 控制台**，以上选择器全部来自**源码阅读**，
       未在真实浏览器里验证过。

这就是本模块的全部理由：**我们有那个控制台**（页面枚举那一趟正在上面跑），
所以「这 445 条选择器在真实渲染里指到东西没有」这件事，我们能答、他答不了。
产出对他**直接可用** —— 所有 spec 都 `import { sel } from '../../support/selectors'`，
所以报出来的键写成 `sel.teams.totalBadge` 这种形状，他 `grep` 一下就定位到那一行。

── 判重不在这里 ────────────────────────────────────────────────────────────
`scripts/check-selectors-integrity.sh` ① 已经在管重复键，而且判据比这里细
（它还答「合并只是新增键还是改了既有键」）。**别把别人门禁已经能判的事重做一遍** ——
两套判重迟早给出不一样的数，而那时候没人知道该信哪个。
这里仍然要**认出**重复键，只为一件事：JS 对象字面量是 **last-wins**，
探的时候必须知道自己探的是后面那个值。所以它只出现在 `counters`/`declarations` 里，
不产出「该改哪一行」那种结论。

── 四档，以及为什么「命中 0」不叫「过期」 ──────────────────────────────────
正面（命中 1 / 命中多个）是**结论性**的：在真实浏览器里、某一页上、这个选择器
确实指到了 1 个（或 n 个）元素。这是他源码阅读拿不到的东西。

反面不是。**这一趟一个控件都不点**（无向枚举，理由在 `qa_survey_guard` 头部），
于是弹窗里的、tab 切过去才渲染的、列表有数据才出现的行内按钮，**结构上不可能**
在这一趟里出现。把它们报成「过期/该删」，等于让他去改一批本来是对的选择器 ——
一次就够让这份报告失去信用。所以第三档叫**「这一趟没见到」**，
并且报告里必须带着那句构造性理由一起给。

`invalid` 是第四档里唯一**结论性**的那个：`querySelectorAll` 直接抛 ——
那条选择器语法坏了，任何 spec 用到它都会当场炸。它跟「没见到」必须分开报。
"""
from __future__ import annotations

import re

# 一层键：`  totalBadge: '…',`。缩进不参与判定（见 `parse_selectors` 的深度那段）。
_KEY = re.compile(r'^\s*([A-Za-z_$][\w$]*)\s*:')
# 顶层命名空间：`export const sel = {` / `export const routes = {`。
# ⚠️ **它也是一层。** 不认它的话 `sel.teams` 和 `routes.teams` 会被算成同一个键；
#   他那边第一版就踩了这个，当场报出 19 处「重复」而一处都不是真的。
#   我们的键还要拿给人 grep，串了台就是指错行。
_ROOT = re.compile(r'^\s*export\s+const\s+([A-Za-z_$][\w$]*)\s*[:=]')


def _value_str(rest: str) -> str | None:
    """`'…'` / `"…"` / `` `…` `` → 引号里那串；认不出返回 `None`。

    **为什么不是一条正则。** `^(['\"`])(.*)\1$` 是贪心的：`'a' + 'b'` 会被读成
    一整串 `a' + 'b` —— 那东西**拼不出选择器**（我们不执行 JS），却会被当成
    一条正常选择器拿去探，然后稳定命中 0。**那是一条假的「没见到」**，
    而这个模块存在的全部理由就是不产出假事实。所以这里扫到第一个闭合引号，
    后面只允许逗号和行尾注释，别的一律不认（→ `unparsed`，报出来但不猜）。

    行尾注释**必须**允许：今天他表里一条都没有（实测 0 行），但这份文件几乎
    每条分支都会被改（他自己实测四个 MR 里三个改了它），哪天谁在值后面补一句
    注释，那条本来能探的选择器就会被记成"我们读不懂"。
    """
    if not rest or rest[0] not in "'\"`":
        return None
    q, i = rest[0], 1
    while i < len(rest):
        c = rest[i]
        if c == "\\":
            i += 2
            continue
        if c == q:
            break
        i += 1
    if i >= len(rest) or rest[i] != q:
        return None
    tail = rest[i + 1:].strip()
    if tail.startswith(","):
        tail = tail[1:].strip()
    if tail and not tail.startswith("//"):
        return None
    return rest[1:i]


# Playwright 的自家伪类/引擎前缀 —— `document.querySelectorAll` 不认。
# **不许"顺手翻译一下"**（比如把 `:visible` 摘掉再探）：摘掉之后探的是另一个
# 选择器，命中数也是另一件事的命中数，而报告上和真的一模一样。
# 他那条 `openOptions` 的注释正好写着 `:visible` 让命中数从 8 变 4 ——
# 这一档宁可空着。
_PW_ONLY = (":visible", ":has-text(", ":nth-match(", "text=", ">>", "internal:",
            ":right-of(", ":left-of(", ":near(", ":above(", ":below(")

# 在页面里跑的那段 JS：**只查不点**，一个 DOM 都不动。
# 约定：命中数 ≥ 0 是真数，**-1 = 这个选择器让 querySelectorAll 抛了**
# （语法坏了）。用 -1 而不是漏掉这条 —— 漏掉的话它和「没见到」混在一起，
# 而「语法坏了」是结论性的、「没见到」不是。
PROBE_JS = """(list) => {
  const out = {};
  for (const it of list) {
    try {
      out[it.key] = document.querySelectorAll(it.css).length;
    } catch (e) {
      out[it.key] = -1;
    }
  }
  return out;
}"""

VERDICTS = ("hitOne", "hitMany", "invalid", "notSeen", "notProbed")

_VERDICT_CN = {
    "hitOne": "命中 1",
    "hitMany": "命中多个",
    "invalid": "选择器语法坏了",
    "notSeen": "这一趟没见到",
    "notProbed": "探不了",
}


def _dedent_key(stack: list[str], key: str) -> str:
    return ".".join(tuple(stack) + (key,))


def parse_selectors(text: str) -> dict:
    """`ui/support/selectors.ts` → 键、值、能不能探。**纯函数，不碰浏览器。**

    深度靠**花括号**算，不靠缩进 —— 这份文件今天是规整的，但它每条分支都会被改
    （他自己实测四个 MR 里三个改了它），合并产物的缩进可能是乱的，而括号不受格式影响。

    值只认三种形状，**认不出的记进 `unparsed` 而不是猜**：
    - 单行字符串 → 可探（除掉 Playwright 专有语法那些）
    - `(id: string) => \\`…\\`` 参数化 → **不探**（见 `notProbed` 那段）
    - `key:` 换行再接字符串 → 认（他有一条 `trendLegendActive` 是这样）
    """
    entries: list[dict] = []
    routes: dict[str, str] = {}
    route_templates: list[str] = []
    dup: dict[str, list[int]] = {}
    seen_lines: dict[str, list[int]] = {}
    unparsed: list[str] = []

    stack: list[str] = []
    pending: tuple[str, int] | None = None

    for lineno, line in enumerate((text or "").split("\n"), 1):
        s = line.strip()
        if s.startswith("//") or s.startswith("*") or s.startswith("/*"):
            continue

        if pending is not None:
            # 上一行是 `key:`，值在这一行。
            v = _value_str(s)
            key, kln = pending
            pending = None
            if v is not None:
                _add(entries, routes, route_templates, seen_lines, dup,
                     key, v, kln)
            else:
                unparsed.append(key)
                continue

        rm = _ROOT.match(line)
        if rm:
            # 新的顶层命名空间：清空栈，以它为根。
            stack = [rm.group(1)]
            continue

        m = _KEY.match(line)
        if m:
            key = _dedent_key(stack, m.group(1))
            rest = line[m.end():].strip()
            v = _value_str(rest)
            if v is not None and "${" not in v:
                _add(entries, routes, route_templates, seen_lines, dup,
                     key, v, lineno)
            elif v is not None or rest.startswith("("):
                # 箭头函数，或者一条自带 `${}` 的模板串 —— 都要运行时的值才拼得
                # 出来，一样探不了。**按值的形状判，不按写法判**：哪天有人把
                # 箭头函数改写成常量模板串，它照旧落「探不了」。
                _add(entries, routes, route_templates, seen_lines, dup,
                     key, None, lineno, parameterized=True)
            elif rest.startswith("{"):
                pass                                  # 分组，键在里面
            elif rest == "" or rest.startswith("//"):
                # `key:` 之后换行接值；那一行也可能先跟一句注释。
                pending = (key, lineno)
            else:
                unparsed.append(key)

        # 深度：只数不在字符串/行注释里的括号。
        code = re.sub(r"`[^`]*`|\"[^\"]*\"|'[^']*'", "", line)
        code = re.sub(r"//.*$", "", code)
        opens, closes = code.count("{"), code.count("}")
        if m and opens > closes:
            stack.append(m.group(1))
        else:
            for _ in range(max(0, closes - opens)):
                if stack:
                    stack.pop()

    declarations: list[str] = []
    if dup:
        declarations.append(
            "%d 个键在同一层里出现两次 —— JS 对象字面量 last-wins，"
            "下面探的是**后面那个**值。判重是他自己 "
            "`scripts/check-selectors-integrity.sh` ① 的活，这里不出结论。" % len(dup))
    if unparsed:
        declarations.append(
            "%d 个键的值这套解析认不出（既不是单行字符串也不是参数化），"
            "这些键一条都没探 —— 认不出就不猜，猜出来的命中数和真的长得一样。" % len(unparsed))

    return {
        "entries": entries,
        "routes": routes,
        "routeTemplates": sorted(route_templates),
        "duplicateKeys": {k: v for k, v in dup.items()},
        "unparsedKeys": sorted(unparsed),
        "counters": {
            "keys": len(entries),
            "probeable": sum(1 for e in entries if e["probe"]),
            "parameterized": sum(1 for e in entries
                                 if e.get("skipReason") == "parameterized"),
            "playwrightOnly": sum(1 for e in entries
                                  if e.get("skipReason") == "playwrightOnly"),
            "routes": len(routes),
            "routeTemplates": len(route_templates),
            "duplicateKeys": len(dup),
            "unparsed": len(unparsed),
        },
        "declarations": declarations,
    }


def _add(entries, routes, route_templates, seen_lines, dup,
         key: str, value: str | None, lineno: int, *, parameterized=False):
    prev = seen_lines.setdefault(key, [])
    prev.append(lineno)
    if len(prev) > 1:
        dup[key] = list(prev)

    ns = key.split(".")[0]
    if ns == "routes":
        if parameterized:
            route_templates.append(key)
        else:
            routes[key.split(".", 1)[1]] = value
        return

    if parameterized:
        entry = {"key": key, "selector": None, "line": lineno, "probe": False,
                 "skipReason": "parameterized"}
    elif any(t in (value or "") for t in _PW_ONLY):
        entry = {"key": key, "selector": value, "line": lineno, "probe": False,
                 "skipReason": "playwrightOnly"}
    else:
        entry = {"key": key, "selector": value, "line": lineno, "probe": True,
                 "skipReason": None}

    # last-wins：同名键的后一条覆盖前一条，跟 JS 一致。
    for i, e in enumerate(entries):
        if e["key"] == key:
            entries[i] = entry
            return
    entries.append(entry)


def probe_payload(parsed: dict) -> list[dict]:
    """喂给 `PROBE_JS` 的那份清单。顺序按键排死 —— 同一份表探两次必须一样。"""
    return [{"key": e["key"], "css": e["selector"]}
            for e in sorted(parsed.get("entries") or [], key=lambda e: e["key"])
            if e["probe"] and e["selector"]]


def merge_probe(acc: dict, page_path: str, result: dict | None) -> dict:
    """把一页的探测结果并进账本。**只留非 0 的**。

    0 是 445 × 40 页里的绝大多数，全存下来账本会胀成几万行，而它一个字的信息都
    没有：「这一页没见到」可以由「探过的页面清单」减出来。**探过哪些页要单独记**
    （`pages`），不然「没见到」和「这一页压根没探」在产物上一模一样 ——
    而后者不是关于选择器的事实，是关于我们这一趟的事实。
    """
    acc.setdefault("pages", [])
    if page_path not in acc["pages"]:
        acc["pages"].append(page_path)
    hits = acc.setdefault("hits", {})
    for key, count in (result or {}).items():
        try:
            n = int(count)
        except (TypeError, ValueError):
            continue
        if n == 0:
            continue
        hits.setdefault(key, {})[page_path] = n
    return acc


def roll_up(parsed: dict, acc: dict | None, *, probed: bool | None = None) -> dict:
    """四档报告。**每条都带上「在哪一页、命中几个」**，不然他没法复查。

    `probed` 显式说这一趟到底探没探（`None`/`False` ⇒ 一条命中都不该有，
    整张表落 `notProbed` + 一条声明）。缺这个开关的话「探了、全都没见到」
    和「压根没探」两种情况的产物完全一样 —— 前者是 445 条待查，
    后者是我们自己没跑，混在一起报出去等于让人去查一批不存在的问题。

    档位优先级：`invalid` > `hitMany` > `hitOne` > `notSeen`。
    `hitMany` 压过 `hitOne` 是因为它才是**可操作**的那个：`.first()` 会
    从 DOM 顺序里抓第一个，抓到哪个不由脚本决定（他 `openOptions` 那条注释
    记着同一个坑）。一个键在 A 页命中 1、在 B 页命中 3，要报的是 B 页那件事。
    """
    entries = sorted(parsed.get("entries") or [], key=lambda e: e["key"])
    hits = (acc or {}).get("hits") or {}
    pages = list((acc or {}).get("pages") or [])
    if probed is None:
        probed = bool(pages)

    rows: list[dict] = []
    for e in entries:
        row = {"key": e["key"], "selector": e["selector"], "line": e["line"]}
        if not e["probe"]:
            row["verdict"] = "notProbed"
            row["why"] = (
                "参数化选择器（要运行时 id 才拼得出来）—— 拿前缀去探等于探了另一个"
                "选择器，命中 0 到底是「改名了」还是「列表是空的」分不开。"
                if e["skipReason"] == "parameterized" else
                "带 Playwright 专有语法（`:visible` 这类），`querySelectorAll` 不认；"
                "摘掉再探就不是这条选择器了。")
        elif not probed:
            row["verdict"] = "notProbed"
            row["why"] = "这一趟没探页面。"
        else:
            per_page = hits.get(e["key"]) or {}
            counts = list(per_page.values())
            row["pages"] = {p: n for p, n in sorted(per_page.items())}
            row["maxCount"] = max(counts) if counts else 0
            if any(n < 0 for n in counts):
                row["verdict"] = "invalid"
                row["why"] = ("`querySelectorAll` 抛了 —— 这条选择器语法坏了，"
                              "任何 spec 用到它都会当场炸。")
            elif row["maxCount"] >= 2:
                row["verdict"] = "hitMany"
                row["why"] = ("真实渲染里指到多个元素 —— `.first()` 抓哪个由 DOM "
                              "顺序说，不由脚本说。")
            elif row["maxCount"] == 1:
                row["verdict"] = "hitOne"
            else:
                row["verdict"] = "notSeen"
        rows.append(row)

    buckets: dict[str, list[str]] = {v: [] for v in VERDICTS}
    for row in rows:
        buckets[row["verdict"]].append(row["key"])

    # 探到过、但这份表里已经没有的键。**正常情况下必须是 0** —— 清单和探测同源。
    # 不是 0 只有一种解释：**报告用的表比探的那趟新**（中间 fetch 过一次），
    # 于是这份报告说的是另一个版本的选择器表。悄悄丢掉的话它长得跟"探过了"
    # 一模一样，而里面每一条「没见到」都可能只是键改了名。
    known = {e["key"] for e in entries}
    stale_keys = sorted(k for k in hits if k not in known)

    declarations = list(parsed.get("declarations") or [])
    if stale_keys:
        declarations.append(
            "有 %d 个探到过命中的键在这份选择器表里找不到 —— **报告用的表和探的那趟"
            "不是同一个版本**（中间取过一次新的）。这份报告的「没见到」不可信，"
            "重新探一趟：%s" % (len(stale_keys), "、".join(stale_keys[:5])))
    if not probed:
        declarations.append(
            "这一趟没在页面上探过选择器 —— 整张表都是「探不了」，"
            "**一条都不能当成「选择器没问题」或「选择器过期」**。")
    elif buckets["notSeen"]:
        declarations.append(
            "「这一趟没见到」有 %d 条，**它不等于「过期」**：无向枚举一个控件都不点，"
            "弹窗里的、tab 切过去才渲染的、列表有数据才出现的控件**结构上不可能**"
            "在这一趟出现。照这一档去改 `selectors.ts` 会改坏一批本来是对的选择器。"
            "这一趟只探了 %d 个页面：%s。"
            % (len(buckets["notSeen"]), len(pages), "、".join(sorted(pages)) or "（无）"))

    return {
        "rows": rows,
        "buckets": buckets,
        "verdictNames": dict(_VERDICT_CN),
        "pagesProbed": sorted(pages),
        "counters": {
            **{v: len(buckets[v]) for v in VERDICTS},
            "keys": len(rows),
            "pagesProbed": len(pages),
            "hitsForUnknownKeys": len(stale_keys),
        },
        "declarations": declarations,
    }
