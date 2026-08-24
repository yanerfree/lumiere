"""审核轮次记下「审的是哪个版本的场景/脚本」

Revision ID: zzs0rvhash
Revises: zzr0aiusage

为什么必须落库：`ai_review` 轮次只记结论,不记"当时看的是哪份内容"。
`tb_sync_orchestrated_scenario` / `tb_sync_ui_script` 把脚本整个覆盖掉之后,
旧轮次的 findings 文本原样留着,没有任何标记提示"这是对着已经被替换掉的内容
算出来的"——活体验证时真撞上:一份 49 分打回的记录,写着"UI 脚本没有 def test_
入口",取出当前脚本一看,那个入口就在文件里。原地复评直接 83 分 approved,
说明旧记录早就是废纸,但只看审核历史看不出来,险些被引导去重写一个本来就能跑
的脚本。

`content_hash` 只在写 `ai_review` 轮次时填,取的是「场景步骤(url/method/断言/
请求体) + UI 脚本版本号」摊平后的签名(见 rounds.content_signature)。列出轮次
时拿这份签名跟当前内容重新算一遍的签名比,不一样就标 `stale: true`——不是删掉
旧记录,是让"这份结论还对不对得上现在的内容"变得可判断,而不是只能靠回忆或
重新跑一次。

可空:存量轮次没有这个信息,不编造,按 NULL 处理成"不知道是否过期"
(前端不显示 stale 标记,而不是显示"一定过期")。
"""
import sqlalchemy as sa
from alembic import op

revision = "zzs0rvhash"
# 上一版这里写的是 zzr0aiusage —— 那个迁移当时**只在本地、没进版本库**，
# 于是干净 clone 里这条链是断的（alembic upgrade head 直接 Can't locate revision，
# 根目录 tests/unit/core/test_schema_invariants.py 两条封样正是为此）。
# 接到真正已提交的 head 上。zzr0aiusage 进库时把它自己的 down_revision 改成
# zzs0rvhash 接在后面即可，别再接 zzq0rvbatch，否则又是两个 head。
down_revision = "zzq0rvbatch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("case_review_rounds",
                  sa.Column("content_hash", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("case_review_rounds", "content_hash")
