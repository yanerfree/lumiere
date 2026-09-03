"""路由表 —— 三方对账里的 **R** 侧，只读一次 HTTP。

`GET {BFF}/api/docs/routes` 是被测系统自己暴露的公开只读接口（无需 JWT）。
它给出「这个系统一共有哪些端点、各归哪个 API 组」，是对账链条

    页面控件 →(HAR)→ 请求 →(normalize_path)→ 路径模板
            →(**这里**)→ API 组 →(域码表第三列)→ 域码 →(清单)→ 场景 ID

中间那一跳的唯一事实源。**别打到网关 :8000 上** —— 那边这条路径是 404。

## 拉不到的时候做什么

**显式声明「本轮无路由表」，并把受影响的那类缺口标成 `notVerified`。**
不是"少算一类缺口然后照常出报告" —— 那样报告上 G2 是 0，而 0 和"没查过"
在页面上长得一模一样，读的人会当成"这个域没有盲区"。这是本模块存在的意义
要抓的那类错，自己先别犯。

## 为什么解析写得这么松

`/api/docs/routes` 是别人的接口，**它的字段名我们说了不算**，哪天从
`{"groups": {...}}` 换成 `{"routes": [...]}` 我们既管不着也拦不住。
所以这里认几种常见形状，认不出来的**逐条记进 `unreadable`**，
而不是静默丢掉 —— 丢一条路由 = 少一个端点 = 少一类缺口，且永远不报错。

## 扔掉的行分两桶，别合并

· `unreadable` —— **形状不认识**。这是我们的解析器该改。
· `skipped` —— 形状认识，但那一行**不是一个可寻址端点**（通配兜底 `/*`、
  `method` 那格漏出来的是处理函数名）。这是被测系统就长这样，判据见 `_skip_reason`。

合成一个数就分不出「该改代码」还是「该照原样接受」；而两边都得有数，
不然「扔了 110 行」和「一行没扔」在报告上是同一个样子。
"""
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ROUTES_PATH = "/api/docs/routes"
# 这是别人环境上的一次只读 GET，卡住就算了，别把整轮对账拖死
TIMEOUT = 15.0
# 认不出来的样本只留头几条：它是给人看形状的，不是给人看全量的
_UNREADABLE_SAMPLE = 20


def _method_path(item: Any) -> tuple[str, str] | None:
    """一条路由 → (METHOD, path)。认不出来返回 None（由调用方记账）。"""
    if isinstance(item, str):
        # "GET /api/foo" 或者光一个路径
        parts = item.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isalpha():
            return parts[0].upper(), parts[1]
        return ("", item.strip()) if item.strip().startswith("/") else None
    if not isinstance(item, dict):
        return None
    path = ""
    for k in ("path", "url", "route", "endpoint"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            path = v.strip()
            break
    if not path:
        return None
    method = ""
    for k in ("method", "methods", "verb"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            method = v.strip().upper()
            break
        if isinstance(v, list) and v:
            method = str(v[0]).strip().upper()
            break
    return method, path


def _skip_reason(method: str, path: str) -> str:
    """这一行是不是「压根不是一个可寻址端点」。是的话返回原因，否则空串。

    实测（2026-09-04，UAG 138 那台）676 行里有 110 行长这样：

        {"method": "echo_route_not_found", "path": "/api/v1/agents/*"}

    那是路由框架的 **no-route 兜底**，`method` 那一格漏出来的是处理函数名。
    这种行留在 R 侧的后果很具体：P 边永远不会有 `PROPFIND /api/v1/*` 这种流量、
    清单也永远不会去测它，于是每一行都稳定产出一条 **G2**——110 条看起来像
    「这个系统有一百多个盲区」的假缺口。缺口报告一旦掺进这种量级的噪声，
    读的人第二次就不看了，真缺口跟着一起沉底。

    判据是**形状**，不是白名单：
      · 方法名必须是纯字母 —— `echo_route_not_found` 带下划线，一眼不是动词；
        而 `PROPFIND` / `REPORT` 这些冷门但真实的动词照收（换白名单就得年年补，
        补漏一个就静默少一个真端点）。
      · 路径带 `*` 的是通配兜底，不是能寻址的端点。

    **别改成静默 `continue`** —— 记进 `skipped` 才能在报告上看见「扔了多少」。
    """
    if method and not method.isalpha():
        return "method 不是 HTTP 动词（像是路由框架的兜底处理函数名）"
    if "*" in path:
        return "路径是通配兜底（带 `*`），不是可寻址端点"
    return ""


def _nested_group(item: Any) -> tuple[str, list] | None:
    """一条「组对象」→ (组名, 它挂着的路由列表)。不是组对象返回 None。

    UAG 的 BFF 就是这个形状：`{"prefix": "adapters", "count": 18, "routes": [...]}`
    —— 组是一层**对象**，真正的路由挂在它的 `routes` 里。不认这一层的话
    676 条路由会整整齐齐地全进 `unreadable`，R 边就此永久 `notVerified`：
    G2 那一列一直写着"未验证"，报告看着还挺诚实，实际是一整维白跑。
    （2026-09-04 活体撞到的就是这个。）
    """
    if not isinstance(item, dict):
        return None
    for k in ("routes", "endpoints", "items", "paths"):
        v = item.get(k)
        if isinstance(v, list):
            break
    else:
        return None
    name = ""
    for k in ("prefix", "group", "tag", "name", "title"):
        g = item.get(k)
        if isinstance(g, str) and g.strip():
            name = g.strip()
            break
    return name, v


def parse_routes(payload: Any) -> dict:
    """路由表原始响应 → `{"routes": [...], "unreadable": [...], "skipped": [...]}`。

    认四种形状：
      1. `{"groups": {"Health": [...], ...}}` / `{"Health": [...], ...}`
      2. `{"routes": [{"group": "Health", "method": "GET", "path": "/healthz"}]}`
      3. 顶层就是列表（同 2 的元素形状）
      4. 组是一层对象、路由挂在它里面：
         `{"groups": [{"prefix": "adapters", "routes": [...]}, ...]}`（UAG 现在这个）

    **组名原样保留，不归一化** —— 归一是对账那一侧的事（`qa_coverage_reconcile`）。
    在这里归一，报告上就再也看不出路由表原文写的是 `Tags` 还是 `Tag`，
    而"组名改了写法"正是要看见的那个信号。
    """
    routes: list[dict] = []
    unreadable: list[dict] = []
    skipped: list[dict] = []

    def keep(group: str, method: str, path: str) -> None:
        why = _skip_reason(method, path)
        if why:
            skipped.append({"group": group, "method": method,
                            "path": path, "why": why})
            return
        routes.append({"group": group, "method": method, "path": path})

    def take(group: str, item: Any, depth: int = 0) -> None:
        nested = _nested_group(item) if depth < 4 else None
        if nested is not None:
            sub, items = nested
            mp = _method_path(item)
            if mp is not None:
                # 既像一条路由、又挂着子路由。**两边都收** —— 只收一边就是静默
                # 少端点，而少一个端点 = 少一类缺口，且永远不报错。
                keep(sub or group, mp[0], mp[1])
            for it in items:
                take(sub or group, it, depth + 1)
            return
        mp = _method_path(item)
        if mp is None:
            if len(unreadable) < _UNREADABLE_SAMPLE:
                unreadable.append({"group": group, "raw": str(item)[:160]})
            return
        keep(group, mp[0], mp[1])

    body = payload
    if isinstance(body, dict):
        for key in ("groups", "byGroup", "data"):
            if isinstance(body.get(key), (dict, list)):
                body = body[key]
                break

    if isinstance(body, dict) and isinstance(body.get("routes"), list):
        body = body["routes"]

    if isinstance(body, dict):
        for group, items in body.items():
            if isinstance(items, list):
                for it in items:
                    take(str(group), it)
            elif isinstance(items, dict):
                # 组名底下是个对象 —— 可能是 `{"routes": [...]}` 那种容器，
                # 交给 `take` 去认；认不出来它自己会记进 unreadable
                take(str(group), items)
            else:
                # 组名底下不是列表 —— 记账，别猜
                if len(unreadable) < _UNREADABLE_SAMPLE:
                    unreadable.append({"group": str(group), "raw": str(items)[:160]})
    elif isinstance(body, list):
        for it in body:
            group = ""
            if isinstance(it, dict):
                for k in ("group", "tag", "tags", "category"):
                    v = it.get(k)
                    if isinstance(v, str) and v.strip():
                        group = v.strip()
                        break
                    if isinstance(v, list) and v:
                        group = str(v[0]).strip()
                        break
            take(group, it)
    else:
        unreadable.append({"group": "", "raw": str(body)[:160]})

    return {"routes": routes, "unreadable": unreadable, "skipped": skipped}


async def fetch_route_table(base_url: str | None,
                            client: httpx.AsyncClient | None = None) -> dict:
    """拉一次路由表。**任何失败都返回 `available=False` + 原因，不抛。**

    返回：
      `{"available": bool, "reason": str, "url": str,
        "routes": [...], "groups": [...], "unreadable": [...], "skipped": [...]}`

    `available=False` 时 `routes` 是空列表 —— 调用方**必须**据此把 G2 标
    `notVerified`，不能把空列表当成"这个系统没有端点"。两者在数字上一模一样，
    在结论上正好相反。
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return {"available": False, "reason": "没有 BASE_URL —— 不知道找哪个环境要路由表，不猜、不用默认值。",
                "url": "", "routes": [], "groups": [], "unreadable": [], "skipped": []}

    url = base + ROUTES_PATH
    try:
        if client is not None:
            resp = await client.get(url, timeout=TIMEOUT)
        else:
            async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                resp = await c.get(url)
        if resp.status_code != 200:
            return {"available": False,
                    "reason": f"{url} 返回 {resp.status_code}"
                              f"（网关 :8000 上这条是 404 —— 先确认端口是 BFF 那个）",
                    "url": url, "routes": [], "groups": [], "unreadable": [], "skipped": []}
        payload = resp.json()
    except Exception as e:  # 网络、超时、非 JSON —— 一律降级，不让它掀掉整轮对账
        logger.warning("路由表拉取失败 %s: %s", url, e)
        return {"available": False, "reason": f"{url} 拉取失败：{type(e).__name__}: {e}"[:300],
                "url": url, "routes": [], "groups": [], "unreadable": [], "skipped": []}

    parsed = parse_routes(payload)
    groups = sorted({r["group"] for r in parsed["routes"] if r["group"]})
    if not parsed["routes"]:
        # 200 但一条都读不出来 ≠ 这个系统没有端点。**这种最像"成功"，所以要单独说。**
        # 全被 `skipped` 扔掉也算这一类，但原因不一样，说清是哪一种。
        why = (f"，{len(parsed['skipped'])} 条全被判成不可寻址端点"
               "（通配兜底/方法名不是动词）—— 这个判据可能过严了"
               if parsed["skipped"] else "（响应形状变了？）")
        return {"available": False,
                "reason": f"{url} 返回 200，但一条路由都没解析出来" + why,
                "url": url, "routes": [], "groups": [],
                "unreadable": parsed["unreadable"], "skipped": parsed["skipped"]}
    return {"available": True, "reason": "", "url": url,
            "routes": parsed["routes"], "groups": groups,
            "unreadable": parsed["unreadable"], "skipped": parsed["skipped"]}


# G2 = 「路由表里有、页面上没见过、清单也没测」的那类盲区。
# 它**完全依赖路由表**：没有路由表就没有"应该有哪些端点"这个底，G2 算不出来。
G2_VERIFIED = "verified"
G2_NOT_VERIFIED = "notVerified"


def route_table_note(table: dict) -> dict:
    """路由表 → 报告里那一段结论。

    **拉不到的时候，报告上必须留下一句话，不能只是让 G2 的数字停在 0。**
    「这个域没有 G2 盲区」和「这一轮我根本没查 G2」在页面上是同一个 0，
    而结论正好相反 —— 前者是好消息，后者是这一趟少验了一整类缺口。
    """
    t = table or {}
    routes = t.get("routes") or []
    unreadable = t.get("unreadable") or []
    # 扔掉的也要有数：`unreadable`（形状不认识）和 `skipped`（认识、但不是端点）
    # 是两件事，合成一个数就分不出「解析器该改」还是「这个系统就这样」。
    skipped = t.get("skipped") or []
    if not t.get("available"):
        return {
            "available": False,
            # 声明的措辞是判据的一部分：页面直接渲染它，别改成"路由表为空"
            "declaration": "本轮无路由表，G2 未验证：" + (t.get("reason") or "原因未记录"),
            "g2": G2_NOT_VERIFIED,
            "routeCount": 0, "groupCount": 0,
            "unreadableCount": len(unreadable),
            "skippedCount": len(skipped),
        }
    return {
        "available": True,
        "declaration": "",
        "g2": G2_VERIFIED,
        "routeCount": len(routes),
        "groupCount": len(t.get("groups") or []),
        # 0 也要渲染：只在非 0 时出现的计数，和"没算过"长得一模一样
        "unreadableCount": len(unreadable),
        "skippedCount": len(skipped),
    }
