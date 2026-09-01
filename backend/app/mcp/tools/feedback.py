"""MCP 工具 —— 平台反馈通道。**CC 唯一一条「往 Lumiere 自己身上写」的路。**

## 为什么要有这条路

在这条通道之前，CC 撞到平台自己的毛病（工具描述有歧义、返回缺字段、门禁误伤）
只有两个去处：写进它自己的会话上下文（会话一结束就没了），或者写进一份
markdown 等人来读 —— 2026-09-01 那份 648 行的汇总就是这么来的，31 条问题
攒了不知道多久，靠一个人手动搬运才进到平台这边。**攒着的东西没有时效性可言**：
等它被读到的时候，一半已经被别的改动顺手修掉了，另一半没人记得当时的现场。

## 边界：什么该走这儿，什么不该

判据只有一句：**这条观察有没有一个「能自己报错」的家？**

  · 被测系统的缺陷 → `lum_submit_analysis`（跟进单会跟着回归红/绿自己动）
  · 被测系统的反直觉行为 → `lum_add_project_note`（下一轮生成会读到）
  · 用例自己的毛病 → 你自己改（`lum_update_case`）
  · **Lumiere 平台自己的毛病** → 才是这里

搞混的代价不对称：平台问题塞进项目须知，它会被当成"被测系统的事实"喂给下一轮
（那是错的知识）；被测系统的缺陷塞进这里，它永远不会被回归验证到 —— 修没修好
没有任何东西会告诉你。

## 三道闸门，各挡各的（判据写在 service 里，这里只说为什么）

  1. **指纹归并** —— 同一件事第二次报不新建行，只 +1。撞得越多排得越靠前。
  2. **证据门槛** —— 正文 ≥40 字；报 bug 必须写清「说好的是什么 / 实际是什么」。
     这不是形式主义：这两句想不清楚的，多半不是缺陷而是用法。
  3. **每把 Key 每天 40 条新指纹** —— 归并命中不计入。挡的是"同一件事说五遍"，
     不是挡认真写的一批 —— 所以这个数**必须高过 31**（2026-09-01 那份汇总
     一次就是 31 条，真实量级），否则挡掉的正好是它声称要放行的那种人。

## 回音是这条通道的命根子

只能写不能读的通道必死。平台侧有现成的反面样本：共享自动化资源那套写通道做好了，
**全平台 0 行** —— 因为写进去之后没有任何地方会再提起它。
所以这里配了四条回音路径：上报当场（wont_fix 短路）、`lum_list_my_feedback`、
`lum_next_duty` 的第 ⑧ 队列、以及被判「不需要处理」时**必须带正确做法**的回复。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import cc_feedback_service as svc


async def report_feedback(
    session: AsyncSession,
    title: str,
    body: str,
    category: str,
    tool_name: str | None = None,
    expected: str | None = None,
    actual: str | None = None,
    repro: str | None = None,
    refs: list | None = None,
    project_id: str | None = None,
) -> dict:
    """把**平台自己的**问题报回来。

    只报 Lumiere 的毛病 —— 被测系统的缺陷走 lum_submit_analysis，
    被测系统的反直觉行为走 lum_add_project_note。
    """
    from app.mcp.middleware import current_caller_key_name, current_caller_project_id

    reporter = await current_caller_key_name()
    if not project_id:
        project_id = await current_caller_project_id()

    evidence = {k: v for k, v in (
        ("expected", (expected or "").strip() or None),
        ("actual", (actual or "").strip() or None),
        ("repro", (repro or "").strip() or None),
        ("refs", refs or None),
    ) if v}

    return await svc.report(
        session,
        title=title,
        body=body,
        category=category,
        tool_name=tool_name,
        evidence=evidence or None,
        project_id=project_id,
        reporter=reporter,
        source="cc",
    )


async def list_my_feedback(
    session: AsyncSession,
    status: str | None = None,
    unread_only: bool = True,
    project_id: str | None = None,
) -> dict:
    """看自己报过的问题现在什么下场 —— 读到的回音**当场算已读**。"""
    from app.mcp.middleware import current_caller_key_name, current_caller_project_id

    reporter = await current_caller_key_name()
    if not project_id:
        project_id = await current_caller_project_id()
    return await svc.list_mine(
        session, project_id=project_id, reporter=reporter,
        status=status, unread_only=unread_only)
