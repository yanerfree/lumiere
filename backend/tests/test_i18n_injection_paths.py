"""词典必须两条执行路径都注入 —— 而 CC 走的恰恰是以前没注入的那条。

实测（语种演示那条用例第一次跑）：`t("services.list.searchPlaceholder")` 返回了**那串键**，
选择器拿键去匹配，红在「element not found」上 —— 谁都看不出是词典没进沙箱。
根因是页面那条（api/scripts.py）自己查了一遍词典，MCP 那条（mcp/tools/ui_scripts.py）
压根没查。这是这个库自己吃过好几次的亏：**同一件事两处各写一份，迟早有一处忘了。**
"""
from __future__ import annotations

import inspect


def test_MCP执行路径注入了词典():
    from app.mcp.tools import ui_scripts

    src = inspect.getsource(ui_scripts.run_ui_script)
    assert "load_locale_table_for_case" in src, "MCP 跑脚本没注入词典，t() 会原样返回键"
    assert "i18n=i18n" in src, "取了却没传给执行器"


def test_执行路径都走同一个取法():
    """各写一份 = 其中一条迟早忘掉，而且只在英文环境下才暴露。

    判据钉在**执行路径**上（按用例取 → load_locale_table_for_case），
    不是禁 load_locale_table 这个名字：导出备份是按 branch 打包、手上压根没有 case_id，
    它按项目取是对的。原来那条按字符串禁名，导出功能一上就误报。
    """
    import inspect

    from app.api import scripts as api_scripts

    for fn in (api_scripts._run_python_stream, api_scripts.run_script):
        assert "load_locale_table_for_case" in inspect.getsource(fn), fn.__name__


def test_取不到词典不炸只退回空表():
    """查库失败不能把整次执行带崩 —— t() 拿空表会原样返回 ref，中文当 ref 时照样对。"""
    from app.services import i18n_harvest_service as h

    src = inspect.getsource(h.load_locale_table_for_case)
    assert "except Exception" in src and "return {}" in src


def test_沙箱按环境变量种被测系统的语言键():
    """浏览器 locale 换不动被测系统 —— 实测 stoa 的语种存在 localStorage['stoa-lang']，
    locale 设成 en-US 页面照旧全中文。环境里配 UI_LANG_STORAGE_KEY，
    conftest 在页面脚本跑之前把它种下去，语种才真的切得动。"""
    import tempfile
    from pathlib import Path

    from app.engine.pw_conftest import write_playwright_conftest

    d = tempfile.mkdtemp()
    write_playwright_conftest(d, {"BASE_URL": "http://x", "TEST_LANGUAGE": "en",
                                  "UI_LANG_STORAGE_KEY": "stoa-lang"})
    txt = Path(d, "conftest.py").read_text(encoding="utf-8")
    assert 'LANG_STORAGE_KEY = "stoa-lang"' in txt
    assert "add_init_script" in txt, "要在页面脚本跑之前种，晚一步就已经按旧语种渲染了"
    assert "en-US" in txt

    d2 = tempfile.mkdtemp()
    write_playwright_conftest(d2, {"BASE_URL": "http://x", "TEST_LANGUAGE": "en"})
    txt2 = Path(d2, "conftest.py").read_text(encoding="utf-8")
    assert 'LANG_STORAGE_KEY = ""' in txt2, "没配就不该乱种键"


def test_规范把两条必配项都写清了():
    """只配 TEST_LANGUAGE 是**不够**的 —— 那只换了期望值。实测 stoa 的语种在
    localStorage['stoa-lang']，不种它就是"设了没生效"，而且表现为全红（假红）。"""
    from app.mcp.tools.sync import _SPEC_API_SCENARIO as api, _SPEC_UI_SCRIPT as ui

    assert "UI_LANG_STORAGE_KEY" in ui and "浏览器 locale 换不动被测系统" in ui
    assert "new_context" in ui, "多角色自己开的 context 不吃注入，这句要留着"
    assert "Accept-Language" in api, "接口侧要说清平台会带上语种头"
