"""MCP 工具 — 项目须知（被测系统的行为知识）。

## 这是什么

写用例的人（和 CC）**必须知道、但从接口文档里看不出来**的那些事。实测跑一轮
攒下来的原话：

  · 「404 有两种」—— 转发路径不填=按路由原样转发，上游无此路径回 404（上游的
     404，HTML）；和网关"无此路由"的 404 完全不同。只断状态码会误判成"没生效"。
  · 「offline 会连带把 enabled 置 false，reactivate 再置回 true」
  · 「租户管理员不能调 /tenants/{id}/isolation-rules，要走 /my-tenant」

这些都不是 bug，但不知道就会写出错的断言。它们过去只存在于某一次会话的上下文里，
会话一结束就没了，下一轮 CC（或下一个人）从零再踩一遍。

## 为什么复用 knowledge_entries 而不是新建表

表、API、路由早就有，`api_note` / `bug_pattern` 这两个分类正好就是这些东西的形状。
缺的从来不是存储，是 ①人没有页面入口写 ②CC 够不着（没有 MCP 工具）
③唯一的消费方是平台侧 AI 生成，而那条路已经下线 —— 于是库里 48 条全是
AI 评审自动写的 review_feedback，没有一条是人或 CC 写的项目知识。

## 为什么硬限 200 字

这些条目**每次生成都要整个喂给 CC**，长了直接挤占 context。而且平台侧原有的
消费代码就是 `content[:100]` 截断的 —— 写长了本来也没人看，只是没人告诉写的人。
所以这里明着拒，并说清该怎么写。

## 上限管的是「这张表里该放什么」，不是「你要写的东西该压多短」

撞上限时有两条出路，走错哪条都在丢东西：

  · 多件事挤在一条里 → **拆**成两条，一条说一件事。
  · 本来就是一份**规范/流程/写法约定** → 它压根不属于这张表，走 skill 通道
    （`lum_push_skill`，不限长度），须知这边只留一条**指路的事实**。

2026-08-30 撞到过反面：外部 CC 想把一份 skill 正文推上来，撞了 200 字，
于是把它压成两条「去看某某」的指路条目 —— 指针留下了，正文没有任何地方收着它，
下一轮顺着指针过去是空的。**光说"请你自己压"，等于在教它丢东西**，
所以拒绝信息里必须同时指路：长文的家在哪、这把 Key 够不着那个工具时该怎么办。
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# 一条只说一件事，超了不是截断而是拒 —— 截断会把最关键的后半句悄悄吃掉。
# MAX_CONTENT 定义在模型那边：HTTP 那条写入通道认的是同一个，别在这儿各写各的。
from app.models.knowledge import MAX_CONTENT, KnowledgeEntry

# CC 能写的分类。review_feedback 不给 —— 那是 AI 评审自己写的，
# 混进来就分不清"被测系统的事实"和"评审对用例的意见"了。
_CC_CATEGORIES = ("api_note", "bug_pattern", "custom")

# 「一份规范」而不是「一条须知」的形状：成条、分段、带标题或代码块。
_BULLET = re.compile(r"^\s*(?:[-*·+]\s|\d+[.、)]\s?|#{1,6}\s)")


def _looks_like_a_spec(content: str) -> bool:
    """像不像一份规范正文 —— **只用来决定给哪条出路，不参与拒不拒**（超了一律拒）。

    判宽一点没关系：误判成规范，无非多给一句"长文走 skill 通道"；
    漏判才是坏的 —— 那就只剩"自己压"，而压的结果是正文丢了。
    """
    if content.lstrip().startswith("---") or "```" in content:
        return True  # SKILL.md 的 frontmatter / 代码块，本来就不该往这张表里塞
    lines = [ln for ln in content.splitlines() if ln.strip()]
    return len(lines) >= 3 and sum(1 for ln in lines if _BULLET.match(ln)) >= 2


_CATEGORY_LABEL = {
    "api_note": "接口/系统行为",
    "bug_pattern": "踩过的坑",
    "custom": "其它",
    "review_feedback": "AI 评审反馈",
}


async def list_project_notes(
    session: AsyncSession,
    project_id: str,
    category: str | None = None,
) -> dict:
    """列出项目须知 —— **动手写用例之前先读一遍**。

    里面是前人（和你自己上几轮）踩出来的坑：接口的哪个行为反直觉、哪个状态会
    连带改别的字段、哪个角色走的是另一条路径。不知道这些就会写出错的断言，
    然后把「被测系统本来就这样」当成 bug 报上去。
    """
    q = select(KnowledgeEntry).where(KnowledgeEntry.project_id == uuid.UUID(project_id))
    if category:
        q = q.where(KnowledgeEntry.category == category)
    rows = (await session.execute(q.order_by(KnowledgeEntry.created_at.desc()).limit(200))).scalars().all()
    return {
        "notes": [{
            "id": str(e.id),
            "category": e.category,
            "categoryLabel": _CATEGORY_LABEL.get(e.category, e.category),
            "title": e.title,
            "content": e.content,
            "source": e.source,
        } for e in rows],
        "total": len(rows),
        "usage": "写用例之前读一遍；这一轮撞出来的新坑用 lum_add_project_note 写回去，"
                 "别让下一轮再踩一遍。",
    }


async def add_project_note(
    session: AsyncSession,
    project_id: str,
    title: str,
    content: str,
    category: str = "api_note",
) -> dict:
    """把这一轮撞出来的坑写回项目须知。

    **一条只说一件事，正文 200 字以内**，写成「现象 + 别踩的坑」。
    这些条目每次生成都会整个喂给下一轮，写长了就是在挤占别人的 context。

    只记**你亲手撞到的事实**（"这个接口 404 有两种"），不记判断结论
    （"这里应该改成 xxx"）—— 结论会过期，事实不会。
    """
    if category not in _CC_CATEGORIES:
        return {"error": f"category 只能是 {' / '.join(_CC_CATEGORIES)}",
                "hint": "review_feedback 是 AI 评审自己写的，不给外部写 —— "
                        "混进来就分不清「系统的事实」和「对用例的意见」了。"}

    title = (title or "").strip()
    content = (content or "").strip()
    if not title or not content:
        return {"error": "title 和 content 都不能为空"}
    if len(title) > 200:
        return {"error": f"标题太长（{len(title)} 字），200 字以内"}
    if len(content) > MAX_CONTENT:
        err = {
            "error": f"正文 {len(content)} 字，超过 {MAX_CONTENT} 字上限 —— 这里不截断。",
            "why": "这些条目每次生成都会整个喂给下一轮 CC，长了直接挤占它的 context；"
                   "而且平台侧原有的消费代码就是取前 100 字，写长了本来也没人看。",
            "howTo": "一条只说一件事，写成「现象 + 别踩的坑」。说不完就**拆成两条** —— "
                     "拆，不是把后半句删掉。",
        }
        if _looks_like_a_spec(content):
            err["thisIsNotANote"] = (
                "这段的形状是**一份规范/流程**，不是一条须知 —— 它压不进 200 字，"
                "硬压就只剩一句「去看某某」，正文当场丢了。别压，换个地方放。"
            )
        # 上限不是"把正文压掉"的理由 —— 长文有它自己的家，这里必须说出来。
        # 只说"请你自己压"的后果实测过一次（2026-08-30，见模块文档）。
        err["whereLongFormGoes"] = (
            "规范 / 流程 / 写法约定的家是 **skill 通道**："
            "lum_push_skill(project_id, content=SKILL.md 全文) 不限长度，"
            "下一轮用 lum_pull_skill 取回全文。须知这边只留一条**指路的事实**"
            "（例：「提 issue 走 /issue，不是 write-issue」）—— 指针和正文各就各位，"
            "而不是只剩一个指向空处的指针。"
        )
        err["ifPushSkillNotInScope"] = (
            "这把 Key 里看不到 lum_push_skill（工具按档位分过范围）时，"
            "**别退而求其次把正文压进须知**，那等于把它丢了。正文先留在本地 "
            ".claude/skills/<name>/，然后把这件事报给用户：在平台「MCP 工具中心 → 工具范围」"
            "里把「Skill 取用与共享」那一档**一起勾上**（档位是多选，勾它不会顶掉"
            "现在这一档），重连后再推。"
        )
        return err

    # 同项目同标题就覆盖 —— 同一件事被记两遍，读的人不知道信哪条
    existing = (await session.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.project_id == uuid.UUID(project_id),
            KnowledgeEntry.title == title,
        )
    )).scalars().first()
    if existing is not None:
        existing.content = content
        existing.category = category
        existing.source = "cc"
        await session.commit()
        return {"id": str(existing.id), "title": title, "replacedExisting": True,
                "note": "同标题的已存在，这次是覆盖。"}

    entry = KnowledgeEntry(
        project_id=uuid.UUID(project_id), category=category,
        title=title, content=content, source="cc",
    )
    session.add(entry)
    await session.commit()
    return {"id": str(entry.id), "title": title, "category": category,
            "replacedExisting": False}
