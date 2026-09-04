"""一次「页面枚举爬取」= 一条 `qa_page_survey`，爬到的每个可操作项 = 一条 item。

**不复用 `review_batches`，也不复用 `qa_catalog_reviews`。** 沿用后者那次
「单独建表并写明理由」的先例（见 `models/qa_catalog_review.py` 开头那三条）：

1. 这里存的是**事实账本**，不是结论。`qa_catalog_reviews.result` 是模型写的读后感，
   而这张表每一行都是「某个角色在某个构建上，某个页面上看见了这个控件」——
   两者的可信度来源完全不同，混在一张表里会让「模型说的」和「爬到的」长得一样。
2. 它没有域码。爬取按**角色**分片（架构 AD-4），一趟能横跨多个域；
   域是**对账**的单位，不是爬取的单位。
3. 生命周期不同：结论一条条留着给人复核；survey 是可再生的，
   同一个构建指纹重爬一趟就该得到同样的东西（S6.4 的两趟 diff 稳定性 AC 就是在验这个）。

**这张表记的是爬取产物，而爬取本身一个写请求都发不出去。** 无向枚举被只读五层
守着（架构 AD-7）——**这是爬虫的约束，不是「被测环境只读」这条规矩**（那条不存在，
有向 UI 脚本照常写，见 `qa_survey_guard` 头部），
产物只留角色名，凭证在落库前 drop 掉 —— 不是脱敏，是整个键扔掉，
因为 HAR 里的 `Authorization` 是**完整可用凭证**，脱敏后的星号一样会让人以为「存了但安全」。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base

# 与 `QaCatalogReview.STATUSES` 同构，多两个终态（架构 AD-4）。
#
# `partial` / `dirty` **必须是独立终态，不能塞进 done 加个 flag** ——
# 理由和 `batchesFailed` 是同一个：「少爬了一片」和「爬完了没问题」
# 在页面上不能长得一样。`dirty` 尤其不能降级成警告：它意味着
# **我们可能动了别人的数据**，那是要人来看的。
STATUSES = ("pending", "running", "done", "partial", "failed", "dirty")
TERMINAL_STATUSES = ("done", "partial", "failed", "dirty")

# item 的 `state`：这一项在这一趟里被看见的程度。
# `reachable` 指「页面上有，但当前角色点不动 / 被禁用」—— 它和「不存在」
# 必须分得开，否则权限收紧会被 diff 报成「功能没了」。
ITEM_STATES = ("present", "enabled", "reachable")


class QaPageSurvey(Base):
    """一趟爬取。账本计数在 `ledger` 里，状态是列（要进 WHERE 和索引）。"""

    __tablename__ = "qa_page_surveys"
    __table_args__ = (
        # 同一个项目 + 环境 + 构建指纹 + 同一个起跑时刻，只能有一趟。
        # 它挡的是「同一个按钮被点两下」，不是缓存键 —— 缓存键少了 started_at
        # （架构 AD-8），复用判断在服务层做。
        UniqueConstraint("project_id", "env_id", "build_fingerprint", "started_at",
                         name="uq_qa_page_surveys_run"),
        Index("ix_qa_page_surveys_project_env_status", "project_id", "env_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid())
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)

    # 环境删了也要留住名字，理由同 `QaCatalogReview.environment_name`：
    # 这趟爬的是哪个环境，是这份事实的一部分。
    env_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environments.id", ondelete="SET NULL"), nullable=True)
    env_name: Mapped[str] = mapped_column(String(120), default="", server_default="")

    # 缓存键的两半（架构 AD-8）。`route_table_hash` 变 ⇒ 只重算 R 侧与 G2，不重爬。
    build_fingerprint: Mapped[str] = mapped_column(String(120), default="", server_default="")
    route_table_hash: Mapped[str] = mapped_column(String(64), default="", server_default="")

    # 这趟用了哪些角色。**只存角色名，不存任何凭证。**
    roles: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    # 账本项会随实现增长（§7 已列 10 项，落地肯定还会加），
    # 每加一项一次迁移不现实 —— 所以是 jsonb 不是列。
    ledger: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # P 边：**页面级**的「打开这一页浏览器发了哪些请求」（归页规则在
    # `qa_page_traffic`）。**不是** item 那一列 `endpoints` 的「点这个控件会打
    # 哪些端点」—— 页面级的边归的是"这一页"，把它摊到页面上每个控件头上等于
    # 凭空造一条 `observed` 的控件→端点边（`EDGE_SOURCES` 白名单防的就是它）。
    # 控件级那一列怎么来的见 `endpoints` 自己的注释（按**点击时间窗**归属，
    # 跟这里的导航时间窗是同一招、不同窗口）。
    #
    # **NULL 读作「这趟还没算过 P 边」，`[]` 读作「算过了，没有边」。**
    # 所以没有 server_default —— 默认值一填，「没算过」就会被读成「没有」，
    # 而那正是 G1 假缺口的来源。
    page_edges: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class QaPageSurveyItem(Base):
    """一个可操作项 = 一行。**diff 的单位就是这一行。**"""

    __tablename__ = "qa_page_survey_items"
    __table_args__ = (
        # **硬约束不是优化。** `key` 重复意味着 anchor 推断塌了（整页退化成 text 锚点、
        # 两个按钮同名），那时候 diff 会变成噪声源。让它在写入时就炸，
        # 比在 diff 结果里表现成「新增 40 项」好查得多。
        # ⇒ 写入路径**不许** on_conflict_do_nothing / do_update，
        #   `test_重复_key_必须炸不许静默去重` 盯着。
        # 2026-09-04 补一句边界：**采集处**（`qa_page_survey_crawl.dedupe_items`）
        # 会把同一页上撞 key 的行合成一行，并把撞了几次记进账本
        # （`anchorCollisions` / `anchorCollisionKeys`）。那不是"绕开这条约束"——
        # 表格每行同一个 `data-testid` 是常见写法，让它撞库的代价是
        # **整趟 200+ 页一格都落不下来**，报出来的 `status=failed` 和"这一趟没跑"
        # 长得一模一样。探测器留着，只是从"炸库"挪到了账本上一个查得到的数。
        UniqueConstraint("survey_id", "key", name="uq_qa_page_survey_items_key"),
        Index("ix_qa_page_survey_items_project_page", "project_id", "page_path"),
        Index("ix_qa_page_survey_items_key", "key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid())
    survey_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("qa_page_surveys.id", ondelete="CASCADE"), nullable=False)

    # **架构 AD-6 的索引列表写了 `INDEX (project_id, page_path)`，但字段清单里没有
    # `project_id`** —— 照字面建不出来。这里把它冗余下来，而不是把索引改成
    # `(survey_id, page_path)`：对账是「这个项目某个域的所有页」，跨 survey 扫，
    # 挂在 survey 上的索引帮不上忙。冗余的代价是一列 uuid，收益是对账不用 join。
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)

    # `key` = page_path + anchor（§7 Q2-C）。diff 靠它对齐两趟。
    key: Mapped[str] = mapped_column(String(500), nullable=False)

    page_path: Mapped[str] = mapped_column(String(300), default="", server_default="")
    page_title: Mapped[str] = mapped_column(String(200), default="", server_default="")

    # anchor 优先 testid（S6.4，复用 `ui_selector_render.infer_kind`）。
    # `anchor_kind` 是**稳定性等级**，不是分类 —— 只能靠 text/style 认出来的项,
    # 换语种/改版就会飘，S6.5 把它们登记成 `status='gap'`。
    anchor: Mapped[str] = mapped_column(String(400), default="", server_default="")
    anchor_kind: Mapped[str] = mapped_column(String(20), default="", server_default="")

    label: Mapped[str] = mapped_column(String(200), default="", server_default="")
    control_type: Mapped[str] = mapped_column(String(40), default="", server_default="")
    state: Mapped[str] = mapped_column(String(20), default="present", server_default="present")

    # 哪些角色看得见它 —— 角色维度的缺口就是从这一列算出来的。只存角色名。
    roles_visible: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 点它会打哪些端点（观测到的，不是猜的）。归属靠**点击时窗**
    # （`qa_page_traffic.bucket_clicks`），和上面 `page_edges` 的导航时窗是两本账。
    # **三态，一个都不许合**（跟 `page_edges` 同一条纪律，所以这里也故意不给
    # `server_default`）：
    #   · `[{...}]` = 点过，发了这几条
    #   · `[]`      = **点过，一条请求都没发** —— G4 就是从这个值来的
    #   · `NULL`    = **没点过**（预算没排到、点不着、或那一趟根本不点控件）
    # 塌成 `[]` 的后果不报错：一千多个没碰过的控件会集体宣布「点了什么都没发」，
    # G4 从个位数涨到四位数，全是假的。
    endpoints: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # 指向 survey：这一项第一次/最近一次是在哪趟看见的。
    # survey 被删时置空而不是跟着删 —— 留一行「首见时间不详」的 item，
    # 好过悄悄少一行可操作项。
    first_seen_survey_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("qa_page_surveys.id", ondelete="SET NULL"), nullable=True)
    last_seen_survey_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("qa_page_surveys.id", ondelete="SET NULL"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
