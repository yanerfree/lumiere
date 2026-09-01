"""CC 反馈 —— 外部 Claude Code 报回来的**平台自己的问题**。

## 这张表和别的表的边界

反馈的对象是 **Lumiere 自己**，不是被测系统。被测系统的缺陷走 `failure_tickets`
（`lum_submit_analysis`），被测系统的反直觉行为走 `knowledge_entries`
（`lum_add_project_note`）。判据只有一句：**这条观察有没有一个「能自己报错」的家？**
有就走那个家，没有才落这儿。

## 为什么是全局表，不带项目边界

`project_id` 可空，而且**只是来源线索**（在哪儿撞到的、去哪儿复现），不是隔离维度：

  · 「`in` 算子两边不归一类型」在哪个项目里撞到都是同一个缺陷，按项目分行就是
    同一件事有 N 个家 —— 正是 `failure_tickets` 的归并口径要防的形状。
  · 处理方只有平台维护者一拨，不按项目分；项目内页面意味着他要逐个项目翻，
    而「要主动翻」的东西实测就是没人翻（共享自动化资源全平台 0 行）。

所以项目删了反馈也要留着 → `ON DELETE SET NULL`，不是 CASCADE。

## 归并口径（抄 failure_tickets，因为要防的是同一件事）

  · 命中**还开着**的同指纹 → occurrences+1，不新建。
  · 命中**已 wont_fix** 的同指纹 → **不新建**，当场把上次的理由甩回去。
    这是这条通道最要紧的一个行为：「不需要处理」必须挡得住第二次上报，
    否则「回复原因」只是一句客套，下一轮照样再来一遍。
  · 命中**已 done** 的同指纹 → **新建** + reopened_from。修好了又复现是**回归**，
    是新信息，并进老账里会把它埋掉。

## 为什么 reported_category 和 category 分两列

前者是 CC 的主张，后者是平台分诊后的裁定。看着冗余，其实是用户那句
「或者是他判断错了，没找对方法」里最要紧的信号 —— 只留一列的话，
「报成 bug、其实是没找对方法」这件事分诊完就消失了；留两列，它就是一条可统计的事实：
**这把 Key 报的 bug 里有几成其实是用法问题** —— 那是工具描述该改的地方。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base

# 分类。**平台判的**那一列用这三个；CC 报的那一列也用这三个（同一套词，好对比）。
#   bug          说了会做 A、实际做了 B（含静默失败：知道出错却返回语法上合法的结果）
#   improvement  行为没错，但代价不合理 / 容易把人带错路
#   requirement  今天没有这个能力
CATEGORIES = ("bug", "improvement", "requirement")

CATEGORY_LABEL = {
    "bug": "缺陷",
    "improvement": "优化",
    "requirement": "需求",
}

# 状态机：
#   new         刚报进来，没人看过
#   triaged     认下了：定了类和严重度
#   in_progress 在改
#   done        改完了（resolution 必填，且必须写「现在该怎么做」）
#   wont_fix    不做（resolution 必填，且同指纹再报会被它短路）
#   duplicate   跟另一条是同一件事（duplicate_of 必填）
STATUSES = ("new", "triaged", "in_progress", "done", "wont_fix", "duplicate")

# 「还没有结论」的那些。只有这两个算待处理 —— 页面默认筛它们，
# 归并也只往这两个上并（in_progress 也并，见 OPEN_STATUSES）。
PENDING_STATUSES = ("new", "triaged")
# 「这件事还没了结」—— 同指纹再报往这些上归并。
OPEN_STATUSES = ("new", "triaged", "in_progress")

STATUS_LABEL = {
    "new": "待处理",
    "triaged": "已认下",
    "in_progress": "处理中",
    "done": "已处理",
    "wont_fix": "不需要处理",
    "duplicate": "重复",
}

# 严重度**只有平台填得了**。CC 自评会单调通胀（每个报的人都觉得自己那条最急），
# 一轮之后这一列就没有区分度了 —— 所以 lum_report_feedback 根本不收这个参数。
SEVERITIES = ("high", "medium", "low")


class CCFeedback(Base):
    __tablename__ = "cc_feedback"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid())

    # ── 来源线索（不是边界，见模块 docstring）──
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    # cc = 走 MCP 报的；import = 人工/脚本导入的历史反馈；human = 页面上手工建的
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="cc")
    # MCP Key 名 / 导入来源 —— 配额按它算，「这把 Key 报的 bug 有几成是用法问题」也按它统计
    reporter: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 撞到的是哪个 lum_* 工具。选填但强烈建议：它是指纹的一半，也是维护者第一眼要看的
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ── 内容 ──
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # {expected, actual, repro, refs:[...]}。bug 类的 expected/actual 是硬校验，
    # 不是可选装饰 —— 「说好的是什么 / 实际是什么」想不清楚就写不出来。
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ── 分类：CC 的主张 vs 平台的裁定 ──
    reported_category: Mapped[str | None] = mapped_column(String(16), nullable=True)
    category: Mapped[str | None] = mapped_column(String(16), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # ── 归并 ──
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    reopened_from: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc_feedback.id", ondelete="SET NULL"), nullable=True)

    # ── 处置 ──
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="new")
    # 回音正文。done / wont_fix 时必填，而且要求写的不是「修好了」而是**现在该怎么做**；
    # wont_fix 时如果是「他没找对方法」，必须把正确方法写出来 —— 只说「你错了」等于没回音。
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc_feedback.id", ondelete="SET NULL"), nullable=True)
    handled_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # AI 分诊建议。**只是建议** —— 它不改 status，人按「采纳」才生效。
    # 理由：wont_fix 的回音会永久短路后续同指纹上报，让 AI 单方面下这个判定，
    # 等于让它能把一类反馈关死，而且以后再也不会有人看到。
    ai_analysis: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # 回音被 CC 取走的时间。next_duty 的「平台反馈有回音」队列靠它消下去 ——
    # 没有这一列的话那个队列会一直挂着同一条，几轮之后 CC 就学会无视它了。
    ack_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
