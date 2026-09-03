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

from app.services import qa_script_endpoints as qse
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

# ⚠ 这里的 base 名单是**口径**，不是"多认几个总没坏处"：
#   · 加 `AUTH` —— 实读他们 `config/env.sh`：`AUTH=${BFF}/api/auth`，
#     登录/刷新那一批全走它，漏掉等于 `/api/auth/*` 整段没人测。
#   · **去掉 `GW`** —— `GW` 是 Kong（`AI=${GW}/ai/v1` 也算），不是 BFF。
#     而 `covers()` 的后缀匹配能吃掉 2 段前缀，于是 `${GW}/v1/chat/completions`
#     会把 BFF 的 `/api/v1/chat/completions` **标成测过了**：
#     一个网关调用抹掉一个 BFF 缺口，不会红，谁都发现不了。
#     它们不是"读不出来"，是**口径外** —— 单独记账（`qOutOfScope`），绝不进 hits。
_URL_TOKEN = re.compile(
    r"\$\{?(?:API|BFF|AUTH|BASE_URL|BASE)\}?(?P<path>/[^\s\"'`)\\|;>]*)")
_OUT_OF_SCOPE_TOKEN = re.compile(
    r"\$\{?(?:%s)\}?/" % "|".join(qse.OTHER_BASES))
_METHOD_FLAG = re.compile(r"-X\s+([A-Za-z]+)")
_CALL_HINT = re.compile(r"\bcurl\b|\bhttpx?\b|\bwget\b")
# 部署前缀最多吃掉几段：`$API` 展开成 `http://host/api` 还是 `http://host`
# 我们**说了不算**（那是别人的脚本和别人的环境）。放 2 段够覆盖 `/api/v1`，
# 再宽就等于"随便对上一个" —— 见 `covers()` 里为什么不直接用 `paths_match`。
_MAX_BASE_SEGMENTS = 2


def extract_endpoints(text: str | None) -> tuple[list[dict], list[dict]]:
    """脚本正文 → `([{method, path, line}], [抽不出来的行])`。**只认写在行里的 url。**

    helper 封装的那一大半在 `qa_script_endpoints` 里（实测（`refs/remotes/origin/main`，369 个脚本）：
    这个函数命中 136，连上 helper 之后 2943）—— 两个一起用，别只用这一个。

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
        if found or _OUT_OF_SCOPE_TOKEN.search(line):
            # 口径外的行（`${GW}`/`${AI}`）不进 hits，也**不算"读不懂"** ——
            # 它读懂了，只是打的不是 BFF。混进账本会让"抽取不完备"这个数虚高，
            # 而那个数是 G3 的可信度指示器。
            continue
        if _CALL_HINT.search(line):
            # 这一行明显在发请求，但 url 拼不出来（变量套变量 / helper 封装）。
            # **不当成「没打过」** —— 那正是会凭空造出 G3 的地方。
            misses.append({"line": line[:200], "why": "url 拼不出来"})
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


# ── 「控件 → 端点」这条边从哪来 ──────────────────────────
#
# P 侧整套账都建在这条边上：点了这个控件，页面发了这几个请求。
# 边一旦造假，G1/G2/G4 全跟着假，**而且是往"看起来更完整"的方向假** ——
# 缺口消失，报告更好看，没有任何一条测试会红。
#
# 所以这里立一张白名单。它拦的不是恶意，是**图省事**：
#   · `observed` —— HAR 里真观测到，这一趟点下去它真发了。
#   · `aborted`  —— L1 把写请求拦下来了，但 method+path 已经到手。
#                   **拦截既是闸门也是事实来源**：拦下来的那一刻，
#                   「这个控件会发这个写请求」已经是观测到的事实。
#   · `static`   —— 前端源码静态提取，**只在构建指纹对得上时**才算数。
#                   源码里写着 ≠ 点下去真会发（条件分支、feature flag、死代码）；
#                   而指纹对不上时连"源码里写着"都不成立 —— 那是另一个版本的源码。
#
# **模型推断不在此列，以后也不许加。** 那是把「猜」从场景层挪到端点层，
# 还更隐蔽：场景层的猜写在 `catalogGaps` 里，读的人知道那是模型说的；
# 端点层的猜混进 `pageEndpoints`，长得跟 HAR 抓来的一模一样。
# 宁可 `endpoints` 为空 —— 空控件按页面归域，那是代码推的、可复现的保守近似。
EDGE_SOURCES = ("observed", "aborted", "static")


def edge_ok(ep: dict, build_fingerprint: str | None = None) -> bool:
    """这条边**说不说得清自己从哪来**。

    ⚠ **没写 `source` 的一律不算数，别默认成 `observed`。**
    默认放行等于这道闸门不存在：以后任何一条新造边的路径，只要"忘了"
    写来源就自动被采信 —— 而这里防的恰恰是忘。
    """
    src = (ep.get("source") or "").strip()
    if src == "static":
        # 指纹没传（`None`）也算对不上。**fail-closed**：
        # 「没查」和「查过了、一致」在这儿绝不能是同一个结果。
        return bool(build_fingerprint) and ep.get("buildFingerprint") == build_fingerprint
    return src in EDGE_SOURCES


def compute_gaps(*, page_items: list[dict] | None,
                 routes: list[dict] | None,
                 scripts: list[dict] | None,
                 index: dict,
                 claimed_domains: set[str] | None = None,
                 route_table_available: bool = True,
                 page_survey_available: bool = True,
                 build_fingerprint: str | None = None,
                 helper_lib: dict[str, str] | None = None) -> dict:
    """三个账本 → 五类缺口。**纯集合运算，不问模型。**

    `scripts` 每条 `{domain, scenarioId, path, text}`。
    `helper_lib` = `{lib/xxx.sh: 正文}`，喂 `qa_script_endpoints.parse_helper_lib`。
    `claimed_domains` = 清单里有场景行的域码（G1/G3 的分界）。

    两条降级声明是**一等公民**，不是附注：
      · 没有路由表 ⇒ `G2 notVerified`（S7.2 已经把这句话准备好了）
      · **没有页面枚举 ⇒ 只剩 G2，那就等于一个更慢的 route-drift** ——
        必须明说，否则这份报告看起来"跑过了、只有 2 类缺口"，
        而它其实一个新维度都没验。
      · **没读到 helper 库 ⇒ Q 边只剩写在行里的 url**（实测（`refs/remotes/origin/main`，369 个脚本） 136 vs 2943，
        差 25 倍），G1/G3 会是一片假缺口。这一条跟上面两条同等，不是附注。
    """
    claimed = set(claimed_domains or ())
    declarations: list[str] = []

    # —— Q 侧 ——
    parsed = qse.parse_helper_lib(helper_lib or {})
    if not parsed["helpers"]:
        declarations.append(
            "没读到 QA 的 helper 库（lib/*.sh），Q 边只认写在行里的 url，"
            "G1/G3 会虚高")
    if parsed["unparsed"]:
        declarations.append(
            "%d 个 helper 的参数位置读不出来，它们的调用点一律记漏读：%s"
            % (len(parsed["unparsed"]),
               "、".join(sorted({u["helper"] for u in parsed["unparsed"]}))))

    q_paths: list[tuple[str, str, str]] = []   # (domain, method, path)
    unextracted: list[dict] = []
    q_inline = q_helper = q_out_of_scope = q_infra = 0
    for sc in scripts or []:
        text = sc.get("text")
        dom, sid = sc.get("domain") or "", sc.get("scenarioId") or ""
        hits, misses = extract_endpoints(text)
        q_inline += len(hits)
        # helper 封装的那一大半。**两个抽取器的命中合并进同一个 `q_paths`** ——
        # 覆盖判定只看"有没有脚本打过这个端点"，跟它写成哪种形状无关。
        hl = qse.extract_helper_calls(text, parsed)
        q_helper += len(hl["hits"])
        q_out_of_scope += len(hl["otherBase"]) + len(_OUT_OF_SCOPE_TOKEN.findall(text or ""))
        q_infra += len(hl["infra"])
        for h in hits + hl["hits"]:
            q_paths.append((dom, h["method"], h["path"]))
        for m in misses + hl["misses"]:
            unextracted.append({"scenarioId": sid, "domain": dom,
                                "line": m["line"], "why": m.get("why") or ""})

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
    edges_unsourced: list[dict] = []
    for it in page_items or []:
        anchor = f"{it.get('page_path') or ''} :: {it.get('anchor') or it.get('label') or ''}"
        raw_eps = it.get("endpoints") or []
        eps: list[dict] = []
        for e in raw_eps:
            if edge_ok(e, build_fingerprint):
                eps.append(e)
            else:
                edges_unsourced.append(
                    {"anchor": anchor, "pagePath": it.get("page_path") or "",
                     "method": (e.get("method") or "").upper(),
                     "path": normalize_path(e.get("path") or ""),
                     "source": str(e.get("source") or "")})
        if raw_eps and not eps:
            # 这个控件**发了请求，只是没有一条说得清出处**。
            # 落进 G4（"点了没有请求"）就是拿一句假话填一个空位：
            # 报告上它会长成「这按钮点下去什么都没发生」，而真相是
            # "发了，但我不敢认"。数在 `edgesUnsourced` 里，别编。
            continue
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

    page_domains: set[str] = set()
    g1: list[dict] = []
    g2: list[dict] = []
    g3: list[dict] = []
    unattributed: list[dict] = []

    def _domains(path: str, group: str | None) -> set[str]:
        return domains_for(path, group, index)

    # G1 / G3：从页面出发
    for k, meta in p_eps.items():
        group = (r_eps.get(k) or {}).get("group") or ""
        doms = _domains(meta["path"], group)
        if _covered(meta["method"], meta["path"]):
            # 测到了 ⇒ 不是缺口，但**这个域在页面上有面**这件事照样成立，
            # 而且是最有力的正面证据。S7.5 靠它把域挡在 `notApplicable` 之外。
            page_domains |= doms
            continue
        if not doms:
            # 归不了属 ≠ 没缺口。单独记账，**不塞进任何一类** ——
            # 塞进 G1 是误报（可能压根不该这个域管），丢掉是漏报（更坏）。
            unattributed.append({"anchor": k, "pagePath": meta["pagePath"]})
            continue
        in_r = k in r_eps
        page_domains |= doms
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
            # 0 也要渲染，理由同上一行注释：这道闸门要是静默，
            # 它就变成了自己要防的那个东西。
            "edgesUnsourced": len(edges_unsourced),
            "pageEndpoints": len(p_eps),
            "routeEndpoints": len(r_eps),
            # Q 边分四本账，别只报一个总数：`helperHits` 一旦掉回 0，
            # 说明 helper 库没读到或对方改了签名 —— 那时候 G1/G3 会**暴涨**，
            # 而暴涨看起来完全像"他们真的少测了很多"。
            "qInlineHits": q_inline,
            "qHelperHits": q_helper,
            "qOutOfScope": q_out_of_scope,
            "qInfraCalls": q_infra,
            "helpersParsed": len(parsed["helpers"]),
            "helpersInfra": len(parsed["infra"]),
            "helpersUnparsed": len(parsed["unparsed"]),
        },
        "endpointsUnextracted": unextracted,
        "endpointsUnattributed": unattributed,
        "edgesUnsourced": edges_unsourced,
        "pageDomains": sorted(page_domains),
    }


# ── 每个域声明「页面维度对我适不适用」 ──────────────────────
#
# 漏掉这一条，新维度上线第一天就废：会系统性地报「这个域缺口巨大」，
# 其实只是那个域的功能**在页面上本来就看不到**（网关、非功能、对外 API、安全）。
#
# 但 `notApplicable` 是个**消音器** —— 判宽一格，那个域的真缺口从此永远不出现，
# **而且永远不会红**。所以三条纪律，方向全是一样的：
#
#   1. **只认正面声明，不认"没观测到"。** 「这一轮没在页面上见到它」既可能是
#      它真没有面，也可能是爬虫压根没跑到那几页 —— 数字上是同一个 0。
#      只有清单自己的「层」列说了话，才算数。
#   2. **认不出来的层一律不消音。** 层列写了个没见过的词 ⇒ `unknown`，
#      不是 `notApplicable`。清单是别人维护的，他加一个新层名，
#      我们这边不能因此把一个域悄悄静音。
#   3. **页面枚举没跑起来时，全世界都是 `unknown`。** 否则一次爬虫失败
#      就把所有域标成"不适用"，报告上一片「无缺口」—— 最毒的那种假绿。
#
# ⚠ **故意不写死 `GW`/`NFR`/`PUB`/`SEC` 这四个域码**（AC 是拿它们举例的）。
#    那是**别人维护的**清单里的编码；写死四个，他加第五个的时候我们不会知道，
#    而症状是那个新域被系统性地误报成"缺口巨大"—— 又一次要靠人去发现。
#    改成认「层」列：判据长在清单自己身上，他加域、改域都自动跟得上。

_UI_TIERS = {"ui", "e2e", "web", "ux", "frontend"}
_NON_UI_TIERS = {"api", "smoke", "nfr", "sec", "perf", "contract",
                 "unit", "integration", "load", "gateway"}

APPLICABLE = "applicable"
NOT_APPLICABLE = "notApplicable"
UNKNOWN = "unknown"


def _tier(raw: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (raw or "").strip().lower())


def page_applicability(*, scenarios: list[dict] | None,
                       page_domains: set[str] | list[str] | None = None,
                       page_survey_available: bool = True) -> dict:
    """每个域：页面维度适不适用。

    返回 `{"byDomain": {域码: {state, reason, tiers}}, "rollup": {...}}`。

    `rollup.denominator` **不含** `notApplicable` 的域，而且它们
    **不给 0 分** —— 0 分和"不适用"在任何一张排行榜上都是天壤之别，
    前者是"这个域很差"，后者是"这个域不归这个维度管"。
    """
    seen = set(page_domains or ())
    by_domain: dict[str, dict] = {}

    tiers_by_domain: dict[str, set[str]] = {}
    for sc in scenarios or []:
        if sc.get("state") == "deprecated":
            # 废弃的场景不参与判断：拿一堆已经不做的场景去决定"这个域有没有面"
            # 是在用过去的事实给现在消音
            continue
        code = sc.get("domain") or ""
        if code:
            tiers_by_domain.setdefault(code, set()).add(_tier(sc.get("tier")))

    for code, tiers in sorted(tiers_by_domain.items()):
        named = {t for t in tiers if t}
        if code in seen:
            by_domain[code] = {"state": APPLICABLE, "tiers": sorted(named),
                               "reason": "页面枚举里真见到过这个域的端点"}
        elif not page_survey_available:
            by_domain[code] = {"state": UNKNOWN, "tiers": sorted(named),
                               "reason": "本轮没有页面枚举 —— 没观测到不等于不适用"}
        elif named and named <= _NON_UI_TIERS:
            by_domain[code] = {"state": NOT_APPLICABLE, "tiers": sorted(named),
                               "reason": "清单里这个域的场景全是非 UI 层："
                                         + "/".join(sorted(named))}
        elif named & _UI_TIERS:
            by_domain[code] = {"state": APPLICABLE, "tiers": sorted(named),
                               "reason": "清单里这个域有 UI 层场景"}
        else:
            by_domain[code] = {"state": UNKNOWN, "tiers": sorted(named),
                               "reason": "层列没写或写了没见过的词 —— 不消音"}

    na = sorted(c for c, v in by_domain.items() if v["state"] == NOT_APPLICABLE)
    unk = sorted(c for c, v in by_domain.items() if v["state"] == UNKNOWN)
    return {
        "byDomain": by_domain,
        "rollup": {
            # 三个数**都要渲染，0 也渲染**：只在非 0 时出现的计数跟"没算过"一样
            "denominator": len(by_domain) - len(na),
            "notApplicable": na,
            "unknown": unk,
        },
    }


# ── G1/G2 → 可直接粘贴的清单表行 ────────────────────────────
#
# 「可直接粘贴」是这条 Story 的**唯一**验收点，而它只有一种诚实的验法：
# **把生成的行喂回 `qa_catalog.parse_catalog`，看它认不认**。
# 断言 `markdown == "| POL-06 | … |"` 什么都证明不了 —— 那只是在核对我自己
# 编出来的形状。（第一版差点写成 `| `POL-06` | …`，带反引号 —— `_ROW_RE`
# 不允许首列有反引号，那行会掉进 `unparsedRows`：**粘进去了，但清单认不出来**，
# 而人看着表格渲染得好好的。）
#
# 编号**一经分配永不复用**（清单自己的规矩）。三个必须一起满足，少一个都会
# 造出「两条不同的场景共用一个 ID」，而症状要到几个月后有人翻 git 历史
# 或者脚本头 `@scenario` 对不上时才出现：
#
#   1. **算最大号时废弃的照算。** 废弃 ≠ 号还回来了。
#   2. **不填空洞。** 中间缺的号是别人退役掉的，不是空位。
#   3. **同一批里的多条各拿各的号。** 全给 max+1 的话，粘进去 `parse_catalog`
#      只留第一条、其余进 `duplicateIds` —— **缺口看着归档了，其实凭空消失**。
#
# 不知道的格子（优先级 P、风险 R）**写成 `?`，不猜**。猜一个 `P2` 上去，
# 一个本该 P0 的缺口就被我们自己埋到队尾了，而且再没人会重新问一遍。

_UNSET = "?"
_UNSET_PRIORITY = "P?"
_MAX_ID_NUM = 999          # 清单 ID 形状是 `\d{2,3}`，超了就不是合法行


def _cell(text: str, limit: int = 80) -> str:
    """表格单元格：竖线会把一行劈成两列，换行会把一行劈成两行。"""
    out = " ".join((text or "").split()).replace("|", "/")
    return out[:limit].strip()


def _existing_numbers(scenarios: list[dict] | None) -> dict[str, tuple[int, int]]:
    """`{域码: (已分配的最大号, 位宽)}` —— **废弃的照算**。

    废弃只是那条场景不做了，号还占着：脚本头里的 `@scenario`、git 历史、
    过往报告全都指着它。复用等于让一个 ID 在不同时间指两个东西。
    """
    out: dict[str, tuple[int, int]] = {}
    for sc in scenarios or []:
        code, _, num = (sc.get("id") or "").rpartition("-")
        if not code or not num.isdigit():
            continue
        top, width = out.get(code, (0, 2))
        out[code] = (max(top, int(num)), max(width, len(num)))
    return out


def _proposed_title(row: dict) -> str:
    if row.get("kind") == "G1":
        label = _cell(row.get("label") or "")
        page = _cell(row.get("pagePath") or "", 40)
        if label:
            return _cell(f"{page} {label}" if page else label)
    return _cell(f"{(row.get('method') or '').upper()} {row.get('path') or ''}")


def propose_rows(*, gaps: dict, scenarios: list[dict] | None = None) -> dict:
    """G1/G2 → 清单表行草案。

    返回 `{"rows": [...], "blocked": [...], "counters": {...}}`。
    `blocked` 是**提不出行**的那些 —— 丢掉它们就是把缺口弄丢，所以单独记一笔。
    """
    used = _existing_numbers(scenarios)

    cand = list(gaps.get("g1") or []) + list(gaps.get("g2") or [])
    # 排序要确定：同一份输入两次跑出来的号必须一样，否则上周有人照着提案
    # 粘了 POL-06，这周同一个缺口变成 POL-07，对不上账
    cand.sort(key=lambda r: ((r.get("domain") or ""), (r.get("kind") or ""),
                             (r.get("anchor") or "")))

    # 一个组可以映射到**多个**域码（S7.1：那个映射是集合）。两条都提，
    # 但标出来让人挑一个 —— 悄悄挑一个等于替别人的清单做归属决定
    doms_by_anchor: dict[str, set[str]] = {}
    for r in cand:
        doms_by_anchor.setdefault(r.get("anchor") or "", set()).add(r.get("domain") or "")

    rows: list[dict] = []
    blocked: list[dict] = []
    for r in cand:
        code = r.get("domain") or ""
        anchor = r.get("anchor") or ""
        if not code:
            blocked.append({"anchor": anchor, "reason": "归不了属，先补域码表第三列"})
            continue
        top, width = used.get(code, (0, 2))
        nxt = top + 1
        if nxt > _MAX_ID_NUM:
            # 硬吐一个 4 位号出去 = 粘进清单里不被认。宁可提不出来，也不提个假的
            blocked.append({"anchor": anchor, "domain": code,
                            "reason": f"{code} 的编号已到 {top}，再加就超出清单的三位格式"})
            continue
        used[code] = (nxt, width)

        kind = r.get("kind") or ""
        # G1 是**在页面上真点到的控件** ⇒ 层就是 ui，这是观测到的事实；
        # G2 只知道路由表里有这条端点，页面上有没有面**没观测过** ⇒ `?`
        tier = "ui" if kind == "G1" else _UNSET
        sid = f"{code}-{nxt:0{width}d}"
        # 兜底在 `_proposed_title` 里面（控件没有可读文案时退回方法+路径）。
        # 这里**不再兜一次** —— 死代码会让人以为这份保证是本地的
        title = _proposed_title(r)
        sibling = sorted(d for d in doms_by_anchor.get(anchor, set()) if d and d != code)

        rows.append({
            "id": sid,
            "domain": code,
            "kind": kind,
            "title": title,
            "priority": _UNSET_PRIORITY,
            "risk": _UNSET,
            "tier": tier,
            "state": "⬜",
            "markdown": f"| {sid} | {title} | {_UNSET_PRIORITY} | {_UNSET} | {tier} | ⬜ |",
            # 哪几个格子是我们**不知道**的，说清楚。留空的格子会被粘走而没人补
            "todo": ["优先级 P", "风险 R"] + ([] if kind == "G1" else ["执行层"]),
            "ambiguousDomains": sibling,
            "evidence": {"method": r.get("method") or "", "path": r.get("path") or "",
                         "pagePath": r.get("pagePath") or "",
                         "controlAnchor": r.get("controlAnchor") or "",
                         "group": r.get("group") or ""},
        })

    return {
        "rows": rows,
        "blocked": blocked,
        # 三个数**都渲染，0 也渲染**。`unattributed` 是 `compute_gaps` 那边
        # 就归不了属、连 G1/G2 都没进的端点 —— 提案表上不写一笔的话，
        # 它们在这条链上彻底消失
        "counters": {"proposed": len(rows), "blocked": len(blocked),
                     "unattributed": len(gaps.get("endpointsUnattributed") or [])},
    }
