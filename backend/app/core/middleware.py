import uuid
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


def _to_camel(name: str) -> str:
    """snake_case -> camelCase"""
    components = name.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


# **这些字段的值是「别人的数据」，不是我们的 schema，一个字都不许动。**
#
# 驼峰化本来只该改我们自己的字段名（created_at → createdAt）。但它是无差别递归的，
# 于是连用户写的 HTTP 请求体一起改了：接口步骤里存的 `upstream_id` 取出来变成
# `upstreamId`，前端加载后**任何一次保存都把驼峰写回库**，这条场景从此对被测系统
# 发驼峰，被 422 `UNKNOWN_FIELD: 不支持的字段 upstreamId` 拒收。
#
# 实测代价：AT-0011 原本 19/19 全绿，在页面上被打开保存过一次之后变成 6 通过
# 13 失败，而库里、页面上看到的都是驼峰，**看不出是被改过的** —— 只会以为
# 用例本来就写错了。同一批更早的 AT-0006/0007/0008 也是这么坏的。
#
# 断言/提取物里的键同样是用户的东西：variables_extract 的键是变量名，
# 一个叫 `my_var` 的变量会被改成 `myVar`，而步骤里引用的仍是 ${my_var}。
_OPAQUE_KEYS = frozenset({
    # 请求侧：用户写什么就发什么
    "body", "headers", "params", "query", "form",
    # 响应/抓包留存：里面是被测系统的原文
    "last_response", "lastResponse", "response_body", "responseBody",
    "request_body", "requestBody", "request_headers", "requestHeaders",
    "captured_requests", "capturedRequests",
    # 键即变量名 / 键即环境变量名
    "variables_extract", "variablesExtract", "env_variables", "envVariables",
    # **键即枚举值**：byStatus / byArea 这类分组计数，键是 `wont_fix`、`api_run`、
    # `__none__` 这些**值**，不是我们的字段名。驼峰化会把它们改成 `wontFix`、
    # `apiRun`、`None` —— 而前端是拿同一份枚举常量去查表的，查不到就渲染成 0。
    # 那是**假的 0**：状态筛选下拉里「不需要处理（0）」和真的一条都没有长得一模一样。
    # （2026-09-03 实测：byStatus 一直就是这么坏的，加 byArea 那排块时才撞出来。）
    "by_status", "byStatus", "by_area", "byArea",
})


def to_camel_case(data: Any) -> Any:
    """递归将 dict key 从 snake_case 转为 camelCase。

    `_OPAQUE_KEYS` 里那些字段的值原样透出 —— 它们装的是用户数据和被测系统原文，
    改它们的键等于篡改测试内容。
    """
    if isinstance(data, dict):
        return {
            _to_camel(k): (v if k in _OPAQUE_KEYS else to_camel_case(v))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [to_camel_case(item) for item in data]
    return data


class CamelCaseResponse(JSONResponse):
    """自动将响应 body 中的 snake_case key 转为 camelCase"""
    def render(self, content: Any) -> bytes:
        return super().render(to_camel_case(content))


class TraceIdMiddleware(BaseHTTPMiddleware):
    """为每个请求生成唯一 trace_id，注入到 request.state 和 response header"""
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or str(uuid.uuid4())
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response
