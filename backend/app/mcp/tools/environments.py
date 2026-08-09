"""MCP 工具 — 环境和变量"""
from __future__ import annotations

import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import environment_service

# 和 sync.py 的 _SECRET_RE 同一套口径 —— 脱敏这件事误报是安全方向，漏报不是。
_SECRET_RE = re.compile(
    r"(PASSWORD|PASSWD|PASS|PWD|TOKEN|SECRET|KEY|AUTH|CREDENTIAL|COOKIE|SESSION)", re.I
)


async def list_environments(session: AsyncSession) -> list[dict]:
    """列出所有测试环境。"""
    envs = await environment_service.list_environments(session)
    return [{"id": str(e.id), "name": e.name, "description": e.description} for e in envs]


async def get_merged_variables(session: AsyncSession, env_id: str) -> dict:
    """某个环境执行时实际会注入哪些变量。

    两处坑，都是实测踩出来的：

    1. **必须返回 dict**。service 返回的是 list，而 FastMCP 要求结构化内容是 dict，
       于是这个工具对**每一个环境**都直接报错
       （`structured_content must be a dict or None. Got list: ...`）。
       它的描述里写着"排查『变量未解析』先查这里"——结果这个"先查"的入口自己一直是坏的。

    2. **凭证要脱敏**。返回里带着 ADMIN_PASSWORD 这类明文值。
       同族的 tb_list_global_data 一直是脱敏的，这条不脱等于开了个后门：
       外部 CC 要的是"有哪些键可以引用"，不是密码本身
       —— 脚本里写 `${ADMIN_PASSWORD}`，值由平台执行时注入。
       所以只修 dict 不脱敏是**把一个崩溃换成一次泄漏**，两件事得一起做。
    """
    rows = await environment_service.get_merged_variables(session, uuid.UUID(env_id))

    def _mask(key: str, value):
        if _SECRET_RE.search(key or ""):
            return "***"
        return value

    items = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        key = r.get("key", "")
        items.append({**r, "key": key, "value": _mask(key, r.get("value"))})

    return {
        "envId": str(env_id),
        "total": len(items),
        "variables": items,
        "usage": (
            "步骤里直接写 ${键名} 就能用，执行时由平台注入，**不要**把值复制进脚本。"
            "凭证类的值这里显示成 ***（要的是能引用哪些键，不是密码本身）。"
        ),
    }
