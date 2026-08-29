"""页面枚举账本的两张表：约束在不在、写入路径许不许静默去重、迁移降不降得回去。

这一层不连库（`backend/tests` 整套都不连），所以分两半验：

* **真库那一半**已经在 scratch 库上跑过：升 → 降 → 再升，重复 key 的第二条 insert
  报 `duplicate key value violates unique constraint "uq_qa_page_survey_items_key"`，
  表里只剩 1 行。那一趟是一次性的，留在实现记录里。
* **这里守的是它不会被人悄悄改掉**：约束从模型或迁移里消失、
  写入路径加一句 `on_conflict_do_nothing`，都得在这儿变红。

第二条尤其要紧 —— 加 `ON CONFLICT DO NOTHING` 是「让报错消失」最顺手的一招，
而它把「anchor 推断塌了」变成了「这一趟少了 40 项」：**同一个 bug，从会炸变成了不吭声。**

Test ID: qa-page-survey-UT-001
Priority: P0
"""
import ast
import pathlib

import pytest

from app.models import qa_page_survey as m

BACKEND = pathlib.Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic/versions/zzx0qasrv_qa_page_surveys.py"
APP = BACKEND / "app"


class TestConstraints:
    """约束与索引"""

    @pytest.mark.unit
    def test_重复_key_的唯一约束在模型里(self):
        uq = [c for c in m.QaPageSurveyItem.__table__.constraints
              if getattr(c, "name", "") == "uq_qa_page_survey_items_key"]
        assert uq, "uq_qa_page_survey_items_key 没了 —— 重复 key 就不再炸了"
        assert sorted(c.name for c in uq[0].columns) == ["key", "survey_id"]

    @pytest.mark.unit
    def test_一趟爬取的唯一约束在模型里(self):
        uq = [c for c in m.QaPageSurvey.__table__.constraints
              if getattr(c, "name", "") == "uq_qa_page_surveys_run"]
        assert uq, "uq_qa_page_surveys_run 没了"
        assert sorted(c.name for c in uq[0].columns) == [
            "build_fingerprint", "env_id", "project_id", "started_at"]

    @pytest.mark.unit
    def test_三条索引都在(self):
        idx = {i.name for i in m.QaPageSurveyItem.__table__.indexes}
        idx |= {i.name for i in m.QaPageSurvey.__table__.indexes}
        for want in ("ix_qa_page_surveys_project_env_status",
                     "ix_qa_page_survey_items_project_page",
                     "ix_qa_page_survey_items_key"):
            assert want in idx, f"{want} 没了"

    @pytest.mark.unit
    def test_按页扫得有_project_id_这一列(self):
        """架构 AD-6 要 `INDEX (project_id, page_path)`，但字段清单里没 project_id。

        这里把它冗余在 item 上（而不是把索引改成 `(survey_id, page_path)`）：
        对账是「这个项目某个域的所有页」，**跨 survey** 扫，挂在 survey 上的索引帮不上忙。
        谁要删这一列，先把对账那条查询改了。
        """
        assert "project_id" in m.QaPageSurveyItem.__table__.columns

    @pytest.mark.unit
    def test_status_是列不是_ledger_里的一个键(self):
        """`ledger` 用 jsonb 是因为账本项会长；`status` 必须是列 —— 它要进 WHERE 和索引。"""
        cols = m.QaPageSurvey.__table__.columns
        assert "status" in cols
        assert str(cols["status"].type).startswith("VARCHAR")
        assert "JSONB" in str(cols["ledger"].type)

    @pytest.mark.unit
    def test_partial_和_dirty_是独立终态(self):
        """塞进 done 加个 flag 就等于「少爬了一片」和「爬完了没问题」长得一样。

        `dirty` 更不能降级成警告：它意味着**我们可能动了别人的数据**。
        """
        for s in ("pending", "running", "done", "partial", "failed", "dirty"):
            assert s in m.STATUSES
        for s in ("done", "partial", "failed", "dirty"):
            assert s in m.TERMINAL_STATUSES
        assert "partial" not in ("done",)


class TestMigration:
    """迁移文件"""

    def _src(self):
        return MIGRATION.read_text(encoding="utf-8")

    def _code(self):
        """去掉模块 docstring 再看。

        文件顶上那段说明里**照抄了约束名**（那是它该写的：解释这条约束为什么是硬约束）。
        连它一起扫，把代码里的名字改错也照样绿 —— 实测就是这么漏过一次的。
        """
        return self._src().split('"""', 2)[2]

    @pytest.mark.unit
    def test_迁移里两张表都能建也都能删(self):
        up, down = self._code().split("def downgrade()")
        for t in ("qa_page_surveys", "qa_page_survey_items"):
            assert f'create_table(\n        "{t}"' in up, f"{t} 建不出来"
            assert f'drop_table("{t}")' in down, f"{t} 降不回去 —— 迁移只能升不能降"

    @pytest.mark.unit
    def test_迁移和模型的约束名对得上(self):
        """名字对不上 = 库里有约束、模型不知道，autogenerate 下次会提议把它删掉。"""
        code = self._code()
        for name in ("uq_qa_page_survey_items_key", "uq_qa_page_surveys_run"):
            assert name in code, f"迁移代码里没有 {name} —— 改名了？那模型那边也得改"

    @pytest.mark.unit
    def test_降级时索引先于表删掉(self):
        """drop_table 会带走自己的索引，但显式 drop_index 写在表后面会直接报错。"""
        down = self._code().split("def downgrade()")[1]
        assert down.index('drop_index("ix_qa_page_survey_items_key"') < down.index(
            'drop_table("qa_page_survey_items")')


class TestNoSilentDedupe:
    """写入路径不许把「炸」变成「不吭声」"""

    @pytest.mark.unit
    def test_写这两张表的地方一律不许_on_conflict(self):
        """AC 原话：重复 key 时**炸**，不静默去重。

        约束建在库上只完成了一半 —— `ON CONFLICT DO NOTHING` 会把它整个绕过去，
        而绕过去之后：anchor 推断塌了（整页退化成 text 锚点、两个按钮同名）
        的症状从「写入报错」变成「这一趟少了 40 项」，
        在 diff 上跟「这些功能被删了」**一模一样**。

        S6.1 落地时写入路径还没有（S6.3 才有），所以这条现在扫不到东西 ——
        它是给**将来那个人**准备的：写入路径一落地就自动纳入扫描范围。
        """
        # 用 ast 而不是正则扫文本：**注释和 docstring 里写着"不许 on_conflict"
        # 是在说明为什么，连它一起禁掉等于逼后来的人把理由删了才能过测试。**
        # （Epic 3 的 `test_页面的数从行本身来` 已经踩过一次同样的坑。）
        offenders = []
        for f in APP.rglob("*.py"):
            src = f.read_text(encoding="utf-8", errors="ignore")
            if "QaPageSurveyItem" not in src and "qa_page_survey_items" not in src:
                continue
            for node in ast.walk(ast.parse(src)):
                if (isinstance(node, ast.Attribute)
                        and node.attr in ("on_conflict_do_nothing", "on_conflict_do_update")):
                    offenders.append(f"{f.relative_to(BACKEND)}:{node.lineno}")
        assert not offenders, (
            "这些地方在写 qa_page_survey_items 的文件里用了 ON CONFLICT —— "
            "重复 key 会被静默吞掉，而它恰恰是 anchor 推断塌了的信号：\n  "
            + "\n  ".join(offenders))
