"""调试代理 — 从前端发送单个 HTTP 请求并返回响应"""

import time
from typing import Optional

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/debug", tags=["debug"])

MAX_BODY = 2 * 1024 * 1024  # 2MB


class SendRequest(BaseModel):
    method: str = "GET"
    url: str
    params: Optional[list[dict]] = None
    headers: Optional[list[dict]] = None
    body: Optional[str] = None
    body_type: Optional[str] = "json"
    auth: Optional[dict] = None


@router.post("/send")
async def debug_send(req: SendRequest):
    headers = {}
    if req.headers:
        for h in req.headers:
            if h.get("enabled") is not False and h.get("key"):
                headers[h["key"]] = h.get("value", "")

    if req.auth:
        at = req.auth.get("type")
        if at == "bearer" and req.auth.get("token"):
            headers["Authorization"] = f"Bearer {req.auth['token']}"
        elif at == "basic" and req.auth.get("username"):
            import base64
            cred = base64.b64encode(f"{req.auth['username']}:{req.auth.get('password', '')}".encode()).decode()
            headers["Authorization"] = f"Basic {cred}"
        elif at == "apikey" and req.auth.get("keyName"):
            if req.auth.get("keyIn") == "query":
                req.params = req.params or []
                req.params.append({"key": req.auth["keyName"], "value": req.auth.get("keyValue", ""), "enabled": True})
            else:
                headers[req.auth["keyName"]] = req.auth.get("keyValue", "")

    params = {}
    if req.params:
        for p in req.params:
            if p.get("enabled") is not False and p.get("key"):
                params[p["key"]] = p.get("value", "")

    method = req.method.upper()
    kwargs = {"params": params or None, "headers": headers or None, "timeout": 30.0}

    if method in ("POST", "PUT", "PATCH") and req.body:
        if req.body_type == "json":
            kwargs["content"] = req.body.encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        elif req.body_type == "form":
            kwargs["content"] = req.body.encode("utf-8")
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif req.body_type == "form-data":
            pairs = {}
            try:
                import json
                for item in json.loads(req.body):
                    if item.get("key"):
                        pairs[item["key"]] = item.get("value", "")
            except Exception:
                pass
            kwargs["data"] = pairs
        else:
            kwargs["content"] = req.body.encode("utf-8")

    t0 = time.perf_counter()
    try:
        # verify=False: 允许自签/内网证书(与其它执行器一致)
        # http2=True: 兼容仅支持 HTTP/2 的网关(ALPN 自动协商,普通 HTTP/1.1 目标不受影响)
        async with httpx.AsyncClient(follow_redirects=True, verify=False, http2=True) as client:
            resp = await client.request(method, req.url, **kwargs)
        duration = round((time.perf_counter() - t0) * 1000)

        body_bytes = resp.content[:MAX_BODY]
        try:
            body_text = body_bytes.decode("utf-8")
        except UnicodeDecodeError:
            body_text = f"(binary {len(body_bytes)} bytes)"

        resp_headers = [{"key": k, "value": v} for k, v in resp.headers.items()]

        return {
            "data": {
                "status_code": resp.status_code,
                "status_text": resp.reason_phrase or "",
                "headers": resp_headers,
                "body": body_text,
                "duration_ms": duration,
                "size": len(resp.content),
            }
        }
    except httpx.ConnectError as e:
        return {"data": {"status_code": 0, "status_text": "连接失败", "headers": [], "body": str(e), "duration_ms": 0, "size": 0}}
    except httpx.TimeoutException:
        return {"data": {"status_code": 0, "status_text": "请求超时", "headers": [], "body": "请求超时（30s）", "duration_ms": 30000, "size": 0}}
    except httpx.ProtocolError as e:
        return {"data": {"status_code": 0, "status_text": "协议不匹配", "headers": [],
                         "body": f"目标未返回合法的 HTTP 响应，可能是非 HTTP 服务或协议不兼容：{e}",
                         "duration_ms": 0, "size": 0}}
    except Exception as e:
        return {"data": {"status_code": 0, "status_text": "错误", "headers": [], "body": str(e), "duration_ms": 0, "size": 0}}
