"""MCP 工具 — LLM Mock（造上游行为 + 断言网关发了什么）。

## 为什么把它接给 CC

被测系统是 **AI 网关**，上游 LLM 不是外围依赖，是**每条调用链测试都绕不开的
东西**。用真上游测网关：慢、费钱、不确定，挂了还分不清是网关的锅还是模型的锅。

而这个 mock 同时是两样东西：

**① 造上游行为** —— 让网关的异常分支可测
   `status_code`（429/500 → 测重试降级熔断）、`delay_ms`（测超时）、
   `finish_reason`（stop/length/content_filter → 测透传）、
   自定义 token 用量（**测网关的计费/配额统计算得对不对**）、
   `model_mode`（测模型映射）、SSE 分片参数（代码注释里就写着
   「对接网关时分片数本身是被验证的指标」）。

**② 断言网关往上游发了什么** —— 这才是决定性的那半
   请求头有没有正确注入鉴权、模型名有没有按映射改写、参数有没有被篡改 ——
   这些在网关**下游**根本看不见，客户端只能看到最终响应。
   没有这个 mock，「网关把请求转对了没有」这条压根验不了。

## 共享资源纪律

mock 路由是**会被改的共享资源**（平台自己的 ④-0 判据：会被改的别共享）。
两条用例同时把 `/v1/chat/completions` 配成不同状态码就会互相打架、还偶发。
所以 `upsert_llm_mock_route` 强制要求路径带一段用例自己的前缀
（`/mock/<用例编号>/...`），天然隔离，不用清理。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_mock import MockRequestLog, MockRoute

# 路由必须带一段自己的前缀 —— 见模块说明里的共享资源纪律
_SHARED_PATHS = {"/v1/chat/completions", "/v1/completions", "/v1/embeddings"}


async def llm_mock_status(session: AsyncSession) -> dict:
    """LLM Mock 服务在不在、有哪些路由、上游地址填什么。"""
    from app.services.llm_mock_manager import mock_server as mgr

    rows = (await session.execute(select(MockRoute).order_by(MockRoute.sort_order))).scalars().all()
    return {
        "running": getattr(mgr, "running", None),
        "port": getattr(mgr, "port", 28100),
        "upstreamBaseUrl": f"http://<平台所在主机>:{getattr(mgr, 'port', 28100)}",
        "routes": [{"id": str(r.id), "name": r.name, "method": r.method, "path": r.path,
                    "enabled": r.enabled, "statusCode": r.status_code,
                    "delayMs": r.delay_ms, "finishReason": r.finish_reason} for r in rows],
        "usage": "把被测网关的上游地址指到 upstreamBaseUrl + 你这条路由的 path，"
                 "就能自己决定上游怎么答（慢/429/截断/自定义 token 用量），"
                 "再用 lum_llm_mock_requests 断言网关到底往上游发了什么。",
    }


async def upsert_llm_mock_route(
    session: AsyncSession,
    name: str,
    path: str,
    status_code: int = 200,
    delay_ms: int = 0,
    response_body: str | None = None,
    finish_reason: str = "stop",
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    model: str | None = None,
    smart: bool = False,
    smart_role: str = "auto",
) -> dict:
    """建/改一条 LLM Mock 路由 —— 决定"上游怎么答"。

    按 `path` 幂等：同一条路径重复调是覆盖，不会堆出一串。

    **path 必须带你自己的前缀**（如 `/mock/TC-FWGL-00001/v1/chat/completions`）。
    直接占用 `/v1/chat/completions` 会被拒 —— 那是所有用例共用的路径，
    你把它配成 429，别人的用例就跟着挂，而且是偶发的、最难查。

    **smart=True 开智能应答**：这条路由的行为改由**请求正文里的指令**决定，
    上面那些 status_code / response_body / finish_reason 参数全部不生效。
    一条路由就能演完所有场景，不用为每个场景各建一条：

      SAY:<文本>   原样回显（只改一个变量做对照实验）
      MODE:HIT     输出含 VIOLATION（不依赖模型的确定性对照）
      MODE:PII     输出含身份证号+手机号，**请求里没有** —— 验护栏查的是输出不是输入
      MODE:EMPTY   零内容事件流（合法形态，网关不该当错误）
      MODE:FILTER  空回复 + finish_reason=content_filter
      MODE:DEFY    无视 stream:false 硬返事件流（验 fail-closed）
      MODE:SLOW    每片 250ms，非流式也按分片累计
      MODE:LOOP    第一轮回 tool_calls，收到 role=tool 后回终局

    `smart_role="checker"`（或路径里带 /checker）= 演**网关护栏调用的那个检查模型**：
    它只回判决，并把「本次收到的待检正文有多长、开头是什么」回显进 reason。
    网关到底把什么喂给了护栏，这是唯一的观测点 —— 断言时读
    `lum_llm_mock_requests` 返回里的 `smartMeta.checkedLen`。
    """
    p = (path or "").strip()
    if not p.startswith("/"):
        return {"error": "path 要以 / 开头"}
    if p in _SHARED_PATHS:
        return {
            "error": f"{p} 是所有用例共用的路径，不给独占。",
            "why": "你把它配成 429/500，别人的用例就跟着挂，而且是偶发的、最难查。",
            "howTo": f"带上你自己的前缀，比如 /mock/<用例编号>{p} —— "
                     "天然隔离，跑完也不用清理。",
        }

    row = (await session.execute(select(MockRoute).where(MockRoute.path == p))).scalars().first()
    created = row is None
    if row is None:
        row = MockRoute(path=p, name=name)
        session.add(row)
    row.name = name
    row.method = "POST"
    row.status_code = status_code
    row.delay_ms = delay_ms
    row.finish_reason = finish_reason
    row.enabled = True
    row.smart_enabled = bool(smart)
    row.smart_role = smart_role if smart_role in ("auto", "upstream", "checker") else "auto"
    if response_body is not None:
        row.response_body = response_body
    if prompt_tokens is not None or completion_tokens is not None:
        row.token_mode = "custom"
        row.custom_prompt_tokens = prompt_tokens
        row.custom_completion_tokens = completion_tokens
    if model:
        row.model_mode = "custom"
        row.custom_model = model
    await session.commit()
    out = {"id": str(row.id), "path": row.path, "created": created,
           "note": "把被测网关这条链路的上游地址指到这个 path 上再跑。"}
    if row.smart_enabled:
        out["smart"] = {
            "role": row.smart_role,
            "usage": "行为由请求正文里的指令决定（SAY: / MODE:xxx），这条路由上的静态响应配置不生效。",
            "assertOn": "跑完读 lum_llm_mock_requests 的 smartMeta：mode 是哪条指令、"
                        "stream 是网关实际发出的值、checker 角色还带 checkedLen/verdict。",
        }
    return out


async def llm_mock_requests(
    session: AsyncSession,
    path: str | None = None,
    limit: int = 20,
) -> dict:
    """**网关到底往上游发了什么** —— 这是断言用的，不是看热闹的。

    鉴权头有没有正确注入、模型名有没有按映射改写、参数有没有被篡改 ——
    这些在网关下游根本看不见，客户端只能看到最终响应。这条是唯一的观测点。
    """
    q = select(MockRequestLog).order_by(MockRequestLog.timestamp.desc()).limit(min(limit, 100))
    if path:
        q = q.where(MockRequestLog.path == path)
    rows = (await session.execute(q)).scalars().all()
    return {
        "requests": [{
            "at": r.timestamp.isoformat() if r.timestamp else None,
            "method": r.method, "path": r.path,
            "caller": r.caller, "ip": r.ip,
            "requestHeaders": r.request_headers,
            "requestBody": r.request_body,
            "requestModel": r.request_model,
            "responseModel": r.response_model,
            "statusCode": r.status_code,
            # 智能应答路由才有。stream 记的是**网关实际发出的值**，流式降级有没有真发生只能看它；
            # checker 角色还带 checkedLen / envelopeLen / verdict —— 护栏拿到了什么，这是唯一的观测点
            "smartMeta": r.smart_meta,
        } for r in rows],
        "total": len(rows),
        "usage": "断言之前先调 lum_llm_mock_reset 清一次，否则上一轮的记录会混进来 —— "
                 "「上游只应收到 1 次请求」这类断言会假过。",
    }


async def llm_mock_reset(session: AsyncSession, path: str | None = None) -> dict:
    """清掉上游请求记录。

    **断言"上游收到几次"之前必须先清**：不清的话上一轮的记录还在，
    「只应收到 1 次」这种断言会假过 —— 而假过比假红更难发现。
    """
    from sqlalchemy import delete as sa_delete

    stmt = sa_delete(MockRequestLog)
    if path:
        stmt = stmt.where(MockRequestLog.path == path)
    res = await session.execute(stmt)
    await session.commit()
    return {"deleted": res.rowcount or 0, "path": path or "(全部)"}


def _req_line(rec: dict) -> str:
    """这条记录的请求行（`GET /api/x HTTP/1.1`）—— method 和路径只在这里面。"""
    return str(rec.get("c2p_request") or "").split("\n", 1)[0].strip()


async def proxy_capture(limit: int = 50, url_contains: str | None = None,
                        method: str | None = None) -> dict:
    """代理观测抓到的真实请求 —— 写接口场景的素材来源。

    活体验证时最费劲的一步是"这个页面动作到底发了哪些请求、body 长什么样"。
    自己开 devtools 抄一遍又慢又容易抄错，而平台的代理已经把它们记下来了。

    **必须能过滤。** 前端跑 Vite 时抓到的绝大多数是 `.jsx?t=` 热更新请求 ——
    实测 156 条里只有 9 条是 `/api/`，limit=50 全被噪声占满，等于抓了也用不了。
    url_contains / method 在**全量**记录上筛，再取最后 limit 条。
    """
    from app.services import proxy_probe_manager as ppm

    probe = getattr(ppm, "proxy_probe", None)
    if probe is None or not getattr(probe, "running", False):
        return {
            "running": False,
            "hint": "代理观测没在跑。到「测试工具 → 代理观测」启动它，"
                    "把浏览器/被测客户端的代理指过去，再来取。",
        }
    # 内部字段名是 _records（不是 events）—— 拿错名字会静默返回空列表，
    # 看起来像"代理开着但一条都没抓到"，而实际是取错了地方。
    all_recs = list(getattr(probe, "_records", []) or [])
    port = getattr(probe, "port", None)

    # **代理跑着但一条都没抓到**是最常见的情况（浏览器没走代理），而"把代理指过去"
    # 这句提示原来只在 running=False 那个分支里 —— 真正需要它的分支反而没有。
    if not all_recs:
        return {
            "running": True, "port": port, "count": 0, "total": 0, "requests": [],
            "hint": f"代理在 {port} 跑着，但**一条都没抓到** —— 浏览器/被测客户端"
                    f"没走它。把代理指到 127.0.0.1:{port} 再在页面上重做一遍动作："
                    f"Playwright 传 launch(proxy={{\"server\": \"http://127.0.0.1:{port}\"}})，"
                    f"浏览器改系统代理设置。指好了先刷一下页面，再来取。",
        }

    recs = all_recs
    if url_contains:
        needle = str(url_contains).lower()
        recs = [r for r in recs
                if needle in str(r.get("target") or "").lower()
                or needle in _req_line(r).lower()]
    if method:
        want = str(method).strip().upper()
        recs = [r for r in recs if _req_line(r).upper().startswith(want + " ")]

    matched = len(recs)
    recs = recs[-min(limit, 200):]
    out = {"running": True, "port": port, "total": len(all_recs),
           "matched": matched, "count": len(recs), "requests": recs,
           "usage": "拿它当写接口场景的素材：真实的 method/url/headers/body 都在里面，"
                    "不用自己开 devtools 抄一遍（抄错了后面全是错的）。"
                    "前端是 Vite 时先 url_contains='/api/' 滤掉热更新噪声。"}
    if (url_contains or method) and matched == 0:
        seen: dict[str, int] = {}
        for r in all_recs[-200:]:
            seen[str(r.get("target") or "?")] = seen.get(str(r.get("target") or "?"), 0) + 1
        top = sorted(seen.items(), key=lambda kv: -kv[1])[:8]
        out["hint"] = (f"过滤后 0 条（全量 {len(all_recs)} 条）。抓到的目标是："
                       + "、".join(f"{t}×{n}" for t, n in top)
                       + "。要么过滤条件写错了，要么这个动作压根没发这个请求 ——"
                         "后者才是你要报的结论。")
    elif matched > len(recs):
        # 截断必须说出来：不说的话"就这几条"和"被砍了"长得一模一样。
        out["hint"] = (f"命中 {matched} 条，只回了最后 {len(recs)} 条（limit={limit}）。"
                       f"要全量就加大 limit（上限 200）或收紧 url_contains。")
    return out
