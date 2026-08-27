"""HTTP 请求客户端 API — 请求集合管理 + 代理发送 + 历史"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections import deque
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.db import get_db
from app.models.http_request import HttpRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/http-client", tags=["http-client"])

_history: deque[dict] = deque(maxlen=200)


# ── Schemas ──

class RequestCreate(BaseModel):
    parent_id: str | None = None
    type: str = "request"
    name: str = "新请求"
    method: str = "GET"
    url: str = ""

class RequestUpdate(BaseModel):
    name: str | None = None
    method: str | None = None
    url: str | None = None
    headers: list | None = Field(default=None)
    body: str | None = Field(default=None)
    body_type: str | None = None
    auth_type: str | None = None
    auth_config: dict | None = Field(default=None)
    parent_id: str | None = Field(default=None)
    sort_order: int | None = None

class SortItem(BaseModel):
    id: str
    sort_order: int
    parent_id: str | None = None

class BatchSortRequest(BaseModel):
    items: list[SortItem]

class SendRequest(BaseModel):
    method: str = "GET"
    url: str
    headers: dict | None = None
    body: str | None = None
    timeout: int = Field(default=120, ge=1, le=600)


# ── 请求集合 CRUD ──

@router.get("/requests")
async def list_requests(session: AsyncSession = Depends(get_db)):
    result = await session.execute(
        select(HttpRequest).order_by(HttpRequest.sort_order, HttpRequest.created_at)
    )
    items = result.scalars().all()
    return {"data": [_to_dict(r) for r in items]}


@router.post("/requests", status_code=201)
async def create_request(body: RequestCreate, session: AsyncSession = Depends(get_db)):
    if body.parent_id:
        err = await _nest_error(session, body.type, uuid.UUID(body.parent_id), None)
        if err:
            return JSONResponse({"error": err}, status_code=400)
    item = HttpRequest(
        type=body.type,
        name=body.name,
        parent_id=uuid.UUID(body.parent_id) if body.parent_id else None,
        method=body.method,
        url=body.url,
    )
    session.add(item)
    await session.flush()
    await session.refresh(item)
    return {"data": _to_dict(item)}


@router.put("/requests/{item_id}")
async def update_request(item_id: uuid.UUID, body: RequestUpdate, session: AsyncSession = Depends(get_db)):
    item = await session.get(HttpRequest, item_id)
    if not item:
        return JSONResponse({"error": "Not found"}, status_code=404)
    data = body.model_dump(exclude_unset=True)
    if "parent_id" in data:
        data["parent_id"] = uuid.UUID(data["parent_id"]) if data["parent_id"] else None
        err = await _nest_error(session, item.type, data["parent_id"], item.id)
        if err:
            return JSONResponse({"error": err}, status_code=400)
    for k, v in data.items():
        setattr(item, k, v)
    await session.flush()
    await session.refresh(item)
    return {"data": _to_dict(item)}


@router.delete("/requests/{item_id}")
async def delete_request(item_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    # 目录有两层，只删直接子级会留下「父目录已经不存在」的孤儿行：它们再也不会出现在树上，
    # 但仍然占着列表和顶部那个请求计数 —— 删不掉也看不见，比报错难查。所以按层往下收。
    ids = [item_id]
    frontier = [item_id]
    for _ in range(4):  # 一级目录 → 子目录 → 请求，四轮兜住；同时防脏数据成环
        if not frontier:
            break
        rows = await session.execute(
            select(HttpRequest.id).where(HttpRequest.parent_id.in_(frontier))
        )
        frontier = [r for (r,) in rows.all() if r not in ids]
        ids.extend(frontier)
    await session.execute(delete(HttpRequest).where(HttpRequest.id.in_(ids)))
    return {"ok": True}


@router.post("/requests/batch-sort")
async def batch_sort(body: BatchSortRequest, session: AsyncSession = Depends(get_db)):
    for item in body.items:
        values = {"sort_order": item.sort_order}
        if item.parent_id is not None:
            values["parent_id"] = uuid.UUID(item.parent_id) if item.parent_id else None
            row = await session.get(HttpRequest, uuid.UUID(item.id))
            err = await _nest_error(session, row.type, values["parent_id"], row.id) if row else None
            if err:
                return JSONResponse({"error": err}, status_code=400)
        await session.execute(
            update(HttpRequest)
            .where(HttpRequest.id == uuid.UUID(item.id))
            .values(**values)
        )
    return {"ok": True}


# ── 发送请求 ──

@router.post("/send")
async def send_request(req: SendRequest):
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
        try:
            resp_body = resp.text
        except Exception:
            resp_body = f"[Binary {len(resp.content)} bytes]"

        actual_req_headers = dict(resp.request.headers) if resp.request else headers

        result = {
            "statusCode": resp.status_code,
            "headers": resp_headers,
            "body": resp_body[:200000],
            "elapsed": elapsed,
            "size": len(resp.content),
            "actualRequest": {
                "method": req.method.upper(),
                "url": str(resp.request.url) if resp.request else req.url,
                "headers": actual_req_headers,
                "body": req.body,
            },
        }

        _history.appendleft({
            "id": str(uuid.uuid4())[:8],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "method": req.method.upper(),
            "url": req.url,
            "statusCode": resp.status_code,
            "elapsed": elapsed,
            "size": len(resp.content),
        })

        return {"data": result}
    except httpx.ConnectError as e:
        return {"error": f"连接失败: {e}"}
    except httpx.TimeoutException:
        return {"error": f"请求超时 ({req.timeout}s)"}
    except Exception as e:
        return {"error": str(e)[:300]}


# ── 历史 ──

@router.get("/history")
async def get_history(limit: int = Query(50, ge=1, le=200)):
    return {"data": list(_history)[:limit], "total": len(_history)}


@router.delete("/history")
async def clear_history():
    _history.clear()
    return {"ok": True}


# ── 工具 ──

async def _nest_error(
    session: AsyncSession, item_type: str, parent_id: uuid.UUID | None, item_id: uuid.UUID | None
) -> str | None:
    """目录只有两层：一级目录 → 子目录 → 请求。返回非空就是这次挪动/新建不能做。

    前端已经按这个规则画树了，这里再拦一次是因为越界的行不会报错、只会从树上消失
    （renderTreeItem 只从根往下递归两层），到时候是一条查不出来源的「请求不见了」。
    """
    if parent_id is None:
        return None
    parent = await session.get(HttpRequest, parent_id)
    if parent is None:
        return "父级不存在"
    if parent.type != "folder":
        return "父级不是目录"
    if item_id is not None and parent_id == item_id:
        return "不能移动到自己里面"
    if item_type == "folder":
        if parent.parent_id:
            return "目录最多两层，子目录里不能再放目录"
        if item_id is not None:
            kids = await session.execute(
                select(HttpRequest.id).where(
                    HttpRequest.parent_id == item_id, HttpRequest.type == "folder"
                )
            )
            if kids.first():
                return "这个目录下还有子目录，挪进去会超过两层"
    return None


def _to_dict(r: HttpRequest) -> dict:
    return {
        "id": str(r.id),
        "parentId": str(r.parent_id) if r.parent_id else None,
        "type": r.type,
        "name": r.name,
        "sortOrder": r.sort_order,
        "method": r.method,
        "url": r.url,
        "headers": r.headers,
        "body": r.body,
        "bodyType": r.body_type,
        "authType": r.auth_type,
        "authConfig": r.auth_config,
        "createdAt": r.created_at.isoformat() if r.created_at else None,
        "updatedAt": r.updated_at.isoformat() if r.updated_at else None,
    }
