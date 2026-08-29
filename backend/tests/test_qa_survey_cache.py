"""S7.8 增量缓存键（AD-8）。

这一份测的东西只有一件：**什么时候可以不去爬别人的测试环境**。
判错一次的代价不是慢一点，是把上一趟的事实端成这一趟的结论 ——
而页面上两者长得一模一样。所以下面几乎每条都朝同一个方向咬：
**缺信号不许被推成「没变」。**
"""
import pathlib

import pytest

from app.services import qa_survey_cache as qsc
from app.services.qa_survey_cache import (
    CRAWL,
    KEY_SPEC,
    Q_SIDE,
    R_SIDE,
    RECOMPUTE,
    REUSE,
    plan_reuse,
    previous_of,
    reconcile_key,
    route_table_hash,
    survey_key,
)

P, E, F = "proj-1", "env-1", "build-abc"


def _key(project_id=P, env_id=E, build_fingerprint=F):
    return survey_key(project_id=project_id, env_id=env_id,
                      build_fingerprint=build_fingerprint)


def _table(routes=None, available=True, unreadable=None):
    return {"available": available, "routes": routes or [],
            "unreadable": unreadable or []}


RT = route_table_hash(_table([{"group": "网关", "method": "GET", "path": "/a"}]))
SHA = "deadbeef"


def _cur(**kw):
    d = {"projectId": P, "envId": E, "buildFingerprint": F,
         "routeTableHash": RT, "qaCommitSha": SHA}
    d.update(kw)
    return d


def _prev(**kw):
    d = {"surveyId": "s-1", "status": "done", "crawledAt": "2026-08-28 10:00:00",
         "projectId": P, "envId": E, "buildFingerprint": F,
         "routeTableHash": RT, "qaCommitSha": SHA}
    d.update(kw)
    return d


class Test键的形状:
    def test_键定义版本折进每一个键(self, monkeypatch):
        """`KEY_SPEC` 一涨，所有旧缓存条目必须**失配**。

        不折进去的话，改了键的定义（多一格、换了归一化写法）之后，
        新口径算出来的键会跟旧缓存里那些**长得一样合法** ——
        于是按新口径判「没变」，复用的却是按旧口径攒的东西。

        ⚠ 对账键这一格**故意喂一个写死的 survey 键**，不喂 `_key()`。
        喂 `_key()` 的话版本是**顺着入参捎进去**的（survey 键自己会变），
        于是「对账键里那一格版本」拆掉照样绿 —— 而 `reconcile_key` 的
        survey 键是个**从外面传进来的字符串**，完全可以是从库里读出来的、
        按旧口径算的那一个。要钉的正是这一格自己。
        """
        a = (_key(), route_table_hash(_table()), reconcile_key(
            survey_key="从库里读出来的旧键", route_table_hash=RT, qa_commit_sha=SHA))
        monkeypatch.setattr(qsc, "KEY_SPEC", KEY_SPEC + 1)
        b = (qsc.survey_key(project_id=P, env_id=E, build_fingerprint=F),
             qsc.route_table_hash(_table()),
             qsc.reconcile_key(survey_key="从库里读出来的旧键", route_table_hash=RT,
                               qa_commit_sha=SHA))
        assert all(x != y for x, y in zip(a, b))

    def test_挪一个字符不许撞出同一个键(self):
        """分段必须**带长度前缀**再哈希。

        直接把几段拼起来的话，`("ab","c")` 和 `("a","bc")` 拼出来是同一串 ——
        两个不同的（项目, 环境）组合共用一个键。而这个键管的是
        「要不要再爬一趟」，撞键 = 把 A 环境的爬取当成 B 环境的结论。
        """
        assert _key(project_id="ab", env_id="c") != _key(project_id="a", env_id="bc")
        # 光加个分隔符也不够：指纹是从构建产物里抄进来的**任意文本**，
        # 分隔符本身也可能出现在里面，那时两段又拼回同一串。
        assert _key(build_fingerprint="a\x1fb") != _key(env_id=E + "\x1fa",
                                                        build_fingerprint="b")

    def test_路由行的三格不许拼成一句话(self):
        """同理，一条路由的 组/方法/路径 拼成 `"a b c"` 是有歧义的：
        `group="a b", method="c"` 和 `group="a", method="b c"` 会撞在一起。
        组名带空格在路由表里很常见（「AI 网关」）。
        """
        x = route_table_hash(_table([{"group": "a b", "method": "c", "path": "/p"}]))
        y = route_table_hash(_table([{"group": "a", "method": "b c", "path": "/p"}]))
        assert x != y

    def test_同样的输入永远同一个键(self):
        assert _key() == _key()
        assert route_table_hash(_table()) == route_table_hash(_table())


class Test没有身份就不是一个键:
    @pytest.mark.parametrize("kw", [{"project_id": ""}, {"env_id": ""},
                                    {"build_fingerprint": ""},
                                    {"build_fingerprint": None},
                                    {"build_fingerprint": "   "}])
    def test_缺一格返回None而不是拿空串凑一个键(self, kw):
        """`None` 不是一个键，是「这一趟没有可复用的身份」。

        ⚠ 拿空串凑的话，「指纹没量到」和「指纹跟上次一样」会**哈希到同一个值**，
        一趟没量到构建的运行就命中了上一趟的缓存 ——
        「我们没能确认构建没变」被记成了「构建没变」。这正是洞四的形状。
        """
        assert _key(**kw) is None

    def test_三样齐了才出键(self):
        assert isinstance(_key(), str) and _key()

    def test_指纹不同键就不同(self):
        assert _key(build_fingerprint="x") != _key(build_fingerprint="y")

    def test_换个环境键就不同(self):
        assert _key(env_id="env-2") != _key()


class Test路由表拉不到不是空表:
    def test_拉不到返回None不是空表的哈希(self):
        """空表的哈希是个**合法的值**，它跟上一轮"也没拉到"的那个值相等 ——
        于是连着两轮拉不到路由表，会推出「路由表没变，R 侧不用重算」，
        而事实是这两轮**谁都没看过路由表**。同 S7.7 的「没探到不是看不见」。
        """
        assert route_table_hash(_table(available=False)) is None
        assert route_table_hash({}) is None
        assert route_table_hash(None) is None

    def test_拉到了但确实是空表照样出哈希(self):
        """那是**观测到的事实**，不是缺信号。两者必须分得开。"""
        h = route_table_hash(_table([]))
        assert isinstance(h, str) and h
        assert h != route_table_hash(_table(available=False))

    def test_路由顺序不影响哈希(self):
        a = {"group": "g", "method": "GET", "path": "/a"}
        b = {"group": "g", "method": "GET", "path": "/b"}
        assert route_table_hash(_table([a, b])) == route_table_hash(_table([b, a]))

    def test_组名原样进哈希不归一化(self):
        """归一是对账那侧的事。在这里归一，「组名改了写法」这个信号
        就再也不会让 R 侧重算 —— 而 G2 正是拿组名对出来的。
        """
        x = route_table_hash(_table([{"group": "AI 网关", "method": "GET", "path": "/a"}]))
        y = route_table_hash(_table([{"group": "ai-网关", "method": "GET", "path": "/a"}]))
        assert x != y

    def test_方法也进哈希(self):
        """`GET /x` 变成 `POST /x` 是路由表实打实变了一次，
        而 R 侧算 G2 靠的就是（组, 方法, 路径）三格 —— 少哪一格都会漏掉一次重算。
        """
        a = route_table_hash(_table([{"group": "g", "method": "GET", "path": "/x"}]))
        b = route_table_hash(_table([{"group": "g", "method": "POST", "path": "/x"}]))
        assert a != b

    def test_读不出来的那几条也进哈希(self):
        x = route_table_hash(_table([], unreadable=["坏行1"]))
        assert x != route_table_hash(_table([]))


class Test对账键:
    @pytest.mark.parametrize("kw", [{"survey_key": None}, {"route_table_hash": None},
                                    {"route_table_hash": ""}, {"qa_commit_sha": ""},
                                    {"qa_commit_sha": None}])
    def test_缺一格就没有对账键(self, kw):
        base = {"survey_key": _key(), "route_table_hash": RT, "qa_commit_sha": SHA}
        base.update(kw)
        assert reconcile_key(**base) is None

    def test_三格齐了才出键且跟着每一格变(self):
        k = reconcile_key(survey_key=_key(), route_table_hash=RT, qa_commit_sha=SHA)
        assert k
        assert k != reconcile_key(survey_key=_key(build_fingerprint="z"),
                                  route_table_hash=RT, qa_commit_sha=SHA)
        assert k != reconcile_key(survey_key=_key(), route_table_hash="other",
                                  qa_commit_sha=SHA)
        assert k != reconcile_key(survey_key=_key(), route_table_hash=RT,
                                  qa_commit_sha="other")

    def test_对账键不等于survey键(self):
        """两个键管的是两件事（重爬 / 重算）。相等的话，
        QA 仓 commit 一变就会连爬取一起作废 —— AD-8 想省的正是这一趟。
        """
        assert reconcile_key(survey_key=_key(), route_table_hash=RT,
                             qa_commit_sha=SHA) != _key()


class _Row:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class Test上一趟怎么读:
    def test_没有上一趟给空字典(self):
        assert previous_of(None) == {}

    def test_字段照抄不猜(self):
        r = _Row(id="s-9", status="partial", started_at="2026-08-28 09:00:00",
                 project_id=P, env_id=E, build_fingerprint=F, route_table_hash=RT)
        got = previous_of(r, qa_commit_sha=SHA)
        assert got["surveyId"] == "s-9" and got["status"] == "partial"
        assert got["crawledAt"] == "2026-08-28 09:00:00"
        assert got["buildFingerprint"] == F and got["routeTableHash"] == RT
        assert got["qaCommitSha"] == SHA

    def test_终态照抄_dirty不许在这一步被洗掉(self):
        """有这个函数就是因为下一个接手的人**只会照着字段名手抄一遍**，
        而抄错 `status`（把 `dirty` 当成"爬完了"）不会有任何东西报错。
        """
        assert previous_of(_Row(status="dirty"))["status"] == "dirty"


class Test什么时候必须重爬:
    def test_没有身份就重爬(self):
        """⚠ 光断 `action == CRAWL` 断不住这一条：指纹缺了的那一趟，
        「算不出键」和「键跟上一趟对不上」**都会走到重爬**，两条路殊途同归。
        要钉住的是它**说的是哪一句** —— 缺信号得写成缺信号，
        不能记成「构建变了」，那是个凭空得出的结论。
        """
        r = plan_reuse(previous=_prev(), current=_cur(buildFingerprint=""))
        assert r["action"] == CRAWL and r["surveyKey"] == ""
        assert "身份" in "；".join(r["reasons"])

    def test_没有上一趟就整站爬(self):
        r = plan_reuse(previous=None, current=_cur())
        assert r["action"] == CRAWL
        assert "首次" in "；".join(r["reasons"])

    def test_上一趟dirty一律不复用(self):
        """`dirty` = 只读爬完、环境里的数却变了。这一趟最该被人看的就是
        「我们动了什么」；拿它当底再端一个正常结论，等于**把那面红旗洗掉**。

        ⚠ 同上：`dirty` 不在可复用白名单里，所以就算把它从「一律不复用」
        那张名单上摘掉，它照样会掉进兜底分支去重爬 —— `action` 一模一样。
        两道闸的分工全在**说出来的那句话**上：白名单只会说「不在可复用之列」，
        而人得知道的是**上一趟动过环境**。所以这里断的是理由。
        """
        r = plan_reuse(previous=_prev(status="dirty"), current=_cur())
        assert r["action"] == CRAWL
        assert "dirty" in "；".join(r["reasons"])
        assert "动过环境" in "；".join(r["reasons"])

    def test_上一趟failed不复用(self):
        r = plan_reuse(previous=_prev(status="failed"), current=_cur())
        assert r["action"] == CRAWL
        assert "没爬到东西" in "；".join(r["reasons"])

    def test_没跑完的终态也不复用(self):
        """`running` / `pending` / 空字符串都不是"爬完了"。
        白名单判定，不是黑名单 —— 将来新加一个终态，默认落在「重爬」这一边。
        """
        for st in ("running", "pending", "", "queued"):
            assert plan_reuse(previous=_prev(status=st),
                              current=_cur())["action"] == CRAWL, st

    def test_指纹变了就重爬而不是重算(self):
        r = plan_reuse(previous=_prev(buildFingerprint="old"), current=_cur())
        assert r["action"] == CRAWL and r["recompute"] == []

    def test_换了环境也重爬(self):
        assert plan_reuse(previous=_prev(envId="env-2"),
                          current=_cur())["action"] == CRAWL


class Test只重算不重爬:
    def test_QA仓commit变了只重算(self):
        """AD-8 想省下来的就是这一趟：清单改一行，不该再去爬一次别人的环境。"""
        r = plan_reuse(previous=_prev(), current=_cur(qaCommitSha="new-sha"))
        assert r["action"] == RECOMPUTE and r["recompute"] == [Q_SIDE]

    def test_路由表变了重算R侧(self):
        r = plan_reuse(previous=_prev(), current=_cur(routeTableHash="new-rt"))
        assert r["action"] == RECOMPUTE and r["recompute"] == [R_SIDE]
        assert "G2" in "；".join(r["reasons"])

    def test_本轮拉不到路由表也得重算_不许推成没变(self):
        """**缺 ≠ 没变。** 这一轮没拉到路由表，就断不了它有没有变。

        把「拉不到」和「没变」合成一档，两轮都拉不到时会一路复用下去，
        而 G2 那一列写着的是上一轮的结论。
        """
        r = plan_reuse(previous=_prev(routeTableHash=""),
                       current=_cur(routeTableHash=""))
        assert r["action"] == RECOMPUTE and R_SIDE in r["recompute"]
        assert r["reconcileKey"] == ""

    def test_拿不到QA_commit也得重算(self):
        r = plan_reuse(previous=_prev(qaCommitSha=""), current=_cur(qaCommitSha=""))
        assert r["action"] == RECOMPUTE and Q_SIDE in r["recompute"]

    def test_两格都变两侧都重算(self):
        r = plan_reuse(previous=_prev(),
                       current=_cur(routeTableHash="new", qaCommitSha="new"))
        assert r["action"] == RECOMPUTE
        assert set(r["recompute"]) == {R_SIDE, Q_SIDE}


class Test原样复用:
    def test_三格都没变才复用(self):
        r = plan_reuse(previous=_prev(), current=_cur())
        assert r["action"] == REUSE and r["recompute"] == []

    def test_partial也能复用但结论里必须说(self):
        """把 `partial` 排除在外的代价是：一个角色少配了凭证的环境
        **永远命中不了缓存**，于是每次都去重爬别人的测试环境 ——
        那是朝错误方向的保守。但复用它必须把终态带出来：
        少了终态，一趟缺着页面的爬取会渲染得跟整站爬完一模一样。
        """
        r = plan_reuse(previous=_prev(status="partial"), current=_cur())
        assert r["action"] == REUSE
        assert "partial" in r["summary"]
        assert r["provenance"]["surveyStatus"] == "partial"

    def test_复用的结论带齐哪一趟什么时候什么指纹(self):
        """§7 原话：复用缓存却不说，就是把陈旧事实伪装成新鲜结论。
        少了时间和指纹，页面上就是一句"已复用缓存"，看的人无从判断它是几天前的。
        """
        r = plan_reuse(previous=_prev(), current=_cur())
        s = r["summary"]
        assert "s-1" in s and "2026-08-28 10:00:00" in s and F in s and "done" in s
        assert r["provenance"]["source"] == "reusedSurvey"

    def test_重爬那一支也照样出出处只是空的(self):
        """只在复用时才出现的出处，和"没记过出处"在产物上长得一模一样。"""
        r = plan_reuse(previous=None, current=_cur())
        assert r["provenance"]["source"] == "freshCrawl"
        assert set(r["provenance"]) == {"source", "surveyId", "crawledAt",
                                        "buildFingerprint", "surveyStatus"}

    def test_重算那一支的出处指向被复用的那一趟(self):
        """重算不重爬 ⇒ 用的仍然是上一趟的爬取，出处照样得写它。"""
        r = plan_reuse(previous=_prev(), current=_cur(qaCommitSha="new"))
        assert r["provenance"]["surveyId"] == "s-1"
        assert "s-1" in r["summary"]

    def test_压根没判过的那一轮出处也得是同一个形状(self):
        """任务层查不到上一趟时用的是 `fresh_provenance()`（那时连 plan 都没有）。

        它跟重爬那一支**必须逐格一样**：少一格，下游一句
        `prov["surveyStatus"]` 就 KeyError，而修它的人多半顺手改成 `.get(...)` ——
        从那以后「没有出处」和「出处是空的」再也分不开。
        """
        assert qsc.fresh_provenance() == plan_reuse(previous=None,
                                                    current=_cur())["provenance"]


class Test出口纪律:
    def test_返回值里没有任何一个布尔(self):
        """`{"cached": true}` 这种字段一旦存在，页面上就会有人只渲染它 ——
        而"复用了"和"复用的是哪一趟"分开之后，前者就是个绿勾。
        """
        def _walk(x):
            if isinstance(x, bool):
                return True
            if isinstance(x, dict):
                return any(_walk(v) for v in x.values())
            if isinstance(x, (list, tuple)):
                return any(_walk(v) for v in x)
            return False

        for prev, cur in ((None, _cur()), (_prev(), _cur()),
                          (_prev(), _cur(qaCommitSha="new")),
                          (_prev(status="dirty"), _cur())):
            assert not _walk(plan_reuse(previous=prev, current=cur))

    def test_每一支都有一句能直接渲染的结论(self):
        for prev, cur in ((None, _cur()), (_prev(), _cur()),
                          (_prev(), _cur(qaCommitSha="new")),
                          (_prev(status="dirty"), _cur())):
            s = plan_reuse(previous=prev, current=cur)["summary"]
            assert s.endswith("。") and ("理由：" in s or "本轮重爬" in s)

    def test_不改入参(self):
        prev, cur = _prev(), _cur()
        a, b = dict(prev), dict(cur)
        plan_reuse(previous=prev, current=cur)
        assert prev == a and cur == b

    def test_全是纯函数不碰IO(self):
        """这个模块只判「要不要去爬」，自己一次都不许发请求、不许读库。
        它一旦能自己去取信号，「拿不到信号」这件事就会被吞在里面。
        """
        src = pathlib.Path(qsc.__file__).read_text(encoding="utf-8")
        for bad in ("httpx", "requests", "AsyncSession", "select(", "open(",
                    "subprocess", "await "):
            assert bad not in src, bad
