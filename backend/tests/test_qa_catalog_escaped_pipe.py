"""清单里的 `\\|`（GFM 转义竖线）不能把列切错位；并排四列的域码表右半边也要读到。"""
from app.services import qa_catalog as q

CAT = """
| 域码 | 域 | 域码 | 域 |
|---|---|---|---|
| `AUTH` | 认证与会话 | `GW` | 网关资源 |
| | | `DEP` | 部署与兼容 |

| ID | 场景 | 类型 | 优先 | 状态 |
|---|---|---|---|---|
| AUTH-01 | 指纹 `a\\|<node>` 去重 | API | P1 | 未建 |
| AUTH-02 | 普通 | UI | P0 | 未建 |
"""


def test_escaped_pipe_keeps_columns_aligned():
    rows, doms, _ = q.parse_catalog(CAT)
    r = {x["id"]: x for x in rows}
    assert r["AUTH-01"]["tier"] == "API"
    assert r["AUTH-01"]["priority"] == "P1"
    assert "a|<node>" in r["AUTH-01"]["title"]


def test_domain_table_right_half_has_names():
    _, doms, _ = q.parse_catalog(CAT)
    assert doms["GW"]["name"] == "网关资源"
    assert doms["DEP"]["name"] == "部署与兼容"
