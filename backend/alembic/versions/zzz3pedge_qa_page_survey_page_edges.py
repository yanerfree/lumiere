"""qa_page_surveys：加 page_edges 列（P 边 = 打开这一页浏览器发了哪些请求）

Revision ID: zzz3pedge
Revises: zzz2kscope

三边对账里 P 侧此前只有一个空壳：`qa_page_survey_items.endpoints` 那一列的语义是
**「点这个控件会打哪些端点」**，而爬取这一趟**一个控件都不点**（无向枚举，理由在
`qa_survey_guard` 头部），所以它永远是 `[]`，P 侧等于没有。

能诚实拿到的是**页面级**的那一句：「打开这一页，浏览器发了这几个请求」。它归页靠
导航时窗（`qa_page_traffic`），是一趟里唯一有据可查的 P 侧事实。

⚠ **为什么是新列，不是往 `endpoints` 里塞。** 塞进去就成了一条 `source=observed`
的「控件→端点」边 —— 凭空造出来的那种。`qa_coverage_reconcile.EDGE_SOURCES` 那张
白名单防的正是这个：造出来的边会让真缺口消失、报告更好看，**没有任何一条测试会红**。
所以页面级的边独立成列，报告上也自带「(页面加载)」的锚点，看得出没人点过什么。

⚠ **为什么在 survey 上，不是新建一张 items 那样的表。** 这一列的写入方永远是整趟
一次性落盘（HAR 出了沙箱目录就没了，见 `run_survey`），没有按行更新、按行查询的
需求；而 items 那张表要 diff、要 `first_seen`、要唯一约束炸给我们看。给它单开一张
表只会多一处「和 survey 状态不一致」的可能。

⚠ HAR 本身**不落库**（它是完整可用凭证的原产地，`sanitize_har` 之后也只是"扔干净
了"，不是"该存"）。所以时窗记在 `ledger.pageWindows` 里 —— 边归错了页的时候，那是
唯一能复查的东西。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "zzz3pedge"
down_revision = "zzz2kscope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 不设 server_default '[]'：**NULL 读作「这趟还没算过 P 边」**（老数据、
    # 以及爬崩了的那几趟），`[]` 读作「算过了，一条边都没有」。默认值一填，
    # 两件事就再也分不开 —— 而「没算过」被读成「没有边」正是 G1 假缺口的来源。
    op.add_column("qa_page_surveys",
                  sa.Column("page_edges", postgresql.JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("qa_page_surveys", "page_edges")
