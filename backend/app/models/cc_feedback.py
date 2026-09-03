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
    **但 AI 判的 wont_fix 挡不住带新证据的重报**（`decided_by='ai'` + 正文里出现了
    上次没有的现象）→ 不短路、转 needs_human 交人。人判的才是终局。
    少了这条口子，AI 一次误判就等于把一类反馈永久关死 —— 而那种错不报错。
  · 命中**已 done** 的同指纹 → **新建** + reopened_from。修好了又复现是**回归**，
    是新信息，并进老账里会把它埋掉。

## 谁来判：**默认 AI 判，人只兜底**

平台整体的设计就是这样 —— 人是来看结果、或者点一下执行的，只有少数 AI 判不了的
才需要人接进来。所以这张表上：

  · 反馈一进来就**自动**跑一次 AI 分诊，直接落 category / severity / status /
    resolution，`decided_by='ai'`。**不等人点按钮**（等人点 = 又变成人驱动）。
  · AI 只在三种时候把事情交回人手上，都写在 `needs_human` 里：
      ① 它自己说判不了（缺需求出处、要产品方向上的取舍、证据不足到没法判）
      ② 模型没产出（限流降级到 CLI 通道时会回空 —— 那种**不落库**，留在 new）
      ③ `done` **AI 永远落不了**：那是「代码改完了」，它没动过代码，说了就是假的。
        这不是留给人的权力，是一件它做不到的事。
  · 两道兜底，都不拦裁定：AI 判的 wont_fix 每 WONT_FIX_SAMPLE_EVERY 条抽 1 给人复核
    （校准准不准），以及上面归并那一条 —— AI 的 wont_fix 能被新证据撬开。

## 为什么 reported_category 和 category 分两列

前者是 CC 的主张，后者是平台分诊后的裁定。看着冗余，其实是用户那句
「或者是他判断错了，没找对方法」里最要紧的信号 —— 只留一列的话，
「报成 bug、其实是没找对方法」这件事分诊完就消失了；留两列，它就是一条可统计的事实：
**这把 Key 报的 bug 里有几成其实是用法问题** —— 那是工具描述该改的地方。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func, text
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

# 故障域 —— 「坏掉的是哪一块子系统」。页面上「范围」那一列、顶部那排计数块都按它分。
#
# **和 TOOL_CATALOG.category 不是一回事**，别拿那个来省事：那是**货架分类**
# （「这个工具该摆在哪一格、我去哪儿找它」），这是**故障域**（「坏掉的是哪个子系统」）。
# 两者最大的一处错位正好落在最大的一撮反馈上：`lum_review_case` 的货架分类是
# 「用例·手工步骤」，而它名下那 6 条反馈说的全是 **AI 评审判据/文案**的毛病 ——
# 按 category 归类，ai_review 这一整块（27%，最大的一块）会被塞进 case。
# **这种错不报错**：页面照样有一列、照样有计数，只是指错了地方。
#
# 另一条也别做：纯派生（不落库、按 tool_name 现场映射）。56 条反馈里有 18 条（32%）的
# tool_name 是**自由文本**（「AI 评审规则文案」「接口场景执行器」「覆盖统计」…），
# 现场映射只能把它们落进「其它」—— 而它们恰恰是含金量最高的一撮：一个人肯手写
# 「AI 评审规则文案」，说明他很清楚自己在说哪一块。
AREAS = (
    "ai_review",   # AI 评审：六维判据、mustFix 文案、评分口径
    "sync",        # 回推入库与校验：orchestrated_scenario / ui_script / 硬编码校验 / 场景变量
    "case",        # 用例读写：增删改查、目录归属、废弃申请
    "gate",        # 交付门禁与体检：check_deliverable / assertion_bite / env_hygiene / checkup
    "api_run",     # 接口场景执行：执行器、断言执行、变量解析
    "report",      # 执行报告与覆盖统计
    "note",        # 项目须知
    "spec",        # 接入规范与工具描述（get_sync_spec，以及工具描述本身写得不对）
    "apidoc",      # 接口库（api_nodes 那一层，文档不是可执行场景）
    "diff",        # 版本对账：端点反查、三堆分法、废弃审核
    "qa_review",   # QA 仓对账结论
    "ui_script",   # UI 脚本执行 / 渲染（**区别于 sync 的入库**）
    "env",         # 环境、变量、全局数据、共享自动化资源
    "other",       # 判过了，确实不属于任何一块 —— **和 NULL 不是一回事**，见下面那一列
)

# 后三个非 other 的域（qa_review / ui_script / env）今天 0 条，但**要一起建**：
# 它们是这条通道明确覆盖的范围，**0 条本身是信息** ——
# 「这块没人报过」和「这块不在范围里」不是一回事。
AREA_LABEL = {
    "ai_review": "AI 评审",
    "sync": "回推入库与校验",
    "case": "用例读写",
    "gate": "交付门禁与体检",
    "api_run": "接口场景执行",
    "report": "执行报告与覆盖",
    "note": "项目须知",
    "spec": "接入规范与工具描述",
    "apidoc": "接口库",
    "diff": "版本对账",
    "qa_review": "QA 仓对账",
    "ui_script": "UI 脚本执行",
    "env": "环境与变量",
    "other": "其它",
}

# 严重度**只有平台填得了**。CC 自评会单调通胀（每个报的人都觉得自己那条最急），
# 一轮之后这一列就没有区分度了 —— 所以 lum_report_feedback 根本不收这个参数。
SEVERITIES = ("high", "medium", "low")

# 谁落的这个裁定。**常见值是 ai** —— 见模块 docstring 的「谁来判」。
#   ai      AI 分诊自己落的（默认路径：反馈一进来就自动跑，不等人点）
#   human   人在页面上落的（只有两种时候：AI 说自己判不了，或抽检复核改判）
#   system  平台按规则落的（不经模型，例如导入时的占位）
DECIDERS = ("ai", "human", "system")

# AI 判的 wont_fix 每几条抽 1 条进人工复核。**只对 wont_fix 抽**，因为它是唯一一个
# 会**挡住后续上报**的裁定 —— 判错了不报错，只是安静地少一批反馈。
# 比平台自证抽检（lum_list_pending_confirm 的每 10 抽 1）更密，理由就是这个不可逆性。
# 抽检**不拦裁定**：抽中的照样立即生效，人另外看一眼用来校准 AI 判得准不准。
WONT_FIX_SAMPLE_EVERY = 5


class CCFeedback(Base):
    __tablename__ = "cc_feedback"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid())

    # ── 来源线索（不是边界，见模块 docstring）──
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    # cc = 走 MCP 报的；import = 人工/脚本导入的历史反馈；human = 走 POST /api/cc-feedback 建的
    # （2026-09-01 起**页面上没有这个入口**了，接口留给导入/回填脚本和 API 测试）
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

    # 故障域，取值见上面的 AREAS。**NULL 和 other 必须分开**，和 decided_by 的 NULL
    # 同一个口径：NULL = 还没人判过它属于哪块；other = 判过了，确实不属于任何一块。
    # 合成一个的话，「没判」会永久伪装成「判过了没归属」，而这一列的价值全在能筛。
    # 落值分三层（AI 判、人兜底，和这张表既有的分工一致）：上报时按 _TOOL_AREA 给默认
    # （命中注册工具名才落，**不做关键词猜测**，不中留 NULL）→ AI 分诊落最终值（判不出
    # 回 null，别硬凑）→ 人在抽屉里改。回填历史数据时匹配不上的**留 NULL 别塞 other**，
    # 塞了 AI 那一层就永远不会再碰它们（它只填空的）。
    #
    # ⚠ **绝不能进 fingerprint_of()**（那个函数的 docstring 里也写了）：掺进去会让同一件事
    # 在改了域之后变成两行（归并失效），更要紧的是 **wont_fix 短路失效** ——
    # 而它失效的表现是「反馈变多了」，看起来完全正常。
    # ⚠ 一条只留**一个主域**，不做多选：跨块的按「坏在哪」选主域。多选之后各域计数加起来
    # ≠ 总数，顶部那排计数块就不能拿来当筛选（点进去看到的和数字对不上）。
    area: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)

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

    # AI 分诊的完整产出（判据、理由、风险、原始 JSON）。**它是裁定的依据，不是建议** ——
    # 状态由同一次调用直接落，见 services/cc_feedback_service.py 的 ai_handle()。
    ai_analysis: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ── 谁判的 / 什么还得人来 ──
    # 这个状态是谁落的（ai / human / system）。页面上单独一列，因为「AI 判的」和
    # 「人判的」在后续行为上**真的不一样**：AI 判的 wont_fix 能被新证据撬开，人判的是终局。
    decided_by: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 非空 = **这条在等人拍板**，内容是 AI 自己说的「我判不了，缺的是什么」。
    # 之所以存 AI 的原话而不是一个布尔：人打开它第一件事是问「为什么轮到我」，
    # 一个 true 回答不了这个问题，而回答不了的话人就只能从头把这条重读一遍。
    needs_human: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AI 判的 wont_fix 抽检样本（WONT_FIX_SAMPLE_EVERY 条抽 1）。**不拦裁定**，
    # 只是让人复核一次用来校准 —— 抽检是量抽样，不是逐条审批。
    sampled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"))

    # 回音被 CC 取走的时间。next_duty 的「平台反馈有回音」队列靠它消下去 ——
    # 没有这一列的话那个队列会一直挂着同一条，几轮之后 CC 就学会无视它了。
    ack_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
