"""任务层：**爬完了但没存下来 ≠ 这一趟成功**（S6.4）。

Test ID: qa-page-survey-task-UT-001

这一层很薄，但有一条判断只在这里：落库炸了的时候，那一趟对下游等于没发生过 ——
可它**已经真的去访问过别人的测试环境了**。两件事都得说出来。
把它当成功记，页面上就会出现一趟"成功但查不到结果"的枚举。
"""
import uuid

import pytest

from app.engine.tasks import page_survey as t


def _payload(status="done", items=None):
    return {"status": status,
            "ledger": {"pagesVisited": 2, "writesBlocked": 1},
            "items": [{"key": "/svc::a"}] if items is None else items}


@pytest.fixture
def 记状态(monkeypatch):
    calls = []

    async def fake(task_id, status, message="", result=None):
        calls.append({"status": status, "message": message, "result": result or {}})

    monkeypatch.setattr(t, "set_task_status", fake)
    return calls


@pytest.fixture
def 假爬取(monkeypatch):
    from app.engine.surveys import qa_page_survey_crawl as c
    box = {"payload": _payload()}

    async def fake(**kw):
        return box["payload"]

    monkeypatch.setattr(c, "run_survey", fake)
    return box


@pytest.fixture
def 假落库(monkeypatch):
    box = {"raise": None, "id": uuid.uuid4()}

    async def fake(**kw):
        if box["raise"] is not None:
            raise box["raise"]
        return box["id"]

    monkeypatch.setattr(t, "_persist", fake)
    return box


class Test存不下来:
    async def test_存不下来就不许叫成功(self, 记状态, 假爬取, 假落库):
        假落库["raise"] = RuntimeError("connection reset")
        r = await t.run_page_survey({}, "task-1", str(uuid.uuid4()), ["admin"], ["/svc"])
        assert r["status"] == "failed"
        assert r["persisted"] is False
        assert 记状态[-1]["status"] == "failed"

    async def test_爬取本身的终态要留着(self, 记状态, 假爬取, 假落库):
        """落库失败会把 status 覆盖成 failed —— 但"爬到哪一步了"不能跟着丢。

        丢了就分不清「压根没爬起来」和「爬完了只是没存住」，
        而后者意味着**那个环境已经被访问过了**，重跑要按重跑对待。
        """
        假爬取["payload"] = _payload(status="partial")
        假落库["raise"] = RuntimeError("boom")
        r = await t.run_page_survey({}, "task-1", str(uuid.uuid4()), ["admin"], ["/svc"])
        assert r["crawlStatus"] == "partial"
        assert "partial" in 记状态[-1]["message"]

    async def test_存下来了才给_survey_id(self, 记状态, 假爬取, 假落库):
        r = await t.run_page_survey({}, "task-1", str(uuid.uuid4()), ["admin"], ["/svc"])
        assert r["persisted"] is True
        assert r["surveyId"] == str(假落库["id"])
        assert 记状态[-1]["status"] == "done"


class Test终态原样带出:
    @pytest.mark.parametrize("status", ["done", "partial", "dirty"])
    async def test_不许在任务层被拉平(self, 记状态, 假爬取, 假落库, status):
        """`partial`/`dirty` 是独立终态，不是"基本成功"。

        任务层把它们归一成 `done` 的话，对账那边就会拿一趟没爬全的结果当完整基线，
        没爬到的页面全部被报成「功能没了」。
        """
        假爬取["payload"] = _payload(status=status)
        r = await t.run_page_survey({}, "task-1", str(uuid.uuid4()), ["admin"], ["/svc"])
        assert r["status"] == status
        assert 记状态[-1]["status"] == status

    async def test_状态里不塞_items(self, 记状态, 假爬取, 假落库):
        """几百个控件塞进 task_status 会把它撑爆，而它是给人看进度的。
        明细在库里，survey_id 已经给了。"""
        假爬取["payload"] = _payload(items=[{"key": f"/p::{i}"} for i in range(50)])
        r = await t.run_page_survey({}, "task-1", str(uuid.uuid4()), ["admin"], ["/svc"])
        assert r["itemCount"] == 50
        assert "items" not in 记状态[-1]["result"]


class Test没开爬:
    async def test_配置不全不开爬且原因原样带出(self, 记状态, monkeypatch, 假落库):
        from app.engine.surveys import qa_page_survey_crawl as c

        async def boom(**kw):
            raise ValueError("没配 BASE_URL")

        monkeypatch.setattr(c, "run_survey", boom)
        r = await t.run_page_survey({}, "task-1", str(uuid.uuid4()), ["admin"], ["/svc"])
        assert r["status"] == "failed"
        assert "没配 BASE_URL" in 记状态[-1]["message"]
        # 没爬起来就没有"存不下来"这回事
        assert "persisted" not in r
