"""Integration — 页面枚举两个副产品的写库那一半（需要真库，S6.5）。

Test ID: qa-survey-byproducts-IT-001

纯判定在 `backend/tests/test_qa_survey_byproducts.py`。这里只测**必须有真库才成立**的：

1. **人工登记过的行，爬取一条都不动** —— 这是整条 Story 最硬的一条，
   而它只有在"库里真的有一行、跑完还是原样"时才算证明。扫源码扫不出来。
2. 对不上的地方进 `lum_list_selectors` 的待整改，**且两边任意一边修好就自己消失**
   （现算不落库，所以不会留一条谁也不敢删的过期记录）。
3. 模块体检不传 `observed_actions` 时真的从 survey 表读到了控件，
   对不上页面的模块则**退回今天的行为**（一条都不给，不拿别的模块的控件凑）。
"""
import uuid

import pytest
from sqlalchemy import select

from app.mcp.tools import test_cases as mcp_cases
from app.mcp.tools.selectors import list_selectors, upsert_selectors
from app.models.selector import ProjectSelector
from app.schemas.branch import CreateBranchRequest
from app.schemas.project import CreateProjectRequest
from app.services import branch_service, project_service
from app.services.qa_page_survey import save_survey
from app.services.qa_survey_byproducts import (
    observed_actions_for_module,
    register_selectors,
)
from tests.conftest import create_test_user

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _item(anchor="new-btn", kind="testid", page="/svc", title="服务管理",
          label="新建服务") -> dict:
    return {"key": f"{page}::{anchor}", "page_path": page, "page_title": title,
            "anchor": anchor, "anchor_kind": kind, "label": label,
            "control_type": "write", "state": "enabled"}


async def _project(db_session, tag: str):
    admin = await create_test_user(db_session, username=f"byp_{tag}", role="admin")
    return await project_service.create_project(
        db_session, CreateProjectRequest(name=f"byp-{tag}", git_url="git@x.com:b/p.git",
                                         script_base_path="/tmp/byp"), admin)


async def _survey(db_session, project_id, items, status="done"):
    s = await save_survey(db_session, project_id=project_id, status=status, items=items)
    await db_session.commit()
    return s


async def _rows(db_session, pid) -> dict:
    rows = (await db_session.execute(
        select(ProjectSelector).where(ProjectSelector.project_id == pid))).scalars().all()
    return {r.key: r for r in rows}


class Test爬到的进登记表:

    async def test_稳定锚点落_active_并标来源(self, db_session):
        proj = await _project(db_session, "reg")
        await _survey(db_session, proj.id, [_item()])

        out = await register_selectors(db_session, proj.id)
        assert out["saved"] == 1

        row = (await _rows(db_session, proj.id))["svc.new-btn"]
        assert row.status == "active" and row.source == "crawl"
        assert row.selector == '[data-testid="new-btn"]'

    async def test_只能靠文案的落_gap_进待补队列(self, db_session):
        """gap 这一档就是为「前端没给抓手」留痕的 —— 不留痕它只会变成
        一句口头的"以后再说"，然后永远没有以后。"""
        proj = await _project(db_session, "gap")
        await _survey(db_session, proj.id, [_item(anchor="批准", kind="text", label="批准")])

        await register_selectors(db_session, proj.id)
        row = (await _rows(db_session, proj.id))["svc.批准"]
        assert row.status == "gap" and row.selector is None and row.gap_note

    async def test_跑到一半的那趟不许读(self, db_session):
        """`running` 那趟正往里写，读它拿到的是半份清单 ——
        同一个模块连着体检两次会给出不一样的缺口，而这份东西的说服力全在两次一样。"""
        proj = await _project(db_session, "running")
        await _survey(db_session, proj.id, [_item()], status="running")

        out = await register_selectors(db_session, proj.id)
        assert out["status"] == "skipped"
        assert await _rows(db_session, proj.id) == {}

    @pytest.mark.parametrize("st", ["partial", "dirty"])
    async def test_没跑全和跑脏的那两趟照读(self, db_session, st):
        """反向锚点：只认 `done` 的话这两趟的观测全丢了，
        而「一律不读」跟「读错了」在页面上长得一模一样 —— 都是一片空白。
        `partial` 是**少**不是**错**；`dirty` 看见的控件仍然是真看见的。"""
        proj = await _project(db_session, f"st{st}")
        await _survey(db_session, proj.id, [_item()], status=st)
        assert (await register_selectors(db_session, proj.id))["saved"] == 1

    async def test_读的是最近那一趟(self, db_session):
        proj = await _project(db_session, "newest")
        await _survey(db_session, proj.id, [_item(anchor="老按钮")])
        await _survey(db_session, proj.id, [_item(anchor="新按钮")])

        await register_selectors(db_session, proj.id)
        assert "svc.新按钮" in await _rows(db_session, proj.id)

    async def test_不读别的项目那一趟(self, db_session):
        别人 = await _project(db_session, "otherproj")
        await _survey(db_session, 别人.id, [_item()])
        我的 = await _project(db_session, "myproj")

        out = await register_selectors(db_session, 我的.id)
        assert out["status"] == "skipped"
        assert await _rows(db_session, 我的.id) == {}

    async def test_同一趟登记两遍不多出行(self, db_session):
        proj = await _project(db_session, "idem")
        await _survey(db_session, proj.id, [_item(), _item(anchor="del-btn")])

        await register_selectors(db_session, proj.id)
        await register_selectors(db_session, proj.id)
        assert len(await _rows(db_session, proj.id)) == 2


class Test人工登记过的绝不覆盖:
    """**这是整条 Story 最硬的一条。** 爬取每次都跑，人是一次一次手改的 ——
    让自动的压过手改的，那次手改就等于没发生，而且下一趟还会再来一遍，
    谁也查不出是谁改的。"""

    async def _setup(self, db_session, tag, **manual):
        proj = await _project(db_session, tag)
        await _survey(db_session, proj.id, [_item()])
        await upsert_selectors(db_session, str(proj.id), [
            {"key": "svc.new-btn", "selector": '[data-testid="人改过的"]',
             "description": "人手改的", **manual}])
        return proj

    async def test_爬取不动人改过的那一行(self, db_session):
        proj = await self._setup(db_session, "keep")
        out = await register_selectors(db_session, proj.id)

        row = (await _rows(db_session, proj.id))["svc.new-btn"]
        assert row.selector == '[data-testid="人改过的"]'
        assert row.source == "manual"
        assert "svc.new-btn" in out["跳过（人工登记过的不覆盖）"]

    async def test_不符的那条进待整改(self, db_session):
        proj = await self._setup(db_session, "diff")
        await register_selectors(db_session, proj.id)

        listed = await list_selectors(db_session, str(proj.id))
        差 = listed["待整改·爬到的与登记不符"]
        assert 差["条数"] == 1
        assert 差["明细"][0]["爬到的"] == '[data-testid="new-btn"]'

    async def test_人改对了这条自己消失(self, db_session):
        """现算不落库：落一张"冲突表"就有了第二份数据，
        而它会过期成一条谁也不敢删的记录。"""
        proj = await self._setup(db_session, "clear")
        await register_selectors(db_session, proj.id)
        await upsert_selectors(db_session, str(proj.id), [
            {"key": "svc.new-btn", "selector": '[data-testid="new-btn"]'}])

        listed = await list_selectors(db_session, str(proj.id))
        assert "待整改·爬到的与登记不符" not in listed

    async def test_爬取自己登记的行下一趟照旧更新(self, db_session):
        """反向锚点：「不覆盖」只针对人工登记的行。
        连自己写的都不敢更新的话，前端改了 testid 这张表就永远停在旧值上。"""
        proj = await _project(db_session, "refresh")
        await _survey(db_session, proj.id, [_item()])
        await register_selectors(db_session, proj.id)

        await _survey(db_session, proj.id, [_item(anchor="new-btn", label="新建服务（改版）")])
        out = await register_selectors(db_session, proj.id)
        assert out["saved"] == 1 and "跳过（人工登记过的不覆盖）" not in out
        assert (await _rows(db_session, proj.id))["svc.new-btn"].source == "crawl"


class Test待补队列不许被爬取淹掉:

    async def test_有人卡着的排在爬取扫出来的前面(self, db_session):
        """一次爬取能扫出几百条文案锚点的 gap 行。`lum_next_duty` 只列 `[:limit]` ——
        把真有用例卡着的那几条挤出去，就等于没记。"""
        from app.mcp.tools.selectors import selector_gaps_for_branch

        proj = await _project(db_session, "queue")
        branch = await branch_service.create_branch(
            db_session, proj.id, CreateBranchRequest(name="b-queue"))
        await _survey(db_session, proj.id, [
            _item(anchor=f"控件{i}", kind="text", label=f"控件{i}") for i in range(5)])
        await register_selectors(db_session, proj.id)
        await upsert_selectors(db_session, str(proj.id), [
            {"key": "zz.最后一个键", "status": "gap", "gap_note": "前端没给抓手",
             "blocked_cases": ["TC-SVC-00001"]}])

        gaps, _ = await selector_gaps_for_branch(db_session, branch.id)
        assert len(gaps) == 6, "计数照旧是全量"
        assert gaps[0]["选择器键"] == "zz.最后一个键"


class Test体检的可操作项从枚举读:

    async def _module_case(self, db_session, tag, module):
        proj = await _project(db_session, f"ck{tag}")
        branch = await branch_service.create_branch(
            db_session, proj.id, CreateBranchRequest(name=f"b-{tag}"))
        await mcp_cases.create_case(
            db_session, branch_id=str(branch.id), title="新建服务-返回 201 且列表可见",
            module=module,
            steps=[{"seq": 1, "action": "操作：打开服务管理页并点新建",
                    "expected": "弹出新建表单"}],
            expected_result="创建成功返回 201，列表里能查到这条服务")
        return proj, branch

    async def test_不传就从最近一趟枚举里读(self, db_session, monkeypatch):
        from app.services.review import checkup

        proj, branch = await self._module_case(db_session, "hit", "服务管理")
        await _survey(db_session, proj.id, [_item(), _item(anchor="del-btn", label="删除服务")])

        seen = {}

        class R:
            content = '```json\n{"coverageGaps": []}\n```'

        async def fake(msgs, **kw):
            seen["user"] = msgs[-1]["content"]
            return R()

        monkeypatch.setattr(checkup.llm_client, "complete", fake)
        out = await checkup.run(db_session, branch.id, module="服务管理",
                                ai_config=object())

        assert "页面上实际探到的可操作项" in seen["user"]
        assert "删除服务" in seen["user"]
        assert "从最近一趟页面枚举里读的" in out["usage"]

    async def test_对不上页面就退回今天的行为(self, db_session, monkeypatch):
        """**不拿别的模块的控件凑。** 串台的控件产出的是
        「这个模块没测导出功能」这种查一次就发现根本不存在的假缺口。"""
        from app.services.review import checkup

        proj, branch = await self._module_case(db_session, "miss", "订阅管理")
        await _survey(db_session, proj.id, [_item()])

        seen = {}

        class R:
            content = '```json\n{"coverageGaps": []}\n```'

        async def fake(msgs, **kw):
            seen["user"] = msgs[-1]["content"]
            return R()

        monkeypatch.setattr(checkup.llm_client, "complete", fake)
        out = await checkup.run(db_session, branch.id, module="订阅管理",
                                ai_config=object())

        assert "页面上实际探到的可操作项" not in seen["user"]
        assert "传 observed_actions" in out["usage"]

    async def test_调用方传了就不去读枚举(self, db_session, monkeypatch):
        """兜底只在**没传**的时候顶上。反过来的话，CC 在页面上现探的那份
        （新、准、带上下文）会被库里那趟旧的盖掉，而调用方看不出发生过这件事。"""
        from app.services.review import checkup

        proj, branch = await self._module_case(db_session, "own", "服务管理")
        await _survey(db_session, proj.id, [_item(anchor="del-btn", label="删除服务")])

        seen = {}

        class R:
            content = '```json\n{"coverageGaps": []}\n```'

        async def fake(msgs, **kw):
            seen["user"] = msgs[-1]["content"]
            return R()

        monkeypatch.setattr(checkup.llm_client, "complete", fake)
        out = await checkup.run(db_session, branch.id, module="服务管理",
                                observed_actions=["服务管理 · 自己探到的（write）"],
                                ai_config=object())

        assert "自己探到的" in seen["user"]
        assert "删除服务" not in seen["user"]
        assert "从最近一趟页面枚举里读的" not in out["usage"]

    async def test_调用方自己传了就用它的(self, db_session):
        proj, _ = await _project(db_session, "explicit"), None
        await _survey(db_session, proj.id, [_item()])
        assert await observed_actions_for_module(db_session, proj.id, "服务管理")
        assert await observed_actions_for_module(db_session, proj.id, "别的模块") == []

    async def test_没跑过枚举也不炸(self, db_session):
        proj = await _project(db_session, "nosurvey")
        assert await observed_actions_for_module(db_session, proj.id, "服务管理") == []
        assert await observed_actions_for_module(
            db_session, uuid.uuid4(), "服务管理") == []
