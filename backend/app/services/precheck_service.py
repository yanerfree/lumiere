"""自动化跑前预检 —— 检查项目级全局资源是否已存在。

策略（按用户决策）：缺失 → 返回「待确认」列表交用户确认/补建，绝不静默自动创建；
已存在 → 放行。区别于用例场景数据（自建自删），全局资源查存在、长期保留。

exists_check 约定：
  {"method":"GET","url":"/api/v1/upstreams","match":{"field":"name","equals":"default-upstream"}}
  - url 相对则拼 BASE_URL；带鉴权(取 S1.3 token)
  - match 省略 → HTTP 2xx 即视为存在
  - match.listPath 可选，指定响应里列表的点路径；否则自动找第一处 dict 列表
"""
from __future__ import annotations

import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation_resource import AutomationResource
from app.models.environment import EnvironmentVariable
from app.services import token_service


async def _load_env_vars(session: AsyncSession, env_id) -> dict[str, str]:
    rows = await session.execute(
        select(EnvironmentVariable).where(
            EnvironmentVariable.environment_id == uuid.UUID(str(env_id))
        )
    )
    return {v.key: v.value for v in rows.scalars().all()}


def _dig(obj: Any, path: str):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _find_first_list(obj: Any):
    """自动定位响应里第一处「dict 元素的列表」。"""
    if isinstance(obj, list):
        return obj if all(isinstance(x, dict) for x in obj) or not obj else obj
    if isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, list) and (not v or isinstance(v[0], dict)):
                return v
        for v in obj.values():
            r = _find_first_list(v)
            if r is not None:
                return r
    return None


def _match_exists(data: Any, match: dict) -> bool:
    """在响应中判断 match 条件是否命中。"""
    if not match:
        return True  # 无 match：能拿到 2xx 即视为存在
    field = match.get("field")
    equals = match.get("equals")
    lst = _dig(data, match["listPath"]) if match.get("listPath") else _find_first_list(data)
    if lst is None:
        # 也许 data 本身就是单对象
        if isinstance(data, dict) and field in data:
            return str(data.get(field)) == str(equals)
        return False
    if not isinstance(lst, list):
        return False
    if field is None:
        return len(lst) > 0
    return any(isinstance(it, dict) and str(it.get(field)) == str(equals) for it in lst)


async def _check_one(client: httpx.AsyncClient, base: str, headers: dict, res: AutomationResource) -> dict:
    chk = res.exists_check or {}
    url = chk.get("url") or ""
    if url and not url.lower().startswith("http"):
        url = f"{base}/{url.lstrip('/')}"
    method = (chk.get("method") or "GET").upper()
    item = {
        "name": res.name,
        "keep": res.keep,
        "canCreate": bool(res.create_def),
        "description": res.description,
    }
    if not url:
        item.update(exists=False, reason="未配置存在性检查 url")
        return item
    try:
        resp = await client.request(method, url, headers=headers)
    except Exception as e:  # noqa: BLE001
        item.update(exists=False, reason=f"检查请求失败: {e}")
        return item
    if resp.status_code == 401:
        item.update(exists=False, reason="鉴权失败(401)——token 可能过期")
        return item
    if resp.status_code >= 400:
        item.update(exists=False, reason=f"HTTP {resp.status_code}")
        return item
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        data = None
    exists = _match_exists(data, chk.get("match") or {})
    item.update(exists=exists, reason=None if exists else "未匹配到目标资源")
    # 顺带把 extract 里声明的值抽出来 —— 光判断"存在"不够，编排步骤要的是它的 id。
    # 形如 {"extract": {"upstreamId": "data.items[0].id"}}
    if exists:
        # 用执行引擎那套 JSONPath-lite：_dig 只认点号，取不了 data[0].id 这种下标，
        # 而列表取第 N 个恰恰是前置资源最常见的写法。
        from app.services.api_test_runner import _extract_value

        values = {}
        for var, path in (chk.get("extract") or {}).items():
            v = _extract_value(data, str(path))
            if v is not None:
                values[str(var)] = v
        if values:
            item["values"] = values
    return item


async def check_resources(
    session: AsyncSession, project_id, env_id, role: str = "ADMIN"
) -> dict:
    """预检项目全局资源。返回 {ok, total, satisfied[], missing[]}。
    ok=True 表示可放行；missing 为待用户确认/补建的资源。"""
    rows = await session.execute(
        select(AutomationResource).where(AutomationResource.project_id == uuid.UUID(str(project_id)))
    )
    resources = rows.scalars().all()
    if not resources:
        return {"ok": True, "total": 0, "satisfied": [], "missing": [], "note": "项目未配置全局资源"}

    if not env_id:
        return {
            "ok": False, "total": len(resources), "satisfied": [],
            "missing": [{"name": r.name, "exists": False, "reason": "未选择环境"} for r in resources],
        }

    env_vars = await _load_env_vars(session, env_id)
    base = (env_vars.get("BASE_URL") or "").rstrip("/")
    token = await token_service.get_target_token(session, env_id, role)
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    satisfied, missing = [], []
    async with httpx.AsyncClient(timeout=15, verify=False) as client:
        for res in resources:
            item = await _check_one(client, base, headers, res)
            (satisfied if item.get("exists") else missing).append(item)

    return {
        "ok": len(missing) == 0,
        "total": len(resources),
        "satisfied": satisfied,
        "missing": missing,
        "tokenAcquired": bool(token),
    }
