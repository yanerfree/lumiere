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
    """只带状态字段的替身 —— apply_case_status / sync_review_status 就只碰这几个。"""

    review_status = None
    target_level = "full"
    steps = [{"seq": 1}]

    def __init__(self, **kw):
        self.ui_status = kw.get("ui_status", "draft")
        self.api_status = kw.get("api_status", "draft")


# ── 维度状态推进：接口这一维此前整个是死的 ──────────────────────────

def test_接口跑通也会推进状态():
    """原先这里写死 `script_type != "ui"` 直接 return。

    后果不是"少了个功能"，是**页面说假话**：接口场景跑通 69 次，api_status
    还停在 debugging，批量执行就报「0 个包含可执行脚本」——它有脚本。
    """
    case = _Case(api_status="debugging")
    apply_case_status(case, "api", "passed", REGRESSION)
    assert case.api_status == "completed"
    # 那个重复的 api_scenario_status 已删 —— 见 test_不许再写第二份维度状态


def test_接口跑挂_回归才打回_调试不打回():
    """和 UI 同一条纪律：调试是"我正在试"，试挂了不代表用例坏了。"""
    debugging = _Case(api_status="completed")
    apply_case_status(debugging, "api", "failed", DEBUG)
    assert debugging.api_status == "completed", "调试失败不该把状态打回去"

    regression = _Case(api_status="completed")
    apply_case_status(regression, "api", "failed", REGRESSION)
    assert regression.api_status == "debugging"


def test_两个维度互不串台():
    """推 api 不能顺手改了 ui —— 用 f-string 拼属性名最容易出这种错。"""
    case = _Case(ui_status="completed", api_status="debugging")
    apply_case_status(case, "api", "passed", REGRESSION)
    assert case.ui_status == "completed"


def test_不认识的类型直接跳过():
    case = _Case()
    apply_case_status(case, "manual", "passed", REGRESSION)
    assert case.ui_status == "draft" and case.api_status == "draft"


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

    cid = "11111111-1111-1111-1111-111111111111"   # caseId 现在必填，见下一条
    assert GenerateRequest(case_id=cid, on_existing="append").on_existing == "append"
    assert GenerateRequest(case_id=cid, on_existing="replace").on_existing == "replace"
    assert GenerateRequest(case_id=cid).on_existing is None
    with pytest.raises(Exception):
        GenerateRequest(case_id=cid, on_existing="drop")


def test_编排生成必须指定用例():
    """`/generate` 的 caseId 从可选变必填（2026-08-15）。

    可选是为了服务已下线的「接口测试」模块那个生成弹窗。留着的话，不传 caseId
    会一路走到插库才撞 source_case_id 的非空约束 —— 报出来是 500 和一句
    IntegrityError，调用方看不出自己少传了什么。声明成必填 = 一条带字段名的 422。
    """
    from app.api.api_test import GenerateRequest

    with pytest.raises(Exception):
        GenerateRequest(api_info="POST /x")


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
    """MCP 的 `_not_executable` 和执行器必须用同一份判据 —— 上一版各写各的：
    一边只看状态说"1 条会跑"，执行器还要求有产物，跑起来变成"0 条会跑"。
    现在两边都只看**有没有产物**，而且都复用 `_has_new_style_script`。
    """
    from app.mcp.tools import plans

    body = _code_of(plans, "_not_executable")
    assert "_has_new_style_script" in body, "没复用执行器那个判据函数"
    for dim in ("api_status", "ui_status"):
        assert dim not in body, f"又在看 {dim} 了 —— 判据该是「有没有产物」"

# ── 铺开体检抓到的：接口场景进不了回归 ──────────────────────────────

@pytest.mark.asyncio
async def test_接口场景也算可执行产物():
    """回归执行器此前只认 `scripts` 表的 api 脚本，而 MCP
    `tb_sync_orchestrated_scenario` 回推的是 `api_test_scenarios`。

    实测：全平台 8 条有接口场景的用例，**0 条**有 api 脚本 ——
    CC 这条链的接口产物一条都进不了计划回归，只能即席跑、不进通过率。
    而建计划时还说"这条会执行"。
    """
    import inspect

    from app.engine.tasks import adhoc_execution

    src = inspect.getsource(adhoc_execution._has_new_style_script)
    assert "ApiTestScenario" in src, "只认 scripts 表的话，回推的接口场景永远进不了回归"
    assert "source_case_id" in src


def test_接口场景的id不许塞进脚本外键():
    """script_runs.script_id 是 scripts 表的外键。把 api_test_scenarios 的 id
    塞进来会撞外键 —— 而且是在**记账**阶段撞：执行明明成功了，整次计划被打死。
    """
    from app.engine.tasks.adhoc_execution import _script_fk
    from app.models.api_test import ApiTestScenario

    class _S:
        id = "script-id"

    # 必须给它一个 id —— 不给的话两种实现都返回 None，埋雷不会红（第一版就这样）
    sc = ApiTestScenario()
    sc.id = "scenario-id"
    assert _script_fk(sc) is None, "接口场景的 id 不该塞进 scripts 外键"
    assert _script_fk(None) is None
    assert _script_fk(_S()) == "script-id", "真脚本还是要记上"


def test_建计划的判据直接复用执行器():
    """上一版两边各写各的：这里只看状态说「1 条会跑」，执行器还要求有可执行产物，
    跑起来变成「0 条会跑」。两个工具当场自相矛盾。"""
    import inspect

    from app.mcp.tools import plans

    src = inspect.getsource(plans._not_executable)
    assert "_has_new_style_script" in src, "没复用执行器的判据，迟早又不一致"


def test_轨迹里印实际URL不是模板():
    """打印 ${BASE_URL} 等于让人自己脑补解析结果，而出问题时最想看的就是真实地址。"""
    import inspect

    from app.engine.tasks import adhoc_execution

    src = inspect.getsource(adhoc_execution._run_orchestrated_scenario)
    assert '(resp.get("request") or {}).get("url")' in src


def test_合成uuid不算写死资源id():
    """负向测试要一个肯定不存在的 id（全 0/全 f）。对这种写法报警，
    等于逼人改掉正当的负向用例，或者学会忽略告警 —— 后者更糟。"""
    from app.mcp.tools.sync import _is_synthetic_uuid

    for v in ("00000000-0000-0000-0000-000000000000",
              "00000000-0000-0000-0000-0000000000ff",
              "ffffffff-ffff-ffff-ffff-ffffffffffff"):
        assert _is_synthetic_uuid(v), v
    for v in ("9912c051-3a5c-4163-ac06-2a442f69a337",
              "b1dca224-ae93-4f42-bd9a-02370cb37583"):
        assert not _is_synthetic_uuid(v), v


def test_项目详情有GET路由():
    """同一路径上 PUT / DELETE 都在，唯独没有 GET —— `GET /api/projects/{id}`
    返回 405。写接口测试的人会理所当然假设它存在（实测第一次就撞上），
    而 405 比 404 更难懂。服务层的 get_project 一直都有，缺的只是这一层。
    """
    from fastapi.routing import APIRoute

    from app.main import app

    got = {m for r in app.routes if isinstance(r, APIRoute)
           and r.path == "/api/projects/{project_id}" for m in r.methods}
    assert {"GET", "PUT", "DELETE"} <= got, f"实际只有 {got}"


def test_自动拆步骤不编预期():
    """填「操作完成，页面状态更新」正是入库门禁要拦的模糊词，平台自己注进去的：
    人写这句会被拒，平台写就通过。而且看起来像填了、实际什么都没说，比空着更糟。
    """
    from app.mcp.tools.test_cases import (
        _FUZZY_WORDS, _split_coarse_steps, _split_warnings,
    )

    out = _split_coarse_steps([
        {"seq": 1, "action": "填入名称，点击确定", "expected": "出现「创建成功」提示"},
    ])
    assert len(out) == 2
    assert out[0]["expected"] == "", "中间步骤不该被编一个预期"
    assert out[1]["expected"] == "出现「创建成功」提示", "最后一步保留原预期"
    for s in out:
        for w in _FUZZY_WORDS:
            assert w not in (s["expected"] or ""), f"平台自己注了模糊词 {w}"
    # 而且要告诉回推方哪几步需要补
    assert _split_warnings(out), "留空了却不说，等于偷偷改了人的输入"


# ── 执行崩了以后，卡住的计划得放出来 ────────────────────────────────

def _handlers_calling(func_src: str, func_name: str, callee: str) -> list[str]:
    """func_name 的哪些 except 分支里调了 callee。

    用 AST 而不是搜文本：这一版的说明注释里就写着函数名，`in src` 那种断言
    会被注释直接喂饱（这个坑本轮已经踩过三次）。
    """
    import ast

    tree = ast.parse(func_src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func_name), None)
    assert fn is not None, f"没找到函数 {func_name}"
    hit = []
    for h in (n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)):
        called = {c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", "")
                  for c in ast.walk(h) if isinstance(c, ast.Call)}
        if callee in called:
            hit.append(ast.unparse(h.type) if h.type else "bare except")
    return hit


def test_执行超时和崩溃两条路都要放开计划():
    """执行是**进程内**的后台任务。它崩了/超时了如果不主动放，计划就停在
    executing，而 start_execution 只收 draft/completed/paused —— 这个计划
    再也触发不了。人工出口只剩「终止」，那会把没跑的用例全记成「跳过」，
    等于拿一次假结果换回一个能用的计划。
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app/engine/tasks/execution.py").read_text()
    assert _handlers_calling(src, "run_automated_execution", "_release_stuck"), "超时那条路没放"
    assert _handlers_calling(src, "_run_execution_inner", "_release_stuck"), "崩溃那条路没放"

    adhoc = (Path(__file__).resolve().parents[1] / "app/engine/tasks/adhoc_execution.py").read_text()
    # 批量执行没有计划，但报告一样会卡在 running / 没有 completed_at
    assert _handlers_calling(adhoc, "run_adhoc_execution", "_close_broken_report"), "批量超时没收口"
    assert _handlers_calling(adhoc, "_run_adhoc_inner", "_close_broken_report"), "批量崩溃没收口"


def test_释放用的是独立连接():
    """崩溃现场那个 session 在 flush 失败之后事务是脏的，再拿它写，
    恢复动作会跟着一起回滚 —— 看着调了，实际没生效。
    """
    import ast
    import inspect

    from app.services import stuck_recovery

    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(stuck_recovery)))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "release_execution")
    calls = {getattr(c.func, "id", "") or getattr(c.func, "attr", "") for c in ast.walk(fn) if isinstance(c, ast.Call)}
    assert "create_async_engine" in calls, "复用了崩溃现场的 session，恢复会被一起回滚"
    assert "dispose" in calls, "自己开的引擎不关，连接池会漏"


def test_看门狗不按计划年龄扫():
    """**这条是反向守卫，比正向更重要。**

    reopen_plan（重新打开已完成的计划）和 resume_plan（恢复暂停的计划）也会把
    状态置成 executing 且不跑任何后台任务，两者都不更新 executed_at。
    一旦看门狗改成"按计划停在 executing 多久"来扫，用户前脚点「恢复」，
    它后脚就把计划又收回 completed，而且悄无声息。

    判据只能是 test_report_scenarios.status == 'running'：这个值只有两个执行器
    在真跑某条用例的那几秒里写，写的同时一定写 started_at。
    """
    from app.services import stuck_recovery

    body = _code_of(stuck_recovery, "sweep_orphaned")  # 剥掉 docstring 再断言
    assert "TestReportScenario.status" in body and "'running'" in body, "扫描判据不是 running 行"
    assert "started_at" in body and "cutoff" in body, "没有年龄门槛"
    # 计划年龄不能成为独立的扫描入口
    assert "Plan.executed_at" not in body, (
        "按 executed_at 扫计划会把用户刚点「恢复」的计划又收回去")


def test_年龄门槛必须盖过一次执行的上限():
    """门槛低于执行上限的话，扫的就不是"死了的"，是"还在跑的"。

    刚踩过一次真事：误起了第二个后端进程，它绑不上端口退出了，但 lifespan
    已经跑过 —— 也就是说它的看门狗对着同一个库扫过一遍。门槛不够，
    它就会把真后端正在跑的执行给收了。
    """
    from app.engine.tasks.execution import _EXECUTION_TIMEOUT
    from app.services.stuck_recovery import STUCK_AFTER

    assert STUCK_AFTER.total_seconds() > _EXECUTION_TIMEOUT, (
        f"门槛 {STUCK_AFTER.total_seconds()}s 没盖过执行上限 {_EXECUTION_TIMEOUT}s")


def test_崩溃的报告要重算统计而不是留一片0():
    """只把行改成 error、不重算汇总的话，报告页显示 0 通过 0 失败、通过率空白，
    和"这次啥也没跑"长得一模一样，用户分不出是空报告还是崩了的报告。
    """
    import ast
    import inspect

    from app.services import stuck_recovery

    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(stuck_recovery)))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "close_report")
    calls = {getattr(c.func, "id", "") or getattr(c.func, "attr", "") for c in ast.walk(fn) if isinstance(c, ast.Call)}
    assert "recompute_report_stats" in calls, "没重算统计，崩掉的报告会显示成空报告"


def test_统计口径只有一份():
    """正常收尾和崩溃恢复要用同一个函数算，否则两边口径迟早漂移
    （flaky 进不进分母这种事，改一处漏一处根本看不出来）。
    """
    import ast
    import inspect

    from app.services import execution_service

    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(execution_service)))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "complete_execution")
    calls = {getattr(c.func, "id", "") or getattr(c.func, "attr", "") for c in ast.walk(fn) if isinstance(c, ast.Call)}
    assert "recompute_report_stats" in calls, "正常收尾自己算了一份，和恢复那份会漂移"


def test_执行提前返回也要收尾():
    """**这条是实测撞出来的，不是想出来的。**

    `_execute` 里有两条 return 发生在 complete_execution 之前：「无用例可执行」
    和「创建沙箱失败」。走那儿出来，计划一直停在 executing，报告永远没有
    completed_at —— 而且看门狗抓不到：一行都没进过 running。
    随手跑一个现成计划就撞上了第二条（项目上留着个过期的 script_base_path）。

    所以判据放在**出口**上，不逐条补：出来了、计划还在 executing，就是漏了。
    逐条补的话，下一个人加第三条 return 时照样漏。
    """
    import ast
    from pathlib import Path

    for f, fn_name in (("app/engine/tasks/execution.py", "_run_execution_inner"),
                       ("app/engine/tasks/adhoc_execution.py", "_run_adhoc_inner")):
        tree = ast.parse((Path(__file__).resolve().parents[1] / f).read_text())
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == fn_name)
        # 兜底必须在 try 的正常出口上，不能只写在 except 里
        normal = [n for n in fn.body if isinstance(n, ast.Try)]
        assert normal, f"{fn_name} 没有 try"
        called = {getattr(c.func, "id", "") or getattr(c.func, "attr", "")
                  for c in ast.walk(normal[0]) if isinstance(c, ast.Call)}
        assert "ensure_finalized" in called, f"{f} 的正常返回路径没有兜底收口"


def _code_of(module, fn_name: str) -> str:
    """函数的**代码**，不含 docstring。

    直接 `ast.unparse(fn)` 会把 docstring 一起吐出来 —— 而说明里往往正好写着
    要断言的那个串（本轮已经被这么骗过四次）。剥掉再断言，守卫才是守卫。
    """
    import ast
    import inspect

    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(module)))
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn_name)
    body = fn.body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str)):
        body = body[1:]
    return "\n".join(ast.unparse(n) for n in body)


def test_没轮到就崩的自动化行记成skipped而不是等人录():
    """崩的时候还没轮到的自动化用例，状态是 pending。

    把它算成"待人工录入"，计划就会挂上 pending_manual，页面提示用户去录一批
    他根本没打算手动做的用例；算成 failed 又是冤枉用例（它压根没跑）。
    只有 skipped 是对的：不进通过率分母，且写明为什么没跑。
    真正等人录的手动行（execution_type='manual'）必须原样留着。
    """
    from app.services import stuck_recovery

    body = _code_of(stuck_recovery, "close_report")
    assert "'automated'" in body and "execution_type" in body, (
        "收 pending 行时没有区分自动化/手动，会把等人录的手动用例一起判死")
    assert "'skipped'" in body, "没跑过的行不该记成 failed/error"

    rel_body = _code_of(stuck_recovery, "release_plan")
    assert "'manual'" in rel_body, (
        "pending_manual 的判据必须只数手动行，否则崩一次就挂上假的待录入")


def test_沙箱失败要告诉人去哪儿改():
    """这句话会原样落进报告的每一行。

    原文案只有「目标路径不是有效的 Git 仓库」——看的人不知道说的是项目配置，
    更不知道去哪个页面改，只会以为自己的用例写坏了。
    """
    from pathlib import Path

    for f in ("app/engine/tasks/execution.py", "app/engine/tasks/adhoc_execution.py"):
        src = (Path(__file__).resolve().parents[1] / f).read_text()
        assert "脚本库路径" in src and "项目设置" in src, f"{f} 的沙箱失败文案没说去哪儿改"


def test_一条没跑的报告不许显示红色0通过率():
    """实测截图为证：沙箱建不起来导致整批没开跑，库里 `pass_rate` 是 NULL，
    页面却画了个**鲜红的 0%** —— 看着像全挂了，其实一条都没跑。

    根因是那个环没用外面按规范口径算好的 rate，自己拿 `passed / total` 又算了
    一遍，而这个分母含 skipped。同一份文件里下面几行还写着"skipped 不进分母"。
    """
    from pathlib import Path

    jsx = Path(__file__).resolve().parents[2] / "frontend/src/pages/report/ReportDetail.jsx"
    src = jsx.read_text(encoding="utf-8")
    ring = src[src.index("function PassRateRing"):]
    ring = ring[:ring.index("\n}")]
    assert "passed / total" not in ring.replace(" ", " "), "环又自己拿含跳过的分母算通过率了"
    assert "rate != null" in ring, "没判 rate 为空，算不出来的通过率会被画成 0%"
    assert "'未执行'" in ring, "算不出来时要说「未执行」，不能默认成 0%"

    # 失败率同理：分母为 0 时给「-」，不给「0.0%」
    line = next(ln for ln in src.splitlines() if "const failRate" in ln)
    assert "null" in line, f"分母为 0 时失败率仍写死了数字：{line.strip()}"


def test_没跑的原因用户读得到():
    """那句"去哪儿改"写在后半句，而列表那一列只有 200px，必被截断。

    收起时得有 Tooltip，展开时得有完整段落 —— 否则我把提示写得再好，
    用户也只能看到「项目「测试平台」配…」。
    """
    from pathlib import Path

    jsx = Path(__file__).resolve().parents[2] / "frontend/src/pages/report/ReportDetail.jsx"
    src = jsx.read_text(encoding="utf-8")
    assert "<Tooltip title={s.errorSummary}" in src, (
        "截断的原因没被 Tooltip 包住（title 必须是完整原文），后半句永远读不到")

    # 展开区：skipped 也要给出原因，不能只认 failed
    assert "status === 'skipped'" in src[src.index("{/* 失败原因"):src.index("{/* 失败原因") + 400], (
        "展开区只认 failed，被跳过的用例展开后什么都不说")


# ── 回推时间筛选：看这一轮 CC 干了什么 ──────────────────────────────

def test_回推筛选要能把老docgen产物排除掉():
    """判据必须是 `source='ai' 且没有生成批次`。

    只判 source='ai' 的话，平台侧那条「喂需求文档批量产用例」流水线的 49 条产物
    会一起混进来 —— 两边用的是**同一个 source 值**，靠 generation_task_id 才分得开
    （它的产物挂着批次，CC 回推的没有）。实测：API自测项目 72 条全是老产物，
    判据写错的话「近 7 天回推的」会把它们全捞出来。
    """
    from app.services import case_service

    body = _code_of(case_service, "list_cases")
    assert "pushed_within" in body, "筛选没接上"
    assert "generation_task_id" in body, (
        "只按 source 判的话，老 docgen 产物会混进「CC 回推」里")
    assert "Case.source == 'ai'" in body


def test_回推筛选只认认识的值():
    """乱传一个值应当等同于不筛，而不是筛出空列表 ——
    后者会让人以为"这一轮什么都没推"，比报错还糟。
    """
    from app.services import case_service

    body = _code_of(case_service, "list_cases")
    assert "('today', 'week')" in body, "没有白名单，未知值的行为不可预期"


def test_平台侧AI生成用例的入口都撤了():
    """两个入口：侧边栏菜单项，和用例管理页顶部那个 primary 按钮。
    只撤一个的话，用户照样点得到 —— 而那条路一个月没人用、8 个批次卡了 3 个。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "frontend/src"
    app = (root / "App.jsx").read_text(encoding="utf-8")
    assert "scenario-gen" not in app, "路由/菜单里还留着入口"

    cases = (root / "pages/cases/CaseManagement.jsx").read_text(encoding="utf-8")
    assert "scenario-gen?taskId=new" not in cases, "用例页顶部那个按钮还在"
    # 别误伤：「从接口生成」是另一条路（只有接口文档时用），它保留
    assert "从接口生成" in cases


def test_卡片上的复制按钮不能顺手改勾选():
    """整张档位卡片是勾选控件，复制图标叠在它上面。

    不 stopPropagation 的话，用户点「复制」会连带把这一档勾上或取消掉 ——
    他以为自己只是复制了段文字，实际改了这个项目的 CC 工具范围。
    """
    from pathlib import Path

    jsx = (Path(__file__).resolve().parents[2]
           / "frontend/src/pages/settings/MCPTools.jsx").read_text(encoding="utf-8")
    block = jsx[jsx.index("{onCopyPrompt && ("):]
    block = block[:block.index("</Tooltip>")]
    # **注释行必须先剥掉。** 上面那段说明里就写着"必须 stopPropagation"，
    # 直接 `in block` 会被自己的注释喂饱 —— 埋雷把 onClick 改掉，守卫照样绿。
    # 这个坑本轮已经踩到第五次了，每次都是"断言的那个串正好出现在解释它的注释里"。
    code = "\n".join(ln for ln in block.splitlines() if not ln.strip().startswith("//"))
    assert "e.stopPropagation()" in code, "点复制会顺手改掉这个项目的 CC 工具范围"


# ── CC 得能改自己写错的东西 ──────────────────────────────────────────

def test_改用例的同名检查要排除自己():
    """不排除自己的话，原样保存一条已存在的用例会被判成「和自己标题完全一样，
    重复入库」——**这条用例就永远改不动了**。而改用例正是 CC 修正自己笔误的
    唯一途径（实测：标题里混进一个俄语词、步骤 8 写的是想当然的页面行为）。
    """
    import inspect

    from app.services import intake_gate

    sig = inspect.signature(intake_gate.check_one)
    assert "exclude_case_id" in sig.parameters, "门禁没法排除自己，改用例必被自己拦住"
    body = _code_of(intake_gate, "check_one")
    assert "Case.id != exclude_case_id" in body, "参数收了但查询没用上"


def test_改用例不许碰状态():
    """红线：状态由平台按执行事实推进、或由人拍板。

    CC 想说「这条能跑了」，就去跑一遍让执行结果说话 —— 让它自己改状态，
    等于自证。参数里根本不收这三个字段，传了会被 MCP 层直接拒。
    """
    import inspect

    from app.mcp.tools.test_cases import update_case

    params = set(inspect.signature(update_case).parameters)
    for forbidden in ("ui_status", "api_status", "manual_status", "review_status"):
        assert forbidden not in params, f"改用例居然能改 {forbidden} —— 破了红线"


def test_改了步骤要说预期确认已失效():
    """`update_case` 服务层会在步骤/预期变化时清掉「预期已确认」标记。
    清了不说，CC 以为还确认着，下次报告里会写「预期已跟用户确认过」——
    那是句假话。
    """
    from app.mcp.tools import test_cases

    body = _code_of(test_cases, "update_case")
    # 钉住**判断本身**，不是"函数里出现过这几个字" —— 那两个串在函数别处也有，
    # 把整个 if 改成 False 都不会红（本轮第七次踩这个坑）。
    assert "'steps' in changed or 'expectedResult' in changed" in body, (
        "没在步骤/预期变化时给出提醒")
    assert "预期已确认" in body, "提醒里没说清失效的是哪个标记"


def test_改用例过的是同一套门禁():
    """建用例拦模糊词、拦同名、自动拆粗步骤；改用例不拦的话，
    从建那条路堵住的东西会从改这条路灌进来。
    """
    from app.mcp.tools import test_cases

    body = _code_of(test_cases, "update_case")
    for fn in ("_validate_case_quality", "check_one", "_split_coarse_steps"):
        assert fn in body, f"改用例没过 {fn}，门禁被绕开了"


# ── 失败得说清为什么：CC 反馈「没法调试」的三条通道 ────────────────

def test_断言求值要记下实际值():
    """实际值在求值时**本来就算出来了**，以前在最后 append 时被丢掉，
    于是断言明细永远是 `actual: null`。

    「期望 success」说不出「实际是 pushing」的话，就分不清这是**抢跑**
    （配置还在下发中）还是**真错**。实测这一条正是 CC 卡住的地方。
    """
    from app.services.api_test_runner import _check_assertions

    out = _check_assertions(
        [{"type": "body_field", "field": "data.status", "operator": "==", "value": "success"}],
        200, {"data": {"status": "pushing"}},
    )
    assert out[0]["passed"] is False
    assert out[0]["actual"] == "pushing", f"实际值没记下来：{out[0]}"

    ok = _check_assertions([{"type": "status", "value": 200}], 404, {})
    assert ok[0]["actual"] == 404, "状态码断言也要带实际值"


def test_失败原因写成人话而不是内部类型名():
    """CC 拿到 `断言未通过: body_field` 只知道"某个字段不对"，
    不知道哪个字段、期望什么、实际什么 —— 只能猜或者绕过。
    """
    from app.services.api_test_runner import describe_assertion, failure_detail

    a = {"type": "body_field", "field": "data.status", "operator": "==",
         "value": "success", "actual": "pushing", "passed": False}
    desc = describe_assertion(a)
    assert "响应字段" in desc and "data.status" in desc and "success" in desc
    assert "body_field" not in desc, "内部类型名漏到界面上了"

    d = failure_detail([a], None)
    assert "pushing" in d["why"], f"没说实际值：{d['why']}"
    assert d["failedAssertions"][0]["actual"] == "pushing"


def test_接口执行给CC的返回要带失败原因():
    """之前每一步只回 {step,status,statusCode,duration}，于是 CC 看到
    「status=fail / statusCode=200」——200 却失败，无从查起。
    而 error / assertions / responseBody 本来就在事件里带着。

    同时钉住**通过的步骤保持精简**：十几步全带响应体，CC 的 context
    会被这一个返回值吃掉。
    """
    from app.mcp.tools import api_tests

    body = _code_of(api_tests, "run_api_test")
    # 钉调用表达式，不是"函数里出现过这个名字" —— import 行也含这个名字，
    # 只把调用删掉、import 留着，守卫照样绿（本轮第八次踩这个坑）。
    assert "row.update(failure_detail(" in body, "失败步骤没带失败原因"
    assert "responseSample" in body, "失败步骤没带响应片段"
    assert "if row['status'] == 'fail'" in body, (
        "没有区分通过/失败 —— 要么都不带（没法查），要么都带（撑爆 context）")
    assert "precheck_result" in body, "共享资源探测结果没回给 CC"


# ── 项目须知：被测系统的行为知识 ──────────────────────────────────

def test_写死告警不报枚举值():
    """`service_type="api"`、`protocol="http"` 是**被测系统的契约**，写死才是对的。
    把它们标成"疑似写死"，等于在教人把常量也做成变量。

    误报的代价不只是噪音：警告多了人就不看了，真正写死的 id 反而被淹掉。
    """
    from app.mcp.tools.sync import _looks_hardcoded

    for field, val in (("service_type", "api"), ("config.protocol", "http"),
                       ("load_balance.strategy", "round_robin"), ("status", "active")):
        assert not _looks_hardcoded(val, field), f"{field}={val} 是枚举值，不该报"
    # 反向：同样的字符串换到非枚举字段上，仍然要报
    assert _looks_hardcoded("api", "name"), "name=api 是业务数据，该报"
    assert _looks_hardcoded("httpbin-upstream", "upstreamName")
    assert _looks_hardcoded("b3f1c2d4-1111-2222-3333-444455556666", "upstream_id")


def test_项目须知正文超长是拒不是截断():
    """这些条目每次生成都会整个喂给下一轮 CC，长了直接挤占它的 context。
    截断会把最关键的后半句悄悄吃掉 —— 所以明着拒，并说清该怎么压。
    """
    from app.mcp.tools import project_notes

    assert project_notes.MAX_CONTENT == 200
    body = _code_of(project_notes, "add_project_note")
    # 钉条件本身。只找 "MAX_CONTENT" 的话，错误消息的 f-string 里也有它，
    # 把整个 if 改成 False 都不会红（本轮第九次踩这个坑）。
    assert "len(content) > MAX_CONTENT" in body, "没有长度上限"
    assert "[:MAX_CONTENT]" not in body and "[:200]" not in body, (
        "截断了 —— 该拒的时候别偷偷砍掉后半句")


def test_CC不能写评审反馈那一类():
    """review_feedback 是 AI 评审对**用例**的意见，项目须知记的是**被测系统的事实**。
    混在一起，读的人分不清哪条是"系统就是这样"、哪条是"这条用例写得不好"。
    """
    from app.mcp.tools.project_notes import _CC_CATEGORIES

    assert "review_feedback" not in _CC_CATEGORIES
    assert "api_note" in _CC_CATEGORIES


def test_知识库接口是路径参数不是查询参数():
    """前端写成 `/knowledge?projectId=` 会 404，而拦截器把它吞了 ——
    页面只是**空着**，不报错。实测就这么骗过了一次自测：后端 9/9 全过、
    页面上一条都没有。
    """
    from pathlib import Path

    from fastapi.routing import APIRoute

    from app.main import app

    paths = {r.path for r in app.routes if isinstance(r, APIRoute)}
    assert "/api/projects/{project_id}/knowledge" in paths

    jsx = (Path(__file__).resolve().parents[2]
           / "frontend/src/pages/settings/AutomationData.jsx").read_text(encoding="utf-8")
    assert "/knowledge?projectId=" not in jsx, "又写成查询参数了，页面会静默空白"
    assert "/projects/${projectId}/knowledge" in jsx


# ── 状态：六态存储、三档展示、人一键发布 ──────────────────────────

def test_发布只能人来做_CC碰不到():
    """`executable` = 能进回归。这条线只有人能跨 —— CC 说「能跑了」等于自证。

    但人也不该为此逐条开详情页：实测 257 条用例里只有 1 条到了可执行，
    整个回归池等于空的，就是被这个摩擦卡住的。所以给批量入口，不给 CC 入口。
    """
    from app.schemas.case import BatchCaseRequest

    actions = BatchCaseRequest.model_fields["action"].annotation.__args__
    assert "publish" in actions and "unpublish" in actions

    from app.mcp.tools.test_cases import update_case
    import inspect
    assert "ui_status" not in inspect.signature(update_case).parameters





def test_发布数只数真改了的():
    """外面那句 `succeeded += 1` 数的是"处理了几条"。拿它当发布数，
    空维度也会报「已发布 1 条，能进回归了」—— 而那一维根本没东西。
    实测被自己的反向用例照出来过。
    """
    from app.services import case_service

    body = _code_of(case_service, "batch_cases")
    assert "touched" in body and "if not touched:" in body, "发布数还是在数「处理了几条」"




def test_写步骤要推进manual_status():
    """没有它，manual_status 永远停在 not_started（全库当时 255 条都是），
    显示层只能靠派生盖住 —— 而派生就是对不上的根源。"""
    from app.services import case_service

    assert hasattr(case_service, "sync_manual_status"), "没有推进 manual_status 的入口"
    # ⚠ 不能用 _code_of：create_case 带 @audit_log 装饰器，inspect 拿到的是 wrapper。
    # 直接读源码文本，并确认两个入口各自都调了（不是只调了一处）。
    import inspect
    src = inspect.getsource(case_service)
    assert src.count("sync_manual_status(case)") >= 2, \
        f"建用例和改步骤两处都要同步，实际只有 {src.count('sync_manual_status(case)')} 处"
    # 落在 create 的 session.add 之前 / update 的 case.steps 赋值之后
    assert "sync_manual_status(case)\n    session.add(case)" in src, "建用例时没同步"
    assert "case.steps = data.steps\n        sync_manual_status(case)" in src, "改步骤时没同步"


def test_审核列默认收起():
    """review_status 只对平台侧 AI 流水线那批用例有意义（那条路已下线，
    47 条停在待审、只有 1 条被点过通过）。和三件套维度状态并排显示，
    人分不清该看哪个。
    """
    from pathlib import Path

    jsx = (Path(__file__).resolve().parents[2]
           / "frontend/src/pages/cases/CaseManagement.jsx").read_text(encoding="utf-8")
    block = jsx[jsx.index("key: 'reviewStatus'"):]
    block = block[:block.index("\n")]
    assert "defaultVisible: false" in block, "审核列又默认显示了"


# ── 抢跑假红：步骤级等待/重试 ───────────────────────────────────────

def test_步骤有等待和重试字段():
    """被测系统的配置下发是异步的（实测网关 0.06~0.5s 且抖动），而步骤之间只隔
    几毫秒 —— 「发布完立刻打网关」必然抢跑。本轮真跑复现过两次。

    **假红比漏测更毒**：它让整份报告不可信，人看两次就不看了。
    """
    from app.models.api_test import ApiTestStep

    for col in ("wait_ms", "retry_timeout_ms", "retry_interval_ms"):
        assert hasattr(ApiTestStep, col), f"步骤上没有 {col}"


def test_重试是重发整步而不是只等一下():
    """固定等待要么白等要么不够，换台机器就崩 —— CC 自己的原话是「很脆」。
    重试等的是「它真的好了」，所以必须**重发请求并重新断言**，不是 sleep 完拉倒。
    """
    from app.services import api_test_runner

    body = _code_of(api_test_runner, "run_step")
    assert "run_single_step" in body and "while" in body, "没有重发循环，只是等一下"
    assert "retry_timeout_ms" in body and "monotonic" in body, "没有超时上界"


def test_重试成功要说重试了几次():
    """一次就过和试了 8 次才过不是一回事 —— 后者说明这个窗口快不够了，
    早晚变成偶发红。吞掉这个信息就是把「快要坏了」藏起来。
    """
    from app.services import api_test_runner

    body = _code_of(api_test_runner, "run_step")
    # 钉只在**成功路径**出现的那句。只找"重试"两个字的话，失败消息里也有，
    # 把成功那行删掉守卫照样绿（本轮第十一次）。
    assert "次后通过" in body, "重试成功了却不说试了几次"
    assert "attempts" in body


def test_写操作开重试要警告():
    """重试会**重发请求**。POST 重发就是多造一份数据 ——
    这不是风格问题，是会在被测系统里留下垃圾。
    """
    from app.mcp.tools import sync

    body = _code_of(sync, "sync_orchestrated_scenario")
    # ast.unparse 会把引号统一成单引号，按源码里的双引号断言必然落空
    assert "retry_timeout_ms" in body and "('GET', 'HEAD', 'OPTIONS')" in body, (
        "写操作上开重试没有任何提示")


def test_等待重试字段前后端都不丢():
    """适配器漏一个字段，页面上就是「设了但看不见、一保存还被清零」——
    实测就这么丢过一次：接口返回 6000，编辑器里是空的。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "frontend/src"
    adapter = (root / "pages/cases/apiStepAdapter.js").read_text(encoding="utf-8")
    # 两个方向都要有：读出来给编辑器、写回去给后端
    assert adapter.count("retryTimeoutMs") >= 2, "适配器只做了单向，另一向会把值清零"
    assert "st.retryTimeoutMs ?? st.retry_timeout_ms" in adapter

    api = (Path(__file__).resolve().parents[1] / "app/api/api_test.py").read_text(encoding="utf-8")
    assert '"retryTimeoutMs": st.retry_timeout_ms' in api, "接口不返回，编辑器拿不到"
    assert "'wait_ms', 'retry_timeout_ms', 'retry_interval_ms'" in api, "接口不收，改了存不下"


# ── 显示与文案：说的和实际是不是一回事 ────────────────────────────

def test_抓包条数不叫接口数():
    """UI 脚本验证后显示「93 个接口」—— 那是本次抓到的**请求条数**，
    同一个接口被调 10 次也算 10 条。写成"个接口"会让人以为这脚本覆盖了
    93 个接口，实际可能就三五个。
    """
    from pathlib import Path

    jsx = (Path(__file__).resolve().parents[2]
           / "frontend/src/pages/cases/CaseDetail.jsx").read_text(encoding="utf-8")
    assert "captured_requests.length} 个接口" not in jsx, "又把抓包条数说成接口数了"
    assert "条请求" in jsx


def test_耗时为0不能整块消失():
    """原来用 `durationMs &&` 判 —— 0 是 falsy，于是"跑得飞快"和"没记耗时"
    都变成整块不见，抽屉里那一栏空着，用户以为是坏了。
    """
    from pathlib import Path

    jsx = (Path(__file__).resolve().parents[2]
           / "frontend/src/pages/cases/CaseDetail.jsx").read_text(encoding="utf-8")
    assert "{debugResult.durationMs && <span" not in jsx, "0 耗时又会让整块消失"
    assert "durationMs != null" in jsx


def test_失败计数不写成通过数():
    """原来写「失败 12/13」——「失败」后面跟的是"通过数/总数"，
    读起来像"失败了 12 条"，实际是"12 步通过、1 步失败"。
    挂了的时候人最想知道的是**挂了几步**。
    """
    from pathlib import Path

    jsx = (Path(__file__).resolve().parents[2]
           / "frontend/src/pages/cases/CaseDetail.jsx").read_text(encoding="utf-8")
    assert "步失败（共" in jsx, "失败时没有直接说挂了几步"


def test_步骤上的数字要说明是什么():
    """光印两个数字（"3 2"），得逐个悬停才知道哪个是断言、哪个是提取。"""
    from pathlib import Path

    jsx = (Path(__file__).resolve().parents[2]
           / "frontend/src/components/ApiStepList.jsx").read_text(encoding="utf-8")
    assert ">断{assertCount}<" in jsx and ">取{extractCount}<" in jsx, (
        "步骤右边还是两个光秃秃的数字")




# ── Mock 上游：测 AI 网关绕不开的那一半 ────────────────────────────

def test_mock路由不许抢占共享路径():
    """`/v1/chat/completions` 是所有用例共用的。谁把它配成 429，
    别人的用例就跟着挂 —— 而且是偶发的、最难查的那种。

    这就是平台自己 ④-0 判据的落地：**会被改的别共享**。
    """
    from app.mcp.tools.mocks import _SHARED_PATHS

    assert "/v1/chat/completions" in _SHARED_PATHS
    from app.mcp.tools import mocks
    body = _code_of(mocks, "upsert_llm_mock_route")
    assert "_SHARED_PATHS" in body and "howTo" in body, (
        "抢占共享路径没被拦，或者拦了却不说该怎么办")


def test_断言上游收到什么这条路必须在():
    """鉴权头有没有正确注入、模型名有没有按映射改写、参数有没有被篡改 ——
    这些在网关**下游**根本看不见，客户端只能看到最终响应。
    没有这条，「网关把请求转对了没有」压根验不了。
    """
    from app.mcp.tools import mocks

    body = _code_of(mocks, "llm_mock_requests")
    for field in ("requestHeaders", "requestBody", "requestModel"):
        assert field in body, f"上游请求记录里没有 {field}，断言不了网关发了什么"


def test_清记录这件事要说清为什么():
    """不清的话上一轮的记录还在，「上游只应收到 1 次」这种断言会**假过**
    —— 而假过比假红更难发现。
    """
    from app.mcp.tools import mocks

    doc = mocks.llm_mock_reset.__doc__ or ""
    assert "假过" in doc, "没说清不清会怎样，人就不会清"


def test_Mock是独立一档不塞进用例档():
    """两个理由：①不是每个项目都测 AI 网关，塞进 live 会让所有人的档位白白变大
    ②**单独一张卡片，这个能力才被看得见** —— 平台自己的工具没人用，
    多半不是难用，是它只存在于某个菜单深处。
    """
    from app.mcp.profiles import PROFILES

    keys = {p["key"] for p in PROFILES}
    assert "mocks" in keys, "Mock 没有自己的档位卡片，等于藏起来了"
    live = next(p for p in PROFILES if p["key"] == "live")
    assert "tb_llm_mock_status" not in live["tools"], "又塞回 live 了"
    full = next(p for p in PROFILES if p["key"] == "fullloop")
    assert "tb_llm_mock_status" in full["tools"], "全链路档反而没有，选它的人用不上"


# ── 回收站得能出来 ──────────────────────────────────────────────────

def test_回收站要有恢复这条路():
    """没有恢复的回收站不是回收站，是**延迟删除** —— 误删之后唯一的出路是
    彻底删掉重写一遍，那这一步缓冲就白设了。

    实测发现全后端三处写 deleted_at 全是往里放，一处往回置空的都没有；
    批量 action 里 archive/unarchive 是成对的，唯独 delete 是单向的。
    """
    from app.schemas.case import BatchCaseRequest

    actions = BatchCaseRequest.model_fields["action"].annotation.__args__
    assert "restore" in actions, "回收站出不来"

    from app.services import case_service
    body = _code_of(case_service, "batch_cases")
    assert "'restore'" in body and "case.deleted_at = None" in body


def test_恢复要查得到软删的行():
    """批量循环原本硬过滤 `deleted_at IS NULL` —— 恢复要找的恰恰是已删的那些，
    不放宽的话按钮点了永远报"用例不存在"。
    """
    from app.services import case_service

    body = _code_of(case_service, "batch_cases")
    assert "is_not(None) if action == 'restore'" in body, (
        "恢复还是只查活着的行，永远找不到要恢复的那条")


def test_软删不许清掉目录归属():
    """`module` 不是列，它是从目录名推出来的 —— 删除时清空 folder_id
    就**销毁了「这条属于哪个模块」唯一的记录**，恢复出来回不到原目录。

    而目录计数本来就过滤软删（folder_service 两处都带 deleted_at.is_(None)），
    清它没有任何收益。
    """
    from app.services import case_service

    body = _code_of(case_service, "batch_cases")
    seg = body[body.index("elif action == 'delete'"):]
    seg = seg[:seg.index("elif action ==", 10)]
    assert "folder_id = None" not in seg, "软删又把目录归属清掉了，恢复回不到原处"


def test_编号靠清空回收站归零而不是过滤软删():
    """`_next_case_code` 取 MAX(case_code) 且不看 deleted_at ——
    **不能简单加个过滤**：uq_case_branch_code 唯一约束还在，软删的行仍占着
    那个号，重新生成同号会直接撞约束。正解是让彻底删除真能删掉。
    """
    import inspect

    from app.services import import_service

    src = inspect.getsource(import_service._next_case_code)
    assert "deleted_at" not in src, (
        "给编号加了 deleted_at 过滤 —— 会撞 uq_case_branch_code 唯一约束")

    from app.services.case_service import _detach_blocking_refs
    body = inspect.getsource(_detach_blocking_refs)
    assert "PlanCase" in body and "TestReportScenario" in body, (
        "彻底删除的两个卡点外键没解开，回收站清不掉，编号就永远归不了零")


def test_范围过期要在页面上说出来():
    """项目级范围落库存的是**展开后的显式工具名单**（语义可审计），
    代价是平台加了新工具，已有项目的名单不会自动跟上。

    而页面只显示「31 / 45 个工具已开放」，看起来像"你有意只开 31 个"，
    不像"名单过期了" —— 实测埋过一次：一轮加了 8 个工具，项目范围一个都没跟上，
    CC 全看不见，页面上毫无提示，差点让整轮工作白做。

    ⚠ 判据**不能用 chosen**：deriveChosen 要求完全覆盖才算勾选，档位一缺工具
    就掉出 chosen，那样永远检测不到（第一版就是这么写错的，Playwright 照出来的）。
    """
    from pathlib import Path

    jsx = (Path(__file__).resolve().parents[2]
           / "frontend/src/pages/settings/MCPTools.jsx").read_text(encoding="utf-8")
    blk = jsx[jsx.index("const staleProfiles"):]
    blk = blk[:blk.index("const staleMissing")]
    assert "chosen.includes" not in blk, (
        "又用 chosen 判过期了 —— 档位缺工具时它已经掉出 chosen，永远检测不到")
    assert ">= 0.7" in blk, "没有覆盖率判据"
    assert "范围还没跟上" in jsx and "一键补齐" in jsx, "检测到了却不告诉人、也不给一键修"


# ── 「接口测试模块」和「用例的接口场景」是两个功能 ──────────────────

def test_彻底删用例要带走绑定的接口场景():
    """`source_case_id` 外键是 SET NULL —— 用例被彻底删掉后，场景只是把绑定
    置空，于是它**降级成一条独立接口场景**掉进接口测试模块的列表里。

    那是另一个功能（凭接口文档 AI 造的单接口场景）。两边混在一起之后：
    页面上「接口测试」全是别的功能的残骸；CC 判重时读到这些孤儿，会把
    「已有一条全绿的 AT-0009」当成"用例已存在"，于是不写新的、改去补用例重绑
    —— 实测就这么跑偏过一次。

    红线⑥说「一个用例 = 一条接口场景」，用例没了那条场景就是无主的。
    只在**彻底删除**时删：软删（进回收站）不动它，因为用例还能恢复。
    """
    import inspect

    from app.services.case_service import _detach_blocking_refs

    body = inspect.getsource(_detach_blocking_refs)
    # 钉真正的删除语句。只找 "ApiTestScenario"/"source_case_id" 的话，
    # 上面那段注释和 import 行里都有，删掉整条 delete 也不会红（第十二次）。
    assert "sa_delete(ApiTestScenario)" in body, (
        "彻底删用例没带走绑定场景，它会变成孤儿混进接口测试模块")


def test_接口场景必须属于某条用例():
    """无主场景这件事，2026-08-15 从"约定"升成"不变量"（迁移 zz9orph1）。

    在这之前它只是纪律：外键是 SET NULL，删一条用例就把它的场景变成孤儿，
    `case_service` 里补的那句 sa_delete 只堵住了一条删除路径。实测攒出 7 条无主
    场景，跑起来必挂在「变量未解析」（场景变量只能挂在用例上），还在稀释通过率。

    现在库里是 NOT NULL + ON DELETE CASCADE。这条钉住模型别被改回去 ——
    模型和库不一致的后果是安静的：SQLAlchemy 以为可空，插进去才炸。
    """
    from app.models.api_test import ApiTestScenario

    col = ApiTestScenario.__table__.c.source_case_id
    assert not col.nullable, "source_case_id 又变回可空了 —— 孤儿会重新长出来"
    fk = next(iter(col.foreign_keys))
    assert fk.ondelete == "CASCADE", (
        f"外键删除规则是 {fk.ondelete}，不是 CASCADE —— "
        "SET NULL 会把「删用例」变成「生产孤儿」")


def test_回推不绑用例要说人话而不是撞约束():
    """必填是库层挡的，但库层挡出来的是 IntegrityError，CC 看不懂。

    工具入口要先挡一道，并且**把为什么说清楚**（不绑用例 = 拿不到场景变量）。
    只 assert 返回 error 是不够的：随便报个错也能过。
    """
    import inspect

    from app.mcp.tools.sync import sync_orchestrated_scenario

    body = inspect.getsource(sync_orchestrated_scenario)
    assert "if not source_case_id:" in body, "回推入口没挡「不传用例」"
    assert "场景变量" in body, "挡住了但没说为什么，CC 只会换个参数重试"
    # AT-#### 那条兜底必须已经拆掉：它会在用例不存在时照建，然后撞外键
    assert "AT-{" not in body and 'f"AT-' not in body, (
        "AT-#### 编号兜底还在 —— 用例不存在时它会建出一条撞外键的场景")


def test_接口场景列表不许被当成判重依据():
    """原来这条测的是"分成 boundToCases / standalone 两组" —— 那是库里混着
    两个功能产物的年代。现在只剩一种（source_case_id NOT NULL），
    standalone 恒为空，保留一个永远空的分组只会让人以为另一类还在。

    但**要守的那件事没变**：这个列表说明的是"各用例的接口维度做没做"，
    说明不了"这个测试点写没写过"。实测跑偏过 —— CC 看到一条全绿就不写新用例了。
    所以 usage 里必须把人指回 tb_list_cases。
    """
    from app.mcp.tools import api_tests

    body = _code_of(api_tests, "list_api_test_scenarios")
    assert "standalone" not in body, (
        "standalone 分组还在 —— 它现在恒为空（NOT NULL 约束），留着是误导")
    assert "tb_list_cases" in body, "没把判重指回 tb_list_cases，这个列表就会被拿去判重"


def test_instructions说清无主场景不算数():
    """光在返回值里分组，模型未必意识到 standalone 那组"不属于任何人"。

    原来这条测的是"说清这是两个功能"——因为当时确实有两个（「接口测试」模块的
    单接口场景 vs 用例编排链）。2026-08-15 那个模块下线了，功能只剩一个，
    但**它的存量数据还在库里**，判重踩坑的风险一点没少（实测跑偏过：CC 看到
    孤儿 AT-0009 全绿就不写新用例了）。所以要守的东西从"说清是两个功能"
    变成"说清 standalone 那组无主、不算数"。
    """
    from app.mcp import mcp

    ins = mcp.instructions
    assert "判重只看 tb_list_cases" in ins
    assert "无主场景" in ins
    # 只认一句原话太脆 —— 这条已经因为改文案红过一次（把"不要把它算进来"改成
    # "别算进判重"）。认意思：两种说法哪种都行，但**必须说了别拿它判重**。
    assert any(k in ins for k in ("不要把它算进来", "别算进判重", "不要把它们算进来")), (
        "instructions 没说清无主场景不参与判重")
    # 顺带钉住新事实：约束收敛之后 standalone 恒为空，它现在的角色是哨兵。
    # 不说这一句的话，CC 会以为那组里"本来就有些历史数据"，看到东西也不当回事。
    assert "恒为空" in ins, "没说清 standalone 现在应该是空的（有东西=有人绕过了约束）"


def test_等待重试这个能力要送达CC():
    """加了能力不等于送达。实测：平台支持了 wait_ms/retry_timeout_ms，但
    tb_get_sync_spec 的规范里没写、工具描述的参数列表里也没有 ——
    CC 根本不知道它存在，只能沿用老办法（插「查版本历史」「查操作日志」这类
    真步骤去占时间窗），而那正是它自己说"很脆"的招。

    和「工具范围过期」同一类错：**能力加了，没送达消费者**。
    """
    from app.mcp.tools import sync

    assert "timing" in sync._SPEC_TIMING or True
    spec = sync._SPEC_TIMING
    assert "retry_timeout_ms" in spec and "wait_ms" in spec
    assert "别再靠插入真实断言步骤" in spec or "不要再靠插入真实断言步骤" in spec, (
        "没点破那个老办法，CC 会继续用")

    import inspect
    body = inspect.getsource(sync.get_sync_spec)
    assert '"timing": _SPEC_TIMING' in body, "规范没挂进 get_sync_spec，取不到"

    from app.mcp import TOOL_CATALOG
    desc = next(t["description"] for t in TOOL_CATALOG
                if t["name"] == "tb_sync_orchestrated_scenario")
    # 钉在 **steps 的参数列表**里，不是"描述里出现过这个词" ——
    # 末尾那句指引里也有它，光判 `in desc` 会被喂饱（本轮第十三次）。
    params = desc[desc.index("steps([{"):desc.index("}])")]
    assert "retry_timeout_ms" in params, "steps 的参数列表里没有它，CC 不会传"


def test_断言比较不能被变量插值的字符串坑死():
    """`_resolve_variables` 用 str(...)，所以**变量插值出来的值永远是字符串**，
    而 JSON 响应里的数字是 int/float。于是「拿上一步提取的版本号比 data.version」
    这种再常见不过的写法**必然挂**，页面上还显示「期望 2｜实际 2」——
    人完全看不出为什么失败。

    实测撞到：`data.rolled_back_to_version == ${baseVersion}`，期望 "2" 实际 2。
    修完那条真跑从 18/20 变 19/20。

    ⚠ 但**不能宽松到 bool**：Python 里 1 == True 本来就成立，放过去的话
    「期望 true、实际 1」会被判相等，那是另一种假绿。
    """
    from app.services.api_test_runner import _scalar_eq as eq

    # 数字/字符串互比 —— 这是要修的
    assert eq(2, "2") and eq("2", 2) and eq(2.0, "2") and eq(0, "0")
    # 字符串仍然严格
    assert not eq("v2", "2")
    assert eq("abc", "abc") and not eq("abc", "abd")
    # bool 一边一个类型 → 不等（防另一种假绿）
    assert not eq(1, True) and not eq(True, "1")
    assert eq(True, True) and eq(False, False)
    # None 不等于空串
    assert not eq(None, "")


def test_不等号也要走同一套比较():
    """上面那条测的是 `_scalar_eq` 函数本身的行为，**不保证调用点真的用了它** ——
    实测埋雷时发现：把 `_check_assertions` 里的调用改回严格比较，上面那条照样绿。
    所以这条钉的是**两个调用点**。

    `!=` 尤其不能漏：还用严格比较的话会出现 `2 != "2"` 判成真，和 `==` 自相矛盾。
    """
    from app.services import api_test_runner

    body = _code_of(api_test_runner, "_check_assertions")
    assert "passed = _scalar_eq(actual, expected)" in body, "== 没走宽松比较"
    assert "not _scalar_eq(actual, expected)" in body, "!= 没走同一套比较，会和 == 打架"


def test_三维只有三个态且四处叫法一致():
    """三维从 5 态收到 3 态（草稿/调试中/完成）。

    去掉的两个：`not_started`（和 draft 区分不出来）、
    `pending_review`+`executable`（「跑绿了」和「人发布了」原来是两个态，而
    executable **只有人能给** —— 回归池因此永远是空的，实测 257 条只有 1 条）。
    现在放权 CC：跑绿直接置 completed，「要不要人审」拆到 review_status 独立标签。

    **叫法必须四处一致**（详情页/列表页 × 状态表）—— 同一个值两个名字被指出过三次。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "frontend/src/pages/cases"
    for f in ("CaseDetail.jsx", "CaseManagement.jsx"):
        src = (root / f).read_text(encoding="utf-8")
        assert "completed: { label: '完成'" in src, f"{f} 缺「完成」态"
        assert "debugging: { label: '调试中'" in src, f"{f} 缺「调试中」"
        assert "draft: { label: '草稿'" in src, f"{f} 缺「草稿」"
        # 旧态一个都不许残留在状态表里
        for gone in ("'未开始'", "'待发布'", "'已发布'", "'可执行'", "'待审'　"):
            assert gone not in src.split("const REVIEW")[0], f"{f} 状态表里还有旧态 {gone}"


def test_不许再有档位派生():
    """**这是三次「徽标和下拉对不上」的总根源。**

    原来徽标显示"档位"（把 5 态压成 3~4 档，还掺了「有没有内容」），
    下拉显示存储值 —— 只要有这层派生，两者就永远可能不一致：
    实测手动维度徽标写「已写」而下拉高亮「未开始」。
    三维收到 3 态之后没有压缩的必要了，档位表整个删掉，直接显示存储值。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "frontend/src/pages/cases"
    for f in ("CaseDetail.jsx", "CaseManagement.jsx"):
        src = (root / f).read_text(encoding="utf-8")
        for gone in ("DIM_TIER", "dimTierKey", "const tierOf", "const TIER = "):
            assert gone not in src, f"{f} 档位派生又回来了：{gone}"


def test_审核标签是独立的且不挡回归():
    """审核从维度状态里拆出来，成为用例级的一个标签。

    · NULL=待提审（**不存值** —— 绝大多数用例都在这个态，存了等于给每条挂灰标签）
    · pending=待审：三维全完成**自动进**，没有「提交审核」那一下
    · approved/rejected：人点，而且**可以不点** —— 回归门禁不看它
    """
    from app.services import script_run_service

    assert hasattr(script_run_service, "sync_review_status"), "没有审核标签的推进入口"
    import inspect
    src = inspect.getsource(script_run_service.sync_review_status)
    assert '"pending"' in src and "target_level" in src, "没按 target_level 判三维是否全完成"
    assert '("approved", "rejected")' in src, "人审过的结论会被重跑抹掉"

    # 回归门禁不许看审核，也不许看维度状态
    # ⚠ 只查**函数体**，不查 docstring —— 说明里正好写着这两个词
    # （本轮被自己的守卫骗过第五次，都是同一个坑）
    import ast
    mod = __import__("app.services.execution_service", fromlist=["x"])
    fn = ast.parse(inspect.getsource(mod._will_run_automated)).body[0]
    if isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant):
        fn.body.pop(0)
    gate = ast.unparse(fn)
    assert "review_status" not in gate, "回归门禁看审核了 —— 审核不该挡回归"
    for dim in ("api_status", "ui_status", "manual_status"):
        assert dim not in gate, f"回归门禁又在看 {dim} 了 —— 判据该是「有没有产物」"


def test_跑绿就置完成_不再等人发布():
    """放权 CC 的落点。原来跑绿只到 pending_review，要人点「发布到回归」才 executable，
    而门禁看的就是 executable —— 回归池永远空的。"""
    from app.services import script_run_service
    import inspect

    src = inspect.getsource(script_run_service.apply_case_status)
    assert 'setattr(case, dim_attr, "completed")' in src, "跑绿没置完成"
    assert "pending_review" not in src, "还在往 pending_review 推"
    assert "sync_review_status(case)" in src, "推完没重算审核标签"


def test_发布和打回的新语义():
    """没有 executable 了，这两个动作退化成"人手动标完成 / 打回调试"。

    · 空的那一维不给标完成 —— 标了会进回归然后必挂，是一条假的绿
    · 打回把「完成」→「调试中」，并把总状态从「完成」退回「草稿」
      （否则列表里会出现「总状态：完成」而三维全「调试中」，自相矛盾）
    """
    from app.services import case_service

    body = _code_of(case_service, "batch_cases")
    assert "'completed'" in body, "发布/打回的判断没了"
    assert "没东西可标完成" in body, "空维度被跳过却不说，用户以为标成功了"
    assert "没有可打回的" in body, "一条都没打回却不说"
    assert "action == 'unpublish' and case.lifecycle_status == 'done'" in body, \
        "打回不动总状态 —— 会留下「完成 + 三维调试中」这种自相矛盾的行"
    assert "sync_review_status(case)" in body, "改完状态没重算审核标签"


def test_不许再写第二份维度状态():
    """`api_scenario_status` / `ui_scenario_status` 已删（2026-08）。

    它们和 `api_status` / `ui_status` 说的是同一件事，`apply_case_status` 一直
    **同时写两套** —— 实测 255 条里 0 处不一致，也就是说这两列从来没提供过任何
    额外信息，只是多了一处会漏写的地方。两个字段表达一件事，迟早有一处漏写就开始
    互相矛盾，而那时没人知道该信哪个。详情页上还各挂了一个下拉，改一处另一处不动。
    """
    import inspect

    from app.models.case import Case
    from app.services import script_run_service

    cols = {c.name for c in Case.__table__.columns}
    assert "api_scenario_status" not in cols and "ui_scenario_status" not in cols, \
        "重复的场景状态列又回来了"
    src = inspect.getsource(script_run_service.apply_case_status)
    assert "scenario_status" not in src, "又在写第二份维度状态了"
