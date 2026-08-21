import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class CaseFolder(Base):
    """用例目录（路径模式，最多 4 层）"""
    __tablename__ = "case_folders"
    __table_args__ = (
        UniqueConstraint("branch_id", "path", name="uq_folder_branch_path"),
        CheckConstraint("depth <= 4", name="ck_folder_max_depth"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("branches.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("case_folders.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    # 改过的旧名字（大写段，不含路径）。CC 回推按 module 字符串找目录，
    # 改完名它手上还是旧词 —— 没有这张别名，旧词会**另建一个同名目录**，
    # 同一个模块在页面上裂成两个，谁都看不出为什么。
    former_names: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Case(Base):
    """用例"""
    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint("branch_id", "case_code", name="uq_case_branch_code"),
        UniqueConstraint("branch_id", "tea_id", name="uq_case_branch_tea_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("branches.id", ondelete="CASCADE"), nullable=False
    )
    case_code: Mapped[str] = mapped_column(String(20), nullable=False)
    tea_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(10), nullable=False)  # api / e2e
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("case_folders.id"), nullable=True
    )
    priority: Mapped[str] = mapped_column(String(5), nullable=False, default="P2", server_default="P2")
    preconditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    expected_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    variables_used: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    api_scenario: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ui_scenario: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # api_scenario_status / ui_scenario_status 已删（2026-08）——
    # 它们和 api_status / ui_status 说的是同一件事，`apply_case_status` 一直同时写两套，
    # 实测 255 条里 0 处不一致。两个字段表达一件事，迟早有一处漏写就开始互相矛盾。
    is_api_template: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_ui_template: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # 核心/标杆用例：供其他用例参考应用它来生成（用例级，区别于 per-scenario 的模板）
    is_core: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    automation_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )  # automated / pending / script_removed / archived （旧字段，保留兼容；新展示用 lifecycle_status）
    # —— 状态体系 v2（2026-07）——
    # 整体生命周期：draft(草稿) / done(完成) / deprecated(废弃)
    lifecycle_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    # 三维执行就绪度：**draft(草稿) / debugging(调试中) / completed(完成)**。
    #
    # 2026-08 从 5 态收到 3 态。去掉的两个和它们的理由：
    #   · not_started —— 和 draft 区分不出来，起点就是草稿
    #   · pending_review / executable —— 原来「跑绿了等人」和「人发布了」是两个态，
    #     而 executable **只有人能给**（红线：CC 说能跑等于自证）。代价是回归池永远空
    #     （实测 257 条只有 1 条 executable）。现在放权 CC：跑绿就置 completed，
    #     「要不要人审」拆到 review_status 那个独立标签上，**且审核不挡回归**。
    manual_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    ui_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    api_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    # 这条用例**要**做到什么程度（C1）：spec 只要手工步骤 / spec_api 步骤+接口 / full 三件套。
    # 和上面三个"已经做到哪儿"的状态配合，就是 CC 断点续跑的判据：
    # target_level=full 但 ui_status != executable 的，就是还欠着的那些。
    # **为什么定这个 level**（尤其"不要 UI/不要接口"的那些）。
    #
    # 只有 target_level 一个值时，人分不出「CC 判断这条纯接口验证不需要 UI」和
    # 「CC 没想，用了默认值」—— 而这两件事的后果完全不同。实测被直接问过：
    # 「他自己会规划吗，用户怎么知道呢，是他规划了没写还是没规划」。
    # 照 expected_confirmed_note 的样子留一句话，回推时没带就提醒（不硬拦）。
    target_level_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_level: Mapped[str] = mapped_column(
        String(16), nullable=False, default="spec", server_default="spec"
    )
    source: Mapped[str] = mapped_column(String(10), nullable=False)  # imported / manual
    script_ref_file: Mapped[str | None] = mapped_column(String(500), nullable=True)
    script_ref_func: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_flaky: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # 自动隔离：非空且未过期 = 隔离中，到期自动回执行队列（不需要定时任务）。
    # 判定依据落在 flaky_evidence 里，人要能复核"凭什么说它 flaky"。
    quarantined_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    flaky_evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # P0 两阶段的第二阶段：有人逐条看过「预期结果」这一列并认可。
    # 改了步骤或预期结果会清掉 —— 确认的是当时那一版，不是终身通行证。
    # 「卡在外部条件上」：等的是什么，一句话（等环境变量 X 加上、等某接口上线）。
    # **不做成状态枚举** —— 状态由执行事实推进（红线），这只是一句归责说明：
    # 「我没写」和「我写不了，因为外面缺东西」在看板上必须分得开。
    blocked_external: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 卡在**产品 bug** 上：跟 blocked_external（等环境/等接口上线）是两回事 ——
    # 那种是"我还写不了"，这种是"我写完了、跑出来是红的，而红的原因不在用例"。
    # 每条：{ref, url?, status: open|fixed, note?, updatedAt}。ref 是外部单号或一句话。
    # 为什么不做成一张缺陷表：平台不是缺陷系统，真单子在 GitHub / Jira / 群里。
    # 这里只需要一个**指针 + 一个开关**，回答"这条为什么红""什么时候能继续"。
    bug_refs: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 标签：自由词，CC 和人都能写（`阻塞`、`冒烟`、`P0回归`、`需要真数据`）。
    # 跟审核标签/生命周期状态刻意分开 —— 那两个有确定语义、驱动门禁，标签只是分拣。
    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 回推时的**场景级反问答案**。存它不是为了留痕，是为了给评审一个锚 ——
    # 评审原来只能从标题猜"这条想验什么"，有了作者自己写的"第 8 步验编号不变"，
    # 就能直接核对：**说的和断言对不上，是最硬的证据**。
    # 形状：{answeredAt, by, verificationPoints, clarity, coverage, expectationSource}
    reflections: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    @property
    def blocked_by_bug(self) -> bool:
        """还卡在 bug 上（至少一条 open）。"""
        from app.services.bug_ref_service import blocked_by_bug
        return blocked_by_bug(self)

    @property
    def has_fixed_bug(self) -> bool:
        """**这条用例曾经发现过 bug，并且已经验回来了**（有 fixed、没有 open）。

        是痕迹不是待办 —— 「哪些用例真抓到过问题」按它筛。
        """
        from app.services.bug_ref_service import has_fixed_bug
        return has_fixed_bug(self)

    @property
    def bug_found_count(self) -> int:
        """这条用例总共关联过几个 bug（含已修的）。痕迹的量化。"""
        return len([r for r in (self.bug_refs or []) if isinstance(r, dict)])
    expected_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # 确认发生在 CC 对话里时，确认人不是平台用户 —— 存自由文本 + 确认了什么
    expected_confirmed_actor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    expected_confirmed_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    # —— AI 生成用例扩展（功能场景测试模块，仅 source=ai 使用；旧数据全部为 NULL）——
    # **审核标签（用例级，一个）**。库里只存 3 个值，第四态用 NULL：
    #   NULL      待提审 —— 三维还没全完成。**用空而不是 'not_submitted'**：
    #                      绝大多数用例都在这个态，存个值等于给每条都挂一个灰标签，
    #                      列表上一片噪音；空就什么都不显示。
    #   pending   待审   —— 三维全完成，**自动进**（谁都不用点「提交审核」）
    #   approved  已审   \
    #   rejected  不通过 /  人点，而且**可以不点** —— 审核不挡回归，建计划直接能跑
    #
    # 这个字段原来给平台侧那条已下线的 AI 流水线用（pending_review/approved/rejected），
    # 47 条停在待审只有 1 条被点过。语义正好对得上，所以复用它，不新增列。
    review_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # {category, text, reviewer, at}
    review_reason: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # {total, static, ai_self, warnings: []}；未评分为 NULL（前端显示 "—"）
    quality_score: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    generation_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generation_tasks.id", ondelete="SET NULL"), nullable=True
    )
    # 需求点 UUID 数组（GIN 索引，覆盖矩阵聚合用）
    requirement_point_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 并发审核乐观锁（FR22）
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# Case.generation_task_id 外键依赖 generation_tasks 表；确保任何导入 Case 的
# 场景（含测试 create_all）metadata 中都有目标表，避免 NoReferencedTableError
from app.models import scenario_gen  # noqa: E402, F401
