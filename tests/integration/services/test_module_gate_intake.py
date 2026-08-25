"""模块门禁不该挡住「往已有模块里放用例」（2026-08-25 的 bug）。

现场（用户截图）：
    模块目录一旦存在，用**完全相同**的 module+submodule 再传就被拒，
    提示「直接往它里面加用例，别再建一个」—— 而那正是调用方刚做的事，
    参数上也没有第二种写法能表达"往里面加"。
    结果 4 条用例被迫散在「MCP Hub」「MCP Hub 内置工具」「MCP Hub 高危工具」
    「MCP Hub 接入指引」四个目录里，本该在一个目录下。
    `tb_update_case` 搬家撞同一堵墙 —— 目标目录存在才叫搬家。

根因：`check_module_placement` 规则 2「同一位置已有 → 硬拒」是**建模块**的判据，
被一起接到了「放用例」这条路上。

这些测试打真库、走真 MCP 工具函数 —— 只钉 `check_module_placement` 的返回值
钉不住这个 bug，因为它单看没错，错的是**谁在调它**。
"""
import uuid

import pytest

from app.mcp.tools import test_cases as mcp_cases
from app.schemas.branch import CreateBranchRequest
from app.schemas.project import CreateProjectRequest
from app.services import branch_service, project_service
from tests.conftest import create_test_user

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

MOD = "MCP Hub"


async def _branch(db_session, tag: str) -> str:
    admin = await create_test_user(db_session, username=f"mgate_{tag}", role="admin")
    project = await project_service.create_project(
        db_session,
        CreateProjectRequest(name=f"mgate-{tag}", git_url="git@x.com:m/g.git",
                             script_base_path="/tmp/mgate"),
        admin,
    )
    branch = await branch_service.create_branch(
        db_session, project.id, CreateBranchRequest(name=f"b-{tag}"))
    return str(branch.id)


async def _create(db_session, bid, title, module=MOD, submodule=None):
    return await mcp_cases.create_case(
        db_session, branch_id=bid, title=title, module=module, submodule=submodule,
        steps=[{"seq": 1, "action": "打开 MCP Key 列表页", "expected": "列出全部 Key"}],
        expected_result="列表返回 200，且每行显示 key_prefix 与所属项目",
    )


async def _path_of(db_session, case_id) -> str:
    from sqlalchemy import select

    from app.models.case import Case, CaseFolder
    folder_id = (await db_session.execute(
        select(Case.folder_id).where(Case.id == uuid.UUID(case_id)))).scalar_one()
    return (await db_session.execute(
        select(CaseFolder.path).where(CaseFolder.id == folder_id))).scalar_one()


class TestSameModuleAcceptsManyCases:
    """一个目录必须装得下多条用例 —— 这是门禁最基本的不该挡。"""

    async def test_一级模块能连着放四条(self, db_session):
        bid = await _branch(db_session, "top4")
        codes = []
        for i, t in enumerate(("MCP Key 列表-按项目过滤生效",
                               "MCP Key 创建-未绑项目时拒绝并提示",
                               "MCP Key 停用-停用后调用返回 401",
                               "MCP Key 内置工具-高危工具需二次确认")):
            r = await _create(db_session, bid, t)
            assert "error" not in r, f"第 {i + 1} 条被门禁挡了：{r.get('problems')}"
            codes.append(r["caseCode"])
        assert len(set(codes)) == 4
        # 四条必须都落在**同一个**目录，不是各自另起一个 ——
        # 这才是用户截图里那个后果，只断"没报错"断不住。
        listed = await mcp_cases.list_cases(db_session, bid)
        assert listed["total"] == 4
        ids = [it["id"] for it in listed["cases"]]
        assert {await _path_of(db_session, i) for i in ids} == {"MCP HUB"}

    async def test_二级模块能连着放三条(self, db_session):
        bid = await _branch(db_session, "sub3")
        for i, t in enumerate(("内置工具清单-列出全部内置工具及其开关",
                               "内置工具停用-停用后 CC 侧调用返回 403",
                               "内置工具入参校验-缺 branchId 时返回 422")):
            r = await _create(db_session, bid, t, submodule="内置工具")
            assert "error" not in r, f"第 {i + 1} 条被门禁挡了：{r.get('problems')}"
        listed = await mcp_cases.list_cases(db_session, bid)
        assert listed["total"] == 3
        ids = [it["id"] for it in listed["cases"]]
        assert {await _path_of(db_session, i) for i in ids} == {"MCP HUB/内置工具"}


class TestUpdateCaseCanMove:
    """搬家：目标目录**存在**才叫搬家，所以它必然踩规则 2。"""

    async def test_搬进已存在的子模块(self, db_session):
        bid = await _branch(db_session, "mv")
        # 先把目标子目录建出来（放一条用例进去）
        await _create(db_session, bid, "内置工具清单-列出全部内置工具及其开关",
                      submodule="内置工具")
        stray = await _create(db_session, bid, "MCP Key 停用-停用后调用返回 401")
        assert "error" not in stray

        moved = await mcp_cases.update_case(
            db_session, case_id=stray["id"], module=MOD, submodule="内置工具")
        assert "error" not in moved, f"搬家被门禁挡了：{moved.get('problems')}"
        assert await _path_of(db_session, stray["id"]) == "MCP HUB/内置工具"
        # 编号是回推/脚本/报告共用的锚点，搬家不能动它
        assert moved["caseCode"] == stray["caseCode"]

    async def test_搬回模块根下(self, db_session):
        bid = await _branch(db_session, "mvroot")
        a = await _create(db_session, bid, "MCP Key 列表-按项目过滤生效")
        b = await _create(db_session, bid, "内置工具清单-列出全部内置工具及其开关",
                          submodule="内置工具")
        assert "error" not in a and "error" not in b
        moved = await mcp_cases.update_case(db_session, case_id=b["id"], module=MOD)
        assert "error" not in moved, f"搬回根下被挡了：{moved.get('problems')}"
        assert await _path_of(db_session, b["id"]) == "MCP HUB"


class TestStillBlocksTheRealSplit:
    """修完不能把该拦的一起放掉 —— 规则 4 那个裂库的口子必须还在。"""

    async def test_顶层已有同名_还想挂到模块下要拒(self, db_session):
        bid = await _branch(db_session, "split1")
        # 顶层先有「内置工具」
        r = await _create(db_session, bid, "内置工具清单-列出全部内置工具及其开关",
                          module="内置工具")
        assert "error" not in r
        # 再想在「MCP Hub」下建一个同名的 → 同一个东西摆两处
        bad = await _create(db_session, bid, "内置工具停用-停用后 CC 侧调用返回 403",
                            module=MOD, submodule="内置工具")
        assert "error" in bad, "裂库那一刀被放过去了"
        assert "劈成两半" in " ".join(bad["problems"])

    async def test_范围词还是硬拒(self, db_session):
        bid = await _branch(db_session, "scope")
        bad = await _create(db_session, bid, "MCP Key 列表-按项目过滤生效", module="平台自身")
        assert "error" in bad and "范围词" in " ".join(bad["problems"])

    async def test_写法不同的重名给的是能照做的话(self, db_session):
        """`mcp hub` 命中已有「MCP Hub」→ 该说"用现成的那个名字"，不该说"别再建一个"。"""
        bid = await _branch(db_session, "variant")
        assert "error" not in await _create(db_session, bid, "MCP Key 列表-按项目过滤生效")
        bad = await _create(db_session, bid, "MCP Key 创建-未绑项目时拒绝并提示",
                            module="mcp-hub")
        assert "error" in bad
        assert "用现成的那个名字" in " ".join(bad["problems"])
