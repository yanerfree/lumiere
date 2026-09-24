"""把订单服务的接口清单导出成标准 OpenAPI 3.0.3 —— 「导出」按钮下载的就是它。

为什么不直接用那个 FastAPI 自带的 `/openapi.json`：
那份是**空壳**。路由签名里请求体写的是 `payload: dict`，于是自动生成出来的文档里
请求体是个「任意对象」、没有一个必填字段、没有错误码、没有鉴权声明 ——
导进别的系统（Apifox / Postman / 智能体的工具面）之后，那边只知道"有这么个地址"，
照它生成的调用几乎必然 422，而人会以为是接口坏了。

所以这里是拿 `demo_shop_catalog` 那份**手写清单**（必填、类型、错误码、鉴权都在里面）
生成的。清单和真实路由有封样测试做双向差集，必填/类型还会跟真实函数签名对一遍，
所以"手写"不等于"随便写"。

⚠ 导出的 `servers.url` 是**这台机器上**那个服务的地址（默认 29000）。
别人拿去跑要自己改 —— 但留着本机地址比留一个 `http://example.com` 有用：
至少在本机导进去就能直接点。
"""
from __future__ import annotations

import json
from typing import Any

from app.services import demo_shop_catalog as catalog

SECURITY_SCHEME = "bearerAuth"


def _operation_id(e: dict) -> str:
    """operationId 要求全局唯一、是个标识符 —— 清单里的 key 本来就唯一，直接用。"""
    return e["key"]


def _example(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _parameters(e: dict) -> list[dict]:
    out: list[dict] = []
    for p in e.get("pathParams") or []:
        out.append({
            "name": p["name"], "in": "path",
            # OpenAPI 规定路径参数必须 required=true，写 false 是非法文档、
            # 有些导入工具会直接拒收整份文件。
            "required": True,
            "description": p.get("desc", ""),
            "schema": {"type": p.get("type", "string")},
            "example": p.get("sample", ""),
        })
    for q in e.get("query") or []:
        item = {
            "name": q["name"], "in": "query",
            "required": bool(q.get("required")),
            "description": q.get("desc", ""),
            "schema": {"type": q.get("type", "string")},
        }
        if q.get("sample"):
            item["example"] = q["sample"]
        out.append(item)
    return out


def _request_body(e: dict) -> dict | None:
    schema = e.get("bodySchema")
    if not schema:
        return None
    content: dict = {"schema": schema}
    ex = _example(e.get("body"))
    if ex is not None:
        content["example"] = ex
    # 必填字段一个都没有的（PATCH/PUT 那种"给什么改什么"），整个请求体也就不是必须的
    return {"required": bool(schema.get("required")), "content": {"application/json": content}}


def _responses(e: dict) -> dict:
    ok = str(e.get("successStatus", 200))
    success: dict = {"description": "成功"}
    ex = _example(e.get("sampleResponse"))
    if ex is not None:
        success["content"] = {"application/json": {"example": ex}}
    out = {ok: success}
    for err in e.get("errors") or []:
        code = str(err["status"])
        # 同一个状态码可能有好几种业务错误码（比如 409 既是库存不够也是已下架），
        # OpenAPI 的 responses 按状态码只能有一条，所以把说明合并进同一条里，
        # 别让后写的那条把先写的覆盖掉 —— 覆盖掉是不报错的，只是少了一半信息。
        prev = out.get(code)
        line = f"`{err['code']}` — {err['when']}"
        if prev and code != ok:
            prev["description"] = prev["description"] + "\n\n" + line
            continue
        out[code] = {
            "description": line,
            "content": {"application/json": {
                "example": {"code": err["code"], "message": err["when"]},
            }},
        }
    return out


def build_openapi(base_url: str, keys: list[str] | None = None) -> dict:
    """生成整份文档。`keys` 给了就只导这几条（页面上勾选导出用）。"""
    picked = [e for e in catalog.ENDPOINTS if not keys or e["key"] in keys]
    paths: dict[str, dict] = {}
    for e in picked:
        op: dict[str, Any] = {
            "tags": [e["group"]],
            "operationId": _operation_id(e),
            "summary": e["name"],
            "description": e["summary"],
            "responses": _responses(e),
        }
        params = _parameters(e)
        if params:
            op["parameters"] = params
        body = _request_body(e)
        if body:
            op["requestBody"] = body
        if e["auth"] != "none":
            op["security"] = [{SECURITY_SCHEME: []}]
            if e["auth"] == "admin":
                op["description"] += "\n\n只有管理员（admin）能调，店员来调返回 403。"
                # 导入方一般不认业务角色，留个扩展字段，认得的就能用上
                op["x-required-role"] = "admin"
        paths.setdefault(e["path"], {})[e["method"].lower()] = op

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "订单服务 Demo",
            "version": "1.0.0",
            "description": (
                "Lumiere 自带的练手被测系统：真落库、有状态机、有权限。\n\n"
                "账号：`admin` / `admin123`（可删单）、`clerk` / `clerk123`（不可删单）。\n\n"
                "先调 `POST /api/login` 拿 token，之后每个请求带 "
                "`Authorization: Bearer <token>`，token 有效期 8 小时。"
            ),
        },
        "servers": [{"url": base_url, "description": "本机跑着的那个订单服务"}],
        "tags": [{"name": g} for g in catalog.GROUP_ORDER
                 if any(e["group"] == g for e in picked)],
        "components": {
            "securitySchemes": {
                SECURITY_SCHEME: {
                    "type": "http", "scheme": "bearer",
                    "description": "`POST /api/login` 返回的那串 token",
                },
            },
        },
        "paths": paths,
    }
