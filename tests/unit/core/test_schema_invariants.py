"""Unit 测试 — 两条结构性不变式：迁移链完整 + 指向 projects 的外键都声明了 ondelete

为什么要有这两条：删项目 500 的修复被别人的 `git add -A` 卷进了两个不相关的提交
（ae38884「侧边栏按职能分组」带走了迁移文件，60c2cf6「菜单按功能重分」带走了 7 个
模型的 ondelete）。代码是对的，但 revert 那两笔中任何一笔都会**静默**破坏：

  · revert 60c2cf6 → 7 个模型的 ondelete 没了，库里还是 CASCADE。两边分叉，
    下次 alembic autogenerate 会生成一个把级联删掉的迁移，删项目 500 复发。
  · revert ae38884 → zzd0fkc1 迁移文件没了，而它已经有下游（zze0i18nmod），
    整条迁移链断在那儿，upgrade / downgrade 全废。

历史没法在共用工作区里重整（会冲掉并行会话未提交的改动），所以改成让这两种破坏
**没法静默落地**：谁 revert 谁立刻红在这里。
"""
import pathlib
import re

import pytest

VERSIONS_DIR = pathlib.Path(__file__).resolve().parents[3] / "backend" / "alembic" / "versions"

# 这几张表删项目时必须跟着删。口径是「项目删掉就全部删掉」，
# 门禁在 project_service.assert_project_deletable 里挡人工资产，这里守物理级联。
_MUST_CASCADE = {
    "ai_usage_logs", "api_test_scenarios", "documents",
    "exploratory_sessions", "knowledge_entries", "project_ai_configs", "test_reports",
}


def _revisions():
    """扫 alembic/versions/，返回 {revision: (父 revision 列表, 文件名)}。

    直接读源码而不 import：迁移文件里有 op.execute 等副作用，import 一遍
    不值当，而 revision / down_revision 是纯字面量，正则够用。

    ⚠ 三种写法都得认，这个仓库里全都有。少认一种就会误报，而且误报方向是
    「说链断了 / 说有一堆 head」—— 看着像大事，实际是解析器自己瞎了：

        revision = "x"                                    ← 手写的
        revision: str = 'x'                               ← alembic 新模板
        down_revision: Union[str, None] = 'y'
        down_revision = ("a", "b")                        ← 合并迁移，一次两个父

    实测：只认第一种时误报 11 个 head；认了类型标注但漏掉 tuple 时误报 12 个 ——
    而 `alembic heads` 自始至终只有 1 个。所以父 revision 一律用「RHS 上所有引号
    字符串」来取，None 自然得到空列表。
    """
    out = {}
    for f in VERSIONS_DIR.glob("*.py"):
        src = f.read_text(encoding="utf-8")
        rev = re.search(r'^revision(?:\s*:[^=]+)?\s*=\s*["\']([^"\']+)["\']', src, re.M)
        if not rev:
            continue
        down_line = re.search(r'^down_revision(?:\s*:[^=]+)?\s*=\s*(.+)$', src, re.M)
        parents = re.findall(r'["\']([^"\']+)["\']', down_line.group(1)) if down_line else []
        out[rev.group(1)] = (parents, f.name)
    return out


class TestMigrationChain:
    """alembic 迁移链"""

    @pytest.mark.unit
    def test_every_down_revision_exists(self):
        revs = _revisions()
        assert revs, f"一个迁移都没扫到，路径不对？{VERSIONS_DIR}"
        dangling = [
            f"{fname}(revision={rev}) 的 down_revision={p} 找不到对应文件"
            for rev, (parents, fname) in revs.items()
            for p in parents
            if p not in revs
        ]
        assert not dangling, (
            "迁移链断了 —— alembic upgrade/downgrade 会直接报 Can't locate revision。"
            "常见原因是删掉/回滚了某个迁移文件，但它已经有下游：\n  " + "\n  ".join(dangling)
        )

    @pytest.mark.unit
    def test_single_head(self):
        revs = _revisions()
        referenced = {p for parents, _ in revs.values() for p in parents}
        heads = sorted(set(revs) - referenced)
        assert len(heads) == 1, (
            f"迁移出现 {len(heads)} 个 head：{heads}。"
            "多 head 时 alembic upgrade head 会失败，需要 merge。"
        )

    @pytest.mark.unit
    def test_no_duplicate_revision_ids(self):
        seen = {}
        dupes = []
        for f in sorted(VERSIONS_DIR.glob("*.py")):
            m = re.search(r'^revision\s*=\s*["\']([^"\']+)["\']', f.read_text(encoding="utf-8"), re.M)
            if not m:
                continue
            if m.group(1) in seen:
                dupes.append(f"{m.group(1)}: {seen[m.group(1)]} 与 {f.name}")
            seen[m.group(1)] = f.name
        assert not dupes, "revision id 撞了，alembic 会随机选一个走：\n  " + "\n  ".join(dupes)


class TestProjectForeignKeyOndelete:
    """模型层指向 projects.id 的外键"""

    @pytest.mark.unit
    def test_all_project_fks_declare_ondelete(self):
        """不许留空。

        留空等于 NO ACTION —— 项目下一有数据删除就撞外键约束冒 500，
        而这正是最初那个 bug 的成因。要 CASCADE 还是 SET NULL 是设计选择，
        但必须**明确写出来**，不能靠默认值。
        """
        import app.main  # noqa: F401 — 先导入完整应用，确保所有模型都注册进 metadata
        from app.models.user import Base

        missing = []
        for table in Base.metadata.tables.values():
            for fk in table.foreign_keys:
                if fk.column.table.name != "projects":
                    continue
                if not fk.ondelete:
                    missing.append(f"{table.name}.{fk.parent.name}")
        assert not missing, (
            "这些指向 projects.id 的外键没声明 ondelete（= NO ACTION，删项目会 500）：\n  "
            + "\n  ".join(sorted(missing))
        )

    @pytest.mark.unit
    def test_cascade_tables_stay_cascade(self):
        """这 7 张必须是 CASCADE —— 迁移 zzd0fkc1 就是为它们加的。

        谁把某一张改成 SET NULL 或删掉 ondelete，这里会红，逼他重新想一遍：
        库里已经是 CASCADE 了，模型改了就是两边分叉。
        """
        import app.main  # noqa: F401
        from app.models.user import Base

        actual = {}
        for table in Base.metadata.tables.values():
            for fk in table.foreign_keys:
                if fk.column.table.name == "projects" and table.name in _MUST_CASCADE:
                    actual[table.name] = fk.ondelete
        assert set(actual) == _MUST_CASCADE, (
            f"表名对不上，模型改过？缺：{sorted(_MUST_CASCADE - set(actual))}"
        )
        wrong = {t: v for t, v in actual.items() if (v or "").upper() != "CASCADE"}
        assert not wrong, f"这些表的 ondelete 不再是 CASCADE，和库里分叉了：{wrong}"
