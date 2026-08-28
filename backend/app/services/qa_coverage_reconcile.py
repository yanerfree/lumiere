"""三方对账 —— **纯集合运算，不做 IO、不问模型**。

三个账本：
  · **P** 页面枚举（Epic 6 爬出来的控件 → 请求 → 路径模板）
  · **R** 路由表（S7.2，`qa_route_table`）
  · **Q** QA 清单（S7.1，`qa_catalog` 的域码表 + 场景清单）

这个文件只负责把三边的**名字对齐**，好让 S7.4 去做交并差。
对齐这件事全是坑，而且**坑的方向是一致的：对不齐 ⇒ 凭空多出缺口**。
两边其实是同一个组，只因写法不同没对上，报告上就长出一条「这个组没人测过」——
去查的人扑个空，第二次就不看这份报告了。

## 三处坑（清单自己警告过的）

1. **组名的大小写和单复数会变。** 域码表原文写过 2.1.1→2.2.0 改过写法；
   按字面比对会**凭空多出 7 个新组**。
2. **`PUB` 不是按组划定的，是按路径前缀**（`/api/public/v1/*`），
   并且**故意**跟 TEM/PRV/AGT/MCP 重叠 —— 同一个端点既属 PUB 又属 TEM。
3. **`Root` 组同属 SMK / MCP / SEC。**

2 和 3 是同一件事的两个例子：**「组 → 域」天生是一对多。**
写成 `dict[str, str]` 的话后一个域把前一个覆盖掉，一个字都不会报错，
而对账那边从此少算一整个域的缺口 —— **少算的缺口不会红，谁都发现不了。**
所以这里的值一律是 `set`。
"""
import re

from app.services.branch_diff_service import WILDCARD, normalize_path

# 归一之后仍然保留原样的尾巴：`status` / `access` 剥成 `statu` / `acces`
# 不只是难看 —— 它会跟别的词撞在一起，把两个真不同的组合并成一个，
# 于是其中一个组的缺口凭空消失。**过度归一和不归一，坏的方向正好相反，都要防。**
_KEEP_TAIL = ("ss", "us", "is")
_IES = re.compile(r"ies$")
_XES = re.compile(r"(?:ses|xes|zes|ches|shes)$")
# 路径前缀的形状：反引号里以 `/` 开头、以 `/*` 或 `*` 收尾的那一段。
# **只认这一种确定形状** —— 第三列里混着中文散文，从散文里"理解"归属规则
# 就是把猜换了个地方放，还更隐蔽，因为它看起来像事实。
_PREFIX_RE = re.compile(r"/(?:[A-Za-z0-9_\-{}]+/)*[A-Za-z0-9_\-{}]*\*")


def norm_group(raw: str | None) -> str:
    """组名 → 可比的键。小写、去分隔符、单复数归一。

    `MCP-Tools` / `MCP Tools` / `mcp_tools` / `MCPTool` 全归到 `mcptool`。
    """
    s = re.sub(r"[^0-9a-z]+", "", (raw or "").strip().lower())
    if not s:
        return ""
    if s.endswith(_KEEP_TAIL):
        return s
    if _IES.search(s) and len(s) > 4:
        return s[:-3] + "y"
    if _XES.search(s) and len(s) > 4:
        return s[:-2]
    # 剥掉复数 s，但**剥完至少还剩 3 个字符** —— `Ops` 剥成 `op` 就开始撞了
    if s.endswith("s") and len(s) - 1 >= 3:
        return s[:-1]
    return s


def _prefixes(raw: str) -> list[str]:
    """从域码表第三列的原文里抠出路径前缀（`/api/public/v1/*`）。"""
    out: list[str] = []
    for m in _PREFIX_RE.finditer(raw or ""):
        p = normalize_path(m.group(0).rstrip("*").rstrip("/"))
        if p and p != "/" and p not in out:
            out.append(p)
    return out


def build_group_index(domains: dict[str, dict]) -> dict:
    """域码表 → 对账用的归属索引。

    返回：
      `byGroup`   `{归一组名: {域码, ...}}` —— **值是集合**，见文件头
      `aliases`   `{归一组名: [清单原文写过的名字, ...]}` —— 归一合并了什么必须留痕，
                  否则「清单把 Tags 改成了 Tag」这个信号被归一化本身吃掉了
      `byPrefix`  `[(路径模板, {域码, ...}), ...]` —— `PUB` 那种按前缀划定的
      `unresolved` `[域码, ...]` —— 第三列有内容但**既没组名也没前缀**，
                  归属规则没读懂。这些域**不能**渲染成「0 缺口」：
                  「没有缺口」和「我根本没法给它归属」在数字上是同一个 0。
    """
    by_group: dict[str, set[str]] = {}
    aliases: dict[str, list[str]] = {}
    by_prefix: list[tuple[str, set[str]]] = []
    unresolved: list[str] = []

    prefix_map: dict[str, set[str]] = {}
    for code, meta in (domains or {}).items():
        groups = meta.get("groups") or []
        raw = meta.get("groupsRaw") or ""
        for g in groups:
            key = norm_group(g)
            if not key:
                continue
            by_group.setdefault(key, set()).add(code)
            names = aliases.setdefault(key, [])
            if g not in names:
                names.append(g)
        prefs = _prefixes(raw)
        for p in prefs:
            prefix_map.setdefault(p, set()).add(code)
        if raw.strip() and not groups and not prefs:
            unresolved.append(code)

    # 长前缀排前面：`/api/public/v1` 比 `/api` 更具体，两条都命中时两个域都算
    for p in sorted(prefix_map, key=lambda x: -len(x)):
        by_prefix.append((p, prefix_map[p]))

    return {"byGroup": by_group, "aliases": aliases,
            "byPrefix": by_prefix, "unresolved": unresolved}


def _under(path: str, prefix: str) -> bool:
    """`/api/public/v1/templates` 在 `/api/public/v1` 底下；`/api/public/v10` 不在。"""
    return path == prefix or path.startswith(prefix + "/")


def domains_for(path: str | None, group: str | None, index: dict) -> set[str]:
    """一个端点 →它属于哪些域。**返回集合，可能是多个，也可能是空。**

    组和前缀两条规则**都走、取并集** —— 清单是故意让它们重叠的
    （`/api/public/v1/templates` 既是 PUB 又是 TEM）。只走一条就会漏掉一个域，
    然后那个域的缺口凭空消失。

    空集合的意思是「这个端点在清单里找不到归属」，**不是**「它没有缺口」——
    调用方（S7.4）必须把它单独记账，不能当成已归属处理。
    """
    out: set[str] = set()
    key = norm_group(group)
    if key:
        out |= (index.get("byGroup") or {}).get(key, set())
    norm = normalize_path(path)
    if norm:
        for prefix, codes in index.get("byPrefix") or []:
            if _under(norm, prefix):
                out |= codes
    return out


# ── Q 侧：从脚本正文里抠端点 ───────────────────────────────────
#
# 沿用 `env_gaps()` 的套路：纯正则扫 `$API/` / `$BFF/` / `curl` 行。
# **宁可漏报不可误报** —— 这条纪律的方向是算过的，不是随口一说：
#   · 漏报（脚本明明打了，没抽出来）→ 这个端点看着没人测 → 多一条 G3 →
#     **噪声，但看得见**，人去查一眼就知道是抽取不完备。
#   · 误报（脚本没打，却算成打了）→ 一个**真缺口凭空消失** → 不会红，谁都发现不了。
# 差几个数量级。所以拿不准一律不认，记进 `endpointsUnextracted`。

_URL_TOKEN = re.compile(
    r"\$\{?(?:API|BFF|BASE_URL|GW|BASE)\}?(?P<path>/[^\s\"'`)\\|;>]*)")
_METHOD_FLAG = re.compile(r"-X\s+([A-Za-z]+)")
_CALL_HINT = re.compile(r"\bcurl\b|\bhttpx?\b|\bwget\b")
# 部署前缀最多吃掉几段：`$API` 展开成 `http://host/api` 还是 `http://host`
# 我们**说了不算**（那是别人的脚本和别人的环境）。放 2 段够覆盖 `/api/v1`，
# 再宽就等于"随便对上一个" —— 见 `covers()` 里为什么不直接用 `paths_match`。
_MAX_BASE_SEGMENTS = 2


def extract_endpoints(text: str | None) -> tuple[list[dict], list[dict]]:
    """脚本正文 → `([{method, path, line}], [抽不出来的行])`。

    第二个返回值是**账本**，不是错误列表：它要一路带到页面上，
    因为「这个端点没人打过」和「这一行我没读懂」是两回事，
    而它们在 G3 里长得一模一样。
    """
    hits: list[dict] = []
    misses: list[dict] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        found = False
        for m in _URL_TOKEN.finditer(line):
            path = normalize_path(m.group("path"))
            if not path or path == "/":
                continue
            mf = _METHOD_FLAG.search(line)
            hits.append({"method": (mf.group(1).upper() if mf else ""),
                         "path": path, "line": line[:200]})
            found = True
        if not found and _CALL_HINT.search(line):
            # 这一行明显在发请求，但 url 拼不出来（变量套变量 / helper 封装）。
            # **不当成「没打过」** —— 那正是会凭空造出 G3 的地方。
            misses.append({"line": line[:200]})
    return hits, misses


def covers(script_path: str, target_path: str) -> bool:
    """脚本里那条 url 算不算打到了这个端点。

    **故意不复用 `branch_diff_service.paths_match`** —— 它的取舍方向跟这里正好相反。
    那边写着「故意偏向多命中：多命中只多过一次 AI 审，漏命中是假绿」；
    在对账这边，多命中意味着**把一个没测的端点算成测过了，缺口凭空消失**，
    而漏命中只是多一条看得见的 G3。同一个函数，两个模块，代价反号。

    所以这里收紧两处：① 后缀匹配最多吃掉 `_MAX_BASE_SEGMENTS` 段前缀
    （够覆盖 `/api/v1` 这种部署前缀，不够"随便对上一个"）；
    ② 脚本侧至少要有 2 段，且不能全是通配 —— `/{}`、`/tools` 这种太弱的锚点
    会跟一堆端点都对上。
    """
    a = [x for x in normalize_path(script_path).split("/") if x]
    b = [x for x in normalize_path(target_path).split("/") if x]
    if not a or not b or len(a) > len(b):
        return False
    if len(b) - len(a) > _MAX_BASE_SEGMENTS:
        return False
    if len(a) < 2 or all(x == WILDCARD for x in a):
        return False
    tail = b[-len(a):]
    return all(x == y or x == WILDCARD or y == WILDCARD for x, y in zip(a, tail))


# ── 五类缺口 ─────────────────────────────────────────────────
#
# G1 ∈P ∧ ∈R ∧ ∉Q   页面点得到、清单一条场景都没有        blame catalog  最硬
# G2 ∈R ∧ ∉P ∧ ∉Q   端点在、页面到不了、也没人测          blame catalog
# G3 ∈P ∧ 清单认领了该域 ∧ 无脚本打过        认领了没兑现   blame script
# G4 ∈P ∧ 控件无任何请求                   纯前端行为      需判断
# G5 present 但 disabled，既无请求也无路由    死按钮/flag     情报，不是缺口
#
# **G1 和 G3 字面上会重叠**（都含 ∈P ∧ ∉Q）。按 blame 分开：
# 清单**根本没认领这个域** ⇒ G1（catalog 的锅）；认领了但脚本没打 ⇒ G3（script 的锅）。
# 不分开的话同一个端点会同时出现在两个清单里，读的人无从知道该找谁。

_SEVERITY = {"G1": "high", "G2": "medium", "G3": "low", "G4": "info", "G5": "info"}


def _ep_key(method: str, path: str) -> str:
    return f"{(method or '').upper()} {normalize_path(path)}".strip()


def compute_gaps(*, page_items: list[dict] | None,
                 routes: list[dict] | None,
                 scripts: list[dict] | None,
                 index: dict,
                 claimed_domains: set[str] | None = None,
                 route_table_available: bool = True,
                 page_survey_available: bool = True) -> dict:
    """三个账本 → 五类缺口。**纯集合运算，不问模型。**

    `scripts` 每条 `{domain, scenarioId, path, text}`。
    `claimed_domains` = 清单里有场景行的域码（G1/G3 的分界）。

    两条降级声明是**一等公民**，不是附注：
      · 没有路由表 ⇒ `G2 notVerified`（S7.2 已经把这句话准备好了）
      · **没有页面枚举 ⇒ 只剩 G2，那就等于一个更慢的 route-drift** ——
        必须明说，否则这份报告看起来"跑过了、只有 2 类缺口"，
        而它其实一个新维度都没验。
    """
    claimed = set(claimed_domains or ())
    declarations: list[str] = []

    # —— Q 侧 ——
    q_paths: list[tuple[str, str, str]] = []   # (domain, method, path)
    unextracted: list[dict] = []
    for sc in scripts or []:
        hits, misses = extract_endpoints(sc.get("text"))
        for h in hits:
            q_paths.append((sc.get("domain") or "", h["method"], h["path"]))
        for m in misses:
            unextracted.append({"scenarioId": sc.get("scenarioId") or "",
                                "domain": sc.get("domain") or "", "line": m["line"]})

    def _covered(method: str, path: str) -> bool:
        for _d, qm, qp in q_paths:
            if qm and method and qm != method.upper():
                continue
            if covers(qp, path):
                return True
        return False

    # —— P 侧 ——
    p_eps: dict[str, dict] = {}
    g4: list[dict] = []
    g5: list[dict] = []
    for it in page_items or []:
        eps = it.get("endpoints") or []
        anchor = f"{it.get('page_path') or ''} :: {it.get('anchor') or it.get('label') or ''}"
        if not eps:
            # 点了没有请求。**disabled 和 enabled 是两回事**：
            # 前者是情报（死按钮/flag 关掉），后者才需要判断值不值得测。
            row = {"kind": "", "domain": "", "anchor": anchor,
                   "pagePath": it.get("page_path") or "", "label": it.get("label") or "",
                   "controlType": it.get("control_type") or "", "blame": "需判断"}
            if (it.get("state") or "") == "present":
                row["kind"], row["blame"] = "G5", "情报"
                row["severity"] = _SEVERITY["G5"]
                g5.append(row)
            else:
                row["kind"] = "G4"
                row["severity"] = _SEVERITY["G4"]
                g4.append(row)
            continue
        for ep in eps:
            k = _ep_key(ep.get("method") or "", ep.get("path") or "")
            p_eps.setdefault(k, {"method": (ep.get("method") or "").upper(),
                                 "path": normalize_path(ep.get("path") or ""),
                                 "pagePath": it.get("page_path") or "",
                                 "label": it.get("label") or "",
                                 "anchor": anchor})

    # —— R 侧 ——
    r_eps: dict[str, dict] = {}
    for r in routes or []:
        k = _ep_key(r.get("method") or "", r.get("path") or "")
        r_eps.setdefault(k, {"method": (r.get("method") or "").upper(),
                             "path": normalize_path(r.get("path") or ""),
                             "group": r.get("group") or ""})

    g1: list[dict] = []
    g2: list[dict] = []
    g3: list[dict] = []
    unattributed: list[dict] = []

    def _domains(path: str, group: str | None) -> set[str]:
        return domains_for(path, group, index)

    # G1 / G3：从页面出发
    for k, meta in p_eps.items():
        if _covered(meta["method"], meta["path"]):
            continue
        group = (r_eps.get(k) or {}).get("group") or ""
        doms = _domains(meta["path"], group)
        if not doms:
            # 归不了属 ≠ 没缺口。单独记账，**不塞进任何一类** ——
            # 塞进 G1 是误报（可能压根不该这个域管），丢掉是漏报（更坏）。
            unattributed.append({"anchor": k, "pagePath": meta["pagePath"]})
            continue
        in_r = k in r_eps
        for d in sorted(doms):
            row = {"domain": d, "method": meta["method"], "path": meta["path"],
                   "anchor": k, "pagePath": meta["pagePath"], "label": meta["label"],
                   "controlAnchor": meta["anchor"]}
            if d in claimed:
                row.update(kind="G3", blame="script", severity=_SEVERITY["G3"],
                           # G3 必带这个数：脚本 url 抽取必然不完备，不带它
                           # 第一版就是一片「你们没兑现」，然后没人再看这份报告
                           endpointsUnextracted=len(unextracted))
                g3.append(row)
            else:
                row.update(kind="G1", blame="catalog", severity=_SEVERITY["G1"])
                g1.append(row)

    # G2：从路由表出发
    if not route_table_available:
        declarations.append("本轮无路由表，G2 未验证")
    else:
        for k, meta in r_eps.items():
            if k in p_eps or _covered(meta["method"], meta["path"]):
                continue
            doms = _domains(meta["path"], meta["group"])
            if not doms:
                unattributed.append({"anchor": k, "pagePath": ""})
                continue
            for d in sorted(doms):
                g2.append({"kind": "G2", "domain": d, "blame": "catalog",
                           "severity": _SEVERITY["G2"], "method": meta["method"],
                           "path": meta["path"], "anchor": k, "group": meta["group"]})

    if not page_survey_available:
        # **这条声明是本模块的存在理由。** 只剩 G2 的话，这份报告做的事
        # 跟 QA 自己的 `check-route-drift.sh` 一模一样（路由表 vs 基线），
        # 只是更慢。不明说的话它看起来像"跑过了，缺口不多"。
        declarations.append("本轮无页面枚举，只有路由表维度 —— 等同 route-drift，"
                            "G1/G3/G4/G5 未验证")

    return {
        "g1": g1, "g2": g2, "g3": g3, "g4": g4, "g5": g5,
        "declarations": declarations,
        "dimensions": {
            "page": "verified" if page_survey_available else "notVerified",
            "routeTable": "verified" if route_table_available else "notVerified",
            "g2": "verified" if route_table_available else "notVerified",
        },
        "counters": {
            # 0 也要渲染：只在非 0 时出现的计数，跟"没算过"长得一模一样
            "endpointsUnextracted": len(unextracted),
            "endpointsUnattributed": len(unattributed),
            "domainsUnresolved": len(index.get("unresolved") or []),
            "scriptsScanned": len(scripts or []),
            "pageEndpoints": len(p_eps),
            "routeEndpoints": len(r_eps),
        },
        "endpointsUnextracted": unextracted,
        "endpointsUnattributed": unattributed,
    }
