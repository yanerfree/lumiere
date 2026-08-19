"""两条来自使用者的反驳，都成立：

**① 共享底座不该是硬依赖。** 原来的规矩是"探不到就报变量未解析，你自己去造"——
纪律上说得通，实践上把一个资源缺失放大成一整批脚本红：二十条链引用同一个共享上游，
它没了就二十条一起挂，而接口场景是声明式 JSON，链子自己写不出 if/else 兜底。
资源怎么造**本来就登记在 create_def 里**，那就该由平台在跑前补上。

**② `t("services.list.searchPlaceholder")` 看不出在验什么。** 键是给机器的，
人读脚本时需要看到那句话。所以第二个参数写中文原文 —— 顺带把"查不到就拿键名
去匹配"这个必挂的坑也堵了。
"""
from __future__ import annotations

import inspect
import tempfile
from pathlib import Path

import pytest


# ── ① 探到"确实没有"就自建 ────────────────────────────────────────

def test_只在明确没匹配上时补建():
    """401/5xx/超时是"没查成"，照着建会造出一堆重复底座 —— 这条闸不能松。"""
    from app.services import api_test_runner as r

    src = inspect.getsource(r._resolve_automation_resources)
    assert 'item.get("state") == "missing"' in src, "补建的触发条件必须是 missing，不是 not exists"
    assert "res.create_def" in src, "没登记 create_def 的不该乱建"


def test_冲突当成别人刚建好():
    """并发跑时两条链可能同时探到 missing，撞唯一约束的那条不该失败。"""
    from app.services.api_test_runner import _auto_create_resource

    src = inspect.getsource(_auto_create_resource)
    assert "(400, 409, 422)" in src and "conflict" in src


def test_补建了必须说出来():
    """平台动了被测环境。悄悄补建比不补建更糟 —— 人得知道环境被谁改过。"""
    from app.services import api_test_runner as r

    assert '"autoCreated": created' in inspect.getsource(r.run_scenario)
    from app.mcp.tools import api_tests
    assert "已按 create_def 补建" in inspect.getsource(api_tests.run_api_test)


def test_没登记create_def要告诉它怎么补():
    from app.services import api_test_runner as r

    src = inspect.getsource(r._resolve_automation_resources)
    assert "登记 create_def 之后平台会在跑前自动补建" in src


def test_规范不再让共享底座变成硬依赖():
    """别再自己加一步「按名字查上游」并断言它必须存在 —— 那正是把一个缺失
    放大成一批红的写法。"""
    from app.mcp.tools.sync import _SPEC_VARIABLES as spec

    assert "别把共享底座写成硬依赖" in spec
    assert "自动补建" in spec and "create_def 不是备查，是兜底" in spec


# ── ② t(键, 中文) ────────────────────────────────────────────────

def _sandbox(env, table):
    import importlib.util
    import sys

    from app.engine.pw_conftest import write_playwright_conftest
    d = tempfile.mkdtemp()
    write_playwright_conftest(d, env, i18n=table)
    spec = importlib.util.spec_from_file_location(f"tea_i18n_{id(d)}", Path(d, "tea_i18n.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


TBL = {"services.list.searchPlaceholder": {"zh-CN": "搜索服务名 / 路由…",
                                           "en-US": "Search service name / route…"}}


def test_UI侧键加中文两种语种都对():
    zh = _sandbox({"TEST_LANGUAGE": "zh"}, TBL)
    en = _sandbox({"TEST_LANGUAGE": "en"}, TBL)
    assert zh.t("services.list.searchPlaceholder", "搜索服务名 / 路由…") == "搜索服务名 / 路由…"
    assert en.t("services.list.searchPlaceholder", "搜索服务名 / 路由…") == "Search service name / route…"


def test_UI侧词典没收录时退回中文而不是键名():
    """这是那个必挂的坑：返回键名 → 选择器拿键去匹配 → element not found，
    而排查的人只看到"元素找不到"，压根想不到是词典的事。"""
    en = _sandbox({"TEST_LANGUAGE": "en"}, TBL)
    assert en.t("services.list.notInDict", "还没收录的那句") == "还没收录的那句"
    assert en.t("services.list.notInDict") == "services.list.notInDict", "没给中文时保持原行为"


def test_接口侧竖线写法两种语种都对():
    from app.services.api_test_runner import _resolve_variables as r

    ref = "${T:services.list.searchPlaceholder|搜索服务名 / 路由…}"
    assert r(ref, {"TEST_LANGUAGE": "zh", "__I18N__": TBL}) == "搜索服务名 / 路由…"
    assert r(ref, {"TEST_LANGUAGE": "en", "__I18N__": TBL}) == "Search service name / route…"
    assert r("${T:not.in.dict|某句还没收录的}", {"TEST_LANGUAGE": "en", "__I18N__": TBL}) \
        == "某句还没收录的"


def test_门禁按有没有带中文分两档警告():
    """带了中文的只是"英文环境测不出英文"，没带的是"选择器必然匹配不上" ——
    两件事严重程度不同，混成一句话，人就不知道该先修哪个。"""
    from app.mcp.tools.sync import _scan_ui_script

    snip = ('def test_x(page):\n'
            '    page.get_by_role("button", name=t("a.b")).click()\n'
            '    page.get_by_text(t("c.d", "确认")).click()\n')
    errors, warns = _scan_ui_script(snip, "python", known_keys=set())
    # 没带中文原文 → **硬拦**：占位换不掉时正例红在「找不到元素」、负例假绿
    assert any("a.b" in e for e in errors), errors
    # 带了中文原文 → 只是软警告：不会挂，但英文环境测的还是中文
    assert any("c.d" in w and "英文环境下测的还是中文" in w for w in warns), warns


def test_规范把可读写法定成默认():
    """写法几经调整：t("键") → t("键","中文") → TEXT.get(...) → 最后定成 ${键|中文}
    （用户的判据：一眼就该是个变量，跟接口断言同形）。钉的是"示例里必须带中文原文"，
    不是某一版语法 —— 示例光写键，CC 就会照着光写键。"""
    from app.mcp.tools.sync import _SPEC_API_SCENARIO as api, _SPEC_UI_SCRIPT as ui

    assert '"${services.action.more|更多}"' in ui
    assert "竖线后面的中文别省" in ui
    assert "|服务名已存在}" in api


def test_文档串里的示例不算引用():
    """活体验证时撞到：脚本 docstring 里解释 `t("键", "中文原文")` 怎么用，
    扫描器把「键」当成真引用报警告。**在注释里解释用法反被门禁警告**，
    这种提示看两次就没人信了。"""
    from app.mcp.tools.sync import _scan_ui_script

    snip = ('def test_x(page):\n'
            '    """用法：t("键", "中文原文") —— 键决定取哪条译文。\n\n'
            '    name="更多" 这种写死中文是不行的（这句只是举例）。\n'
            '    """\n'
            '    page.get_by_text(t("services.list.searchPlaceholder", "搜索服务名 / 路由…")).click()\n'
            '    # 注释里也提一句 t("另一个键")\n')
    _, warns = _scan_ui_script(snip, "python", known_keys={"services.list.searchPlaceholder"})
    assert not any("键" in w and "词典里没有" in w for w in warns), warns
    assert not any("硬编码" in w for w in warns), f"注释里的举例被当成写死中文：{warns}"


def test_真代码里的引用照旧扫得到():
    from app.mcp.tools.sync import _scan_ui_script

    snip = ('def test_x(page):\n'
            '    page.get_by_text(t("a.b")).click()\n')
    errors, warns = _scan_ui_script(snip, "python", known_keys=set())
    assert any("a.b" in x for x in errors + warns), (errors, warns)


# ── TEXT：一眼是变量表，不是函数调用 ─────────────────────────────

def test_注入的是解析好的扁平表():
    """脚本里别的取值都是 os.getenv(...)，只有文案是个函数调用 `t("键")` ——
    同一份脚本两套取法，读的人看不出 t() 也是"平台注入的数据"。
    改成注入 {键: 这次该用的那句话}，脚本里就是取变量。
    """
    zh = _sandbox({"TEST_LANGUAGE": "zh"}, TBL)
    en = _sandbox({"TEST_LANGUAGE": "en"}, TBL)
    assert zh.TEXT["services.list.searchPlaceholder"] == "搜索服务名 / 路由…"
    assert en.TEXT["services.list.searchPlaceholder"] == "Search service name / route…"
    assert isinstance(en.TEXT, dict), "就该是个 dict —— 别再包一层 API"


def test_裸下标取不到要当场抛():
    """**立场变过一次，现在是抛。** 返回键名的下场：正例红在「找不到元素」上（看得见），
    而「不应出现」那类负例**假绿** —— 键名当文案去匹配、匹配不到任何元素，
    "不该存在"当然成立。恒真断言不喊疼，所以宁可当场炸。
    词典不全是常态 → 那就用带默认值的 TEXT.get(键, "中文")，它照旧不抛。"""
    import pytest as _pt

    en = _sandbox({"TEST_LANGUAGE": "en"}, TBL)
    with _pt.raises(KeyError):
        en.TEXT["not.in.dict"]
    assert en.TEXT.get("not.in.dict", "还没收录那句") == "还没收录那句"


def test_老写法t保留为别名():
    """库里已有 3 个脚本 13 处在用 t() —— 废掉等于把别人的脚本弄坏。"""
    en = _sandbox({"TEST_LANGUAGE": "en"}, TBL)
    assert en.t("services.list.searchPlaceholder", "搜索服务名 / 路由…") \
        == "Search service name / route…"
    assert en.t("not.in.dict", "兜底中文") == "兜底中文"


def test_扫描器认三种写法():
    from app.mcp.tools.sync import _t_refs

    code = ('TEXT["a.b"]\n'
            'TEXT.get("c.d", "确认")\n'
            't("e.f")\n'
            't("g.h", "更多")\n')
    refs = _t_refs(code)
    assert refs == {"a.b": False, "c.d": True, "e.f": False, "g.h": True}


def test_TEXT留给要循环拼接的场合():
    """默认写法是 ${键|中文}（占位替换）。TEXT 表还留着 —— 循环/拼接时占位写不出来，
    而且库里已有脚本在用 t()，废掉会把别人的脚本弄坏。"""
    from app.mcp.tools.sync import _SPEC_UI_SCRIPT as ui

    assert "from tea_i18n import TEXT" in ui and 'TEXT.get("键", "中文")' in ui
    assert "别名" in ui, "老写法还在用，规范里要说清它等价"


def test_文案传给自定义函数也要报():
    """被自己漏改逼出来的：改造网关脚本时 `_open_more_menu(page, "发布上线")` 没换掉 ——
    文案传给的是自定义函数，按定位器 API 名单扫的规则看不见它，
    我和平台扫描器一起漏了，跑英文才红出来。现在反过来判：
    正文里的中文字面量除了正当去处（步骤名/日志/造数据赋值）一律提醒。"""
    from app.mcp.tools.sync import _scan_ui_script, _stray_cn_literals

    code = ('_open_more_menu(page, "发布上线")\n'
            'with tea_step("打开更多菜单"):\n'
            '    pass\n'
            'SVC_PREFIX = "自测服务"\n'
            'print("跑完了")\n'
            'page.get_by_text("${a.b|已发布}")\n')
    stray = _stray_cn_literals(code)
    assert "发布上线" in stray
    for ok in ("打开更多菜单", "自测服务", "跑完了", "已发布"):
        assert ok not in stray, f"{ok} 是正当用法，不该报"

    _, warns = _scan_ui_script('def test_x(page):\n    _open_more_menu(page, "发布上线")\n',
                               "python", known_keys=set())
    assert any("不在定位器 API 上" in w for w in warns)
