"""UI 脚本里的文案写成占位变量 `${键|中文原文}` —— 和接口断言 `${T:键|中文}` 同形。

**为什么不是函数调用。** 脚本里别的取值都是变量形态（`os.getenv("SV_x")`、
接口断言的 `${svcName}`），只有文案曾经是 `t("键")`：同一份脚本两套取法，
读的人看不出 t() 也是"平台注入的数据"。用户直接点了这一条：
「为什么不是像取变量这种 如 ${button.text.ok}」。

替换发生在执行前的源码文本上（和替换 os.getenv 默认值同一处），三件事必须钉住：
转义、不碰环境变量、没换掉的要说出来。
"""
from __future__ import annotations

from app.services.ui_text_render import locale_of, render

TBL = {
    "services.list.searchPlaceholder": {"zh-CN": "搜索服务名 / 路由…",
                                        "en-US": "Search service name / route…"},
    "only.zh": {"zh-CN": "只有中文的那条"},
    "quote.demo": {"zh-CN": '带"引号"和\\反斜杠'},
}


def _r(src, lang="zh"):
    return render(src, TBL, locale_of({"TEST_LANGUAGE": lang}))


def test_按语种换成对应文案():
    zh, _ = _r('x = "${services.list.searchPlaceholder|搜索服务名 / 路由…}"')
    en, _ = _r('x = "${services.list.searchPlaceholder|搜索服务名 / 路由…}"', "en")
    assert zh == 'x = "搜索服务名 / 路由…"'
    assert en == 'x = "Search service name / route…"'


def test_环境变量不碰():
    """不带点号的不是文案键。`${BASE_URL}` 在 f-string 里是另一回事，动它就把脚本改坏了。"""
    src = 'page.goto(f"{BASE_URL}/login")  # ${BASE_URL} ${TEST_TOKEN}'
    out, st = _r(src)
    assert out == src and st["resolved"] == []


def test_引号和反斜杠要转义():
    """替换进源码字符串字面量里，不转义就是语法错误或者字符串提前结束。"""
    out, _ = _r("x = \"${quote.demo}\"")
    assert out == 'x = "带\\"引号\\"和\\\\反斜杠"'
    ns = {}
    exec(out, ns)                      # 真的能编译执行，且值正确
    assert ns["x"] == '带"引号"和\\反斜杠'


def test_词典有键但缺语种时退回它自己的中文():
    """比留下 ${} 好得多：那样一定红在"找不到元素"上，退回中文至少是在测中文那版。"""
    out, st = _r('x = "${only.zh}"', "en")
    assert out == 'x = "只有中文的那条"' and st["fellBack"] == ["only.zh"]


def test_词典压根没有就用竖线后面的中文():
    out, st = _r('x = "${not.in.dict|还没收录的那句}"', "en")
    assert out == 'x = "还没收录的那句"' and st["fellBack"] == ["not.in.dict"]


def test_没词典也没中文就原样留着并报出来():
    """静默换成空串或键名都更糟：前者选择器匹配一切，后者查不出原因。
    原样留着 + 明确报出来，人一眼知道是占位没解析。"""
    out, st = _r('x = "${also.missing}"', "en")
    assert out == 'x = "${also.missing}"' and st["missing"] == ["also.missing"]


def test_历史写法T前缀也认():
    out, _ = _r('x = "${T:services.list.searchPlaceholder}"', "en")
    assert out == 'x = "Search service name / route…"'


def test_两条执行路径都渲染():
    """上次"词典只在 MCP 那条路没注入"的坑，这次别再各写一份。"""
    import inspect
    from pathlib import Path

    from app.mcp.tools import ui_scripts
    assert "render_text(content" in inspect.getsource(ui_scripts.run_ui_script)
    api = (Path(__file__).resolve().parents[1] / "app/api/scripts.py").read_text(encoding="utf-8")
    assert "render_text(content" in api


def test_没换掉的占位要回给调用方():
    import inspect

    from app.mcp.tools import ui_scripts
    src = inspect.getsource(ui_scripts.run_ui_script)
    assert "textPlaceholdersUnresolved" in src and "textFellBackToChinese" in src


def test_规范里这就是默认写法():
    from app.mcp.tools.sync import _SPEC_UI_SCRIPT as ui

    assert '"${services.action.more|更多}"' in ui
    assert "tb_render_ui_script(case_id" in ui, "本地跑不了这条必须给解法，否则「本地先跑通」的纪律就破了"


def test_本地跑有工具可用():
    """`${}` 的代价只有一条：本地直接 pytest 跑是字面量。那就得有个工具吐渲染后的正文，
    否则"本地先跑通再回推"这条纪律在文案上是空的（让人自己维护词典副本不算解法）。"""
    from app.mcp import TOOL_CATALOG
    from app.mcp.profiles import PROFILES

    cat = {t["name"]: t["description"] for t in TOOL_CATALOG}
    assert "tb_render_ui_script" in cat
    d = cat["tb_render_ui_script"]
    assert "textUnresolved" in d, "没换掉的键要回给调用方"
    # 一个文件直接跑 —— 用户的判据：下载下来就该能跑，不该有"代价"
    assert "直接 pytest 跑的文件" in d and "语种开关" in d
    assert "凭据默认不烧" in d, "凭证脱敏是既有纪律，别悄悄开后门"
    ui = next(p for p in PROFILES if p["key"] == "uiscript")
    assert "tb_render_ui_script" in ui["tools"]

    from app.mcp.tools.sync import _SPEC_UI_SCRIPT as spec
    assert "tb_render_ui_script(case_id" in spec


def test_渲染出来的文件三样都烧进去():
    """用户的判据：下载下来就该能直接跑。所以文案、os.getenv 默认值、
    被测系统的语种开关，三样都得在同一个文件里 —— 少任何一样本地就是红的。
    （第一版只渲染了文案，本地跑照样红：脚本英文了、系统还说中文。）"""
    import inspect

    from app.mcp.tools import ui_scripts
    src = inspect.getsource(ui_scripts.render_ui_script)
    assert "render(script.content" in src, "① 文案"
    assert "os.getenv" in src and "build_run_env" in src, "② 环境变量默认值"
    assert "add_init_script" in src and "def context(context)" in src, "③ 语种开关写进同一个文件"


def test_凭据默认不烧进返回内容():
    """同族工具（tb_get_merged_variables / tb_list_global_data）一直对凭证脱敏，
    这里不能开后门；要自包含得显式传 include_credentials。"""
    import inspect

    from app.mcp.tools import ui_scripts
    src = inspect.getsource(ui_scripts.render_ui_script)
    assert "if include_credentials else" in src and "_SECRET_RE.search(k)" in src
    assert "exportEnv" in src, "不烧就得告诉人 export 哪几个，否则还是跑不起来"


def test_按getenv里的键替换不看左边名字():
    """`PROJECT_ID = os.getenv("SV_projectId", "")` 是再自然不过的写法，而三处替换
    原来都要求左右同名，一个都替换不到。平台跑时真环境变量在进程里、运行时照样取得到，
    **这个漏洞一直被藏着**；本地渲染成一个文件跑才暴露（那一行拿到空串，
    页面地址拼成 /projects//cases）。"""
    from app.services.ui_text_render import bake_env_defaults

    src = ('PROJECT_ID = os.getenv("SV_projectId", "")\n'
           'PWD = os.getenv("ADMIN_PASSWORD")\n'
           'X = os.getenv("NOT_IN_ENV", "keep")\n')
    out, baked = bake_env_defaults(src, {"SV_projectId": "p-1", "ADMIN_PASSWORD": "s3cr3t"},
                                   skip={"ADMIN_PASSWORD"})
    assert 'os.getenv("SV_projectId", "p-1")' in out
    assert 'os.getenv("ADMIN_PASSWORD")' in out, "skip 的不能烧进去"
    assert 'os.getenv("NOT_IN_ENV", "keep")' in out, "环境里没有的别动"
    assert baked == ["SV_projectId"]


def test_三处替换收口成一个函数():
    """MCP 跑 / 页面跑 / 本地渲染原来各写一份正则 —— 这库自己吃过好几次
    "同一件事两处各写一份"的亏。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for f in ("app/mcp/tools/ui_scripts.py", "app/api/scripts.py"):
        src = (root / f).read_text(encoding="utf-8")
        assert "bake_env_defaults" in src, f
        assert "os\\.getenv\\(\\s*\"{re.escape(var_name)}\"" not in src, f"{f} 还留着旧正则"


# ── 命名空间键：这一节是活体回推 v4 抓出来的那个洞 ────────────────────────────

def test_带命名空间的键也要替换():
    """**这是那个 bug 本体。** 被测系统的键是 i18next 形态 `ns:a.b`，
    而这里的正则原来只认 `[\\w.-]`，冒号不在里面 —— 一条都匹配不上，
    占位原样进了断言，而三个统计桶全是空的（连门禁都没得警告）。"""
    tbl = {"subscription:stats.pendingApproval": {"zh-CN": "待审批", "en-US": "Pending Approval"}}
    src = 'page.get_by_text("${subscription:stats.pendingApproval|待审批}")'
    out, st = render(src, tbl, "en-US")
    assert out == 'page.get_by_text("Pending Approval")'
    assert st["resolved"] == ["subscription:stats.pendingApproval"]


def test_命名空间键词典没有时退回竖线后的中文():
    """CC 报的现象："按规范这种情况该退回竖线后的中文，实际是原样留着"。"""
    out, st = render('x = "${subscription:stats.pendingApproval|待审批}"', {}, "en-US")
    assert out == 'x = "待审批"' and st["fellBack"] == ["subscription:stats.pendingApproval"]


def test_两种命名空间分隔符互认():
    """词典里存的是点号（importer 用 f"{ns}.{key}" 收的），被测系统里写的是冒号。
    不互认的下场：查不到 → 静默退回中文 → **英文环境测的其实是中文，一点红都没有**。
    实测 CC 报"5 个键没登记"，其中 4 条词典里明明有，只是写成了点号。"""
    from app.services.ui_text_render import key_aliases, with_aliases

    assert key_aliases("subscription:manage.rejectBtn") == [
        "subscription:manage.rejectBtn", "subscription.manage.rejectBtn"]
    assert key_aliases("subscription.manage.rejectBtn") == [
        "subscription.manage.rejectBtn", "subscription:manage.rejectBtn"]
    # 只换第一个分隔符 —— 命名空间只有一层，后面的点是键路径
    assert key_aliases("a.b.c")[1] == "a:b.c"

    tbl = with_aliases({"subscription.manage.rejectBtn": {"en-US": "Reject", "zh-CN": "驳回"}})
    out, _ = render('x = "${subscription:manage.rejectBtn}"', tbl, "en-US")
    assert out == 'x = "Reject"'


def test_词典出库就带别名():
    """互认要发生在**取词典**那一层，不然渲染、TEXT 注表、门禁三处各判一次，
    漏一处就又是"某条路上静默退回中文"。"""
    import inspect

    from app.services import i18n_harvest_service as h
    assert "with_aliases" in inspect.getsource(h.load_locale_table)


# ── 未解析的占位必须拦在执行之前（恒真断言） ──────────────────────────────────

def test_没解析的占位要被扫出来():
    from app.services.ui_text_render import unresolved

    left = unresolved('a = "${subscription:manage.rejectBtn}"\n'
                      'b = f"{BASE_URL}/x"  # ${BASE_URL} 不是文案键\n'
                      'c = "${services.list.searchPlaceholder}"')
    assert left == ["subscription:manage.rejectBtn", "services.list.searchPlaceholder"]


def test_占位没解析就不许开跑():
    """**CC 说得对：危险的不是那条红，是同一趟里全绿的那两条负例。**

    占位坏掉时：
      · 正例（断言文案出现）红在「找不到元素」上 —— 看得见
      · 负例（断言"不应出现"）**假绿** —— 占位匹配不到任何元素，"不该存在"当然成立
    实测（活体回推 v4）：第 10、12 步两条「不应出现」全绿，而占位压根没被替换。
    恒真断言不会自己喊疼，所以只能拦在执行前 —— 让它红在看得见的地方。
    """
    import tempfile
    from pathlib import Path

    from app.engine.executor import execute_single_case

    d = tempfile.mkdtemp()
    Path(d, "test_ui.py").write_text(
        'from playwright.sync_api import Page, expect\n'
        'def test_x(page: Page):\n'
        '    expect(page.get_by_text("${subscription:manage.rejectBtn}")).not_to_be_visible()\n',
        encoding="utf-8")
    r = execute_single_case(sandbox_dir=d, script_ref_file="test_ui.py", timeout=30)
    assert r["status"] == "error", "跑起来会是绿的 —— 那正是恒真断言"
    assert "subscription:manage.rejectBtn" in r["error_summary"]
    assert "假绿" in r["error_summary"]


def test_四条执行路径都拦():
    """MCP 跑 / 批量 / 页面 POST /run / 页面 run-stream。
    前三条都过 executor（拦截落在那儿，连"某条路忘了渲染"也一起拦住），
    run-stream 自己起 pytest 子进程，所以那条要单独写一次。"""
    import inspect
    from pathlib import Path

    from app.engine import executor
    from app.mcp.tools import ui_scripts

    assert "unresolved" in inspect.getsource(executor.execute_single_case)
    assert "textPlaceholdersUnresolved" in inspect.getsource(ui_scripts.run_ui_script)
    api = (Path(__file__).resolve().parents[1] / "app/api/scripts.py").read_text(encoding="utf-8")
    assert api.count("_unresolved_text(content)") >= 1, "run-stream 那条要自己拦一次"
    # POST /run 这条路以前压根没渲染文案（第四条路又漏一处）
    assert 'if script_type == "ui":' in api and "render_text(content" in api


def test_裸下标查不到要抛不要返回键名():
    """返回键名和未替换的占位是同一种坏法：负例假绿。词典不全就用 TEXT.get(键, 中文)。"""
    import sys
    import tempfile
    from pathlib import Path

    from app.engine.pw_conftest import write_playwright_conftest

    d = tempfile.mkdtemp()
    write_playwright_conftest(d, {"BASE_URL": "http://x", "TEST_LANGUAGE": "en"},
                              i18n={"ns:a.b": {"en-US": "Hi", "zh-CN": "嗨"}})
    sys.path.insert(0, d)
    sys.modules.pop("tea_i18n", None)      # 别的测试先 import 过，那是上一份沙箱的表
    try:
        import tea_i18n
        assert tea_i18n.TEXT["ns:a.b"] == "Hi"
        assert tea_i18n.TEXT.get("ns:zzz", "中文兜底") == "中文兜底", "带默认值的照旧不抛"
        try:
            tea_i18n.TEXT["ns:zzz"]
            raise AssertionError("裸下标查不到必须抛")
        except KeyError as e:
            assert "tb_upsert_i18n_terms" in str(e), "报错要说清怎么修"
    finally:
        sys.path.remove(d)
        sys.modules.pop("tea_i18n", None)


def test_门禁和渲染共用一个正则():
    """门禁那份原来不认命名空间键：CC 照规范写的占位在门禁眼里根本不存在
    （一句警告都没有），执行时也没替换 —— **同一个漏洞两处一起漏**。"""
    from app.mcp.tools import sync as sync_mod
    from app.services.ui_text_render import REF_RE

    assert sync_mod._PH_RE is REF_RE
    assert REF_RE.pattern in sync_mod._T_REF_RE.pattern


def test_裸键词典又没有是硬拦不是警告():
    """执行时已经拒跑了，回推还放行只是让人多跑一趟。"""
    from app.mcp.tools.sync import _scan_ui_script

    code = ('import os\n'
            'def test_x(page):\n'
            '    page.get_by_text("${subscription:manage.rejectBtn}").click()\n'
            '    assert page.get_by_text("x")\n')
    errors, _ = _scan_ui_script(code, "python", set())
    assert any("拒绝执行" in e for e in errors)
    # 词典里是点号形态也算已登记 —— 不然这条硬拦会误伤（实测 5 条里 4 条）
    errors2, _ = _scan_ui_script(code, "python", {"subscription.manage.rejectBtn"})
    assert not errors2


def test_命名空间键里的中文不算硬编码():
    """门禁那份正则不认冒号时，`${ns:键|中文}` 里的中文会被当成"硬编码中文"报出来 ——
    照规范写反被骂，人只能把中文删掉，于是又回到"占位没兜底"。"""
    from app.mcp.tools.sync import _scan_ui_script

    code = ('import os\n'
            'def test_x(page):\n'
            '    page.get_by_role("button", name="${subscription:manage.rejectBtn|驳回}")\n'
            '    assert page.get_by_text("x")\n')
    _, warns = _scan_ui_script(code, "python", {"subscription.manage.rejectBtn"})
    assert not any("硬编码" in w or "中文字面量" in w for w in warns), warns


def test_规范把多角色种语种的正确写法写出来():
    """CC 实测栽的坑：`add_init_script("() => {...}")` 只定义了个箭头函数，永不执行，
    不报错也不生效 —— 语种没换过去，全红而且是假红。"""
    from app.mcp.tools.sync import _SPEC_UI_SCRIPT as ui

    assert "add_init_script" in ui and "箭头函数" in ui
    assert "try{localStorage.setItem(" in ui, "得给能抄的那一行"


def test_规范要求扫描类断言先等就绪锚点():
    """「翻遍都找不到」在空列表上恒真 —— 触发重新拉取之后的空窗期里，一张卡都没渲染，
    断言就在那一瞬间通过。实测 CC 的 TC-DYGL-00016：第一版 44/44 全绿是空过的，
    加了"卡片数 4→3"这个锚点后步骤数变 58，多出来的 14 步正是原先被跳过的扫描。"""
    from app.mcp.tools.sync import _SPEC_UI_SCRIPT as ui

    assert "就绪锚点" in ui and "to_have_count" in ui
    assert "一条数据都没有" in ui, "判据要给得能自查"
