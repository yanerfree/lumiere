"""四方对比：手工步骤 ↔ UI 脚本 ↔ **真实流量** ↔ 接口场景。

为什么非要真跑一遍：外部 CC 自己抓到过一个最典型的错 ——
页面打开订阅管理时调的是 `/api/v1/subscriptions/provider-unified`，
而它 22 条接口场景全用 `/api/v1/subscriptions/provider`。
后者确实存在、返回 200，所以**用例一直是绿的**；但页面根本不用它 ——
`provider-unified` 坏掉、少给字段、跨租户条目漏掉，那批用例一条都不会红。

这种错静态审核永远看不出来（两个 URL 都合法、都能通）。
只有"真跑一遍 + 看页面到底发了什么请求"能发现。所以审核不能只停在读代码。

判据全是确定的（URL 集合比对、条数比对），不需要模型。
"""
from __future__ import annotations

import re

_API_SEG = re.compile(r"/api/")
_IDLIKE = re.compile(
    r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|/\d{2,}", re.I)
# 每个页面都会发的公共请求，不算"这条用例调的接口"
_NOISE = re.compile(r"auth/(me|login|refresh)|system/services|/health|\.js|\.css|\.map|"
                    r"favicon|sockjs|hot-update|/i18n|global-variables|environments$")


def norm(url: str, method: str = "GET") -> str | None:
    """把一条请求归一成「METHOD 路径」，id 段换成 {id}。判 URL 一不一样只能靠它 ——
    不归一的话每次跑出来的 uuid 都不同，永远比不上。"""
    u = (url or "").split("?")[0]
    if not _API_SEG.search(u):
        return None
    path = "/api/" + u.split("/api/", 1)[1]
    if _NOISE.search(path):
        return None
    return f"{(method or 'GET').upper()} {_IDLIKE.sub('/{id}', path)}"


def _from_traffic(captured: list) -> dict[str, int]:
    out: dict[str, int] = {}
    for q in (captured or []):
        if not isinstance(q, dict):
            continue
        k = norm(q.get("url"), q.get("method"))
        if k:
            out[k] = out.get(k, 0) + 1
    return out


def _from_scenario(steps: list) -> dict[str, int]:
    out: dict[str, int] = {}
    for st in (steps or []):
        # 场景里的 URL 带 ${BASE_URL} 和 ${变量}，先剥掉变量再归一
        # `${var}` 换成 `{id}` —— **不要带斜杠**：`/api/projects/${projId}` 会变成
        # `/api/projects//{id}`，跟流量侧的 `/api/projects/{id}` 永远对不上，
        # 于是每一条带路径变量的步骤都被当成"页面不调的幽灵端点"（滥报）。
        u = re.sub(r"\$\{[^}]+\}", "{id}", str(st.get("url") or ""))
        k = norm(u if "/api/" in u else "/api/" + u.lstrip("/"), st.get("method"))
        if not k:
            continue
        # **整条 URL 全是变量的，跳过**。`${BASE_URL}${path}` 归一之后是
        # `/api/{id}{id}` —— 它跟任何真实流量都对不上，于是每次都被报成
        # 「页面一次都没发过的幽灵端点」。活体验证里就冒出来两条这种。
        # 假的幽灵端点比不报更坏：这条判据是全套审核里最硬的一条，
        # 它一旦开始喊狼来了，真的那条也没人信了。
        body = k.split(" ", 1)[1].replace("/api/", "", 1)
        if not re.sub(r"\{id\}|/", "", body):
            continue
        out[k] = out.get(k, 0) + 1
    return out


def compare(captured: list, scenario_steps: list, manual_steps: list,
            script_content: str | None = None) -> list[dict]:
    """产出**平台事实**（不是猜）。每条都指到具体 URL 或步骤。"""
    traffic = _from_traffic(captured)
    api_chain = _from_scenario(scenario_steps)
    out: list[dict] = []
    if not traffic:
        return out          # 这次没抓到流量（没跑 UI 或被回收了），不下任何结论

    # ① 接口场景用了页面压根不调的端点 —— 就是 provider / provider-unified 那个错
    # **只比归一后的完整 key，不做 substring 兜底。**
    # 第一版加了「路径是某条流量的子串就算命中」想容忍 id 差异，结果
    # `GET /api/v1/subscriptions/provider` 是 `.../provider-unified` 的前缀 ——
    # 它把自己要抓的那个案例滤掉了。id 差异两边都已经归一成 {id}，不需要兜底。
    ghosts = [k for k in api_chain if k not in traffic]
    if ghosts:
        out.append({"kind": "endpoint_not_used_by_page", "severity": "blocker", "where": "api",
                    "detail": f"接口场景里这些端点，这次页面执行**一次都没发过**："
                              f"{'、'.join(ghosts[:5])}。"
                              f"真实流量里出现的是：{'、'.join(list(traffic)[:5])}。"
                              f"两个端点都可能存在、都返回 200，所以用例照样绿 —— "
                              f"但页面用的不是你验的那个，它坏掉这条用例不会红。"
                              f"照真实流量改端点。"})

    # ② 页面真调了、接口场景一条都没验的写操作
    missed = [k for k in traffic
              if k.split(" ")[0] in ("POST", "PUT", "PATCH", "DELETE") and k not in api_chain]
    if missed:
        out.append({"kind": "traffic_not_covered", "severity": "major", "where": "api",
                    "detail": f"页面这次真发了这些写请求，接口场景里没有对应步骤："
                              f"{'、'.join(missed[:5])}。要么补进接口链，要么说明为什么不用验。"})

    # ③ 手工步骤和脚本动作数严重不匹配
    if script_content:
        acts = len(re.findall(r"\.(click|fill|check|select_option|press|set_input_files)\(",
                              script_content))
        real = [s for s in (manual_steps or [])
                if isinstance(s, dict) and not str(s.get("action") or "").startswith(("前置", "清理"))]
        if real and acts == 0:
            out.append({"kind": "script_no_action", "severity": "blocker", "where": "ui",
                        "detail": f"手工步骤写了 {len(real)} 步操作，UI 脚本里一个点击/输入都没有 —— "
                                  f"这条脚本没有在页面上做任何事。"})
        elif real and acts < len(real) / 2:
            out.append({"kind": "script_fewer_actions", "severity": "major", "where": "ui",
                        "detail": f"手工步骤 {len(real)} 步，脚本里只有 {acts} 个页面动作 —— "
                                  f"多半有几步没落实（或者步骤写得比实际做的细）。逐条对一遍。"})
    return out
