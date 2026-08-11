"""用例管理模块走查修复的封样。

这一轮的问题有个共同形状：**页面说的和库里存的不是一回事**。
「无脚本」其实是状态没推、「场景列没有 API」其实是查错了存储、
「报告全是接口」其实是把入口通道当成了执行方式。
所以下面每条钉的都是同一件事：显示出来的那句话，得能对上真实数据。
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.api.plans import _exec_kind_by_report
from app.services.api_test_runner import (
    ScenarioResult,
    StepResult,
    _first_error,
    _readable_trace,
)
from app.services.script_run_service import DEBUG, REGRESSION, apply_case_status


class _Case:
    """只带状态字段的替身——apply_case_status 就只碰这几个。"""

    def __init__(self, **kw):
        self.ui_status = kw.get("ui_status", "not_started")
        self.api_status = kw.get("api_status", "not_started")
        self.ui_scenario_status = kw.get("ui_scenario_status", "draft")
        self.api_scenario_status = kw.get("api_scenario_status", "draft")


# ── 维度状态推进：接口这一维此前整个是死的 ──────────────────────────

def test_接口跑通也会推进状态():
    """原先这里写死 `script_type != "ui"` 直接 return。

    后果不是"少了个功能"，是**页面说假话**：接口场景跑通 69 次，api_status
    还停在 debugging，批量执行就报「0 个包含可执行脚本」——它有脚本。
    """
    case = _Case(api_status="debugging")
    apply_case_status(case, "api", "passed", REGRESSION)
    assert case.api_status == "pending_review"
    assert case.api_scenario_status == "completed"


def test_接口跑挂_回归才打回_调试不打回():
    """和 UI 同一条纪律：调试是"我正在试"，试挂了不代表用例坏了。"""
    debugging = _Case(api_status="executable")
    apply_case_status(debugging, "api", "failed", DEBUG)
    assert debugging.api_status == "executable", "调试失败不该把状态打回去"

    regression = _Case(api_status="executable")
    apply_case_status(regression, "api", "failed", REGRESSION)
    assert regression.api_status == "debugging"


def test_两个维度互不串台():
    """推 api 不能顺手改了 ui —— 用 f-string 拼属性名最容易出这种错。"""
    case = _Case(ui_status="executable", api_status="debugging")
    apply_case_status(case, "api", "passed", REGRESSION)
    assert case.ui_status == "executable"
    assert case.ui_scenario_status == "draft"


def test_不认识的类型直接跳过():
    case = _Case()
    apply_case_status(case, "manual", "passed", REGRESSION)
    assert case.ui_status == "not_started" and case.api_status == "not_started"


# ── 报告的「执行方式」：三级回退 ────────────────────────────────────

class _FakeSession:
    """execute() 只回一批 (report_id, script_type)。"""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _stmt):
        rows = self._rows

        class R:
            def all(self):
                return rows
        return R()


R1, R2, R3 = (uuid.uuid4() for _ in range(3))


@pytest.mark.asyncio
async def test_执行方式优先信执行痕迹():
    """script_runs 记的是**真跑了什么**，比任何声明都可信。"""
    got = await _exec_kind_by_report(
        _FakeSession([(R1, "ui")]), [R1], {R1: "api"}, {R1: "plan"}
    )
    assert got[R1] == "ui", "计划声明 api，实际跑的 ui —— 以实际为准"


@pytest.mark.asyncio
async def test_一份报告里两种脚本记成混合():
    got = await _exec_kind_by_report(
        _FakeSession([(R1, "ui"), (R1, "api")]), [R1], {}, {}
    )
    assert got[R1] == "mixed"


@pytest.mark.asyncio
async def test_没有执行痕迹时退回计划声明():
    """老计划报告在 record_run 接进来之前生成，没有第一级。"""
    got = await _exec_kind_by_report(
        _FakeSession([]), [R1, R2], {R1: "e2e", R2: "api"}, {}
    )
    assert got[R1] == "ui" and got[R2] == "api"


@pytest.mark.asyncio
async def test_接口测试通道按定义就是接口():
    """api_test 这条通道只跑接口场景，是定义使然，不是猜。"""
    got = await _exec_kind_by_report(_FakeSession([]), [R1], {}, {R1: "api_test"})
    assert got[R1] == "api"


@pytest.mark.asyncio
async def test_三条都不命中就留白_不许编():
    """显示「—」比显示一个猜出来的值好。猜错了没人知道它是猜的。"""
    got = await _exec_kind_by_report(_FakeSession([]), [R3], {}, {R3: "adhoc"})
    assert got[R3] is None


# ── 给人看的执行轨迹 ────────────────────────────────────────────────

def _step(name, status="pass", **kw):
    return StepResult(
        step_id=str(uuid.uuid4()), step_name=name, method=kw.get("method", "GET"),
        url=kw.get("url", "http://h/x"), status=status,
        status_code=kw.get("status_code", 200), duration=kw.get("duration", 5),
        assertions=kw.get("assertions", []), response_body=None,
        error=kw.get("error"), request_data=None,
    )


def test_轨迹把断言写成人话_而不是只印类型():
    """只印 `✓ status` 等于没说。人要看的是"断言了什么"。"""
    r = ScenarioResult(scenario_id="s", scenario_title="登录链路", steps=[
        _step("登录取 token", assertions=[
            {"type": "status", "operator": "==", "value": 200, "passed": True},
        ]),
    ])
    out = _readable_trace(r)
    assert "状态码 == 200" in out
    assert "type" not in out, "断言的内部字段名不该漏到界面上"


def test_失败步骤带出实际值():
    r = ScenarioResult(scenario_id="s", scenario_title="x", steps=[
        _step("建项目", status="fail", assertions=[
            {"type": "status", "operator": "==", "value": 201,
             "passed": False, "actual": 500},
        ], error="Internal Server Error"),
    ])
    out = _readable_trace(r)
    assert "✗" in out and "实际 500" in out
    assert "错误：Internal Server Error" in out


def test_错误摘要只取第一个挂掉的步骤():
    """列表里那一列只显示一行，给三条等于一条都看不清。"""
    r = ScenarioResult(scenario_id="s", scenario_title="x", steps=[
        _step("一", status="pass"),
        _step("二", status="fail", error="first boom"),
        _step("三", status="fail", error="second boom"),
    ])
    summary = _first_error(r)
    assert "二" in summary and "first boom" in summary
    assert "second boom" not in summary


def test_全通过时没有错误摘要():
    r = ScenarioResult(scenario_id="s", scenario_title="x", steps=[_step("一")])
    assert _first_error(r) is None


# ── 报告名的时区 ────────────────────────────────────────────────────

def test_报告名用本地时区而不是UTC():
    """名字给人看，时间就得是人所在时区的。

    用 UTC 拼名字、列表按本地渲染 createdAt，同一行会显示相差 8 小时的两个时间
    （实测「批量执行 · 08-10 08:17」配「2026/8/10 16:17:06」）。
    这里不假设具体时区，只钉住"两处命名都做了 astimezone 换算"。
    """
    import inspect

    from app.api import plans
    from app.services import api_test_runner

    adhoc = inspect.getsource(plans.execute_adhoc)
    assert "astimezone()" in adhoc, "批量执行报告名没做本地时区换算"

    api_src = inspect.getsource(api_test_runner._create_report)
    assert "astimezone()" in api_src, "接口测试报告名没做本地时区换算"

    now = datetime(2026, 8, 10, 8, 17, tzinfo=timezone.utc)
    assert now.astimezone().strftime("%m-%d %H:%M") != "08-10 08:17" or \
        datetime.now().astimezone().utcoffset().total_seconds() == 0


# ── 就地审核：打回必须带理由 ────────────────────────────────────────

def test_打回没带理由后端会拒():
    """后端这道校验一直在（review_reason.category 必填）。

    钉住它，是因为前端很容易只发 reviewStatus —— 我就这么干过一次：
    下拉里加了「打回」，点下去 400，而我当时没点过所以没发现。
    """
    import inspect

    from app.services import case_service

    src = inspect.getsource(case_service.update_case)
    assert 'data.review_status == "rejected"' in src
    assert "REASON_REQUIRED" in src


def test_前端打回走弹窗收理由_不直接提交():
    """对应上面那条后端校验。前端只发 reviewStatus 的话，用户点了就是个红叉。

    盯的是「打回」这条路必须把 reviewReason 一起发出去。
    """
    from pathlib import Path

    jsx = Path(__file__).resolve().parents[2] / "frontend/src/pages/cases/CaseManagement.jsx"
    src = jsx.read_text(encoding="utf-8")
    body = src[src.index("const rejectCase"):]
    body = body[:body.index("\n  }")]
    assert "reviewReason" in body, "打回没带 reviewReason，后端会 400"
    assert "category" in body, "reviewReason 里必须有 category"
    # 「通过」不需要理由，别顺手也要求填
    approve = src[src.index("const approveCase"):]
    approve = approve[:approve.index("\n  }")]
    assert "reviewReason" not in approve


def test_打回分类和生成向导保持同一套():
    """两处各写一套的话，同一个"场景重复"会落成两个不同的 category，
    质量归因统计直接分裂 —— 而且没人会发现，因为两边各自都是对的。"""
    import re
    from pathlib import Path

    base = Path(__file__).resolve().parents[2] / "frontend/src"

    def cats(path):
        src = (base / path).read_text(encoding="utf-8")
        block = src[src.index("REJECT_CATEGORIES = ["):]
        return set(re.findall(r"value: '([^']+)'", block[:block.index("]")]))

    assert cats("pages/cases/CaseManagement.jsx") == \
        cats("pages/scenario-gen/components/Stage5Review.jsx")


# ── 抓包过滤：接口视图 91% 是噪音的那个洞 ──────────────────────────

def _har(entries):
    """把 (url, mime, method) 拼成一份最小 HAR。"""
    return {"log": {"entries": [
        {"startedDateTime": "2026-08-10T10:00:00Z", "time": 5,
         "request": {"method": m, "url": u, "headers": []},
         "response": {"status": 200, "content": {"mimeType": t, "text": "{}"}}}
        for u, t, m in entries
    ]}}


def _parse(entries):
    import json
    import os
    import tempfile

    from app.engine.har import parse_har

    fd, path = tempfile.mkstemp(suffix=".har")
    os.write(fd, json.dumps(_har(entries)).encode())
    os.close(fd)
    try:
        return [r["url"] for r in parse_har(path)]
    finally:
        os.unlink(path)


def test_前端源码模块不算接口():
    """实测一次登录+建项目抓到 75 条，其中 **68 条是前端源码模块**，
    真接口只有 7 条。人要在 75 行里挑出那 7 行 —— 等于没给。

    漏的两处：`.jsx/.tsx/.ts/.vue` 不在扩展名表里；dev server 发的是
    `text/javascript`，而 mime 表里只有 `application/javascript`。
    """
    got = _parse([
        ("http://h/api/projects", "application/json", "POST"),
        ("http://h/src/main.jsx?t=1786354198555", "text/javascript", "GET"),
        ("http://h/src/pages/cases/CaseDetail.jsx?t=1", "text/javascript", "GET"),
        ("http://h/src/utils/i18n.ts", "text/javascript", "GET"),
        ("http://h/src/App.vue", "text/javascript", "GET"),
    ])
    assert got == ["http://h/api/projects"], got


def test_扩展名这条路径单独也要拦得住():
    """埋雷时发现：只测 `text/javascript` 的话，把扩展名表改回旧版测试照样绿 ——
    两条路径互相兜底，等于只验了一条。

    所以这里故意给一个**不在 mime 名单里**的类型，逼着扩展名那条规则单独生效。
    真实场景确实会遇到：有的服务器给 .ts/.vue 返回 application/octet-stream。
    """
    got = _parse([
        ("http://h/api/x", "application/json", "GET"),
        ("http://h/src/main.jsx?t=1", "application/octet-stream", "GET"),
        ("http://h/src/a.tsx", "application/octet-stream", "GET"),
        ("http://h/src/b.vue", "application/octet-stream", "GET"),
        ("http://h/src/c.ts", "application/octet-stream", "GET"),
    ])
    assert got == ["http://h/api/x"], got


def test_mime这条路径单独也要拦得住():
    """反过来：没有扩展名、只能靠 mime 认出来的那些。"""
    got = _parse([
        ("http://h/api/x", "application/json", "GET"),
        ("http://h/module/abc123", "text/javascript", "GET"),
        ("http://h/style/abc123", "text/css", "GET"),
    ])
    assert got == ["http://h/api/x"], got


def test_开发服务器自己的请求不算接口():
    """这些没有扩展名，光靠扩展名和 mime 都拦不住。"""
    got = _parse([
        ("http://h/api/login", "application/json", "POST"),
        ("http://h/@vite/client", "text/javascript", "GET"),
        ("http://h/@react-refresh", "text/javascript", "GET"),
        ("http://h/node_modules/.vite/deps/antd.js", "text/javascript", "GET"),
        ("http://h/_next/static/chunks/main.js", "text/javascript", "GET"),
    ])
    assert got == ["http://h/api/login"], got


def test_不按api前缀做白名单():
    """被测系统的接口前缀是什么，我们不该假设 —— `/v1/`、`/gateway/` 都得留。

    反过来做成白名单的话，那类系统的接口视图会整个空掉。
    """
    got = _parse([
        ("http://h/v1/gateway/services", "application/json", "GET"),
        ("http://h/login", "text/html", "GET"),
    ])
    assert got == ["http://h/v1/gateway/services", "http://h/login"], got


# ── 已有接口场景时点「编排」 ────────────────────────────────────────

@pytest.mark.parametrize("has_existing,mode,expect,why", [
    (False, None,      "create",  "没有就新建，跟以前一样"),
    (False, "append",  "create",  "没有可追加的，还是新建"),
    (True,  None,      "refuse",  "★已有却没表态 → 拒绝。此前是静默多建一条"),
    (True,  "append",  "append",  "接到现有场景后面"),
    (True,  "replace", "replace", "换掉现有步骤"),
    (True,  "merge",   "refuse",  "不认识的取值当没表态，别猜"),
])
def test_已有场景时怎么落(has_existing, mode, expect, why):
    """静默多建一条的后果不是"多了个东西"：用例页面只显示步骤最多的那条，
    多出来的既看不见也删不掉，却照样被分支批量执行捞走；新的步骤更多时，
    反而把原来跑通的那条顶掉 —— 全程无提示。
    """
    from app.services.ai.api_scenario_gen_service import resolve_existing_action

    assert resolve_existing_action(has_existing, mode) == expect, why


def test_接口层只认append和replace():
    """乱传一个字符串不能被当成"表态"放行。"""
    from app.api.api_test import GenerateRequest

    assert GenerateRequest(on_existing="append").on_existing == "append"
    assert GenerateRequest(on_existing="replace").on_existing == "replace"
    assert GenerateRequest().on_existing is None
    with pytest.raises(Exception):
        GenerateRequest(on_existing="drop")


# ── 空目录 ──────────────────────────────────────────────────────────

def test_删用例不许顺手删目录():
    """目录是**模块分类**，不是用例的容器。

    删掉「环境管理」下最后一条用例，不代表这个模块不存在了 —— 替人把分类
    删掉是越权。上一版我做成了自动回收，是判错了：观察到的现象（一屏空目录）
    真正的原因是**计数只算自己**，不是目录该被删。
    """
    import inspect

    from app.services import case_service

    for fn in (case_service.batch_hard_delete, case_service.empty_trash):
        src = inspect.getsource(fn)
        assert "folder" not in src.lower(), f"{fn.__name__} 不该碰目录"


def test_目录计数含子目录_且只汇总一次():
    """两件事都要成立，少一件人就看到假数：

    1. 父目录的数必须**含子目录** —— 否则「环境管理 (0)」点进去冒出 20 条
    2. 但只能汇总**一次** —— 我上一版在计数时先向上累加了一遍，
       而 `_sum_counts` 本来就在递归汇总，结果「项目管理」1+11 显示成 23。
       教训：改之前先看接口**真实返回**，别只读源码前半段就下结论。
    """
    import inspect

    from app.services import case_service, folder_service

    src = inspect.getsource(folder_service.list_folder_tree)
    assert "_sum_counts" in src, "没有向上汇总，父目录会显示 0"
    # 计数那一步必须是纯直属，不能再自己爬一遍父链
    head = src[:src.index("# 构建树")]
    assert "parent_of" not in head and "count_map[cur]" not in head, "汇总了两次"

    # 另一头：筛选确实含后代，两边口径才对得上
    assert "_collect_descendant_ids" in inspect.getsource(case_service.list_cases)


def test_批量清空目录不接受删光指令():
    """服务端按**给定的 id 名单**删，而且逐个重判一次是否真的空 ——
    页面拉到名单和点确认之间，别人可能刚往里放了用例。"""
    import inspect

    from app.services import folder_service

    src = inspect.getsource(folder_service.prune_empty_folders)
    assert "folder_ids" in src
    assert "CaseFolder.id.in_(folder_ids)" in src, "必须限定在名单内"
    assert "if cases or children:" in src, "必须服务端重判，不能信名单"


def test_HMR的websocket不算接口_但真websocket要留():
    """vite 的 HMR socket 是 `ws://host:5173/?token=xxx`，握手 101。

    不能一刀切把 101 全滤掉 —— 被测系统自己的实时功能也走 websocket，
    把那些证据丢了，实时相关的失败就再也查不出来。判据是**根路径**。
    """
    import json
    import os
    import tempfile

    from app.engine.har import parse_har

    har = {"log": {"entries": [
        {"startedDateTime": "2026-08-10T10:00:00Z", "time": 1,
         "request": {"method": "GET", "url": u, "headers": []},
         "response": {"status": 101, "content": {"mimeType": ""}}}
        for u in ["ws://h:5173/?token=abc", "ws://h:5173/socket.io/?EIO=4", "wss://h/ws/notify"]
    ]}}
    fd, path = tempfile.mkstemp(suffix=".har")
    os.write(fd, json.dumps(har).encode()); os.close(fd)
    try:
        got = [r["url"] for r in parse_har(path)]
    finally:
        os.unlink(path)
    assert got == ["ws://h:5173/socket.io/?EIO=4", "wss://h/ws/notify"], got


# ── AI 评审：统计归平台算，不许 LLM 自己数 ──────────────────────────

def test_评审的统计数字由平台算():
    """实测它编得有鼻子有眼：报告说「50 条没有前置条件、50 条没有预期结果、
    P0 只有 3 条」，库里真实是 6 / 5 / 11 —— 每个数都错，而且错得像真的。

    原因很直白：送进 LLM 的只有「[优先级] 标题 (N步)」，
    它**从没看见过** preconditions 和 expected_result 这两个字段。
    人照这个报告去改用例会被带偏，比不给报告更糟。
    """
    import inspect

    from app.services.ai import skill_executor

    src = inspect.getsource(skill_executor)
    i = src.index("tb-quality-review Skill")
    body = src[i:i + 9000]

    assert 'facts = {' in body, "平台没有自己算统计"
    assert 'report["statistics"] = facts' in body, "没有用平台的数覆盖 LLM 编的"
    assert "不要输出任何统计数字" in body, "提示词没禁止 LLM 自己数"
    # 也得真把字段喂给它看，否则禁了它也判不了
    assert "无前置条件" in body and "无预期结果" in body


def test_没有api端点时不许列缺失的api():
    """apisTotal=0 却列出 9 个「缺失的 API」—— 那是凭空想的，还印在报告里。"""
    import inspect

    from app.services.ai import skill_executor

    src = inspect.getsource(skill_executor)
    i = src.index("tb-quality-review Skill")
    body = src[i:i + 9000]
    assert 'cov["missingApis"] = []' in body
    assert 'cov["apisTotal"] = facts["apisTotal"]' in body


def test_评审要取全量而不是被静默截断():
    """list_cases 内部 min(page_size, 100)，传 200 也只回 100。
    105 条的分支上报告顶上写「共 100 条」，那 5 条既没评审也没人知道被漏了。"""
    import inspect

    from app.services.ai import skill_executor

    src = inspect.getsource(skill_executor)
    i = src.index("tb-quality-review Skill")
    body = src[i:i + 9000]
    assert "while True:" in body and "page += 1" in body, "没有分页取全"
    assert "page_size=200" not in body, "还在用会被静默截断的写法"


def test_抽样时必须在报告里说明():
    """只评了前 50 条却按全量下结论，人会当成全量结论。"""
    import inspect

    from app.services.ai import skill_executor

    src = inspect.getsource(skill_executor)
    i = src.index("tb-quality-review Skill")
    body = src[i:i + 9000]
    assert 'sampledCases"] < facts["totalCases"]' in body
    assert "仅代表抽样部分" in body


# ── 导出 ────────────────────────────────────────────────────────────

def test_导出跟随页面筛选():
    """筛「待审核」点导出，导出来的还是全部 105 条 —— 人拿到文件不会发现，
    因为文件里看不出它本该是 41 条。

    页面用的是 lifecycleStatus / reviewStatus / uiStatus / apiStatus，
    导出端点一个都没接。
    """
    import inspect

    from app.api import cases as cases_api

    sig = inspect.signature(cases_api.export_cases_excel).parameters
    for name in ("lifecycle_status", "review_status", "ui_status", "api_status"):
        assert name in sig, f"导出没接 {name}"

    src = inspect.getsource(cases_api.export_cases_excel)
    for name in ("lifecycle_status=", "review_status=", "ui_status=", "api_status="):
        assert name in src, f"{name} 接了参数却没往下传"


def test_勾选了行就只导勾的():
    import inspect

    from app.api import cases as cases_api

    assert "case_ids" in inspect.signature(cases_api.export_cases_excel).parameters
    src = inspect.getsource(cases_api.export_cases_excel)
    assert "_Case.id.in_(picked)" in src


def test_前端导出和列表用同一套筛选口径():
    """两边各写一套的话，页面显示 41 条、导出 105 条，而且没人会发现。"""
    from pathlib import Path

    jsx = Path(__file__).resolve().parents[2] / "frontend/src/pages/cases/CaseManagement.jsx"
    src = jsx.read_text(encoding="utf-8")
    # 精确锚定：新增的 handleExportBackup 排在前面，模糊匹配会切错块
    exp = src[src.index("const handleExport = async"):]
    exp = exp[:exp.index("\n  }")]
    assert "selectedRowKeys.length" in exp, "没有优先导勾选的"
    for k in ("reviewStatus", "lifecycleStatus", "readyFilter"):
        assert k in exp, f"导出漏了 {k}"


# ── 导入：破坏性动作不能是默认 ──────────────────────────────────────

def test_没删也要如实报告有几条不在文件里():
    """"什么都没发生"和"有 3 条我没动"是两回事。人得知道有这回事，
    才谈得上决定要不要开同步删除。"""
    import inspect

    from app.services import import_service

    src = inspect.getsource(import_service.import_cases)
    assert '"notInFile"' in src and '"notInFileSample"' in src


# ── 导出/导入回环不许丢步骤预期 ─────────────────────────────────────

@pytest.mark.parametrize("steps", [
    [{"seq": 1, "action": "点新建", "expected": "弹出对话框"},
     {"seq": 2, "action": "提交", "expected": "提示成功"}],
    [{"seq": 1, "action": "只有动作"}],
    [{"seq": 1, "action": "混着来", "expected": "有预期"}, {"seq": 2, "action": "没预期"}],
    [],
])
def test_每步预期原样回环(steps):
    """**测行为，不测源码字符串。**

    上一版断言的是「导出函数的源码里出现了 ' → '」—— 埋雷把格式化改回只写
    action，测试照样绿，因为我自己写的注释里就有这个分隔符。
    这轮第二次踩"注释满足断言"了。

    这里直接把编码-解码跑一遍，比对进出是否一致。
    """
    from app.api.cases import steps_to_text, text_to_steps

    assert text_to_steps(steps_to_text(steps)) == steps


def test_只写动作就会丢预期_这是埋雷要抓的():
    """反向确认上面那条真的有效：换成只写 action 的旧写法，回环必然对不上。"""
    from app.api.cases import text_to_steps

    old_style = "1. 点新建\n2. 提交"
    got = text_to_steps(old_style)
    assert all("expected" not in s for s in got)
    assert got != [{"seq": 1, "action": "点新建", "expected": "弹出对话框"},
                   {"seq": 2, "action": "提交", "expected": "提示成功"}]


def test_回环解析的正反例():
    """真跑一遍解析：带箭头的要解出 expected，不带的只有 action。"""
    import io

    from openpyxl import Workbook

    from app.api.cases import _parse_excel_to_cases

    wb = Workbook()
    ws = wb.active
    ws.append(["用例ID", "标题", "模块", "测试步骤", "预期结果"])
    ws.append(["TC-1", "带预期", "M", "1. 点新建 → 弹出对话框\n2. 提交 → 提示成功", "整体预期"])
    ws.append(["TC-2", "无预期", "M", "1. 点新建\n2. 提交", "整体预期"])
    buf = io.BytesIO()
    wb.save(buf)

    got = {c["title"]: c["steps"] for c in _parse_excel_to_cases(buf.getvalue())}
    assert got["带预期"] == [
        {"seq": 1, "action": "点新建", "expected": "弹出对话框"},
        {"seq": 2, "action": "提交", "expected": "提示成功"},
    ], got["带预期"]
    assert got["无预期"] == [
        {"seq": 1, "action": "点新建"},
        {"seq": 2, "action": "提交"},
    ], got["无预期"]


# ── 评审结论落地：导入不删、Excel 说实话、备份不覆盖 ────────────────

def test_导入代码里不存在任何删除动作():
    """用 AST 查，不用字符串匹配。

    上一版断言 `"case.deleted_at" not in src`，埋雷时改成 `c.deleted_at = None`
    就绕过去了 —— 换个变量名断言就失效。这轮第三次被"源码文本断言"骗。
    这里遍历语法树：函数体内**任何**对 `.deleted_at` 的赋值都算违规。
    """
    import ast
    import inspect
    import textwrap

    from app.services import import_service

    tree = ast.parse(textwrap.dedent(inspect.getsource(import_service.import_cases)))
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if not isinstance(t, ast.Attribute):
                continue
            # 任何对 deleted_at 的赋值都算删除动作
            if t.attr == "deleted_at":
                bad.append((node.lineno, "deleted_at"))
            # folder_id 赋成 None 才是问题（清空归属，原值不留、还原不回去）；
            # 重新导入时按「模块」列改归属是正常更新，不能一并禁掉。
            elif t.attr == "folder_id" and isinstance(node.value, ast.Constant) \
                    and node.value.value is None:
                bad.append((node.lineno, "folder_id=None"))
    assert not bad, f"导入不该删除或清空归属：{bad}"

    src = inspect.getsource(import_service.import_cases)
    assert "sync_delete" not in src, "同步删除这条语义应该整个不存在"
    assert '"notInFile"' in src, "但要如实说有几条不在文件里"


def test_接口层没有同步删除参数():
    import inspect

    from app.api import cases as cases_api

    assert "sync_delete" not in inspect.signature(cases_api.import_cases).parameters


def test_excel不再有名存实亡的脚本列():
    """「脚本文件/脚本函数」257 条里只有 3 条有值。那不叫覆盖率低，
    叫误导 —— 读的人会以为平台在管脚本。换成三件套有无标记。"""
    import inspect

    from app.api import cases as cases_api

    src = inspect.getsource(cases_api.export_cases_excel)
    head = src[src.index("headers = ["):src.index("]", src.index("headers = ["))]
    assert "脚本文件" not in head and "脚本函数" not in head
    for col in ("接口场景", "UI脚本", "场景变量"):
        assert col in head, f"缺 {col} 标记列"


def test_excel必须说出自己缺了什么():
    """人拿到这张表默认会以为"用例就这些"。沉默的缺失比报错更伤人。"""
    import inspect

    from app.api import cases as cases_api

    src = inspect.getsource(cases_api.export_cases_excel)
    assert 'wb.create_sheet("说明")' in src, "文件里没写清缺什么"
    assert "这份文件里没有什么" in src
    assert "X-Export-Summary" in src, "没把数字给前端，页面上提示不出来"


def test_备份包里两个脚本不会互相覆盖():
    """**测行为，不测源码字符串。**

    上一版断言 `"tests/{code}/" in src`，埋雷把路径改回扁平后测试照样绿 ——
    因为下面兜底那行里还有同一个字符串。改成直接跑路径分配。

    CC 回推的脚本几乎都叫 test_ui.py / test_api.py，实测 8 个脚本
    压出来只剩 2 个文件。
    """
    from app.api.scripts import backup_path

    seen = set()
    paths = []
    for code in ("TC-登录认证-00001", "TC-登录认证-00002", "TC-项目管理-00001"):
        p = backup_path(code, "api", "test_api.py", seen)
        seen.add(p)
        paths.append(p)
    assert len(set(paths)) == 3, f"不同用例的同名脚本撞在一起了：{paths}"
    assert all("00001" in p or "00002" in p or "项目管理" in p for p in paths)


def test_同一用例多个同名脚本也不会覆盖():
    """同一条用例同一类型还可能有多个版本，编号目录也救不了，得再兜一层。"""
    from app.api.scripts import backup_path

    seen = set()
    got = []
    for _ in range(3):
        p = backup_path("TC-A-1", "ui", "test_ui.py", seen)
        seen.add(p)
        got.append(p)
    assert len(set(got)) == 3, got


def test_备份包有对照清单():
    """解压出来一堆 test_api.py，认不出哪个是哪条用例。"""
    import inspect

    from app.api import scripts as scripts_api

    assert "MANIFEST.tsv" in inspect.getsource(scripts_api.export_scripts)


def test_备份README必须说明跑不起来是预期的():
    """脚本的取值来自场景变量/全局引用/步骤提取物，都不在包里。
    不说清楚的话，人会以为备份坏了 —— 他缺的是环境，不是资产。
    另外必须写明凭据不在包里，且不提供"包含凭据"这种选项。
    """
    import inspect

    from app.api import scripts as scripts_api

    src = inspect.getsource(scripts_api.export_scripts)
    assert "跑不起来，这是预期的" in src
    assert "凭据一个字都不在这个包里" in src


# ── CC 闭环体检：跑整条链时抓到的 ────────────────────────────────────

@pytest.mark.parametrize("path,expect", [
    ("$.data.token", "tok"),      # ★ MCP 工具说明写的是 "jsonpath"，CC 照着写就是这个
    ("data.token", "tok"),
    ("$.data.items[0].id", "i1"),
    ("data.items[0].id", "i1"),
    ("$", None),                  # 整体
])
def test_提取路径要认jsonpath前缀(path, expect):
    """`variables_extract:{name:jsonpath}` —— 参数说明里写着 jsonpath，
    实现却只认 `data.token`。CC 写 `$.data.token` 会**静默**取不到值，
    报错落在下一步的「变量未解析」上，把人指向环境变量，而根因在上一步。
    实测在闭环体检里踩到了。
    """
    from app.services.api_test_runner import _extract_value

    body = {"data": {"token": "tok", "items": [{"id": "i1"}]}}
    got = _extract_value(body, path)
    assert (got == expect) if expect is not None else (got == body)


def test_提取失败要在当步报错_不要甩给下一步():
    """断言过了但没取到值 —— 原先只静默记 ok:false，这一步照样算 pass。
    后面每一步都报「变量未解析」，指错地方。"""
    import inspect

    from app.services import api_test_runner

    src = inspect.getsource(api_test_runner)
    assert "这一步的响应里没取到" in src, "提取失败没在当步报出来"
    assert "availableTopKeys" in src, "没告诉人响应里实际有哪些键"
    assert "**先往上看**" in src, "下游的变量未解析没指向上游提取"


def test_场景变量在所有执行路径都注册裸名():
    """抽屉和工具说明都写「UI 和接口共用同一份」，接口那边注了 `SV_x` 和裸名 `x`，
    UI 那边只注 `SV_x` —— CC 写 os.getenv("PROJ_NAME") 拿到空串，**还不报错**，
    表现成"填了个空名字"。实测踩到了。
    """
    import inspect

    from app.mcp.tools import ui_scripts
    from app.services.scenario_variable_service import add_bare_names

    env = {}
    add_bare_names(env, {"SV_RUN_ID": "r1", "SV_PROJ_NAME": "p-1"})
    assert env["SV_PROJ_NAME"] == "p-1"
    assert env["PROJ_NAME"] == "p-1", "裸名没注册"
    assert "RUN_ID" not in env, "SV_RUN_ID 是平台自己的，不该占用裸名"

    # 环境变量同名时不许被覆盖：环境说的是"这个环境是什么"，优先级更高
    env2 = {"PROJ_NAME": "来自环境"}
    add_bare_names(env2, {"SV_PROJ_NAME": "来自场景变量"})
    assert env2["PROJ_NAME"] == "来自环境"

    # 每条执行路径都得走这个函数，少一条就又出现"两边不一样"
    for mod in (ui_scripts,):
        assert "add_bare_names" in inspect.getsource(mod)


def test_执行服务里flaky_service是模块级导入():
    """函数内 import 只在那一个函数里有效。start_execution 用了却没导入，
    计划里只要有一条不是 executable 的用例就走到那个分支 → NameError
    把整个计划执行打死。实测 tb_run_plan 直接崩。
    """
    import ast
    import inspect

    from app.services import execution_service

    tree = ast.parse(inspect.getsource(execution_service))
    top = {a.asname or a.name.split(".")[-1]
           for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
           for a in n.names}
    assert "flaky_service" in top, "得是模块级导入，不能藏在某个函数里"


def test_建计划和跑计划都要说清哪些不会执行():
    """进回归的门槛是「该维度 = 可执行」，而这一步只有人能推（CC 不改状态是红线）。
    不说的话：计划建出来、跑起来、报告里一条 pending —— 三步都在暗示"它会跑"，
    而它一条都没跑。实测踩到了。
    """
    import inspect

    from app.mcp.tools import plans

    create = inspect.getsource(plans.create_plan)
    assert "blockedCases" in create and "willRun" in create
    assert "只能由人在平台上确认" in create

    run = inspect.getsource(plans.run_plan)
    assert "skippedAsManual" in run and "willRun" in run


def test_不会执行的判据和执行器保持一致():
    """这里报"会跑"、执行器却跳过，等于换个地方说谎。两边必须同一套判据。"""
    import inspect

    from app.mcp.tools.plans import _not_executable
    from app.services import execution_service

    mine = inspect.getsource(_not_executable)
    theirs = inspect.getsource(execution_service._will_run_automated)
    for key in ('"executable"', "script_ref_file", "automation_status"):
        assert key in mine and key in theirs, f"两边判据对不上：{key}"
