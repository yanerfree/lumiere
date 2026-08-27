"""模型包。

这里**只放"没有别的地方会 import 它"的模型**，不是模型总入口 ——
其余模型都由各自的 api/service 顺带 import 进 metadata，别往这儿搬。

`ExploratorySession` / `ExploratoryFinding`：「探索测试」2026-08-27 下线
（docs/cc-platform-loop-spec.md §15），`api/exploratory.py` 删了，表和 3 条会话 /
1 条发现留着。`exploratory_sessions` 还在 `test_schema_invariants` 的
`_MUST_CASCADE` 里 —— 不 import 就是下面 `Document` 那三条后果原样再来一遍。

`Document`：「文档管理」模块 2026-08-27 整个下线（docs/cc-platform-loop-spec.md §14），
页面/路由/api/documents.py 全删了，**但表和库里那 5 条真数据留着**。
它原本是靠 `api/documents.py` 那条 import 链进 `Base.metadata` 的，那条链没了之后：

  · `alembic revision --autogenerate` 会认为模型里没有这张表 → **提议 DROP TABLE documents**，
    合进去就是把那 5 条数据删掉，而 diff 上只是一行 op.drop_table；
  · `Base.metadata.create_all` 建的测试库里不再有这张表；
  · `test_schema_invariants` 的 CASCADE 封样会漏掉它（表名对不上，实测红过）。

所以这一行是**防误删**，不是"以防万一留着"。要真删表，先删数据再删这行。
"""
from app.models.document import Document  # noqa: F401 — 见上：少了它 autogenerate 会提议 DROP
from app.models.exploratory import (  # noqa: F401 — 同上，「探索测试」2026-08-27 下线
    ExploratoryFinding,
    ExploratorySession,
)
