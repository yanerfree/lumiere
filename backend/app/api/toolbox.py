"""工具箱 API — 为前端工具箱提供 AI 辅助能力、HTTP 代理和认证工具"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import time

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/toolbox", tags=["toolbox"])


class RegexGenRequest(BaseModel):
    description: str = Field(..., max_length=500)


# 正则里几乎必然出现 \d \w \s，而这些在 JSON 字符串里是**非法转义** ——
# 模型只要少写一个反斜杠，json.loads 就炸。原来的兜底是把**整段原文**塞进 regex
# 字段返回，页面于是把一整个 JSON 文档填进正则输入框（实测截图为证），
# 用户看到的是"正则语法错误"，完全不知道发生了什么。而且这事儿是间歇的 ——
# 模型偶尔会规规矩矩写 \\d，那次就正常，下次又不正常，比稳定坏更难查。
_JSON_BAD_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')
_REGEX_FIELD = re.compile(r'"regex"\s*:\s*"((?:[^"\\]|\\.)*)"')
_FLAGS_FIELD = re.compile(r'"flags"\s*:\s*"([a-z]*)"')
_EXPLAIN_FIELD = re.compile(r'"explanation"\s*:\s*"((?:[^"\\]|\\.)*)"')


def parse_regex_payload(content: str | None) -> dict | None:
    """把模型回的内容解成 {regex, flags, explanation}。解不出来返回 None。

    三层，一层比一层将就，但**没有一层会把整段原文当成正则**：
      ① 直接 json.loads（模型规规矩矩转义了）
      ② 把非法转义的反斜杠补成合法的再 loads（`\\d` → `\\\\d`，最常见的一种）
      ③ 正则把 "regex" 字段抠出来（JSON 结构本身也坏了时）
    """
    text = (content or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        parts = text.split("\n", 1)
        text = (parts[1] if len(parts) > 1 else "").rsplit("```", 1)[0].strip()

    for candidate in (text, _JSON_BAD_ESCAPE.sub(r"\\\\", text)):
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict) and str(data.get("regex", "")).strip():
            return {
                "regex": str(data["regex"]),
                "flags": str(data.get("flags") or ""),
                "explanation": str(data.get("explanation") or ""),
            }

    m = _REGEX_FIELD.search(text)
    if m and m.group(1).strip():   # 抠出来是空的等于没生成成功，别拿空串糊弄
        f = _FLAGS_FIELD.search(text)
        e = _EXPLAIN_FIELD.search(text)
        return {
            "regex": m.group(1).replace('\\"', '"'),
            "flags": f.group(1) if f else "",
            "explanation": e.group(1).replace('\\"', '"') if e else "",
        }

    # 模型只回了一行光秃秃的正则（没按 JSON 格式）也认，但必须**不像 JSON**，
    # 否则又会把整个文档当成正则 —— 那正是要修的那个 bug。
    if not text.startswith("{") and "\n" not in text and len(text) <= 200:
        return {"regex": text, "flags": "", "explanation": ""}
    return None


@router.post("/generate-regex")
async def generate_regex(
    body: RegexGenRequest,
    session: AsyncSession = Depends(get_db),
):
    """工具箱「正则测试 → AI 生成」。

    这条以前直接 `complete()` 不带 capability，于是「AI 能力→模型」页面里
    `toolbox-regex` 这个档位绑了模型也不生效 —— 能力清单干脆把它标成"已下线、
    后端无任何调用方"。**但功能一直是通的**（实测能生成），标成下线是页面在说假话。
    走一遍 resolve_ai_config，这个档位才真的管用。

    工具箱是全局页面、不属于任何项目，所以 project_id 传 None 走全局配置。
    """
    from app.services.ai_config_resolver import resolve_ai_config

    ai_config = await resolve_ai_config(None, session, capability="toolbox-regex")
    try:
        from app.services.ai.llm_client import complete
        resp = await complete(
            config=ai_config,
            messages=[
                {"role": "system", "content": (
                    "你是一个正则表达式专家。用户会用自然语言描述需求，你需要返回对应的 JavaScript 正则表达式。\n"
                    "要求：\n"
                    "1. 只返回正则表达式本身，不要加 / 包裹\n"
                    "2. 同时给出简短说明\n"
                    "3. 严格按以下 JSON 格式返回，不要有其他内容：\n"
                    '{"regex": "正则表达式", "flags": "标志位", "explanation": "简短说明"}'
                )},
                {"role": "user", "content": body.description},
            ],
            max_tokens=200,
        )
        from app.services.ai.usage import log_ai_call
        # 工具箱是全局页面 → project_id=None（迁移 zzr0aiusage 为此放开了 NOT NULL）。
        # 不记的话「AI 能力→模型」页会说"正则生成从没被调用过"，而它一直是通的。
        await log_ai_call(session, project_id=None, capability="toolbox-regex",
                          model=(ai_config.model if ai_config else None), resp=resp)
        await session.commit()
        data = parse_regex_payload(resp.content)
        if data is None:
            logger.warning("正则生成：模型回的内容解不出正则 —— %r", (resp.content or "")[:200])
            return {"error": "AI 这次没按格式回，没能解出正则。换个说法再试一次。"}
        return {"data": data}
    except Exception as e:
        logger.warning("正则生成失败: %s", e)
        return {"error": str(e)[:200]}


class HttpRequestBody(BaseModel):
    method: str = Field(default="GET")
    url: str
    headers: dict | None = None
    body: str | None = None
    timeout: int = Field(default=30, ge=1, le=120)


@router.post("/http-request")
async def send_http_request(req: HttpRequestBody):
    try:
        headers = dict(req.headers) if req.headers else {}
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=req.timeout, follow_redirects=True, verify=False) as client:
            resp = await client.request(
                method=req.method.upper(),
                url=req.url,
                headers=headers,
                content=req.body.encode("utf-8") if req.body else None,
            )
        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        resp_headers = dict(resp.headers)
        ct = resp_headers.get("content-type", "")
        try:
            resp_body = resp.text
        except Exception:
            resp_body = f"[Binary {len(resp.content)} bytes]"
        return {
            "data": {
                "statusCode": resp.status_code,
                "headers": resp_headers,
                "body": resp_body[:100000],
                "elapsed": elapsed,
                "size": len(resp.content),
            }
        }
    except httpx.ConnectError as e:
        return {"error": f"连接失败: {e}"}
    except httpx.TimeoutException:
        return {"error": f"请求超时 ({req.timeout}s)"}
    except Exception as e:
        return {"error": str(e)[:300]}


# ── 认证工具 ──

class JwtSignRequest(BaseModel):
    payload: dict
    secret: str
    algorithm: str = "HS256"

@router.post("/jwt-sign")
async def jwt_sign(body: JwtSignRequest):
    try:
        header = {"alg": body.algorithm, "typ": "JWT"}
        def b64url(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
        h = b64url(json.dumps(header, separators=(",", ":")).encode())
        p = b64url(json.dumps(body.payload, separators=(",", ":"), ensure_ascii=False).encode())
        signing_input = f"{h}.{p}".encode()
        if body.algorithm == "HS256":
            sig = hmac.new(body.secret.encode(), signing_input, hashlib.sha256).digest()
        elif body.algorithm == "HS384":
            sig = hmac.new(body.secret.encode(), signing_input, hashlib.sha384).digest()
        elif body.algorithm == "HS512":
            sig = hmac.new(body.secret.encode(), signing_input, hashlib.sha512).digest()
        else:
            return {"error": f"不支持的算法: {body.algorithm}"}
        token = f"{h}.{p}.{b64url(sig)}"
        return {"data": {"token": token}}
    except Exception as e:
        return {"error": str(e)[:200]}


class HmacSignRequest(BaseModel):
    message: str
    secret: str
    algorithm: str = "SHA-256"

@router.post("/hmac-sign")
async def hmac_sign(body: HmacSignRequest):
    try:
        algo_map = {"SHA-1": hashlib.sha1, "SHA-256": hashlib.sha256, "SHA-384": hashlib.sha384, "SHA-512": hashlib.sha512}
        hash_fn = algo_map.get(body.algorithm)
        if not hash_fn:
            return {"error": f"不支持的算法: {body.algorithm}"}
        sig = hmac.new(body.secret.encode(), body.message.encode(), hash_fn).digest()
        return {"data": {
            "hex": sig.hex(),
            "base64": base64.b64encode(sig).decode(),
        }}
    except Exception as e:
        return {"error": str(e)[:200]}
