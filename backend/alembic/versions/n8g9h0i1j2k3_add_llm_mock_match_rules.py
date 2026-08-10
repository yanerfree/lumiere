"""mock_routes：智能应答改成可见可编辑的条件应答规则表

原来「智能应答」是个黑盒 bool：命中哪些关键词、命中后回什么，全写死在
llm_mock_engine.py 里，页面上看不见也改不了。这次把它实体化成 match_rules，
内置那条（测试用例关键词 → 用例 JSON）作为一条**普通规则**回填进每条已启用的路由，
从此可看、可改、可删，也可以自己加。

smart_response 顺势改名 match_enabled（语义从"启用内置智能应答"变成"启用规则表"）。
用 RENAME 而不是删列重建，数据原样保留。

Revision ID: n8g9h0i1j2k3
Revises: 4c8ca997563b
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "n8g9h0i1j2k3"
down_revision = "4c8ca997563b"
branch_labels = None
depends_on = None

# 与 llm_mock_engine 里原硬编码等价的内置规则（由脚本从源码生成，不是手抄）
BUILTIN_RULES = r"""[{"id": "builtin-testcase", "enabled": true, "name": "测试用例生成", "field": "prompt", "op": "contains_any", "value": ["测试用例", "JSON 数组", "test case", "测试设计", "设计测试用例"], "response_body": "[\n  {\n    \"title\": \"正常创建-必填字段完整\",\n    \"type\": \"api\",\n    \"priority\": \"P0\",\n    \"preconditions\": \"已登录，具有创建权限\",\n    \"steps\": [\n      {\n        \"action\": \"发送 POST 请求，body 包含所有必填字段\",\n        \"expected\": \"返回 201，响应包含新建资源的 id\"\n      },\n      {\n        \"action\": \"查询新建资源详情\",\n        \"expected\": \"返回 200，数据与提交一致\"\n      }\n    ],\n    \"expected_result\": \"资源创建成功，数据完整\",\n    \"module\": \"${request.model}\",\n    \"submodule\": null,\n    \"tags\": [\n      \"正向\",\n      \"CRUD\"\n    ]\n  },\n  {\n    \"title\": \"异常-缺少必填字段\",\n    \"type\": \"api\",\n    \"priority\": \"P0\",\n    \"preconditions\": \"已登录\",\n    \"steps\": [\n      {\n        \"action\": \"发送 POST 请求，body 缺少必填字段\",\n        \"expected\": \"返回 400/422，提示缺少必填字段\"\n      }\n    ],\n    \"expected_result\": \"拒绝创建，返回明确的错误提示\",\n    \"module\": \"${request.model}\",\n    \"submodule\": null,\n    \"tags\": [\n      \"异常\",\n      \"参数校验\"\n    ]\n  },\n  {\n    \"title\": \"异常-重复数据唯一性校验\",\n    \"type\": \"api\",\n    \"priority\": \"P0\",\n    \"preconditions\": \"数据库中已存在相同唯一键的记录\",\n    \"steps\": [\n      {\n        \"action\": \"发送 POST 请求，body 包含已存在的唯一键值\",\n        \"expected\": \"返回 409 或 400，提示数据重复\"\n      }\n    ],\n    \"expected_result\": \"拒绝重复创建\",\n    \"module\": \"${request.model}\",\n    \"submodule\": null,\n    \"tags\": [\n      \"异常\",\n      \"业务规则\"\n    ]\n  },\n  {\n    \"title\": \"边界值-字段长度上限\",\n    \"type\": \"api\",\n    \"priority\": \"P1\",\n    \"preconditions\": \"已登录\",\n    \"steps\": [\n      {\n        \"action\": \"发送 POST 请求，某字段值达到长度上限\",\n        \"expected\": \"返回 201 或明确的长度限制错误\"\n      },\n      {\n        \"action\": \"发送 POST 请求，某字段值超过长度上限\",\n        \"expected\": \"返回 400，提示超出长度\"\n      }\n    ],\n    \"expected_result\": \"边界值内正常处理，超出时有明确提示\",\n    \"module\": \"${request.model}\",\n    \"submodule\": null,\n    \"tags\": [\n      \"边界值\"\n    ]\n  },\n  {\n    \"title\": \"权限校验-未登录访问\",\n    \"type\": \"api\",\n    \"priority\": \"P1\",\n    \"preconditions\": \"未登录（无 Token）\",\n    \"steps\": [\n      {\n        \"action\": \"不带 Authorization 头发送请求\",\n        \"expected\": \"返回 401 Unauthorized\"\n      }\n    ],\n    \"expected_result\": \"未认证时拒绝访问\",\n    \"module\": \"${request.model}\",\n    \"submodule\": null,\n    \"tags\": [\n      \"权限\",\n      \"安全\"\n    ]\n  }\n]", "status_code": null}]"""


def upgrade() -> None:
    op.alter_column("mock_routes", "smart_response", new_column_name="match_enabled")
    op.add_column(
        "mock_routes",
        sa.Column("match_rules", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    # 原来 smart_response=true 的路由，行为由"隐式关键词命中"变成"显式规则命中"，
    # 回填后行为完全一致，区别只是现在页面上看得见了。
    op.execute(
        sa.text("UPDATE mock_routes SET match_rules = CAST(:r AS jsonb) WHERE match_enabled").bindparams(r=BUILTIN_RULES)
    )


def downgrade() -> None:
    op.drop_column("mock_routes", "match_rules")
    op.alter_column("mock_routes", "match_enabled", new_column_name="smart_response")
