"""CC 反馈通道 —— 收→并→分诊→回音 整条链，打真接口。

和 backend/tests/test_cc_feedback_gates.py 分工明确：那边测**纯判据**
（校验、指纹、状态机不变量），这边测**跨请求的行为** —— 归并、短路、复发、
回音取走。后者只有在真库上才成立：它们全都依赖「上一条留下的行还在」，
而那正是最容易在重构里被改坏、又最难被发现的部分 —— 改坏的表现不是报错，
是**多出一行**，而没人会去数行数。
"""
import pytest

from tests.conftest import create_test_user, make_auth_headers

BODY = ("调 lum_get_case 想读回 bugRefs，返回里没有这个字段。"
        "工具描述写的是「读一条用例的全部内容」，实际只有十来个字段。")


@pytest.fixture(autouse=True)
def _no_auto_triage(monkeypatch):
    """**把新反馈进来时的自动分诊关掉。**

    线上默认是开的（人是来看结果的，不是来点每一条的），但在测试里它会：
    ① 真打模型 —— 在没有网关的机器上变成偶发红，而且慢；
    ② 用一个**独立 session** 改同一行 —— 于是这一整个文件里「报完立刻查状态」
       的断言全都变成竞态：查早了是 new，查晚了已经被 AI 判过了。
    那种红看起来像归并/短路逻辑坏了，而实际一行代码都没错。
    """
    from app.services import cc_feedback_service as svc
    monkeypatch.setattr(svc, "AUTO_TRIAGE", False)


def _fake_model(monkeypatch, content, model="claude-opus-5"):
    """把 AI 那两个依赖换成假的。**打 patch 的位置是定义处的模块**，
    不是 cc_feedback_service —— 它在函数体里 import，所以属性查找发生在调用时。"""
    class _Cfg:
        pass

    _Cfg.model = model

    class _Resp:
        pass

    _Resp.content = content

    async def _fake_cfg(*a, **kw):
        return _Cfg()

    async def _fake_complete(*a, **kw):
        return _Resp()

    monkeypatch.setattr("app.services.ai_config_resolver.resolve_ai_config", _fake_cfg)
    monkeypatch.setattr("app.services.ai.llm_client.complete", _fake_complete)


async def _admin(db_session, username="fb_admin"):
    u = await create_test_user(db_session, username=username, role="admin")
    headers, _ = make_auth_headers(u)
    return headers, u.username


async def _report(client, headers, *, title, category="improvement",
                  tool="lum_get_case", body=BODY, evidence=None):
    return await client.post("/api/cc-feedback", headers=headers, json={
        "title": title, "body": body, "category": category,
        "toolName": tool, "evidence": evidence,
    })


async def _first(client, headers, title="读不回 bugRefs", **kw):
    r = await _report(client, headers, title=title, **kw)
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def _triage(client, headers, fid, **kw):
    return await client.post(f"/api/cc-feedback/{fid}/triage", headers=headers, json=kw)


# ── 收 ────────────────────────────────────────────────────────────

class TestReport:
    async def test_报一条能读回来(self, client, db_session):
        h, _ = await _admin(db_session)
        d = await _first(client, h)
        got = (await client.get(f"/api/cc-feedback/{d['id']}", headers=h)).json()["data"]
        assert got["status"] == "new"
        assert got["reportedCategory"] == "improvement"
        # 平台还没判过类 —— 这两列分开存，「CC 判错了」才有形状
        assert got["category"] is None
        assert got["categoryMismatch"] is False
        assert got["occurrences"] == 1
        assert got["body"].startswith("调 lum_get_case")

    async def test_闸门在接口层也生效(self, client, db_session):
        """页面和 MCP 走的是同一个 report()。这里确认 HTTP 这条路没绕过去 ——
        绕过去的话，页面录入就成了一个「不用讲证据」的后门，
        而后门一旦存在，赶时间的人都会走后门。"""
        h, _ = await _admin(db_session)
        r = await _report(client, h, title="太短", body="这个工具不好用")
        assert r.status_code == 400
        assert "40" in r.text                      # 说清下限是多少，不是只说"太短"

        r = await _report(client, h, title="缺证据的缺陷", category="bug")
        assert r.status_code == 400
        assert "expected" in r.text                # 且要说清缺哪一半

        r = await _report(client, h, title="类别写错", category="feature")
        assert r.status_code == 400

    async def test_bug带齐证据能进(self, client, db_session):
        h, _ = await _admin(db_session)
        d = await _first(client, h, title="读不回 bugRefs", category="bug",
                         evidence={"expected": "描述说读全部内容",
                                   "actual": "返回里没有 bugRefs"})
        got = (await client.get(f"/api/cc-feedback/{d['id']}", headers=h)).json()["data"]
        assert got["evidence"]["actual"] == "返回里没有 bugRefs"

    async def test_普通用户进不来(self, client, db_session):
        """这一页是平台维护面（和「服务监控」「操作日志」同档）。
        真正把门的是路由守卫，不是前端藏菜单 —— 藏菜单挡不住直接打接口。"""
        u = await create_test_user(db_session, username="fb_member", role="user")
        h, _ = make_auth_headers(u)
        assert (await client.get("/api/cc-feedback", headers=h)).status_code == 403
        assert (await _report(client, h, title="越权报一条")).status_code == 403


# ── 并 ────────────────────────────────────────────────────────────

class TestMerge:
    async def test_同指纹并进去而不是新建(self, client, db_session):
        h, _ = await _admin(db_session)
        a = await _first(client, h)
        # 正文换一套说法 —— 指纹不含正文，照样得并上
        r = await _report(client, h, title="读不回 bugRefs",
                          body="又撞了一次：想确认 bugRefs 到底写进去没有。" + BODY)
        d = r.json()["data"]
        assert d.get("merged") is True
        assert d["id"] == a["id"]
        assert d["occurrences"] == 2

        items = (await client.get("/api/cc-feedback?pendingOnly=true",
                                  headers=h)).json()["data"]["items"]
        assert len(items) == 1                      # 不是两行

    async def test_标题只差标点大小写也并(self, client, db_session):
        h, _ = await _admin(db_session)
        a = await _first(client, h)
        r = await _report(client, h, title="  读不回 BugRefs。  ")
        assert r.json()["data"]["id"] == a["id"]

    async def test_不同工具的同名毛病不并(self, client, db_session):
        """「静默失败」这种标题在好几个工具上都成立，只按标题并会糊成一条。"""
        h, _ = await _admin(db_session)
        a = await _first(client, h, title="静默失败")
        r = await _report(client, h, title="静默失败", tool="lum_update_case")
        assert r.json()["data"]["id"] != a["id"]

    async def test_归并之后正文保留第一次那份(self, client, db_session):
        """第一次写得最完整（人还记得现场）。让后来的覆盖它，等于每撞一次
        证据就退化一点，最后剩下一句「又来了」。"""
        h, _ = await _admin(db_session)
        a = await _first(client, h)
        await _report(client, h, title="读不回 bugRefs", body="又来了，" + BODY)
        got = (await client.get(f"/api/cc-feedback/{a['id']}", headers=h)).json()["data"]
        assert got["body"].startswith("调 lum_get_case")


# ── 分诊必须留下回音 ──────────────────────────────────────────────

class TestTriageGates:
    async def test_处理完了不写回音会被拒(self, client, db_session):
        """用户定的规矩：「就回复他原因」。落成硬校验，否则赶时间时第一个被
        跳过的就是回音 —— 而这条通道的全部价值就在回音上。"""
        h, _ = await _admin(db_session)
        d = await _first(client, h)
        r = await _triage(client, h, d["id"], status="done", category="improvement")
        assert r.status_code == 400
        r = await _triage(client, h, d["id"], status="wont_fix", category="improvement")
        assert r.status_code == 400
        # 拒绝里要写清「不做的理由怎么写」，不能只报字段名
        assert "正确方法" in r.text or "写出来" in r.text

    async def test_不需要处理写了理由就能落(self, client, db_session):
        h, _ = await _admin(db_session)
        d = await _first(client, h)
        r = await _triage(client, h, d["id"], status="wont_fix", category="improvement",
                          resolution="平台已经有：lum_check_deliverable 的 notes 会带出来。")
        assert r.status_code == 200, r.text
        assert r.json()["data"]["status"] == "wont_fix"

    async def test_认下必须定类(self, client, db_session):
        h, _ = await _admin(db_session)
        d = await _first(client, h)
        assert (await _triage(client, h, d["id"], status="triaged")).status_code == 400
        r = await _triage(client, h, d["id"], status="triaged", category="bug")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["category"] == "bug"
        # CC 报的是 improvement、平台判成 bug —— 这个落差要留得住，
        # 它是「他判断错了/没找对方法」唯一可统计的形状
        assert data["categoryMismatch"] is True

    async def test_标为重复必须说并到哪一条(self, client, db_session):
        h, _ = await _admin(db_session)
        a = await _first(client, h, title="甲问题")
        b = await _first(client, h, title="乙问题")
        assert (await _triage(client, h, b["id"], status="duplicate")).status_code == 400
        r = await _triage(client, h, b["id"], status="duplicate", duplicateOf=a["id"])
        assert r.status_code == 200
        assert r.json()["data"]["duplicateOf"] == a["id"]

    async def test_处理完就不在待处理里了(self, client, db_session):
        h, _ = await _admin(db_session)
        d = await _first(client, h)
        await _triage(client, h, d["id"], status="done", category="bug",
                      resolution="已补上字段，重启后端后重新调一次就能看到。")
        body = (await client.get("/api/cc-feedback?pendingOnly=true",
                                 headers=h)).json()["data"]
        assert body["items"] == []
        assert body["summary"]["pending"] == 0
        assert body["summary"]["byStatus"]["done"] == 1


# ── 短路：不需要处理必须挡得住第二次 ──────────────────────────────

class TestShortCircuit:
    async def test_不需要处理会短路同指纹的再次上报(self, client, db_session):
        """整条通道最要紧的一个行为。挡不住的话，「回复原因」只是一句客套，
        下一轮 CC 照原样再报一遍 —— 人肉转述那套循环原封不动地回来了。"""
        h, _ = await _admin(db_session)
        d = await _first(client, h)
        await _triage(client, h, d["id"], status="wont_fix", category="improvement",
                      resolution="平台已经有：lum_check_deliverable 的 notes 会带出来。")

        got = (await _report(client, h, title="读不回 bugRefs")).json()["data"]
        assert got["alreadyDecided"] == "wont_fix"
        assert got["id"] == d["id"]
        # 当场把上次的理由甩回去 —— 只说「已判过」等于没回音
        assert "lum_check_deliverable" in got["resolution"]
        # 而且不新建行
        assert (await client.get("/api/cc-feedback?keyword=bugRefs",
                                 headers=h)).json()["data"]["total"] == 1

    async def test_短路也要留出翻案的路(self, client, db_session):
        """判错了却没法再提，就是把「不需要处理」变成了删除键。
        换个标题 = 换个指纹，重新走一遍正门。"""
        h, _ = await _admin(db_session)
        d = await _first(client, h)
        await _triage(client, h, d["id"], status="wont_fix", category="improvement",
                      resolution="不做，理由 A。")
        got = (await _report(client, h, title="读不回 bugRefs（和上次不同：批量场景下）",
                             body="上次判的是单条读取，这次说的是批量：" + BODY)).json()["data"]
        assert got["id"] != d["id"]
        assert "alreadyDecided" not in got

    async def test_修好了又复现是新开一条不是并回老账(self, client, db_session):
        """并进老账会把「中间好过」埋掉 —— 而修过又回来的问题和一直没修的问题，
        处理方式完全不同。"""
        h, _ = await _admin(db_session)
        d = await _first(client, h)
        await _triage(client, h, d["id"], status="done", category="bug",
                      resolution="已补上 bugRefs 字段，重启后端后重新调一次就能看到。")

        got = (await _report(client, h, title="读不回 bugRefs")).json()["data"]
        assert got["id"] != d["id"]
        assert got["reopenedFrom"] == d["id"]

    async def test_复发之后老的那条不再挡新的(self, client, db_session):
        """短路只认**最近一条**同指纹。认最早那条的话，一条早年的 wont_fix
        会把这个指纹永久钉死，之后所有上报都石沉大海。"""
        h, _ = await _admin(db_session)
        d = await _first(client, h)
        await _triage(client, h, d["id"], status="done", category="bug",
                      resolution="已修，重启后端后重新调一次。")
        again = (await _report(client, h, title="读不回 bugRefs")).json()["data"]
        # 复发那条还开着 → 第三次上报应并进复发那条，而不是被老的 done 又开一条
        third = (await _report(client, h, title="读不回 bugRefs")).json()["data"]
        assert third["id"] == again["id"]
        assert third["occurrences"] == 2


# ── 回音取走 ──────────────────────────────────────────────────────

class TestEcho:
    async def test_有结论才算回音(self, client, db_session):
        """没结论的行不该出现在 CC 的待办里 —— 一个「我们看到了」的通知
        对它没有任何可做的动作。"""
        from app.services import cc_feedback_service as svc
        h, who = await _admin(db_session)
        d = await _first(client, h)
        assert await svc.unread_echoes(db_session, reporter=who) == []

        await _triage(client, h, d["id"], status="done", category="bug",
                      resolution="已补上字段。")
        echoes = await svc.unread_echoes(db_session, reporter=who)
        assert [e["id"] for e in echoes] == [d["id"]]
        # done 的回音必须带「验之前先重启」—— 不带的话 CC 在旧进程上验，
        # 会得出「你没修」的结论，然后重报一遍
        assert "restart-backend" in echoes[0]["beforeYouVerify"]

    async def test_读过就不再挂在待办上(self, client, db_session):
        """取不走的话，next_duty 会一直挂着同一条，几轮之后 CC 就学会无视它 ——
        一个永远不消的待办等于没有待办。"""
        from app.services import cc_feedback_service as svc
        h, who = await _admin(db_session)
        d = await _first(client, h)
        await _triage(client, h, d["id"], status="wont_fix", category="improvement",
                      resolution="平台已经有，用 lum_check_deliverable。")

        first = await svc.list_mine(db_session, reporter=who)
        assert first["markedRead"] == 1
        assert await svc.unread_echoes(db_session, reporter=who) == []
        assert (await svc.list_mine(db_session, reporter=who))["markedRead"] == 0

    async def test_改了结论会重新变成未读(self, client, db_session):
        """后来又改主意了要能再送到 CC 手上 —— 送不出去，这条通道就是单向的。"""
        from app.services import cc_feedback_service as svc
        h, who = await _admin(db_session)
        d = await _first(client, h)
        await _triage(client, h, d["id"], status="wont_fix", category="improvement",
                      resolution="不做，理由 A。")
        await svc.list_mine(db_session, reporter=who)          # 读走
        assert await svc.unread_echoes(db_session, reporter=who) == []

        await _triage(client, h, d["id"], status="done", category="improvement",
                      resolution="改主意了，已经做了。")
        again = await svc.unread_echoes(db_session, reporter=who)
        assert len(again) == 1 and "改主意" in again[0]["resolution"]

    async def test_判的类和报的类不一样要在回音里说(self, client, db_session):
        """光改一列没用 —— CC 看不到「你判错了」，下次照旧那么报。"""
        from app.services import cc_feedback_service as svc
        h, who = await _admin(db_session)
        d = await _first(client, h)          # 报的是 improvement
        await _triage(client, h, d["id"], status="done", category="bug",
                      resolution="确实是缺陷，已修。")
        echo = (await svc.unread_echoes(db_session, reporter=who))[0]
        assert "categoryChanged" in echo
        assert "缺陷" in echo["categoryChanged"]


# ── AI 处置：判得了的自己落，判不了的才给人 ─────────────────────

class TestAIHandle:
    """2026-09-01 口径反转：原来 AI「只写建议不改状态」，现在**直接落裁定**。

    反转的前提是把不可逆性拆掉，而不是把守卫拆掉 —— 原来那道人工闸的理由是
    「wont_fix 会永久短路同指纹的后续上报」，现在 AI 判的 wont_fix 可以被带新
    证据的重报翻案（转人），人判的才终局。这一节就是在验那个前提真的成立：
    只要「AI 能自己落」而「翻不了案」，一类反馈就会安静地消失且不报错。
    """

    async def test_ai直接把裁定落下来(self, client, db_session, monkeypatch):
        h, _ = await _admin(db_session)
        d = await _first(client, h)
        _fake_model(monkeypatch,
                    '{"verdict":"wont_fix","category":"improvement","severity":"low",'
                    '"resolution":"平台已经有：lum_check_deliverable 的 notes 会带出来。",'
                    '"reasoning":"他没找对方法","risk":"x"}')

        r = await client.post(f"/api/cc-feedback/{d['id']}/analyze", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["verdict"] == "wont_fix"

        got = (await client.get(f"/api/cc-feedback/{d['id']}", headers=h)).json()["data"]
        assert got["status"] == "wont_fix"          # 状态真的动了
        assert got["category"] == "improvement"
        assert got["decidedBy"] == "ai"             # 谁判的要留住 —— 它决定还能不能翻案
        assert "lum_check_deliverable" in got["resolution"]
        assert got["needsHuman"] is None
        assert got["handledBy"].startswith("AI")

    async def test_判不了的落到等人拍板而不是硬判一个(self, client, db_session, monkeypatch):
        """猜的代价不对称：猜错一个 wont_fix 会挡掉后续上报（一类反馈就此消失、
        而且不报错），转给人只是多等一会儿。所以任何拿不准都往人那边倒。"""
        h, _ = await _admin(db_session)
        d = await _first(client, h)
        _fake_model(monkeypatch,
                    '{"verdict":"needs_human","needsHuman":"这条要产品定：到底该不该有这个字段",'
                    '"reasoning":"需求没写"}')

        r = await client.post(f"/api/cc-feedback/{d['id']}/analyze", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["verdict"] == "needs_human"

        got = (await client.get(f"/api/cc-feedback/{d['id']}", headers=h)).json()["data"]
        assert got["status"] == "new"               # 没落裁定 → 状态不动
        assert got["decidedBy"] is None             # 也不算「AI 判的」
        assert "产品定" in got["needsHuman"]

    async def test_乱输出一律降级成等人拍板(self, client, db_session, monkeypatch):
        """模型不按格式回是常态，不是异常。降级成 needs_human 而不是抛错：
        抛错的话这条会静静地留在 new 上，谁也不知道它卡在哪儿。"""
        h, _ = await _admin(db_session)
        d = await _first(client, h)
        _fake_model(monkeypatch, "我觉得这条挺重要的，建议尽快处理。")

        r = await client.post(f"/api/cc-feedback/{d['id']}/analyze", headers=h)
        assert r.json()["data"]["verdict"] == "needs_human"
        got = (await client.get(f"/api/cc-feedback/{d['id']}", headers=h)).json()["data"]
        assert got["status"] == "new"
        assert got["aiAnalysis"]["parseFailed"] is True   # 原文留着，人能看它到底说了啥

    async def test_判不做却写不出理由不放过(self, client, db_session, monkeypatch):
        """wont_fix 的全部价值在回音上。判了「不做」又说不出正确做法 = 没判明白，
        放过去就是让 CC 下一轮照原样再撞一次，而平台这边只会静默 +1。"""
        h, _ = await _admin(db_session)
        d = await _first(client, h)
        _fake_model(monkeypatch,
                    '{"verdict":"wont_fix","category":"improvement","reasoning":"没必要"}')

        r = await client.post(f"/api/cc-feedback/{d['id']}/analyze", headers=h)
        assert r.json()["data"]["verdict"] == "needs_human"
        got = (await client.get(f"/api/cc-feedback/{d['id']}", headers=h)).json()["data"]
        assert got["status"] == "new"
        assert "回音" in got["needsHuman"]

    async def test_ai给done按认下落(self, client, db_session, monkeypatch):
        """done 的含义是**代码改完了**，AI 没改过代码。但它判「该改」是有用的，
        所以折到 triaged，而不是丢掉或者转人。"""
        h, _ = await _admin(db_session)
        d = await _first(client, h)
        _fake_model(monkeypatch,
                    '{"verdict":"done","category":"bug","severity":"high",'
                    '"resolution":"已补上字段","reasoning":"确实少了"}')

        r = await client.post(f"/api/cc-feedback/{d['id']}/analyze", headers=h)
        data = r.json()["data"]
        assert data["verdict"] == "triaged"
        assert "coercedFromDone" in data["aiAnalysis"]
        got = (await client.get(f"/api/cc-feedback/{d['id']}", headers=h)).json()["data"]
        assert got["status"] == "triaged"

    async def test_模型回空不落库(self, client, db_session, monkeypatch):
        """空回复必须报错，不能存成一次「分析完了」。

        存了就是页面上一块**空的** AI 分析 —— 看着像模型读过觉得没啥可说，
        实际是一个字都没回来；而且它还会把上一次真有内容的分析覆盖掉。
        这不是假想：主路 429 降级到 CLI 通道（那头是 Claude Code）时，
        反馈正文本身长得像一件待办，它会去做事而不作答，回来就是空的。

        这种情况**也不写 needs_human** —— 模型没说过话，不能替它说「它判不了」，
        那会把一次限流记成一次判不动，然后这条被扔到人手上白等。
        """
        h, _ = await _admin(db_session, username="fb_admin_empty")
        d = await _first(client, h, title="模型回空的那条")
        _fake_model(monkeypatch, "   ")

        r = await client.post(f"/api/cc-feedback/{d['id']}/analyze", headers=h)
        assert r.status_code == 400, r.text
        err = r.json()["error"]
        assert "没有返回内容" in err["message"]
        assert "重试" in (err.get("detail") or "")   # 拒绝要带出路，不能只说"失败"

        got = (await client.get(f"/api/cc-feedback/{d['id']}", headers=h)).json()["data"]
        assert got["aiAnalysis"] is None       # 没留下空壳
        assert got["status"] == "new"
        assert got["needsHuman"] is None


# ── AI 判的不需要处理：必须翻得动 ─────────────────────────────────

class TestReopenAIWontFix:
    """「AI 能自己落 wont_fix」这件事只有在能翻案的前提下才成立。

    翻案的判据是**有新东西**，不是「又说了一遍」—— 否则复读就能推翻裁定，
    那道短路等于没有；而人判的必须终局，否则永远翻下去，wont_fix 就没有意义了。
    """

    async def _ai_wont_fix(self, client, h, monkeypatch, title="读不回 bugRefs"):
        d = await _first(client, h, title=title)
        _fake_model(monkeypatch,
                    '{"verdict":"wont_fix","category":"improvement","severity":"low",'
                    '"resolution":"用 lum_check_deliverable 的 notes。","reasoning":"r"}')
        await client.post(f"/api/cc-feedback/{d['id']}/analyze", headers=h)
        return d

    async def test_带新说法重报会转给人(self, client, db_session, monkeypatch):
        h, _ = await _admin(db_session)
        d = await self._ai_wont_fix(client, h, monkeypatch)

        got = (await _report(client, h, title="读不回 bugRefs",
                             body="上次说去用 lum_check_deliverable，但它只在交付门禁里出现，"
                                  "我要的是读用例时就能看到。" + BODY)).json()["data"]
        assert got["id"] == d["id"]                 # 不新建行
        assert got["escalated"] == "needs_human"
        assert "lum_check_deliverable" in got["resolution"]   # 上次的理由照旧甩回去

        row = (await client.get(f"/api/cc-feedback/{d['id']}", headers=h)).json()["data"]
        assert row["needsHuman"]
        # 页面上「等人拍板」是跨状态筛的 —— 这条还挂在 wont_fix 上，按状态筛会漏掉
        assert row["status"] == "wont_fix"
        items = (await client.get("/api/cc-feedback?awaitingHuman=true",
                                  headers=h)).json()["data"]["items"]
        assert [i["id"] for i in items] == [d["id"]]

    async def test_照原样复读不算新证据(self, client, db_session, monkeypatch):
        h, _ = await _admin(db_session)
        d = await self._ai_wont_fix(client, h, monkeypatch)

        got = (await _report(client, h, title="读不回 bugRefs")).json()["data"]  # 正文一字不改
        assert got.get("escalated") is None
        assert got["decidedBy"] == "ai"
        assert "跟上次不同" in got["note"]           # 要告诉它翻案的门槛是什么
        row = (await client.get(f"/api/cc-feedback/{d['id']}", headers=h)).json()["data"]
        assert row["needsHuman"] is None

    async def test_人判的不需要处理是终局(self, client, db_session):
        """人拍过板的不再被翻。翻得动的话，wont_fix 就成了一个可以无限重开的
        建议框 —— 人这道兜底也就白设了。"""
        h, _ = await _admin(db_session)
        d = await _first(client, h)
        await _triage(client, h, d["id"], status="wont_fix", category="improvement",
                      resolution="不做，理由 A。")

        got = (await _report(client, h, title="读不回 bugRefs",
                             body="换个说法再报一次，情况完全不同：" + BODY)).json()["data"]
        assert got.get("escalated") is None
        assert got["decidedBy"] == "human"
        row = (await client.get(f"/api/cc-feedback/{d['id']}", headers=h)).json()["data"]
        assert row["needsHuman"] is None

    async def test_人拍板之后就不再欠人什么了(self, client, db_session, monkeypatch):
        """人处理完，「等人拍板」那一撮必须清干净 —— 清不掉就是一个永远不消的
        待办，几轮之后没人会再看那个筛选。"""
        h, _ = await _admin(db_session)
        d = await self._ai_wont_fix(client, h, monkeypatch)
        await _report(client, h, title="读不回 bugRefs",
                      body="新说法：" + BODY)                     # 转人

        r = await _triage(client, h, d["id"], status="triaged", category="improvement")
        assert r.status_code == 200, r.text
        assert r.json()["data"]["decidedBy"] == "human"
        assert (await client.get("/api/cc-feedback?awaitingHuman=true",
                                 headers=h)).json()["data"]["total"] == 0


# ── 批量：人不该一条条点 ──────────────────────────────────────────

class TestBatch:
    async def test_批量把待处理的一次推完(self, client, db_session, monkeypatch):
        h, _ = await _admin(db_session)
        a = await _first(client, h, title="甲问题")
        b = await _first(client, h, title="乙问题")
        _fake_model(monkeypatch,
                    '{"verdict":"triaged","category":"bug","severity":"medium",'
                    '"reasoning":"r","fixHint":"h"}')

        r = await client.post("/api/cc-feedback/ai-handle", headers=h, json={})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["accepted"] == 2

        # 后台顺序跑 —— 等它跑完（一次假模型调用是即时的，给几个 tick 就够）
        for _ in range(50):
            st = (await client.get("/api/cc-feedback/batch-status",
                                   headers=h)).json()["data"]
            if not st["running"] and st["startedAt"]:
                break
            import asyncio
            await asyncio.sleep(0.05)
        assert st["running"] is False
        assert st["done"] == 2 and st["failed"] == 0

        for fid in (a["id"], b["id"]):
            got = (await client.get(f"/api/cc-feedback/{fid}", headers=h)).json()["data"]
            assert got["status"] == "triaged" and got["decidedBy"] == "ai"

    async def test_进度查得到而且不会被当成id(self, client, db_session):
        """`/batch-status` 必须声明在 `/{feedback_id}` 前面 —— 否则它会被那条
        动态路由吃掉，然后炸在 uuid 解析上（500，而且报的是「反馈不存在」那类
        看不出真因的错）。"""
        h, _ = await _admin(db_session)
        r = await client.get("/api/cc-feedback/batch-status", headers=h)
        assert r.status_code == 200, r.text
        assert set(("running", "total", "done", "failed")) <= set(r.json()["data"])

    async def test_没东西可判就直说(self, client, db_session):
        """空批次静默成功是最坏的一种：页面上什么都不动，人只会再点一次。"""
        h, _ = await _admin(db_session)
        r = await client.post("/api/cc-feedback/ai-handle", headers=h, json={})
        assert r.status_code == 400
        assert "没有要处理" in r.text

    async def test_勾选的那几条才判(self, client, db_session, monkeypatch):
        h, _ = await _admin(db_session)
        a = await _first(client, h, title="甲问题")
        b = await _first(client, h, title="乙问题")
        _fake_model(monkeypatch,
                    '{"verdict":"triaged","category":"bug","severity":"low","reasoning":"r"}')

        r = await client.post("/api/cc-feedback/ai-handle", headers=h,
                              json={"ids": [a["id"]]})
        assert r.json()["data"]["accepted"] == 1
        for _ in range(50):
            st = (await client.get("/api/cc-feedback/batch-status",
                                   headers=h)).json()["data"]
            if not st["running"] and st["startedAt"]:
                break
            import asyncio
            await asyncio.sleep(0.05)
        got = (await client.get(f"/api/cc-feedback/{b['id']}", headers=h)).json()["data"]
        assert got["status"] == "new"          # 没勾的那条一个字没动


# ── 范围：默认落域 / 筛 / 计数 / 不影响归并 ────────────────────────

class TestArea:
    async def test_上报按工具名落默认域(self, client, db_session):
        """CC 不填也得有域，否则这一列在真实数据上永远是空的 ——
        它上报时手里只有工具名，那就是唯一能不猜就用的判据。"""
        h, _ = await _admin(db_session)
        d = await _first(client, h)                       # tool=lum_get_case
        assert d["area"] == "case"
        got = (await client.get(f"/api/cc-feedback/{d['id']}", headers=h)).json()["data"]
        assert got["area"] == "case"
        assert got["areaLabel"] == "用例读写"

    async def test_自由文本工具名留空不猜(self, client, db_session):
        """库里三成 tool_name 是自由文本（「AI 评审规则文案」这类）。
        猜错的那半没有任何地方会报错，所以这一层宁可留空，交给后面两层。"""
        h, _ = await _admin(db_session)
        d = await _first(client, h, title="规则文案有歧义", tool="AI 评审规则文案")
        assert d["area"] is None

    async def test_CC填的域优先于工具名默认(self, client, db_session):
        h, _ = await _admin(db_session)
        r = await client.post("/api/cc-feedback", headers=h, json={
            "title": "评审判据自相矛盾", "body": BODY, "category": "improvement",
            "toolName": "lum_get_case", "area": "ai_review",
        })
        assert r.status_code == 200, r.text
        assert r.json()["data"]["area"] == "ai_review"

    async def test_域填错不退回反馈只是告诉他(self, client, db_session):
        """一条写齐了证据的反馈，不该因为域名打错一个字就被拒 ——
        拒了他得重写一遍，下次就干脆不填了。所以退化成「忽略 + 说一句」。"""
        h, _ = await _admin(db_session)
        r = await client.post("/api/cc-feedback", headers=h, json={
            "title": "某个毛病", "body": BODY, "category": "improvement",
            "toolName": "lum_get_case", "area": "评审",
        })
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["areaIgnored"] == "评审"
        assert d["area"] == "case"                        # 退回按工具名落的那个
        assert "ai_review" in (d.get("note") or "")       # 可用值摆出来

    async def test_人能改域而且非法值当场拒(self, client, db_session):
        """页面上这一格是下拉框，出现非法值只可能是脚本在调 ——
        悄悄修好等于让那个脚本一直错着。"""
        h, _ = await _admin(db_session)
        d = await _first(client, h)
        ok = await _triage(client, h, d["id"], status="triaged",
                           category="bug", area="ai_review")
        assert ok.status_code == 200, ok.text
        assert ok.json()["data"]["area"] == "ai_review"
        bad = await _triage(client, h, d["id"], status="triaged",
                            category="bug", area="不存在的域")
        assert bad.status_code == 400

    async def test_改了域还是同一行(self, client, db_session):
        """域不进指纹。进了的话同一件事改完域会分裂成两行，
        wont_fix 的短路也跟着失效 —— 表现是「反馈变多了」，看着很正常。"""
        h, _ = await _admin(db_session)
        d = await _first(client, h)
        await _triage(client, h, d["id"], status="triaged",
                      category="bug", area="ai_review")
        again = await _first(client, h)                   # 同工具 + 同标题
        assert again["id"] == d["id"]
        assert again["occurrences"] == 2
        assert again["area"] == "ai_review"               # 判过的不被上报的默认值盖掉

    async def test_按域筛和计数(self, client, db_session):
        h, _ = await _admin(db_session)
        await _first(client, h, title="甲问题", tool="lum_get_case")
        await _first(client, h, title="乙问题", tool="lum_run_api_test")
        await _first(client, h, title="丙问题", tool="执行报告说不清")
        r = await client.get("/api/cc-feedback?area=case", headers=h)
        assert r.status_code == 200, r.text
        assert [x["title"] for x in r.json()["data"]["items"]] == ["甲问题"]
        by = r.json()["data"]["summary"]["byArea"]
        assert by.get("case") == 1 and by.get("api_run") == 1 and by.get("__none__") == 1, by

    async def test_待判域筛得出来(self, client, db_session):
        """NULL 不是 other。没有这个筛法，「该我填的」那批就没法一次捞出来。"""
        h, _ = await _admin(db_session)
        await _first(client, h, title="甲问题", tool="lum_get_case")
        await _first(client, h, title="丙问题", tool="执行报告说不清")
        r = await client.get("/api/cc-feedback?area=__none__", headers=h)
        assert r.status_code == 200, r.text
        assert [x["title"] for x in r.json()["data"]["items"]] == ["丙问题"]

    async def test_AI判域只填空的(self, client, db_session, monkeypatch):
        """AI 只填空白。盖掉人判过的域，人就没法用这一列做长期归类了 ——
        改一次被 AI 改回去一次，而且不报错。"""
        h, _ = await _admin(db_session)
        blank = await _first(client, h, title="丙问题", tool="执行报告说不清")
        assert blank["area"] is None
        judged = await _first(client, h, title="丁问题", tool="覆盖统计不对")
        await _triage(client, h, judged["id"], status="triaged",
                      category="bug", area="ai_review")
        _fake_model(monkeypatch,
                    '{"verdict":"triaged","category":"bug","severity":"low",'
                    '"area":"report","reasoning":"报告那侧的统计口径不对"}')
        for fid, want in ((blank["id"], "report"), (judged["id"], "ai_review")):
            r = await client.post(f"/api/cc-feedback/{fid}/analyze", headers=h)
            assert r.status_code == 200, r.text
            got = (await client.get(f"/api/cc-feedback/{fid}", headers=h)).json()["data"]
            assert got["area"] == want

    async def test_AI判不了裁定也照样落域(self, client, db_session, monkeypatch):
        """域和裁定是两件事：人打开一条「等人拍板」时，知道它坏在哪一块有用。"""
        h, _ = await _admin(db_session)
        d = await _first(client, h, title="丙问题", tool="执行报告说不清")
        _fake_model(monkeypatch,
                    '{"verdict":"needs_human","area":"report",'
                    '"needsHuman":"这条要产品定","reasoning":"需求没写"}')
        r = await client.post(f"/api/cc-feedback/{d['id']}/analyze", headers=h)
        assert r.json()["data"]["verdict"] == "needs_human"
        got = (await client.get(f"/api/cc-feedback/{d['id']}", headers=h)).json()["data"]
        assert got["status"] == "new"                     # 裁定没落
        assert got["area"] == "report"                    # 域落了

    async def test_AI回null不落其它(self, client, db_session, monkeypatch):
        """判不出来就留空等人，别拿 other 假装判过了 —— other 是终局，空的能被补上。"""
        h, _ = await _admin(db_session)
        d = await _first(client, h, title="丙问题", tool="执行报告说不清")
        _fake_model(monkeypatch,
                    '{"verdict":"triaged","category":"bug","area":null,'
                    '"reasoning":"说不清是哪一块"}')
        r = await client.post(f"/api/cc-feedback/{d['id']}/analyze", headers=h)
        assert r.status_code == 200, r.text
        got = (await client.get(f"/api/cc-feedback/{d['id']}", headers=h)).json()["data"]
        assert got["area"] is None
