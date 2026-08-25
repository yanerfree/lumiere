"""AI 调用记账 —— 「哪些 AI 入口真被用过」的唯一事实来源。

## 为什么必须有这个

「AI 能力 → 模型」那页原来只能回答"配了什么"，回答不了"用了什么"。
于是用户自己的结论是「系统里用到 AI 的好像只有 AI 审核吧，其他都没用到」——
而库里 `scenario-*` 有 111 条调用记录（8-09 那几天在跑场景生成）。
**页面说不清，人就只能猜**，猜完就照着猜的结论去砍功能。

记账原来只有三处（tb-case-generate / tb-quality-review / scenario-*），
文档生成、探索 Charter、正则生成、接口场景编排这四条链路一次都没记过 ——
它们不是"没被用"，是"没被数"。这两件事在页面上长得一模一样，而
**「没被数」当成「没被用」是会误删功能的**，比不显示更坏。

## 口径

一次成功的模型返回记一条。失败不记（失败该进日志，不该进"用过"的证据）。
`project_id` 为 NULL 的全局调用（工具箱走的是全局配置）也记 —— 列的是"入口用没用"，
不是项目账单，漏了它页面就会说"正则生成从没被调用过"，而那是错的。
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def log_ai_call(
    session: AsyncSession,
    *,
    project_id: uuid.UUID | None,
    capability: str,
    model: str | None,
    resp=None,
    est_chars: int | None = None,
    duration_ms: int = 0,
) -> None:
    """记一次 AI 调用。**永不抛异常** —— 记账失败不该弄挂业务。

    `capability` 用 ai_capabilities 注册表里的 key，页面按它对齐能力清单。

    `resp` 有 usage 就用真值；流式（SSE）拿不到 usage，用 `est_chars` 传回吐的字符数，
    按**中文约 1 字 1 token** 粗估。粗估的地方页面上要标出来 —— 把估算值和
    真实计量混在同一个数字里，那个数字就再没人能拿它对账了。
    """
    try:
        from app.models.case_file import AIUsageLog

        pt = getattr(resp, "prompt_tokens", 0) or 0
        ct = getattr(resp, "completion_tokens", 0) or 0
        if not (pt or ct) and est_chars:
            ct = int(est_chars)
        session.add(AIUsageLog(
            project_id=project_id,
            skill_name=capability,
            model=model,
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=pt + ct,
            duration_ms=duration_ms,
        ))
        await session.flush()
    except Exception as e:  # noqa: BLE001
        logger.warning("AI 用量记账失败（不阻塞）capability=%s: %s", capability, e)
