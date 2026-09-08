"""P 边：HAR → 打了哪些端点。两个粒度，**两套时窗，绝不互相摊派**。

· **页面级**（`bucket_entries`）——「打开这个页面，浏览器发了这几个请求」，
  靠**导航时窗**归页。
· **控件级**（`bucket_clicks`）——「点这个按钮，发出了这几个请求」，
  靠**点击时窗**归控件。`QaPageSurveyItem.endpoints` 那一列
  2026-09-04 之前一行都没写过，因为那时爬取一个控件都不点。

**纯函数，零 IO、零模型。**

⚠ **两个粒度绝不能互相摊派。** 把页面级流量写进控件级的 `endpoints`，
等于凭空造出一条 `observed` 的「控件→端点」边，而
`qa_coverage_reconcile.EDGE_SOURCES` 那张白名单防的正是这个 —— 造出来的边
让缺口消失、报告更好看，**没有任何一条测试会红**。所以页面级流量走
`compute_gaps(page_edges=…)` 这个**独立入参**，在报告上也自带
「(页面加载)」的锚点，读的人一眼能看出哪些边没人点过。

反过来也一样：控件级的边**不回填**页面级。页面级那一列问的是"打开它就会发
什么"，掺进点击才发的那几条，这个问题就再也问不出来了。

## 归页靠时窗，不靠 HAR 里的哪个字段

一个角色一个 HAR 文件，里面是这趟从登录到关闭的**所有**请求，混在一起。
`record_har_path` 这种单文件模式下 `pageref` 全是同一个（不按导航分组），
所以唯一可用的锚是**时间**：`crawl_role` 每导航一页记一个时窗，
entry 按 `startedDateTime` 落格。

**落不进任何时窗的一律不归页**（`edgesUnwindowed`，记数 + 留样本）。
挑一个"最近的"页面塞进去是这里最容易犯的错：那条边会长成「这个页面打了这个
端点」，读的人照着去查一个根本不存在的关系，查空两次之后这份报告就没人看了。
同理**落进两个时窗的也不归页**（`edgesAmbiguous`）—— 二选一就是猜。

## 时窗的尾巴要延长到下一次导航

`goto` + `networkidle` 收工之后浏览器**还停在这一页上**，轮询、懒加载、
`setInterval` 都还在发请求 —— 那些请求确实是这一页发的。所以有效时窗是
`[本页 goto 开始, 下一页 goto 开始)`，最后一页延到 context 关闭那一刻。
靠延长才归进来的边记 `edgesTail`：延长是个**判断**，得让人看见它带进来多少。

## 哪些 entry 算「端点」

HAR 里绝大多数是 js/css/图标/HMR。分三档，两个方向的代价不对称：
· **多算**一条（把 `/assets/index-a1b2.js` 当端点）⇒ G1 里多一条噪声，**看得见**。
· **少算**一条（把真 API 当静态资源扔了）⇒ 一个真缺口凭空消失，**永远不报错**。
所以拿不准的一律**算进来并打上 `classified="unclear"`**，不扔。
唯一按方法排掉的是 `OPTIONS` 预检：它不是页面"调"的端点，而且真正那条请求
（GET/POST）本来就在同一份 HAR 里 —— 排掉它一条边都不少。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from app.services.branch_diff_service import normalize_path
from app.services.qa_survey_guard import is_write_request

# Playwright 写在 entry 上的 `_resourceType`（HAR 扩展字段，下划线开头 ——
# **它哪天没了我们管不着**，所以下面还有前缀和扩展名两条兜底，且兜不住的
# 进 `unclear` 而不是被扔掉）。
_API_TYPES = frozenset(("xhr", "fetch", "eventsource", "websocket"))
_ASSET_TYPES = frozenset(("document", "stylesheet", "script", "image", "font",
                          "media", "manifest", "texttrack", "ping"))

# 兜底二：一眼是静态文件的扩展名。**只认结尾**，`/api/v1/files.js/meta` 不算。
_ASSET_EXT = re.compile(
    r"\.(?:js|mjs|cjs|jsx|ts|tsx|css|map|png|jpe?g|gif|svg|webp|avif|ico|bmp|"
    r"woff2?|ttf|otf|eot|mp[34]|webm|wav|wasm|html?|txt|pdf)$", re.I)

# 落进哪一格的样本留几条 —— 它是给人看**形状**的，不是给人看全量的。
_SAMPLE = 20

# 归一化后的 url 被 `drop_credentials` 截断过的标记（那边超过 300 字的串
# 一律尾巴换成这个字符）。
_TRUNCATED = "…"


def _ts(raw) -> datetime | None:
    """HAR 的 `startedDateTime` → tz-aware datetime；读不出来返回 `None`。

    **不带时区的一律按 UTC 读**，而不是按本机时区：本机时区一变，
    整趟的边会集体落到时窗外面 —— 那至少表现成 `edgesUnwindowed` 一片，
    看得见；按本机时区猜则会把边**归到错的页面**上，看不见。
    """
    s = str(raw or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def api_prefixes_from_routes(routes) -> tuple[str, ...]:
    """从 R 侧路由表推出「API 前缀」。**只当兜底用**（`_resourceType` 优先）。

    每条路由取**第一段和前两段各一个**（`/api/v1/teams` → `/api` 和 `/api/v1`）。
    为什么两个都要：被测系统自报的 676 条路由分布在 `/api/v1`、`/api/public`、
    `/api/auth`、`/mcp` 四处 —— 只取两段的话 `/mcp/tools/call` 会碎成
    `/mcp/tools`（每个资源一个前缀），只取一段又会把 `/api` 底下的分不出来。
    宽一点在这里没有代价：静态资源不走 `/api`，而且 `_resourceType` 判出来的
    asset **优先于**前缀判定（顺序见 `classify_entry`）。

    **拿不到路由表就是空元组** —— 那时候分类只剩 `_resourceType` 和扩展名，
    `bucket_entries` 会把这件事写进 `declarations`。
    """
    out: set[str] = set()
    for r in routes or []:
        p = str((r or {}).get("path") or "").strip()
        if not p.startswith("/"):
            continue
        segs = [s for s in p.split("/") if s]
        if not segs:
            continue
        out.add("/" + segs[0])
        out.add("/" + "/".join(segs[:2]))
    return tuple(sorted(out))


def classify_entry(entry: dict, api_prefixes: tuple[str, ...] = ()) -> str:
    """一条 HAR entry 是 `api` / `asset` / `unclear`。

    `unclear` **不是"扔掉"的意思**，是"算进来但标出来"（见模块头那段代价对比）。
    """
    rt = str((entry or {}).get("_resourceType") or "").strip().lower()
    if rt in _API_TYPES:
        return "api"
    if rt in _ASSET_TYPES:
        return "asset"
    url = str(((entry or {}).get("request") or {}).get("url") or "")
    path = urlsplit(url).path if url else ""
    if any(path == p or path.startswith(p + "/") for p in api_prefixes):
        return "api"
    if _ASSET_EXT.search(path):
        return "asset"
    return "unclear"


def effective_windows(windows, closed_at=None) -> list[dict]:
    """把 `crawl_role` 记的时窗按「尾巴延到下一次导航」算出有效区间。

    返回 `[{path, start, end, tightEnd}]`（`datetime`），按 `start` 排序。
    区间是**左闭右开**的，这样相邻两页的边界只可能落进一格 —— 换成闭区间
    的话，恰好压在边界上的请求会同时命中两页，然后被 `edgesAmbiguous` 吃掉，
    白丢一条真边。

    读不出时间的时窗**整条丢掉并不参与延长**（它的 `path` 会因此一条边都
    拿不到）—— 拿一个读不出来的边界去延长别人的时窗，等于把边归到错的页上。

    时窗上 `tail: False` = **这一格不许延长**（登录那格就是：提交完浏览器自己
    跳走了，延长会把落地页的流量记到 `/login` 名下）。它右边那段真空里的请求
    落进 `edgesUnwindowed` —— 记不了账好过归错页。
    """
    rows = []
    for w in windows or []:
        start = _ts((w or {}).get("startedAt"))
        if start is None:
            continue
        end = _ts((w or {}).get("endedAt")) or start
        rows.append({"path": str((w or {}).get("path") or ""),
                     "start": start, "tightEnd": max(end, start),
                     # `kind="directed"` = **有向链路自己那一格**（我们亲手点的
                     # 建/改/删）。它在这本账里只做两件事：占住那段时间（免得
                     # 那些请求落进 `edgesUnwindowed`，读起来像"归不了页"），
                     # 以及当上一页的右边界。**一条页面级边都不许造** ——
                     # 造了就是一句「打开这一页会自动 POST」的错话，
                     # 而对账那边会拿它去判 QA 有没有测这个写接口。
                     "kind": str((w or {}).get("kind") or "page"),
                     "tail": (w or {}).get("tail", True) is not False})
    rows.sort(key=lambda r: r["start"])
    closed = _ts(closed_at)
    for i, r in enumerate(rows):
        nxt = rows[i + 1]["start"] if i + 1 < len(rows) else closed
        r["end"] = max(r["tightEnd"], nxt) if (nxt and r["tail"]) else r["tightEnd"]
    return rows


def _match(rows: list[dict], at: datetime) -> list[dict]:
    """命中哪几格。区间左闭右开，**第二遍只为宽度为 0 的那格兜底**。

    左闭右开是为了让压在两页边界上的请求只可能落进一格（闭区间会让它同时命中
    两页，然后被 `edgesAmbiguous` 吃掉 —— 白丢一条真边）。代价是宽度为 0 的
    时窗（`endedAt` 没记上、退化成 `start == end`）永远命中不了，所以第二遍拿
    `tightEnd` 做闭区间捞一次。捞出来两格照样算 ambiguous，不二选一。
    """
    return [r for r in rows if r["start"] <= at < r["end"]] or \
           [r for r in rows if r["start"] <= at <= r["tightEnd"]]


def _path_of(url: str) -> tuple[str, str]:
    """url → (归一化路径, 说不通的理由)。理由非空就别造边。

    截断过的 url 只有一种情况能用：截断点在 `?` 之后 —— 那时候被切掉的是
    query，而 query 本来就要剥。路径本身可能被切了一半的，**记账不造边**：
    切出来的半截路径会变成一个根本不存在的端点（多一条假缺口），而真的那条
    同时消失。

    省略号从哪来（顺序别记反）：`drop_credentials` 的 300 字截尾对
    `key == "url"` 有豁免（走 `_clean_url`，只换 query 的值），所以**今天**
    `request.url` 走不到这个分支；能走到的是它的**深度封顶**（`depth > 12`
    返回 `"…"`）。这段是为豁免哪天被拿掉留的 —— 那种改动不报错，只会让一批
    P 边安静地变成假路径。封样在 `test_qa_page_traffic.py`。
    """
    raw = str(url or "")
    if not raw:
        return "", "url 是空的"
    if raw.endswith(_TRUNCATED) and "?" not in raw:
        return "", "url 被截断过，路径可能不全"
    path = urlsplit(raw).path
    if not path:
        return "", "url 里没有路径"
    return normalize_path(path), ""


def bucket_entries(har: dict | None, windows, *, role: str = "",
                   closed_at=None, api_prefixes: tuple[str, ...] = ()) -> dict:
    """一个角色的 HAR + 它的时窗 → 页面级边 + 账本。

    返回 `{edges, counters, samples, declarations}`。
    边形状：`{pagePath, method, path, source, status, role, classified, tail}`。

    `source` 只有两种，**都在 `EDGE_SOURCES` 里**：
      · `observed` —— 有响应，这一趟真发出去也真回来了。
      · `aborted`  —— L1 把写请求拦下来了（HAR 里没有 response）。
        **拦截既是闸门也是事实来源**：拦下的那一刻，「这个页面会发这个写请求」
        已经是观测到的事实（`qa_coverage_reconcile` 那张白名单的原话）。
        判「是不是写」复用 `is_write_request`，不在这里另写一套 —— 两套判据
        对不上的时候，`aborted` 会静静变成 `observed`。
    """
    log = ((har or {}).get("log") or {}) if isinstance(har, dict) else {}
    entries = log.get("entries") or []
    rows = effective_windows(windows, closed_at)

    edges: list[dict] = []
    samples: dict[str, list] = {"unwindowed": [], "ambiguous": [], "unusable": []}
    c = {"entriesTotal": len(entries), "apiEntries": 0, "assetEntries": 0,
         "unclearEntries": 0, "preflightEntries": 0, "edgesObserved": 0,
         "edgesAborted": 0, "edgesTail": 0, "edgesUnwindowed": 0,
         "edgesAmbiguous": 0, "edgesUnusable": 0, "windows": len(rows),
         # 401/403 单独记一格。**不是为了过滤掉它们** —— 页面确实调了这个端点，
         # 那条边是真的。是为了让"这一趟其实没登上"能自己冒出来：
         # 一次没登上的爬取，别的账全是绿的（分片 ok、页数够、边也有），
         # 只有这一格会异常地高。见下面 `merge_edges` 里的 declaration。
         "edgesUnauthorized": 0,
         # 有向链路那段时间里的请求。**单独一格**：算进 `edgesUnwindowed`
         # 会让"我们自己造的数据那几下"看起来像归不了页的漏账。
         "edgesDirected": 0}

    for e in entries:
        if not isinstance(e, dict):
            continue
        req = e.get("request") or {}
        method = str(req.get("method") or "").strip().upper()
        if method == "OPTIONS":
            c["preflightEntries"] += 1
            continue
        kind = classify_entry(e, api_prefixes)
        if kind == "asset":
            c["assetEntries"] += 1
            continue
        c["apiEntries" if kind == "api" else "unclearEntries"] += 1

        path, why = _path_of(req.get("url") or "")
        at = _ts(e.get("startedDateTime"))
        if why or at is None:
            c["edgesUnusable"] += 1
            if len(samples["unusable"]) < _SAMPLE:
                samples["unusable"].append(
                    {"method": method, "url": str(req.get("url") or "")[:200],
                     "why": why or "读不出 startedDateTime"})
            continue
        hit = _match(rows, at)
        if not hit:
            c["edgesUnwindowed"] += 1
            if len(samples["unwindowed"]) < _SAMPLE:
                samples["unwindowed"].append({"method": method, "path": path,
                                              "at": at.isoformat()})
            continue
        if len(hit) > 1:
            c["edgesAmbiguous"] += 1
            if len(samples["ambiguous"]) < _SAMPLE:
                samples["ambiguous"].append(
                    {"method": method, "path": path, "at": at.isoformat(),
                     "pages": [r["path"] for r in hit]})
            continue
        win = hit[0]
        if win.get("kind") == "directed":
            # 有向链路的那几下有自己的账（`ledger["directed"]` 里的
            # `chain.writes`）和自己的控件级边（点击时窗）。页面级这本
            # **只记一个数** —— 页面级问的是「打开这一页发了什么」。
            c["edgesDirected"] += 1
            continue
        resp = e.get("response") or {}
        status = resp.get("status")
        blocked = not isinstance(status, int) or status <= 0
        # 写请求没有响应 ⇒ 是 L1 拦下的那一刻；读请求没有响应就是这一趟没成
        # （网络抖动/超时）——**那也照记 `observed`**：请求确实发出去了，
        # 「页面会调这个端点」不因为没收到回包而变假。
        source = "aborted" if (blocked and is_write_request(method, str(req.get("url") or ""))) \
            else "observed"
        tail = at >= win["tightEnd"]
        if isinstance(status, int) and status in (401, 403):
            c["edgesUnauthorized"] += 1
        c["edgesAborted" if source == "aborted" else "edgesObserved"] += 1
        if tail:
            c["edgesTail"] += 1
        edges.append({"pagePath": win["path"], "method": method, "path": path,
                      "source": source, "status": status if isinstance(status, int) else 0,
                      "role": role, "classified": kind, "tail": tail})

    declarations: list[str] = []
    if not rows:
        declarations.append(
            f"角色 {role or '?'} 没有可用的导航时窗，这一份 HAR 一条边都归不了页")
    if not api_prefixes:
        declarations.append(
            "没有路由表前缀兜底，entry 分类只靠 Playwright 的 `_resourceType` "
            "和扩展名 —— 认不出的都记 `unclear` 并照样进 P 边")
    return {"edges": edges, "counters": c, "samples": samples,
            "declarations": declarations}


def merge_edges(results) -> dict:
    """多个角色的桶合成一份 P 边。**同一条边被几个角色看见就是几个角色。**

    合并键是 `(pagePath, method, path)`，角色取并集 —— 不是"拿主爬那份当底"：
    低权角色看得见、主爬这个只读账号看不见的请求，正是角色维度唯一有价值的
    信号（`merge_shards` 那边同一个理由）。

    `source` 上 `aborted` 压过 `observed`：拦下过就是拦下过，那是要能看见的事实。
    """
    edges: dict[tuple, dict] = {}
    counters: dict[str, int] = {}
    samples: dict[str, list] = {}
    declarations: list[str] = []
    for res in results or []:
        for k, v in (res.get("counters") or {}).items():
            counters[k] = counters.get(k, 0) + int(v or 0)
        for k, v in (res.get("samples") or {}).items():
            samples.setdefault(k, [])
            samples[k].extend(v[:max(0, _SAMPLE - len(samples[k]))])
        for d in res.get("declarations") or []:
            if d not in declarations:
                declarations.append(d)
        for e in res.get("edges") or []:
            key = (e.get("pagePath") or "", e.get("method") or "", e.get("path") or "")
            cur = edges.get(key)
            if cur is None:
                row = dict(e)
                row["roles"] = [e["role"]] if e.get("role") else []
                row.pop("role", None)
                edges[key] = row
                continue
            if e.get("role") and e["role"] not in cur["roles"]:
                cur["roles"].append(e["role"])
            if e.get("source") == "aborted":
                cur["source"] = "aborted"
            cur["tail"] = bool(cur.get("tail")) and bool(e.get("tail"))
    out = [edges[k] for k in sorted(edges)]
    for row in out:
        row["roles"] = sorted(row["roles"])
    counters["pageEdges"] = len(out)

    # 「这一趟其实没登上」要能自己冒出来。判据只用这一趟自己的数：
    # 401/403 占了可用边的三成以上，那就不是"某个角色少个权限"，
    # 是会话压根没建起来 —— 那种情况下 P 边记的是登录页的流量，
    # 而**别的每一格都正常**（分片 ok、页数够、边也不少）。
    # 门槛写死 30% 是有意的：登录没成时实测是 35%，而正常一趟里
    # 401 只会零星出现在低权角色够不着的那几个端点上。
    seen = counters.get("edgesObserved", 0) + counters.get("edgesAborted", 0)
    un = counters.get("edgesUnauthorized", 0)
    if seen and un * 10 >= seen * 3:
        declarations.append(
            f"{un}/{seen} 条 P 边是 401/403 —— 这一趟多半**根本没登进去**，"
            f"记下来的是登录页的流量。别拿它当「页面不调这些端点」的证据；"
            f"先看 `loginFailed` 和 `selectorReport`（只命中登录框那几条 "
            f"= 每一页渲染的都是登录页）。")

    return {"edges": out, "counters": counters, "samples": samples,
            "declarations": declarations}


# ══ 控件级 P 边：点击时窗 ══════════════════════════════════════════════
#
# 需求出处：docs/qa-domain-live-verification-plan.md §13.2b。
# 招数和页面级**同一个**（时间是唯一可用的锚），换的只是窗口：
# 页面级是「本页 goto → 下一页 goto」，控件级是「点下去 → 反应稳定下来」。
#
# 三处刻意不同，每一处都是为了不把边归错主人：
#
# 1. **一律不延长尾巴。** 页面级要延长，因为 networkidle 之后浏览器还停在
#    那一页上轮询，那些请求确实是它发的。控件级不行：点完紧接着就是关层 /
#    goto 回来，延长会把关层和重载的流量算成"点这个按钮发的"。
#    两次点击之间那段真空里的请求**谁也不算** —— 记不了账好过归错控件。
# 2. **没有右边界的时窗整条不算**（`clickWindowsUnclosed`）。
#    读不出右边界的窗必然一条边都归不进来，而"归不进来"和"点了确实没发请求"
#    在产物上长得一模一样 —— 后者是 G4 的判据，混进去就是一条假 G4。
# 3. **落在所有点击时窗之外的 entry 不记账。** 页面级那边"归不了页"是个缺口，
#    这边不是：一趟里绝大多数请求本来就是页面加载发的，它们有自己的归属。
#    在这儿数一个几百的 unwindowed 只会让人以为漏了几百条边。


def effective_click_windows(windows) -> tuple[list[dict], list[dict]]:
    """点击时窗 → 有效区间。**不延长，不猜边界。**

    回两样：能用的 `[{key, pagePath, anchor, label, effect, start, end, tightEnd}]`
    （按 `start` 排序），和读不出边界、整条弃掉的那几格。

    `key` 是归属键（`qa_page_survey_crawl.item_key`），**不是 `(页, anchor)`**：
    弹层里的控件带 `[新建]` 这样的前缀，同名同 anchor 的两行只有 key 分得开。
    """
    rows: list[dict] = []
    unclosed: list[dict] = []
    for w in windows or []:
        w = w or {}
        start = _ts(w.get("startedAt"))
        end = _ts(w.get("endedAt"))
        row = {"key": str(w.get("key") or ""),
               "pagePath": str(w.get("page") or ""),
               "anchor": str(w.get("anchor") or ""),
               "label": str(w.get("label") or ""),
               "effect": str(w.get("effect") or "")}
        if start is None or end is None or end < start:
            unclosed.append(row)
            continue
        rows.append({**row, "start": start, "end": end, "tightEnd": end})
    rows.sort(key=lambda r: r["start"])
    return rows, unclosed


def bucket_clicks(har: dict | None, windows, *, role: str = "",
                  api_prefixes: tuple[str, ...] = ()) -> dict:
    """一个角色的 HAR + 它的点击时窗 → **控件级**边 + 账本。

    边形状：`{key, pagePath, anchor, label, effect, method, path, source,
    status, role, classified}`。`source` 的两个值和页面级同义（`observed` / `aborted`），
    判"是不是写"同样复用 `is_write_request` —— 两套判据对不上的时候，
    `aborted` 会静静变成 `observed`。

    `effect` 跟着边走，**不许丢**：`navigate` 那一档里混着**目标页自己的加载
    流量**（点「新建」跳到表单页，随后那几条 GET 是新页面发的，不是这个按钮
    "调"的）。丢了 `effect`，这些边就会以「点这个按钮会调这个端点」的名义
    进对账，而那是一句错话；留着它，读的人一眼分得开。

    `attempted` 是「这个控件我们真点过」的唯一凭据 —— 少了它，
    「点了没发请求」和「压根没点」就写不出差别（见 `attach_control_edges`）。
    """
    log = ((har or {}).get("log") or {}) if isinstance(har, dict) else {}
    entries = log.get("entries") or []
    rows, unclosed = effective_click_windows(windows)

    edges: list[dict] = []
    samples: dict[str, list] = {"clickAmbiguous": [],
                                "clickUnclosed": unclosed[:_SAMPLE]}
    c = {"clickWindows": len(rows), "clickWindowsUnclosed": len(unclosed),
         "controlEdges": 0, "controlEdgesObserved": 0, "controlEdgesAborted": 0,
         "controlEdgesAfterNavigate": 0, "controlEdgesAmbiguous": 0,
         "controlEdgesUnusable": 0, "controlEdgesUnauthorized": 0,
         # 「点了、有窗、一条边都没发」——**这才是 G4 要找的东西**。
         # 它和「压根没点」必须分开：后者那一行的 `endpoints` 是 NULL。
         "controlsSilent": 0, "controlsWithEdges": 0}

    hit_windows: set[int] = set()
    for e in entries:
        if not isinstance(e, dict):
            continue
        req = e.get("request") or {}
        method = str(req.get("method") or "").strip().upper()
        if method == "OPTIONS":
            continue
        kind = classify_entry(e, api_prefixes)
        if kind == "asset":
            continue

        path, why = _path_of(req.get("url") or "")
        at = _ts(e.get("startedDateTime"))
        if why or at is None:
            # 页面级那边已经把这条记进 `edgesUnusable` 了，这儿是本粒度的同一格
            # —— 两个粒度各记各的账，加起来不是重复计数。
            c["controlEdgesUnusable"] += 1
            continue
        hit = _match(rows, at)
        if not hit:
            continue                 # 页面加载发的，有自己的归属（见上面第 3 条）
        if len(hit) > 1:
            c["controlEdgesAmbiguous"] += 1
            if len(samples["clickAmbiguous"]) < _SAMPLE:
                samples["clickAmbiguous"].append(
                    {"method": method, "path": path, "at": at.isoformat(),
                     "controls": [r["key"] for r in hit]})
            continue
        win = hit[0]
        hit_windows.add(id(win))
        resp = e.get("response") or {}
        status = resp.get("status")
        blocked = not isinstance(status, int) or status <= 0
        source = "aborted" if (blocked and is_write_request(
            method, str(req.get("url") or ""))) else "observed"
        c["controlEdgesAborted" if source == "aborted" else "controlEdgesObserved"] += 1
        if isinstance(status, int) and status in (401, 403):
            c["controlEdgesUnauthorized"] += 1
        if win["effect"] == "navigate":
            c["controlEdgesAfterNavigate"] += 1
        edges.append({"key": win["key"],
                      "pagePath": win["pagePath"], "anchor": win["anchor"],
                      "label": win["label"], "effect": win["effect"],
                      "method": method, "path": path, "source": source,
                      "status": status if isinstance(status, int) else 0,
                      "role": role, "classified": kind})

    c["controlEdges"] = len(edges)
    c["controlsWithEdges"] = len(hit_windows)
    c["controlsSilent"] = len(rows) - len(hit_windows)

    declarations: list[str] = []
    if unclosed:
        declarations.append(
            f"角色 {role or '?'} 有 {len(unclosed)} 次点击没记下右边界，"
            "这几个控件的 endpoints 留 NULL（没算过）——**别读成「点了没发请求」**，"
            "后者是 G4，两回事")
    return {"edges": edges, "counters": c, "samples": samples,
            "declarations": declarations,
            "attempted": [{"key": r["key"], "pagePath": r["pagePath"],
                           "anchor": r["anchor"], "effect": r["effect"]}
                          for r in rows]}


def merge_control_edges(results) -> dict:
    """多个角色的控件级桶合成一份。合并键 `(key, method, path)`。

    和 `merge_edges` 同构：角色取并集、`aborted` 压过 `observed`。
    `attempted` 也要合 —— 一个控件在 A 角色那儿没点成、在 B 角色那儿点成了，
    合完必须算"点过"。
    """
    edges: dict[tuple, dict] = {}
    counters: dict[str, int] = {}
    samples: dict[str, list] = {}
    declarations: list[str] = []
    attempted: dict[tuple, dict] = {}
    for res in results or []:
        for k, v in (res.get("counters") or {}).items():
            counters[k] = counters.get(k, 0) + int(v or 0)
        for k, v in (res.get("samples") or {}).items():
            samples.setdefault(k, [])
            samples[k].extend(v[:max(0, _SAMPLE - len(samples[k]))])
        for d in res.get("declarations") or []:
            if d not in declarations:
                declarations.append(d)
        for a in res.get("attempted") or []:
            attempted.setdefault(a.get("key") or "", a)
        for e in res.get("edges") or []:
            key = (e.get("key") or "", e.get("method") or "", e.get("path") or "")
            cur = edges.get(key)
            if cur is None:
                row = dict(e)
                row["roles"] = [e["role"]] if e.get("role") else []
                row.pop("role", None)
                edges[key] = row
                continue
            if e.get("role") and e["role"] not in cur["roles"]:
                cur["roles"].append(e["role"])
            if e.get("source") == "aborted":
                cur["source"] = "aborted"
    out = [edges[k] for k in sorted(edges)]
    for row in out:
        row["roles"] = sorted(row["roles"])
    counters["controlEdgesMerged"] = len(out)
    return {"edges": out, "counters": counters, "samples": samples,
            "declarations": declarations,
            "attempted": [attempted[k] for k in sorted(attempted)]}


def attach_control_edges(items, control_edges, attempted) -> dict:
    """把控件级边挂到 item 的 `endpoints` 上。**只挂真点过的那几行。**

    三态，一个都不许合：
      · 点过、有边  → `endpoints = [...]`
      · 点过、没边  → `endpoints = []`（「算过了，确实没发请求」= G4 的料）
      · **没点过**  → `endpoints = None`（「没算过」）

    合并任意两态的后果方向都一样：一千多个没碰过的控件会变成「点了没发请求」，
    于是 G4 一夜之间从个位数涨到四位数，**全是假的**。

    回账本 `{controlEdgesAttached, controlEdgesSilentRows, controlEdgesUnmatched}`。
    `unmatched` 是有边、但在 item 里找不着对应行的（弹层内部的按钮、
    去重合过的行）——**记数，不硬塞**：塞给一个凑近的 anchor
    就是把边归给了错的按钮，而那种错不报错。
    """
    by_key: dict[str, list] = {}
    for e in control_edges or []:
        by_key.setdefault(e.get("key") or "", []).append(
            {kk: vv for kk, vv in e.items()
             if kk not in ("key", "pagePath", "anchor")})
    tried = {a.get("key") or "" for a in attempted or []}
    matched: set[str] = set()
    attached = silent = 0
    for it in items or []:
        k = it.get("key") or ""
        if k not in tried:
            it["endpoints"] = None       # 没点过 ≠ 点了没反应
            continue
        # **查不弹出。** 同一个 key 在产物里出现两行是真会发生的
        # （`merge_shards` 只跨分片合，分片内的重复它故意不动）——
        # 弹出去的话第二行会拿到 `[]`，白得一条「点了什么都没发」的 G4。
        rows = by_key.get(k) or []
        matched.add(k)
        it["endpoints"] = rows
        if rows:
            attached += 1
        else:
            silent += 1
    return {"controlEdgesAttached": attached, "controlEdgesSilentRows": silent,
            "controlEdgesUnmatched": sum(len(v) for k, v in by_key.items()
                                         if k not in matched)}
