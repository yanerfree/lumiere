"""这一批全是同一类坑：**键名对不上就静默假绿**。

来源是一次活体验证：外部 CC 照工具说明干活，平台每一步都回「成功」，
而真相是值没存住 / 提取被丢了 / 这条路压根没渲染。共同点是
**入库那一刻不喊疼，执行时才炸，而错误指向的是后果不是原因**。

这里钉的是"必须喊出来"这件事本身，不是某个具体键名 ——
键名会变，静默这个毛病不会自己好。
"""
from __future__ import annotations

import inspect
import re

from app.mcp.tools import sync as sync_tools
from app.services.ui_text_render import unresolved, unresolved_hint

# ── ① 文案占位：修复建议要指向真因 ─────────────────────────────────


def test_带中文原文的占位说明是漏渲染_不是叫人补占位():
    """`${键|中文}` 渲染过一定会退回中文。它还在正文里 = 这条执行路径没渲染。

    实测踩到：计划执行路径没调 render，报错却叫人「占位里补上 ${键|中文原文}」——
    而脚本里写的本来就是 ${cases.nav.title|用例导航}。建议解决不了问题，
    还把注意力从真因（漏渲染）上引开。
    """
    src = 'page.get_by_text("${cases.nav.title|用例导航}").click()'
    hint = unresolved_hint(src)
    assert unresolved(src) == ["cases.nav.title"]
    assert "没做文案渲染" in hint
    assert "补上 ${键" not in hint, "已经写了中文原文，不该再叫人去补"


def test_没写中文原文的仍然叫人登记():
    hint = unresolved_hint('page.get_by_text("${cases.nav.title}").click()')
    assert "tb_upsert_i18n_terms" in hint and "没做文案渲染" not in hint


# ── ② 执行路径必须渲染文案 ────────────────────────────────────────

def test_四条执行路径都要渲染文案():
    """**这条是结构性防线。** 词典只在一条路注入，另一条静默跑字面量 ——
    这个库栽过两次（第二次是计划/回归/批量共用的 _run_new_style_script）。
    后果不对称：正例红在「找不到元素」，而「不应出现」这类负例**假绿**。

    判据：凡是把脚本正文写进沙箱去跑的地方，都得调 render。
    """
    from app.api import scripts as api_scripts
    from app.engine.tasks import adhoc_execution
    from app.mcp.tools import ui_scripts

    for mod in (ui_scripts, api_scripts, adhoc_execution):
        src = inspect.getsource(mod)
        writes_script = "write_text(content" in src
        assert writes_script, f"{mod.__name__} 不再写脚本正文了？这条判据要跟着改"
        assert re.search(r"render as render_text", src), \
            f"{mod.__name__} 把脚本写进沙箱却没渲染文案 —— 负例会假绿"
        assert "bake_env_defaults" in src, \
            f"{mod.__name__} 没烧 os.getenv 默认值 —— 本地渲染的那份会拿到空串"


# ── ③ 步骤键名：读回来的必须能原样写回去 ──────────────────────────

def test_驼峰步骤键有别名_读改写不丢东西():
    """tb_get_api_test 吐驼峰、写回只认下划线 —— 于是"读回来改一个 URL 再存回去"
    会把所有 variables_extract 静默丢掉，然后报「存在悬空变量引用」。
    """
    for camel in ("variablesExtract", "groupName", "waitMs",
                  "retryTimeoutMs", "retryIntervalMs"):
        assert camel in sync_tools._STEP_ALIASES, f"{camel} 没有别名，读改写会丢它"
        assert sync_tools._STEP_ALIASES[camel] in sync_tools._STEP_FIELDS


def test_读回来的每个键要么能写回要么在只读名单里():
    """名单漏一个，读改写就会收到一条假警报（"忽略了 lastStatusCode"），
    而真丢东西时反被这条噪声盖住。所以拿 api_tests 的**源码**当真相。
    """
    from app.mcp.tools import api_tests
    src = inspect.getsource(api_tests)
    body = src[src.index("async def get_api_test_scenario"):]
    body = body[:body.index("def _last_run_facts")]
    emitted = set(re.findall(r'"(\w+)":\s*st\.', body))
    emitted |= set(re.findall(r'out\["(\w+)"\]', inspect.getsource(api_tests._last_run_facts)))
    known = (set(sync_tools._STEP_FIELDS) | set(sync_tools._STEP_ALIASES)
             | sync_tools._STEP_READONLY)
    assert not (emitted - known), f"这些读回来的键既写不回、也没进只读名单：{emitted - known}"


# ── ④ 覆盖缺口按话题归并 ─────────────────────────────────────────

def test_同一件事的不同措辞归成一桶():
    """这一页存在的理由就是 count 列。按字面比（原来是"头 12 个字"）的结果是
    三条讲越权的各自 1×，等于没合并 —— 而 LLM 每轮措辞都不一样。
    """
    from app.services.review.gap_merge import merge
    buckets, total = merge([
        ("模块级缺口：缺少越权访问其他项目用例的场景", "TC-1"),
        ("没有覆盖无权限用户访问该接口应返回 403 的用例", "TC-2"),
        ("建议补充跨租户读取他人数据的鉴权测试", "TC-3"),
        ("缺少分页边界的用例", "TC-4"),
    ])
    top = buckets[0]
    assert top["count"] == 3 and top["topic"] == "权限与越权"
    assert top["cases"] == ["TC-1", "TC-2", "TC-3"]
    assert len(top["phrasings"]) == 3, "合并了就得留下原话，否则归并是黑箱"
    assert total == 2


def test_归并键不当标签显示():
    """命中不了话题时退回实词签名。那是**键**，直接显示就是
    「其他：expectedconfirmednote-false-matchkeychanged-path」这种噪声。
    """
    from app.services.review.gap_merge import merge
    buckets, _ = merge([("expectedConfirmedNote 提到的那条分支没覆盖", "TC-9")])
    b = buckets[0]
    assert b["topic"].startswith("其他") and b["matchedTopic"] is False
    assert b["display"] == b["gap"], "没命中话题就显示原话，不显示签名"


def test_截断要能被看出来():
    from app.services.review.gap_merge import merge
    gaps = [(f"缺少第 {i} 类场景 —— 各不相同的措辞{i}", f"TC-{i}") for i in range(12)]
    buckets, total = merge(gaps, top=8)
    assert len(buckets) == 8 and total == 12, "只回 top 的话，'就这几类'和'被砍了'长得一样"


# ── ⑤ 报告工具：照着提示做不该报参数错 ────────────────────────────

def test_报告工具只给reportId也能调():
    """tb_run_plan 返回的是 taskId + reportId，提示写着"拿 reportId 来查"，
    而这两个工具此前只认 plan_id —— 照提示做一定报参数错。
    """
    from app.mcp.tools import test_reports
    for fn in (test_reports.get_report_summary, test_reports.get_failed_scenarios):
        p = inspect.signature(fn).parameters
        assert p["plan_id"].default is None, f"{fn.__name__} 仍然强制要 plan_id"
        assert "report_id" in p


# ── ⑥ 场景变量：写错键名不许静默存空 ──────────────────────────────

class _FakeSession:
    """够用的假 session：这个函数只查同名、加行、提交三件事。

    用假的而不是抽出纯函数来测：要钉住的是**整条入库路径**的行为
    （回什么 message、有没有真的 add），而不是某个校验片段。
    """

    def __init__(self):
        self.added = []
        self.commits = 0

    async def execute(self, *_a, **_k):
        class _R:
            @staticmethod
            def scalar_one_or_none():
                return None
        return _R()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


async def _upsert(items):
    s = _FakeSession()
    r = await sync_tools.upsert_scenario_variables(
        s, "11111111-1111-4111-8111-111111111111", items)
    return r, s


async def test_value写成value_template的别名要回显():
    """此前 value 取不到 → 存成空串 → 回「新增 1」errors 空，看上去全绿；
    直到执行期整条链挂在「变量未解析：${x}」上。
    """
    r, s = await _upsert([{"name": "svcName", "kind": "literal", "value": "abc"}])
    assert r["created"] == ["svcName"] and s.added[0].value_template == "abc"
    assert r["renamedFromValue"] == ["svcName"]
    assert "value_template" in r["message"], "改了键名却不说，等于换一种静默"


async def test_空值直接拒不入库():
    r, s = await _upsert([{"name": "svcName", "kind": "literal", "value_template": ""}])
    assert r["created"] == [] and s.added == []
    assert r["status"] == "partial" and "不能为空" in r["errors"][0]["reason"]


async def test_random的空前缀仍然允许():
    """random 的 value_template 只是前缀，空前缀照样解析得出值 ——
    把它一起拦掉是过度收紧，会逼人绕开校验。"""
    r, s = await _upsert([{"name": "svcName", "kind": "random", "value_template": ""}])
    assert r["created"] == ["svcName"] and not r["errors"]


async def test_写错kind不再静默变literal():
    """把 random 拼成 rand 的人以为存进去的是"每次换新的"，
    拿到的却是"整段固定"，然后在执行期以另一副面孔炸出来。"""
    r, s = await _upsert([{"name": "svcName", "kind": "rand", "value_template": "x"}])
    assert s.added == [] and "不认识" in r["errors"][0]["reason"]


async def test_拼错的字段名要报错不要静默丢():
    r, s = await _upsert([{"name": "svcName", "kind": "literal",
                           "value_template": "x", "var_typ": "string"}])
    assert s.added == [] and "不认识的字段" in r["errors"][0]["reason"]


# ── ⑦ 代理观测：跑着但一条都没抓到，是最常见的情况 ────────────────

class _FakeProbe:
    running = True
    port = 28900

    def __init__(self, records):
        self._records = records


def _rec(i, target, line):
    return {"id": i, "target": target, "c2p_request": line + "\nHost: x\n"}


async def _capture(records, **kw):
    from app.mcp.tools import mocks
    from app.services import proxy_probe_manager as ppm
    ppm.proxy_probe = _FakeProbe(records)
    return await mocks.proxy_capture(**kw)


async def test_跑着但零条要给出把代理指过去的提示():
    """这句话原来只在 running=False 那个分支里 —— 真正需要它的分支反而没有。"""
    r = await _capture([])
    assert r["running"] is True and r["count"] == 0
    assert "127.0.0.1:28900" in r["hint"]


async def test_按URL和方法过滤():
    """前端跑 Vite 时抓到的绝大多数是 .jsx?t= 热更新：实测 156 条里只有 9 条 /api/，
    limit=50 全被噪声占满，等于抓了也用不了。"""
    recs = [_rec(i, "localhost:5173", f"GET /src/x{i}.jsx?t=1 HTTP/1.1") for i in range(147)]
    recs += [_rec(200 + i, "localhost:8756", m + " /api/projects HTTP/1.1")
             for i, m in enumerate(["GET"] * 6 + ["POST"] * 3)]
    assert (await _capture(recs, limit=50, url_contains="/api/"))["matched"] == 9
    assert (await _capture(recs, limit=50, url_contains="/api/", method="POST"))["matched"] == 3


async def test_过滤后零条要说清抓到的是什么():
    """空手回等于让人猜"是我过滤写错了还是这个动作压根没发这个请求" ——
    而后者才是要报出去的结论。"""
    r = await _capture([_rec(1, "localhost:5173", "GET /x.jsx HTTP/1.1")],
                       url_contains="/api/")
    assert r["matched"] == 0 and "localhost:5173" in r["hint"]


async def test_截断要回显():
    recs = [_rec(i, "h", "GET /api/x HTTP/1.1") for i in range(30)]
    r = await _capture(recs, limit=5, url_contains="/api/")
    assert r["matched"] == 30 and r["count"] == 5 and "只回了最后 5 条" in r["hint"]


# ── ⑧ 反问的事实不能有恒假项 ─────────────────────────────────────

def test_反问里的UI脚本事实取决于真的有没有脚本():
    """`reflect.build` 的第四个参数（脚本）此前调用方从来没传 ——
    facts 里的「UI 脚本」恒为 false。而这四问值钱就值钱在"事实是平台数的"。
    """
    from types import SimpleNamespace

    from app.services.review import reflect
    case = SimpleNamespace(title="a-b", reflections=None, expected_confirmed_note=None,
                           folder_id="f", id="c")
    assert reflect.build(case, {"steps": []}, [], None)[0]["facts"]["UI 脚本"] is False
    assert reflect.build(case, {"steps": []}, [],
                         {"version": 4, "chars": 5240})[0]["facts"]["UI 脚本"] is True


def test_回推那条路真的把脚本查出来传进去了():
    """光有第四个参数不够 —— 上一次的 bug 就是"参数在、调用方不传"。"""
    src = inspect.getsource(sync_tools._reflect_block)
    assert "_latest_ui_script" in src, "reflect.build 又没拿到脚本，facts 会恒假"
