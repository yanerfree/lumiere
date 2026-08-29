"""Integration — services/qa_page_survey.py 的落库那一半（需要真库）。

Test ID: qa-page-survey-store-IT-001

这里只测**必须有真库才成立**的三件事，纯判定那部分在
`backend/tests/test_qa_page_survey_diff.py`：

1. `first_seen_survey_id` 跨趟保留 —— 「这个控件是哪一版冒出来的」全靠它；
2. `(survey_id, key)` 撞了要**抛**，不许 `on_conflict` 顶过去；
3. `save_survey` 不 commit，事务边界归调用方。

第 2 条为什么不能在单测里扫源码：扫 `on_conflict` 这个词，**这段注释自己就能满足它** ——
封样测试写在被它守护的那份源码旁边时，一句文档就能把它骗过去。真判据是"撞了要炸"。
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.environment import Environment
from app.models.qa_page_survey import QaPageSurvey, QaPageSurveyItem
from app.schemas.project import CreateProjectRequest
from app.services import project_service
from app.services.qa_page_survey import latest_survey, save_survey
from tests.conftest import create_test_user


def _item(key: str, **kw) -> dict:
    return {"key": key, "page_path": key.split("::")[0], "anchor": key.split("::")[-1],
            "anchor_kind": "testid", "label": "新建", "control_type": "write",
            "state": "enabled", **kw}


async def _project(db_session, name: str):
    creator = await create_test_user(db_session, username=f"srv_{name}", role="admin")
    return await project_service.create_project(
        db_session, CreateProjectRequest(name=name, git_url="git@x.com:t/r.git",
                                         script_base_path="/tmp/x"), creator)


class TestFirstSeen:

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_老控件的_first_seen_指向第一趟(self, db_session):
        """「这个控件是哪一版冒出来的」只有这一个出处。

        每趟都写成本趟 id 的话，所有控件永远看起来都是"这版新增的",
        对账那边就再也分不出新功能和老功能。
        """
        proj = await _project(db_session, "survey-firstseen")
        s1 = await save_survey(db_session, project_id=proj.id, status="done",
                               items=[_item("/svc::new")])
        await db_session.commit()

        s2 = await save_survey(db_session, project_id=proj.id, status="done",
                               items=[_item("/svc::new"), _item("/svc::really-new")])
        await db_session.commit()

        rows = (await db_session.execute(
            select(QaPageSurveyItem).where(QaPageSurveyItem.survey_id == s2.id)
        )).scalars().all()
        got = {r.key: (r.first_seen_survey_id, r.last_seen_survey_id) for r in rows}
        assert got["/svc::new"] == (s1.id, s2.id)          # 老的：第一次见还是第一趟
        assert got["/svc::really-new"] == (s2.id, s2.id)   # 新的：这趟才冒出来

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_别的项目的同名控件不算见过(self, db_session):
        """key 是 `页面::锚点`，两个项目撞出同一个 key 太容易了（`/settings::保存`）。

        不按项目隔离的话，A 项目的一趟会把 B 项目控件的"首次出现"改写成 A 的那一趟。
        """
        a = await _project(db_session, "survey-scope-a")
        b = await _project(db_session, "survey-scope-b")
        sa = await save_survey(db_session, project_id=a.id, status="done",
                               items=[_item("/settings::save")])
        await db_session.commit()
        sb = await save_survey(db_session, project_id=b.id, status="done",
                               items=[_item("/settings::save")])
        await db_session.commit()

        row = (await db_session.execute(
            select(QaPageSurveyItem).where(QaPageSurveyItem.survey_id == sb.id)
        )).scalar_one()
        assert row.first_seen_survey_id == sb.id
        assert row.first_seen_survey_id != sa.id


class TestKey撞了要炸:

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_同一趟里两个同_key_的控件写不进去(self, db_session):
        """AD-6：撞 key 意味着锚点推断塌了（整页退化成文案锚点、两个按钮同名）。

        用 `on_conflict_do_nothing` 顶过去的话，那一趟会**少存一批行**，
        下一趟 diff 报成「新增 N 项」—— 没人查得出源头，只会当成前端改版。
        让它在写入时就炸，是这条链上唯一还能定位到原因的地方。
        """
        proj = await _project(db_session, "survey-dupe")
        with pytest.raises(IntegrityError):
            await save_survey(db_session, project_id=proj.id, status="done",
                              items=[_item("/svc::save"), _item("/svc::save")])
        await db_session.rollback()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_不同趟的同_key_是正常的(self, db_session):
        """反向锚点：唯一约束只管一趟之内。跨趟同 key 正是常态（控件没变），
        约束要是加到 (project_id, key) 上，第二趟就一行都存不进来。"""
        proj = await _project(db_session, "survey-cross")
        await save_survey(db_session, project_id=proj.id, status="done",
                          items=[_item("/svc::save")])
        await db_session.commit()
        await save_survey(db_session, project_id=proj.id, status="done",
                          items=[_item("/svc::save")])
        await db_session.commit()

        n = len((await db_session.execute(
            select(QaPageSurveyItem).where(QaPageSurveyItem.project_id == proj.id)
        )).scalars().all())
        assert n == 2


class Test事务边界:

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_save_survey_自己不_commit(self, db_session):
        """自己 commit 的话，「爬完了但后续步骤炸了」会留下一趟半截记录，
        而任务层那条 `persisted=False` 的判断就永远说谎。"""
        proj = await _project(db_session, "survey-tx")
        await db_session.commit()

        survey = await save_survey(db_session, project_id=proj.id, status="done",
                                   items=[_item("/svc::a")])
        sid = survey.id
        await db_session.rollback()

        assert (await db_session.execute(
            select(QaPageSurvey).where(QaPageSurvey.id == sid))).scalar_one_or_none() is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_一条_item_都没有也存得下这一趟(self, db_session):
        """零控件的一趟必须留痕：它是 `partial`/`failed` 的证据本身。
        跳过不存的话，页面上看起来就像这趟枚举从没发生过。"""
        proj = await _project(db_session, "survey-empty")
        survey = await save_survey(db_session, project_id=proj.id, status="failed",
                                   items=[], ledger={"shardsFailed": ["TimeoutError"]},
                                   error="全挂了")
        await db_session.commit()
        got = (await db_session.execute(
            select(QaPageSurvey).where(QaPageSurvey.id == survey.id))).scalar_one()
        assert got.status == "failed"
        assert got.ledger["shardsFailed"] == ["TimeoutError"]


class TestLatestSurvey:
    """`latest_survey` —— 「上一趟是哪一趟」。

    它是 `plan_reuse` 的**唯一入参来源**，答错的后果不是少命中一次缓存，
    是**拿另一个环境（或更早一趟）的爬取当成这一轮的结论端出去**。
    所以这三条必须在真库上验：排序、认环境、不按状态筛。
    """

    @staticmethod
    async def _envs(db_session, project_id):
        """新建项目会自动铺 4 个环境（`project_defaults.py`），取头两个用。"""
        rows = (await db_session.execute(
            select(Environment).where(Environment.project_id == project_id)
            .order_by(Environment.name))).scalars().all()
        assert len(rows) >= 2, "默认环境没铺出来，这条测试的前提就不成立"
        return rows[0], rows[1]

    @staticmethod
    async def _trip(db_session, project_id, env_id, *, status, at, fp=""):
        s = await save_survey(db_session, project_id=project_id, env_id=env_id,
                              build_fingerprint=fp, status=status, items=[])
        s.started_at = at          # 显式压时间：不靠插入顺序，也不靠时钟精度
        await db_session.commit()
        return s

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_取最近那一趟_不是最后插进去那一趟(self, db_session):
        """**故意把更早的那一趟后插入。**

        照插入顺序（或按 id）取的实现在这里才会红 —— 而两种写法在
        "顺着插" 的数据上给出的答案一模一样，测不出来。
        """
        proj = await _project(db_session, "survey-latest")
        e1, _ = await self._envs(db_session, proj.id)
        new = await self._trip(db_session, proj.id, e1.id, status="done",
                               at=datetime(2026, 8, 28, 10, tzinfo=timezone.utc), fp="新")
        await self._trip(db_session, proj.id, e1.id, status="done",
                         at=datetime(2026, 8, 20, 10, tzinfo=timezone.utc), fp="旧")

        got = await latest_survey(db_session, proj.id, e1.id)
        assert got is not None and got.id == new.id and got.build_fingerprint == "新"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_别的环境那一趟不算这个环境的上一趟(self, db_session):
        """它爬的是**别人的测试环境**。认错环境 ⇒ 把 A 环境的爬取当成 B 环境的结论，
        而页面上两者长得一模一样。"""
        proj = await _project(db_session, "survey-env-scope")
        e1, e2 = await self._envs(db_session, proj.id)
        await self._trip(db_session, proj.id, e2.id, status="done",
                         at=datetime(2026, 8, 28, 10, tzinfo=timezone.utc), fp="别的环境")
        mine = await self._trip(db_session, proj.id, e1.id, status="done",
                                at=datetime(2026, 8, 20, 10, tzinfo=timezone.utc), fp="我的")

        got = await latest_survey(db_session, proj.id, e1.id)
        assert got.id == mine.id      # 时间更早，但环境对得上

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_没绑环境的那些趟是独立的一档(self, db_session):
        """`env_id is None` 不是"不筛"，是一个自己的口径。
        当成通配的话，没绑环境的一趟会冒充任何一个环境的上一趟。"""
        proj = await _project(db_session, "survey-env-null")
        e1, _ = await self._envs(db_session, proj.id)
        await self._trip(db_session, proj.id, e1.id, status="done",
                         at=datetime(2026, 8, 28, 10, tzinfo=timezone.utc), fp="绑了环境")
        naked = await self._trip(db_session, proj.id, None, status="done",
                                 at=datetime(2026, 8, 20, 10, tzinfo=timezone.utc), fp="没绑")

        assert (await latest_survey(db_session, proj.id, None)).id == naked.id

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_最近一趟是failed也照样返回它(self, db_session):
        """**不在这里按状态筛。**

        跳过 failed 去翻出更早那趟 `done`，就等于"上一趟没跑成"这件事从没发生过 ——
        而它前面还压着一趟没跑完的。可复用与否只有一个判官（`plan_reuse`），
        这里只负责把事实原样交上去。
        """
        proj = await _project(db_session, "survey-failed-latest")
        e1, _ = await self._envs(db_session, proj.id)
        await self._trip(db_session, proj.id, e1.id, status="done",
                         at=datetime(2026, 8, 20, 10, tzinfo=timezone.utc), fp="好的那趟")
        bad = await self._trip(db_session, proj.id, e1.id, status="failed",
                               at=datetime(2026, 8, 28, 10, tzinfo=timezone.utc), fp="挂了那趟")

        got = await latest_survey(db_session, proj.id, e1.id)
        assert got.id == bad.id and got.status == "failed"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_一趟都没有就是None(self, db_session):
        proj = await _project(db_session, "survey-none")
        e1, _ = await self._envs(db_session, proj.id)
        assert await latest_survey(db_session, proj.id, e1.id) is None
