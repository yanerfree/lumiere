"""业务动作面：从端点算出**动作名**，从脚本的调用顺序算出**业务链路骨架**。

需求出处：[docs/qa-domain-live-verification-plan.md](../../../docs/qa-domain-live-verification-plan.md)
§13.2（链路骨架 = 同一个脚本文件里的调用顺序）+ §14.2（业务不只是增删改查）。

── 为什么不写「动词表」 ────────────────────────────────────────────

§14.2 明写了：**一条都不许靠"列个清单去认"**。列举 submit/approve/enable/publish…
必然漏（下一个产品换成 `commit`/`ratify`/`activate` 就全瞎），而且**漏的时候不报错** ——
报告上只是少了一个动作，看起来完全正常。

所以这里的判据只有三样，全都跟产品无关：

1. **末段是不是 id**（归一化之后就是 `{}`）—— 是 ⇒ 打的是某一条具体数据，
   动作由 HTTP 方法定（增删改查）。
2. **这条路径能不能 GET** —— 这是主判据。能 GET 的是**资源**：
   `POST /x/{}/tools` 配着 `GET /x/{}/tools`，那是"往子集合里加一个"；
   `POST /x/{}/submit` 没有对应的 GET，那才是**动作**。
   ⚠ **不能用"末段是不是 id"来分这两者** —— `/x/{}/tools` 和 `/x/{}/submit`
   长得一模一样。
3. **方法是不是 GET** —— GET 的深路径（`/x/{}/logs`）是**读**，单独一档 `subread`：
   它不是业务动作，但**也不许丢** —— 导出/下载/统计/下钻都长这样，
   丢了就等于"这个域没有导出功能"。

── 没有 GET 集合的时候（`readable_paths=None`）────────────────

那份集合来自 R 边（路由表）或 Q 边自己的 GET 命中。两边都空的时候
**判据要换一个，而且要换成偏保守的那个**，不能拿第 2 条硬判 ——
硬判的话 `POST /api/v1/services`（建一条，最普通的 create）会因为
"抽不到 GET" 被算成一个叫 `services` 的业务动作，
于是每个域都会凭空多出一堆动作名，**而且看起来完全正常**。

保守判据：**末段紧跟在 id 后面**（`/x/{}/submit`）才算动作。
它会漏掉不带 id 的动作（`POST /x/import`、`POST /cache/clear`），
但方向是对的 —— **少报一个动作是缺一格，多报一个是造一格假的**，
后者会让"页面上没这个按钮"变成一条查不出来的假缺口。
`why` 里写明依据弱。

── 为什么链路按**文件顺序**读 ────────────────────────────────────

对方的一个 `.sh` 场景脚本 = 一条业务流程，从上往下就是执行顺序。
「建 → 提交 → 审批 → 生效」是**他写在文件里的事实**，不是我们猜的，
也不需要问模型（§13.2）。所以只要给每条命中记一个行号，按行号排就是骨架。

⚠ **骨架只是假设，不是结论**（§13.3）：它告诉我们"该按什么顺序去点什么"，
结论只由真点下去之后页面发出了什么决定。
"""
from __future__ import annotations

from app.services.branch_diff_service import WILDCARD, normalize_path

# 末段是 id、或者整条就是集合根时，动作名由方法定。
# 这四个词**不是产品名词**，是 HTTP 方法的中性译名 —— 换产品照样成立。
CRUD_BY_METHOD = {
    "POST": "create",
    "PUT": "update",
    "PATCH": "update",
    "DELETE": "delete",
    "GET": "read",
    "HEAD": "read",
    "OPTIONS": "read",
}

READ_METHODS = ("GET", "HEAD", "OPTIONS")

# 「同一条路径」允许差几段部署前缀。R 边来自路由表（带 `/api/v1`），
# Q 边来自对方脚本（`api_get "/services"`，前缀在 `$API` 里）——
# 同一条路径在两本账上写法不一样，按字面比一条都对不上，
# 于是每个普通的 create 都会被判成业务动作。
_MAX_BASE_SEGMENTS = 2


def _segs(path: str) -> list[str]:
    return [x for x in normalize_path(path or "").split("/") if x]


def _tail_eq(short: list[str], long_: list[str]) -> bool:
    """`short` 是不是 `long_` 去掉最多 2 段前缀之后剩下的那一截。

    ⚠ **段与段必须逐字相等，`{}` 只跟 `{}` 相等** —— 故意不做通配容忍，
    也**故意不复用 `qa_coverage_reconcile.covers()`**：那边的取舍是"宁可多命中"，
    在这儿多命中意味着 `POST /x/import` 撞上 `GET /x/{}` 被判成"往子集合里加一个"，
    于是 `import` 这个业务动作**凭空消失**。两个模块，代价反号。
    """
    if not short or len(short) > len(long_):
        return False
    if len(long_) - len(short) > _MAX_BASE_SEGMENTS:
        return False
    return short == long_[-len(short):]


def is_readable(path: str, readable_paths) -> bool:
    """这条路径「能不能 GET」。两个方向都试 —— 谁带前缀我们不知道。"""
    segs = _segs(path)
    if not segs:
        return False
    for r in readable_paths or ():
        rs = _segs(r)
        if _tail_eq(segs, rs) or _tail_eq(rs, segs):
            return True
    return False

# `kind` 的四档。**`action` 和 `crud` 必须分开**：
# 「这个域有几个非增删改查的动作」是 §14.2 要回答的问题，
# 混成一档之后那个问题就永远答不出来了。
VERB_KINDS = ("action", "crud", "subread", "")


def action_verb(method: str, path: str, readable_paths=None) -> dict:
    """`(method, path)` → `{verb, kind, why}`。**纯函数，不认识任何产品名词。**

    `readable_paths` 给的是「已知能 GET 到的归一化路径」集合（R 边最准，
    Q 边自己的 GET 命中也行）。给了才分得清子资源和动作，见模块头第 2 条。
    传 `None` 表示没这份信息 —— 那时一律按动作算，并在 `why` 里说明依据弱。
    """
    m = (method or "").strip().upper()
    p = normalize_path(path or "")
    segs = [x for x in p.split("/") if x]
    crud = CRUD_BY_METHOD.get(m, "")

    if not segs:
        return {"verb": "", "kind": "", "why": "路径读不出来"}

    if not m:
        # 方法读不出来的命中（Q 边那半会有）—— **不许当成动作**，
        # 否则一条读不懂的行会给这个域凭空加一个业务动作。
        return {"verb": "", "kind": "", "why": "方法读不出来"}

    last = segs[-1]

    if last == WILDCARD:
        # `/x/{}` —— 打的是某一条具体数据，动作就是方法本身。
        return {"verb": crud, "kind": "crud", "why": "末段是 id"}

    if m in READ_METHODS:
        # 读的深路径：导出 / 下载 / 统计 / 下钻都长这样。不是动作，但不许丢。
        # 只有一段（`/health`）算不上"下钻"，当普通的读。
        if len(segs) >= 2:
            return {"verb": last, "kind": "subread", "why": "GET 深路径"}
        return {"verb": crud, "kind": "crud", "why": "集合根"}

    if readable_paths is not None:
        if is_readable(p, readable_paths):
            # 能列出来 ⇒ 它是资源（集合或子集合），写它就是增删改查。
            return {"verb": crud, "kind": "crud", "why": "同路径有 GET，是资源"}
        return {"verb": last, "kind": "action", "why": "同路径没有 GET"}

    # 没有 GET 集合 —— 换保守判据：末段紧跟在 id 后面才算动作（见模块头）。
    if len(segs) >= 2 and segs[-2] == WILDCARD:
        return {"verb": last, "kind": "action",
                "why": "末段跟在 id 后面（没给可读路径集合，依据弱）"}
    return {"verb": crud, "kind": "crud",
            "why": "没给可读路径集合，按增删改查算（保守）"}


def readable_paths_of(hits) -> set[str]:
    """从一堆命中里挑出「能 GET 到」的归一化路径，喂给 `action_verb`。

    R 边（`/api/docs/routes`）是更好的来源；这个是**兜底** ——
    对账那边两份都有，优先 R，缺 R 时用这个也比 `None` 强。
    """
    out: set[str] = set()
    for h in hits or []:
        if (h.get("method") or "").upper() in READ_METHODS:
            p = normalize_path(h.get("path") or "")
            if p:
                out.add(p)
    return out


def _key(h) -> tuple[str, str]:
    return ((h.get("method") or "").upper(), normalize_path(h.get("path") or ""))


def chain_of(hits, *, readable_paths=None, keep_reads: bool = False) -> list[dict]:
    """一个脚本文件的命中（按文件顺序）→ 这条业务链路的骨架。

    每一环 `{seq, method, path, verb, kind}`。默认**只留写操作** ——
    链路是"这条业务怎么往前走"，中间那些查列表、查详情是查证不是环节；
    `keep_reads=True` 时连读一起留（调试用）。

    两处压缩，都是为了让骨架能落到"按钮"上：

    · **连续重复压成一条** —— 轮询、重试、循环里打同一条端点，在文件里是几十行，
      在业务上是一步。不压的话骨架会长得像一条"点 40 次提交"的链。
    · **只压连续的** —— `建 → 提交 → 建 → 提交` 是真的两轮，不许压成一轮。

    ⚠ 顺序来自 `lineNo`。命中没有 `lineNo` 时**按传入顺序**处理（两个抽取器
    各自是按行扫的，同一个文件里合并之后就需要 `lineNo` 才排得对）。
    """
    rows = list(hits or [])
    if any(h.get("lineNo") for h in rows):
        rows.sort(key=lambda h: (h.get("lineNo") or 0))

    out: list[dict] = []
    for h in rows:
        m = (h.get("method") or "").upper()
        if not keep_reads and (m in READ_METHODS or not m):
            continue
        v = action_verb(m, h.get("path") or "", readable_paths)
        step = {"method": m, "path": normalize_path(h.get("path") or ""),
                "verb": v["verb"], "kind": v["kind"],
                "lineNo": h.get("lineNo") or 0}
        if out and (out[-1]["method"], out[-1]["path"]) == (step["method"], step["path"]):
            continue
        out.append(step)

    for i, step in enumerate(out, 1):
        step["seq"] = i
    return out


def verb_inventory(hits, *, readable_paths=None) -> dict:
    """一个域的所有命中 → 这个域**有哪些业务动作**。

    回三档，分开数（§14.2）：`actions` 非增删改查的、`crud` 增删改查的、
    `subreads` 读的深路径。每档里每个动作词记它出现在哪几条端点上。

    **这就是"动作面的脚本那一半"** —— 页面那一半在爬取侧，两边一拼出两类发现：
    脚本打过但页面没这个按钮 / 页面有这个动作但脚本一次没打过。
    """
    buckets: dict[str, dict[str, list[str]]] = {"actions": {}, "crud": {}, "subreads": {}}
    bucket_of = {"action": "actions", "crud": "crud", "subread": "subreads"}
    for h in hits or []:
        m = (h.get("method") or "").upper()
        v = action_verb(m, h.get("path") or "", readable_paths)
        b = bucket_of.get(v["kind"])
        if not b or not v["verb"]:
            continue
        ep = f"{m} {normalize_path(h.get('path') or '')}".strip()
        seen = buckets[b].setdefault(v["verb"], [])
        if ep not in seen:
            seen.append(ep)
    return {k: {vb: sorted(eps) for vb, eps in sorted(v.items())} for k, v in buckets.items()}
