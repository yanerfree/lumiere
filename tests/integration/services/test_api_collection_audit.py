"""接口库的写入到底有没有落进操作日志 —— 真跑一遍，不是静态扫。

封样测试（backend/tests/test_api_library_traceability.py）只能看出"代码里调了
_audit_node"，看不出**那一行真的写出了一行日志**。而这次要防的恰恰是
「查不出来」：写了记账代码但记出来的行没有项目、没有来源、筛不出来，
跟没记一样。所以这里断到库里那一行的字段上。
"""
import pytest
from sqlalchemy import select

from app.core.audit import set_audit_context
from app.models.audit_log import AuditLog
from app.schemas.project import CreateProjectRequest
from app.services import api_collection_service, project_service
from tests.conftest import create_test_user


async def _mk_project(db_session, who: str):
    user = await create_test_user(db_session, username=who, role="admin")
    project = await project_service.create_project(
        db_session,
        CreateProjectRequest(name=f"{who}-proj", git_url="git@x.com:a/b.git",
                             script_base_path=f"/tmp/{who}"),
        user,
    )
    return user, project


async def _logs(db_session, project_id):
    # 不按 created_at 排：它是 server_default=func.now()，而 Postgres 的 now()
    # 取的是**事务开始时间** —— 同一个事务里写的几行时间戳一模一样，排出来的顺序
    # 是随机的（第一版就这么红了一次，报成"delete 跑到 update 前面去了"）。
    # 线上每个 HTTP 请求各自一个事务，不会撞；测试里三个动作在一个事务内，会撞。
    rows = (await db_session.execute(
        select(AuditLog)
        .where(AuditLog.project_id == project_id, AuditLog.target_type == "api_node")
    )).scalars().all()
    return rows


class TestApiNodeAudit:

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_增删改都留下能筛出来的一行(self, db_session):
        user, project = await _mk_project(db_session, "apiaudit_crud")
        set_audit_context(user_id=user.id)

        node = await api_collection_service.create_node(
            db_session, project.id, user.id,
            {"node_type": "endpoint", "name": "查订阅", "method": "GET",
             "url": "/api/v1/subscriptions"},
        )
        await api_collection_service.update_node(
            db_session, node["id"], {"url": "/api/v2/subscriptions"},
        )
        await api_collection_service.delete_node(db_session, node["id"])

        rows = await _logs(db_session, project.id)
        by_action = {r.action: r for r in rows}
        assert set(by_action) == {"create", "update", "delete"}, \
            f"三个动作没都留痕: {sorted(r.action for r in rows)}"
        for r in rows:
            # 这两个字段是「能不能被筛出来」的全部：项目对不上，页面按项目筛就漏；
            # 对象名为空，日志列表里只剩一行看不出改了什么的记录。
            assert r.project_id == project.id, "所属项目没记上，按项目筛会漏掉它"
            assert r.target_name == "查订阅", f"对象名没记上: {r.target_name!r}"
        # 接口库最容易出问题的就是「录了名字、没填 url」的空节点，
        # url 不进 changes 的话，日志上看不出这条到底有没有内容。
        assert by_action["create"].changes.get("url") == "/api/v1/subscriptions", \
            f"创建时的 url 没进 changes: {by_action['create'].changes}"
        assert "url" in (by_action["update"].changes.get("fields") or ""), \
            f"改了哪个字段没记上: {by_action['update'].changes}"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_来源分得出是MCP还是页面(self, db_session):
        """这就是 2026-08-27 那个问题的答案该从哪儿来。"""
        user, project = await _mk_project(db_session, "apiaudit_actor")

        # 页面点的：HTTP 依赖里不设 actor_type
        set_audit_context(user_id=user.id)
        await api_collection_service.create_node(
            db_session, project.id, user.id,
            {"node_type": "folder", "name": "页面建的"},
        )
        # MCP 写的：middleware.on_call_tool 会注入这两个字段
        set_audit_context(user_id=user.id, actor_type="mcp", actor_label="uag-cc使用")
        await api_collection_service.create_node(
            db_session, project.id, user.id,
            {"node_type": "folder", "name": "MCP建的"},
        )
        set_audit_context(user_id=user.id)

        rows = await _logs(db_session, project.id)
        by_name = {r.target_name: r for r in rows}
        assert by_name["页面建的"].actor_type is None, "页面写的不该带 mcp 来源"
        assert by_name["MCP建的"].actor_type == "mcp", \
            "MCP 写的没记来源 —— 「接口库还在被 MCP 写吗」就又只能靠猜了"
        assert by_name["MCP建的"].actor_label == "uag-cc使用", "分不出是哪一把 Key/哪台机器"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_导入整批只记一条(self, db_session):
        """一个 collection 上百个接口，逐条记会把日志冲成一片。"""
        user, project = await _mk_project(db_session, "apiaudit_import")
        set_audit_context(user_id=user.id)
        collection = {
            "info": {"name": "订阅服务"},
            "item": [
                {"name": "列表", "request": {"method": "GET", "url": {"raw": "/a"}}},
                {"name": "详情", "request": {"method": "GET", "url": {"raw": "/b"}}},
            ],
        }
        count = await api_collection_service.import_postman(
            db_session, project.id, user.id, collection,
        )
        assert count == 2
        rows = await _logs(db_session, project.id)
        assert len(rows) == 1 and rows[0].action == "import", \
            f"导入应该只记一条 import，实际: {[(r.action, r.target_name) for r in rows]}"
        assert rows[0].changes.get("imported") == 2, "没记下这批导了几个"
