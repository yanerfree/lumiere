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


def parse_routes(payload: Any) -> dict:
    """路由表原始响应 → `{"routes": [{group, method, path}], "unreadable": [...]}`。

    认三种形状：
      1. `{"groups": {"Health": [...], ...}}` / `{"Health": [...], ...}`
      2. `{"routes": [{"group": "Health", "method": "GET", "path": "/healthz"}]}`
      3. 顶层就是列表（同 2 的元素形状）

    **组名原样保留，不归一化** —— 归一是对账那一侧的事（`qa_coverage_reconcile`）。
    在这里归一，报告上就再也看不出路由表原文写的是 `Tags` 还是 `Tag`，
    而"组名改了写法"正是要看见的那个信号。
    """
    routes: list[dict] = []
    unreadable: list[dict] = []

    def take(group: str, item: Any) -> None:
        mp = _method_path(item)
        if mp is None:
            if len(unreadable) < _UNREADABLE_SAMPLE:
                unreadable.append({"group": group, "raw": str(item)[:160]})
            return
        method, path = mp
        routes.append({"group": group, "method": method, "path": path})

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

    return {"routes": routes, "unreadable": unreadable}


async def fetch_route_table(base_url: str | None,
                            client: httpx.AsyncClient | None = None) -> dict:
    """拉一次路由表。**任何失败都返回 `available=False` + 原因，不抛。**

    返回：
      `{"available": bool, "reason": str, "url": str,
        "routes": [...], "groups": [...], "unreadable": [...]}`

    `available=False` 时 `routes` 是空列表 —— 调用方**必须**据此把 G2 标
    `notVerified`，不能把空列表当成"这个系统没有端点"。两者在数字上一模一样，
    在结论上正好相反。
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return {"available": False, "reason": "没有 BASE_URL —— 不知道找哪个环境要路由表，不猜、不用默认值。",
                "url": "", "routes": [], "groups": [], "unreadable": []}

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
                    "url": url, "routes": [], "groups": [], "unreadable": []}
        payload = resp.json()
    except Exception as e:  # 网络、超时、非 JSON —— 一律降级，不让它掀掉整轮对账
        logger.warning("路由表拉取失败 %s: %s", url, e)
        return {"available": False, "reason": f"{url} 拉取失败：{type(e).__name__}: {e}"[:300],
                "url": url, "routes": [], "groups": [], "unreadable": []}

    parsed = parse_routes(payload)
    groups = sorted({r["group"] for r in parsed["routes"] if r["group"]})
    if not parsed["routes"]:
        # 200 但一条都读不出来 ≠ 这个系统没有端点。**这种最像"成功"，所以要单独说。**
        return {"available": False,
                "reason": f"{url} 返回 200，但一条路由都没解析出来（响应形状变了？）",
                "url": url, "routes": [], "groups": [],
                "unreadable": parsed["unreadable"]}
    return {"available": True, "reason": "", "url": url,
            "routes": parsed["routes"], "groups": groups,
            "unreadable": parsed["unreadable"]}


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
    if not t.get("available"):
        return {
            "available": False,
            # 声明的措辞是判据的一部分：页面直接渲染它，别改成"路由表为空"
            "declaration": "本轮无路由表，G2 未验证：" + (t.get("reason") or "原因未记录"),
            "g2": G2_NOT_VERIFIED,
            "routeCount": 0, "groupCount": 0,
            "unreadableCount": len(unreadable),
        }
    return {
        "available": True,
        "declaration": "",
        "g2": G2_VERIFIED,
        "routeCount": len(routes),
        "groupCount": len(t.get("groups") or []),
        # 0 也要渲染：只在非 0 时出现的计数，和"没算过"长得一模一样
        "unreadableCount": len(unreadable),
    }
