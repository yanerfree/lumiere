"""CC 反馈通道 —— 收→并→分诊→回音 整条链，打真接口。

和 backend/tests/test_cc_feedback_gates.py 分工明确：那边测**纯判据**
（校验、指纹、状态机不变量），这边测**跨请求的行为** —— 归并、短路、复发、
回音取走。后者只有在真库上才成立：它们全都依赖「上一条留下的行还在」，
而那正是最容易在重构里被改坏、又最难被发现的部分 —— 改坏的表现不是报错，
是**多出一行**，而没人会去数行数。
"""
from tests.conftest import create_test_user, make_auth_headers

BODY = ("调 lum_get_case 想读回 bugRefs，返回里没有这个字段。"
        "工具描述写的是「读一条用例的全部内容」，实际只有十来个字段。")


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


# ── AI 分诊 ───────────────────────────────────────────────────────

class TestAITriage:
    async def test_ai只给建议不改状态(self, client, db_session, monkeypatch):
        """wont_fix 会**永久短路**同指纹的后续上报。让 AI 单方面落这个状态，
        等于给它一个「把一类反馈永久关死、而且以后没人会再看到」的开关 ——
        这种错不报错，只是安静地少一批反馈。"""
        h, _ = await _admin(db_session)
        d = await _first(client, h)

        class _Cfg:
            model = "claude-opus-5"

        class _Resp:
            content = ('{"category":"bug","severity":"high",'
                       '"suggestedStatus":"wont_fix","reasoning":"r",'
                       '"suggestedResolution":"用 lum_check_deliverable","risk":"x"}')

        async def _fake_cfg(*a, **kw):
            return _Cfg()

        async def _fake_complete(*a, **kw):
            return _Resp()

        monkeypatch.setattr("app.services.ai_config_resolver.resolve_ai_config", _fake_cfg)
        monkeypatch.setattr("app.services.ai.llm_client.complete", _fake_complete)

        r = await client.post(f"/api/cc-feedback/{d['id']}/analyze", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["aiAnalysis"]["suggestedStatus"] == "wont_fix"

        got = (await client.get(f"/api/cc-feedback/{d['id']}", headers=h)).json()["data"]
        assert got["status"] == "new"          # 状态一个字没动
        assert got["category"] is None

    async def test_模型回空不落库(self, client, db_session, monkeypatch):
        """空回复必须报错，不能存成一次「分析完了」。

        存了就是页面上一块**空的** AI 分析 —— 看着像模型读过觉得没啥可说，
        实际是一个字都没回来；而且它还会把上一次真有内容的分析覆盖掉。
        这不是假想：主路 429 降级到 CLI 通道（那头是 Claude Code）时，
        反馈正文本身长得像一件待办，它会去做事而不作答，回来就是空的。
        """
        h, _ = await _admin(db_session, username="fb_admin_empty")
        d = await _first(client, h, title="模型回空的那条")

        class _Cfg:
            model = "claude-opus-5"

        class _Resp:
            content = "   "

        async def _fake_cfg(*a, **kw):
            return _Cfg()

        async def _fake_complete(*a, **kw):
            return _Resp()

        monkeypatch.setattr("app.services.ai_config_resolver.resolve_ai_config", _fake_cfg)
        monkeypatch.setattr("app.services.ai.llm_client.complete", _fake_complete)

        r = await client.post(f"/api/cc-feedback/{d['id']}/analyze", headers=h)
        assert r.status_code == 400, r.text
        err = r.json()["error"]
        assert "没有返回内容" in err["message"]
        assert "重试" in (err.get("detail") or "")   # 拒绝要带出路，不能只说"失败"

        got = (await client.get(f"/api/cc-feedback/{d['id']}", headers=h)).json()["data"]
        assert got["aiAnalysis"] is None       # 没留下空壳
