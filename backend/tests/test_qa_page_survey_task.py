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
    box = {"payload": _payload(), "kw": None}

    async def fake(**kw):
        box["kw"] = kw
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


# ── 复用判断（S7.8 / 架构 AD-8）────────────────────────────────────────────
#
# 这一层唯一的新活儿是**要不要真的去爬**。判据全在 `qa_survey_cache`（纯函数、
# 单独一份测试钉着），所以这里钉的是另外三件它管不到的事：
#   ① 说"复用"的时候，那趟爬取**真的没发生**（省下来的就是这一趟）；
#   ② 复用的产物里没有一个凭空捏的数（`itemCount: 0` 那种）；
#   ③ 查库失败时说的是**我们自己那句话**，不是 `plan_reuse` 的「没有上一趟」。

FP = "build-2026-08-29"


@pytest.fixture
def 数爬取(monkeypatch):
    """跟 `假爬取` 一样，但**数一下到底爬没爬**。

    复用那条路径的全部价值就是"这一趟没发生"，而它跟"爬了但结果一样"在返回值上
    分不出来 —— 只有计数分得出来。
    """
    from app.engine.surveys import qa_page_survey_crawl as c
    box = {"calls": 0, "payload": _payload()}

    async def fake(**kw):
        box["calls"] += 1
        return box["payload"]

    monkeypatch.setattr(c, "run_survey", fake)
    return box


@pytest.fixture
def 假上一趟(monkeypatch):
    """替掉查库那一步。默认查得到、可复用、三格都没变。"""
    box = {"raise": None, "prev": None, "calls": []}

    async def fake(*, project_id, env_id, qa_commit_sha):
        box["calls"].append({"projectId": str(project_id), "envId": str(env_id),
                             "qaCommitSha": qa_commit_sha})
        if box["raise"] is not None:
            raise box["raise"]
        return box["prev"]

    monkeypatch.setattr(t, "_previous", fake)
    return box


def _prev(pid, eid, *, status="done", fp=FP, rt="rt-1", sha="sha-1"):
    return {"surveyId": "s-9", "status": status, "crawledAt": "2026-08-28 10:00:00",
            "projectId": str(pid), "envId": str(eid), "buildFingerprint": fp,
            "routeTableHash": rt, "qaCommitSha": sha}


async def _run(pid, eid, **kw):
    kw.setdefault("build_fingerprint", FP)
    kw.setdefault("route_table_hash", "rt-1")
    kw.setdefault("qa_commit_sha", "sha-1")
    return await t.run_page_survey({}, "task-1", str(pid), ["admin"], ["/svc"],
                                   env_id=str(eid), **kw)


class Test能复用就别去爬:
    async def test_复用那一轮一次都没爬(self, 记状态, 数爬取, 假落库, 假上一趟):
        pid, eid = uuid.uuid4(), uuid.uuid4()
        假上一趟["prev"] = _prev(pid, eid)
        r = await _run(pid, eid)
        assert r["action"] == "reuse"
        assert 数爬取["calls"] == 0            # ← 省下来的就是这一趟
        assert r["surveyId"] == "s-9"          # 上一趟那个，不是这一轮落库的

    async def test_复用的结论必须写明是哪一趟(self, 记状态, 数爬取, 假落库, 假上一趟):
        """「已复用缓存」四个字等于没说 —— 看的人无从判断它是几天前的。

        出处必须和结论在**同一句话**里：拆成两个字段，页面上迟早只渲染前半句。
        """
        pid, eid = uuid.uuid4(), uuid.uuid4()
        假上一趟["prev"] = _prev(pid, eid)
        r = await _run(pid, eid)
        note = r["cacheNote"]
        assert "s-9" in note and "2026-08-28" in note and FP in note
        assert 记状态[-1]["message"] == note   # 页面上看到的就是这一句

    async def test_复用一趟partial不许渲染成爬全了(self, 记状态, 数爬取, 假落库, 假上一趟):
        """终态照抄被复用那一趟的。写死 `done` 的话，一趟少了几页的爬取
        复用之后跟整站爬完长得一模一样，缺的那些页面就此消失。"""
        pid, eid = uuid.uuid4(), uuid.uuid4()
        假上一趟["prev"] = _prev(pid, eid, status="partial")
        r = await _run(pid, eid)
        assert r["status"] == "partial" and 记状态[-1]["status"] == "partial"
        assert "partial" in 记状态[-1]["message"]

    async def test_复用那一轮不许给出任何一个没数过的数(self, 记状态, 数爬取, 假落库,
                                                        假上一趟):
        """这一轮没有去数控件。给个 `itemCount: 0` 就是把「没观测」渲染成
        「观测到 0」—— CLAUDE.md 里那条「新增字段在旧后端上渲染成假的 0」的自造版。
        让下游 KeyError，比让它画一个 0 好查。
        """
        pid, eid = uuid.uuid4(), uuid.uuid4()
        假上一趟["prev"] = _prev(pid, eid)
        r = await _run(pid, eid)
        assert "itemCount" not in r and "items" not in r and "ledger" not in r

    async def test_只重算也不用去爬(self, 记状态, 数爬取, 假落库, 假上一趟):
        """QA 仓 commit 变了 ⇒ 只重算 Q 侧。**AD-8 想省的就是这个。**"""
        pid, eid = uuid.uuid4(), uuid.uuid4()
        假上一趟["prev"] = _prev(pid, eid, sha="老的")
        r = await _run(pid, eid)
        assert r["action"] == "recompute" and r["recompute"] == ["qaCatalog"]
        assert 数爬取["calls"] == 0

    async def test_查上一趟得带着项目和环境去查(self, 记状态, 数爬取, 假落库, 假上一趟):
        """查错环境不是"少一次缓存命中"那个量级的错 —— 它爬的是别人的测试环境。"""
        pid, eid = uuid.uuid4(), uuid.uuid4()
        假上一趟["prev"] = _prev(pid, eid)
        await _run(pid, eid)
        assert 假上一趟["calls"] == [{"projectId": str(pid), "envId": str(eid),
                                      "qaCommitSha": "sha-1"}]


class Test该爬的时候一定去爬:
    async def test_指纹变了就真去爬(self, 记状态, 数爬取, 假落库, 假上一趟):
        pid, eid = uuid.uuid4(), uuid.uuid4()
        假上一趟["prev"] = _prev(pid, eid, fp="上一版")
        r = await _run(pid, eid)
        assert 数爬取["calls"] == 1
        assert r["provenance"]["source"] == "freshCrawl"
        assert r["surveyId"] == str(假落库["id"])

    async def test_重爬那一轮也带出处_只是空的(self, 记状态, 数爬取, 假落库, 假上一趟):
        """只在复用时才出现的出处，跟"没记过出处"长得一模一样。"""
        pid, eid = uuid.uuid4(), uuid.uuid4()
        假上一趟["prev"] = _prev(pid, eid, fp="上一版")
        r = await _run(pid, eid)
        assert set(r["provenance"]) == {"source", "surveyId", "crawledAt",
                                        "buildFingerprint", "surveyStatus"}
        assert r["provenance"]["surveyId"] == ""

    async def test_没有构建指纹就不去翻库(self, 记状态, 数爬取, 假落库, 假上一趟):
        """算不出身份的那一轮，`plan_reuse` 第一支就判重爬、`previous` 根本不看,
        所以那趟库可以不查。

        ⚠ 但**理由仍然得是 `plan_reuse` 说的那句**：任务层要是自己编一句
        「没指纹，重爬」，就出现了第二个判官 —— 两边口径哪天分叉，
        页面上会显示一个跟实际行为对不上的原因。
        """
        pid, eid = uuid.uuid4(), uuid.uuid4()
        r = await _run(pid, eid, build_fingerprint="")
        assert 假上一趟["calls"] == []
        assert 数爬取["calls"] == 1
        assert "身份" in r["cacheNote"]

    async def test_查不着上一趟_按重爬走但这句话得自己说(self, 记状态, 数爬取, 假落库,
                                                          假上一趟):
        """**这一条是这个文件里最要紧的。**

        查库炸了的时候把 `{}` 喂给 `plan_reuse`，它会答「没有上一趟：首次必须整站」
        —— 一句我们**并没有验证过**的话。上一趟很可能好端端在库里，只是这一下没读到。
        行为上两者都是重爬（对的方向），差别全在**页面上写的那句话**：
        一句是事实，另一句是我们编的。这正是这个模块存在的意义要抓的那类错。
        """
        pid, eid = uuid.uuid4(), uuid.uuid4()
        假上一趟["raise"] = RuntimeError("connection reset")
        r = await _run(pid, eid)
        assert 数爬取["calls"] == 1            # 拿不准 ⇒ 重爬，代价看得见
        assert "没能查到上一趟" in r["cacheNote"]
        assert "首次" not in r["cacheNote"]    # ← 绝不许借那句话
        assert r["provenance"]["source"] == "freshCrawl"

    async def test_查库炸了必须抛出来_不许在这一层吞成空(self, monkeypatch):
        """上一条成立的**前提**：`_previous` 自己不吞异常。

        吞了的话「库里确实没有上一趟」和「查这一下失败了」会长成同一个 `{}`，
        而 `plan_reuse` 拿到 `{}` 说的正是那句「没有上一趟：首次必须整站」——
        于是上一条测试照样绿，谎话却已经说出去了。
        （上面那条测的是任务层怎么处理这个异常，这条测的是异常真的到得了任务层。）
        """
        from app.deps import db as dbmod

        def boom(*a, **kw):
            raise RuntimeError("pool exhausted")

        monkeypatch.setattr(dbmod, "async_session_factory", boom)
        with pytest.raises(RuntimeError):
            await t._previous(project_id=str(uuid.uuid4()), env_id=None,
                              qa_commit_sha="sha-1")


# ── 活体计划这条缝：传得下去、崩了不连坐、没有就不猜 ────────────────────

def _plan(**over):
    plan = {"routeTable": {"available": True, "routes": [{"path": "/api/x"}]},
            "selectorProbe": {"/svc": ["[data-testid=a]"]},
            "selectorParsed": {"keys": [{"key": "a"}]},
            "envVars": {"AUDITOR_USERNAME": "qa-auditor",
                        "AUDITOR_PASSWORD": "s3cret"},
            "buildFingerprint": "", "declarations": ["计划声明一条"]}
    plan.update(over)
    return plan


@pytest.fixture
def 假对账(monkeypatch):
    box = {"raise": None, "rec": {"gaps": {"counters": {"total": 3}},
                                  "proposals": [], "applicability": [],
                                  "declarations": ["对账声明一条"]}}

    async def fake(**kw):
        if box["raise"] is not None:
            raise box["raise"]
        box["kw"] = kw
        return box["rec"]

    monkeypatch.setattr(t, "_reconcile", fake)
    return box


class Test活体计划:
    async def test_计划里的三样要真传到爬取那边(self, 记状态, 假爬取, 假落库, 假对账):
        """**这条盯的是一次改名事故。**

        任务层本来就有一个局部变量叫 `plan`（`plan_reuse` 算出来的"要不要复用"）。
        活体计划的参数也叫 `plan` 的话，后者会被前者盖掉 —— 于是路由表、选择器
        清单、环境变量三样全变成 `None`：选择器探测和三边对账**整片消失，而且
        一条错都不报**，页面上看着就像"这一趟没什么可探的"。
        """
        r = await t.run_page_survey({}, "task-1", str(uuid.uuid4()), ["auditor"],
                                   ["/svc"], plan=_plan())
        assert r["status"] == "done"
        kw = 假爬取["kw"]
        assert kw["routes"] == [{"path": "/api/x"}]
        assert kw["selector_probe"] == {"/svc": ["[data-testid=a]"]}
        assert kw["env_vars"]["AUDITOR_PASSWORD"] == "s3cret"

    async def test_没给计划就一格都不补(self, 记状态, 假爬取, 假落库):
        """不补 ≠ 补 0。

        今天之前的调用方压根没算过选择器和对账，账本里就**不该有**这两个键；
        垫一个空壳进去的话，「没算过」会渲染成「算过、是 0 个缺口」。
        """
        r = await t.run_page_survey({}, "task-1", str(uuid.uuid4()), ["admin"],
                                   ["/svc"])
        assert "selectorReport" not in r["ledger"]
        assert "reconcile" not in r["ledger"]

    async def test_对账崩了不算这一趟失败_但缺口数不许落0(self, 记状态, 假爬取,
                                                        假落库, 假对账):
        """爬取已经真的访问过别人的环境了，那份结果得留下。

        对账是纯计算、可以重算 —— 但重算之前那一格必须写成「没算」，
        不是写成 0。这两者在页面上长得一模一样，而含义正好相反。
        """
        假对账["raise"] = RuntimeError("git 仓读不到")
        r = await t.run_page_survey({}, "task-1", str(uuid.uuid4()), ["auditor"],
                                   ["/svc"], plan=_plan())
        assert r["status"] == "done"          # ← 不连坐
        rec = r["ledger"]["reconcile"]
        assert rec["available"] is False
        assert "git 仓读不到" in rec["reason"]
        assert any("不是 0" in d for d in rec["declarations"])
        assert "counters" not in rec

    async def test_对账跑成了就带上开关(self, 记状态, 假爬取, 假落库, 假对账):
        r = await t.run_page_survey({}, "task-1", str(uuid.uuid4()), ["auditor"],
                                   ["/svc"], plan=_plan())
        rec = r["ledger"]["reconcile"]
        assert rec["available"] is True
        assert rec["gaps"]["counters"]["total"] == 3
        assert "对账声明一条" in rec["declarations"]

    async def test_选择器没解析出来就没有报告(self, 记状态, 假爬取, 假落库, 假对账):
        """`selectors.ts` 找不到/解析不出来的那一趟，不许出一份"0 条选择器"的报告。"""
        r = await t.run_page_survey({}, "task-1", str(uuid.uuid4()), ["auditor"],
                                   ["/svc"], plan=_plan(selectorParsed=None))
        assert "selectorReport" not in r["ledger"]
        assert r["ledger"]["reconcile"]["available"] is True

    async def test_复用那一支不受影响(self, 记状态, 假爬取, 假落库, 假对账,
                                     monkeypatch):
        """判"要不要复用"的仍然只有 `plan_reuse`，活体计划不插手这个判断。"""
        from app.services import qa_survey_cache as sc

        monkeypatch.setattr(sc, "plan_reuse", lambda **kw: {
            "action": "reuse", "recompute": [], "reasons": ["测试"],
            "summary": "复用上一趟",
            "provenance": {"source": "reuse", "surveyId": "s-1",
                           "surveyStatus": "partial"}})
        monkeypatch.setattr(t, "_previous", lambda **kw: _noop())
        r = await t.run_page_survey({}, "task-1", str(uuid.uuid4()), ["auditor"],
                                   ["/svc"], env_id=str(uuid.uuid4()),
                                   build_fingerprint="bf-1", plan=_plan())
        assert r["status"] == "partial"
        assert 假爬取["kw"] is None            # 没去爬


async def _noop():
    return {}


class Test凭证边界:
    def test_活体计划不许走_arq_enqueue(self):
        """**计划里带着完整可用的账号密码，只能在进程内传。**

        arq 的 job 参数是要 pickle 进 redis 的 —— 那等于把一份可用凭证落在一个
        没人想到要去清的地方（还带 TTL，过期前谁都能读）。所以这条路只能是
        `qa_catalog_review.spawn`（= `asyncio.create_task`）。

        扫的是"`enqueue_job(` 后面 200 个字符里出不出现 `run_page_survey`"，
        不是"这个文件提不提 enqueue" —— `worker.py` 的注释里就有那个词，
        按词扫会把一句正确的注释判成违规（那种测试第一次红就会被人删掉）。
        """
        import pathlib
        import re

        root = pathlib.Path(t.__file__).resolve().parents[2]   # backend/app
        bad = []
        for f in sorted(root.rglob("*.py")):
            src = f.read_text(encoding="utf-8")
            if "run_page_survey" not in src:
                continue
            for m in re.finditer(r"enqueue_job\(", src):
                if "run_page_survey" in src[m.start():m.start() + 200]:
                    bad.append(str(f.relative_to(root.parent)))
        assert bad == [], f"这些地方把活体计划塞进了 redis：{bad}"

    def test_plan_只能按关键字传(self):
        """位置传参的话，调用处看不出这一份是"带凭证的"，加个参数就错位。"""
        import inspect

        kind = inspect.signature(t.run_page_survey).parameters["plan"].kind
        assert kind is inspect.Parameter.KEYWORD_ONLY
