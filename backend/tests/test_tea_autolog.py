"""UI 脚本自动埋点：让不改一行的普通 Playwright 脚本也出步骤和验证结果。

**这是被指出两轮才真做完的一项。** 现象：执行历史展开只有「脚本跑完没有报错」加一坨
pytest 启动横幅，脚本里 16 个 expect() 验了什么、挂在第几步全看不到。

我第一轮当成"二期项"punt 了，第二轮才发现能修：`tea_step` 要脚本自己 `with` 包起来，
而 CC 写的是普通 Playwright —— 那就在平台注入的 conftest 里自动把断言和
goto/click/fill 包一层。中间踩了三个坑，都在下面各有一条测试钉着：

1. 只 print 标记不够 —— 非流式路径（tb_run_ui_script / 批量回归）压根不读 stdout，
   它读 tea_capture flush 出来的 JSON。所以要同时挂进 tea_step 的步骤表。
2. pytest 默认吞 stdout，标记流不出来 —— 命令要加 `-s`。
3. 选择器里的中文是**双重转义**（`\\\\u8bf7`），解一次只脱一层，
   页面上还是一串 `\\u8bf7\\u9009\\u62e9`。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 插件是给沙箱用的、按裸模块名导入，测试里也照这个方式加载
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/engine/plugins"))

import tea_autolog  # noqa: E402


# ── 选择器翻译：看不懂的步骤名等于没有步骤名 ──────────────────────

def test_中文单层转义要解开():
    out = tea_autolog._pretty('.menu-item >> internal:has-text="\\u53d1\\u5e03\\u4e0a\\u7ebf"i >> nth=0')
    assert "发布上线" in out, out
    assert "\\u" not in out


def test_中文双层转义也要解开():
    """Playwright 实际吐的是双重转义 —— 解一次只脱一层，页面上仍是 `\\u8bf7...`。
    这条是实测漏出去的那个现象的封样。"""
    out = tea_autolog._pretty('.input >> internal:has-text="\\\\u8bf7\\\\u9009\\\\u62e9"i >> nth=0')
    assert "请选择" in out, out
    assert "\\u" not in out, out


def test_角色和名字翻成人话():
    assert tea_autolog._pretty('internal:role=button[name="\\u66f4\\u591a"i]') == "button「更多」"


def test_占位符和testid翻成人话():
    assert tea_autolog._pretty('internal:attr=[placeholder="payment-api"i]') == "placeholder=「payment-api」"
    assert tea_autolog._pretty('internal:testid=[data-testid="sync-status-bar"s]') == "testid=sync-status-bar"


def test_nth从0基改成第几个():
    """`nth=0` 对人是"第1个"。照原样印会让人以为跳过了一个。"""
    assert "第1个" in tea_autolog._pretty('tbody tr >> nth=0')
    assert "第3个" in tea_autolog._pretty('tbody tr >> nth=2')


def test_普通css选择器原样保留():
    """别把能读的东西改坏。"""
    assert tea_autolog._pretty('input[autocomplete="current-password"]') == 'input[autocomplete="current-password"]'


def test_怪串不死循环():
    """解码循环有次数上限 —— 遇到解不动的串要能退出。"""
    out = tea_autolog._pretty("\\umnop 不是合法转义")
    assert isinstance(out, str)


# ── 埋点装载 ────────────────────────────────────────────────────

def test_能包住断言和动作():
    n = tea_autolog.install()
    assert n >= 1, "一个都没包上，等于埋点没装"
    import playwright.sync_api as pw
    assert getattr(pw.expect, "_tea_wrapped", False), "expect 没被包 —— 验证结果那一半就没了"
    assert getattr(pw.Locator.click, "_tea_wrapped", False)
    assert getattr(pw.Page.goto, "_tea_wrapped", False)


def test_重复装载不叠加():
    """conftest 可能被 pytest 多次导入。叠加的话一步会打出好几对标记。"""
    tea_autolog.install()
    before = tea_autolog.install()
    assert before == 0, "第二次装载又包了一层"


def test_步骤同时落print和tea_step表():
    """两条执行路径各读一个 —— 只打 print 的话非流式路径拿到 0 步（实测踩过）。"""
    import tea_step
    tea_step.reset()
    tea_autolog.reset()
    tea_autolog._emit("点击 button「确认」", "action", lambda: None)
    steps = tea_step.get_steps()
    assert len(steps) == 1, "没挂进 tea_step 的步骤表 —— 非流式路径会拿到 0 步"
    assert steps[0]["action"] == "点击 button「确认」"
    assert steps[0]["status"] == "passed"


def test_失败的步骤记下错误且不吞异常():
    import tea_step
    tea_step.reset()
    tea_autolog.reset()

    def boom():
        raise AssertionError("元素找不到: #submit")

    try:
        tea_autolog._emit("点击 #submit", "action", boom)
    except AssertionError:
        pass
    else:
        raise AssertionError("异常被吞了 —— 那会把失败的用例变成通过")
    s = tea_step.get_steps()[0]
    assert s["status"] == "failed" and "元素找不到" in s["error"]


# ── 接线：三处都不能漏 ──────────────────────────────────────────

def test_conftest里装了埋点():
    src = (Path(__file__).resolve().parents[1] / "app/engine/pw_conftest.py").read_text(encoding="utf-8")
    assert "import tea_autolog" in src and "tea_autolog.install()" in src


def test_两处沙箱都复制了插件():
    """漏一处，那条路径就静默走 conftest 的 except 分支，又变回只有 pytest 一行。"""
    root = Path(__file__).resolve().parents[1] / "app"
    for f in ("engine/executor.py", "api/scripts.py"):
        src = (root / f).read_text(encoding="utf-8")
        assert "tea_autolog.py" in src, f"{f} 没复制自动埋点插件"


def test_pytest带了不捕获stdout():
    """没有 -s，print 的标记进不了流 —— 跑的时候面板一直是「0 步完成」。"""
    src = (Path(__file__).resolve().parents[1]
           / "app/engine/command_builder.py").read_text(encoding="utf-8")
    assert '"-s"' in src, "pytest 命令缺 -s"


def test_步骤要落库():
    """执行历史展开读的是 script_runs 那一行，不存就只剩 pytest 那坨。"""
    src = (Path(__file__).resolve().parents[1]
           / "app/services/script_run_service.py").read_text(encoding="utf-8")
    assert 'steps=result.get("steps")' in src, "record_run 没把步骤存下来"
    api = (Path(__file__).resolve().parents[1] / "app/api/scripts.py").read_text(encoding="utf-8")
    assert '"steps": r.steps' in api, "执行历史接口没把步骤回给前端"
    # **SSE 那条路也要存。** 页面「运行验证」走的是它；只在非流式路径存的话，
    # 人从页面点的每一次运行在执行历史里都没有步骤 —— 实测漏过一次。
    assert '"steps": steps,' in api, "SSE 路径的 record_run 没把步骤存下来"


# ── 步骤名要给测试人员看，不是给机器看 ──────────────────────────

def _loc(sel):
    class L:
        _selector = sel
    return L()


def test_数量为0翻成不应出现():
    """`to_have_count = 0` 字面是"数量等于 0"，实际意思是"这东西不该存在"。
    而这恰恰是 TC-FWGL-00001 最关键的一条断言（草稿态不该有启用/禁用按钮）——
    印成英文方法名，测试人员读不出它在验什么。"""
    out = tea_autolog._assert_label(
        tea_autolog._desc(_loc('internal:role=button[name="\\u7981\\u7528"s]')),
        "to_have_count", (0,))
    assert out == "button「禁用」 不应出现", out


def test_数量非0照常说个数():
    out = tea_autolog._assert_label("列表行", "to_have_count", (3,))
    assert "应有 3 个" in out, out


def test_文本断言翻成结论式():
    d = tea_autolog._desc(_loc('internal:testid=[data-testid="sync-status-bar"s]'))
    assert tea_autolog._assert_label(d, "to_contain_text", ("草稿",)) \
        == "「testid=sync-status-bar」 应包含文本「草稿」"
    assert "应不含文本「草稿」" in tea_autolog._assert_label(d, "not_to_contain_text", ("草稿",))


def test_可见性断言不带多余的值():
    d = tea_autolog._desc(_loc('internal:attr=[placeholder="payment-api"i]'))
    assert tea_autolog._assert_label(d, "to_be_visible", ()) == "placeholder=「payment-api」 应可见"


def test_书名号不套两层():
    """`_pretty` 翻出来的 `button「禁用」` 已经带书名号，再包一层是
    `「button「禁用」」`，比不加更难读。"""
    assert tea_autolog._quote("button「禁用」") == "button「禁用」"
    assert tea_autolog._quote("tbody tr") == "「tbody tr」"


def test_未知断言名不吞掉():
    """认不出来的原样显示 —— Playwright 加了新断言方法时要看得见，不能显示成空白。"""
    assert "to_be_in_viewport" in tea_autolog._assert_label("x", "to_be_in_viewport", ())


# ── 密码不许印进执行历史 ────────────────────────────────────────

def test_密码字段的值必须遮掉():
    """执行历史给人看、会被分享、还会进 CC 的上下文。实测第 3 步印出了 'Admin@123'。"""
    out = tea_autolog._fill_label(_loc('input[autocomplete="current-password"]'), ("Admin@123",))
    assert "Admin@123" not in out, out
    assert "***" in out


def test_各种密钥字段名都遮():
    for sel in ("input[name=password]", "#passwd", "[data-testid=api_key]",
                "input[name=secret]", "#token"):
        out = tea_autolog._fill_label(_loc(sel), ("s3cr3t",))
        assert "s3cr3t" not in out, sel


def test_普通字段照常印值():
    """遮太宽也不行 —— 服务名、路由这些正是排查时要核对的。"""
    out = tea_autolog._fill_label(
        _loc('internal:attr=[placeholder="payment-api"i]'), ("tb-fwgl1-x",))
    assert "tb-fwgl1-x" in out


# ── 收尾阶段的提示：必须靠确定信号，不许靠"沉默"猜 ──────────────

def test_收尾提示走确定的teardown标记():
    """最后一步跑完之后还有约 2.2 秒在关浏览器、把 HAR 落盘，期间一个事件都没有 ——
    面板停在「37 步完成，等待中...」，**看着就是卡死了**，被当成 bug 报了两次。

    前两版都是靠"沉默超过 N 秒"猜，两次都猜错：
      · 第一版用「有过输出」判 → 启动阶段（起浏览器、加载首页）就有 2 秒沉默，
        3.1 秒弹出「正在收尾」，那时才 0 步
      · 第二版加「已跑过步骤」 → 中途 wait_for_url / expect 重试也能停 1.2 秒以上，
        第 20 步时又弹了一次
    而两次我的断言都只查"出现过收尾"，照样绿 —— **断言松，功能错了也看不出来**。

    现在靠 conftest 的 `pytest_runtest_teardown` 打的 `##TEARDOWN##` 标记，
    位置确定，不猜。
    """
    conftest = (Path(__file__).resolve().parents[1]
                / "app/engine/pw_conftest.py").read_text(encoding="utf-8")
    assert "pytest_runtest_teardown" in conftest, "conftest 没打收尾标记"
    assert "##TEARDOWN##" in conftest

    api = (Path(__file__).resolve().parents[1] / "app/api/scripts.py").read_text(encoding="utf-8")
    assert '"##TEARDOWN##" in text' in api, "SSE 没转发收尾标记"
    assert "event: finishing" in api, "没发 finishing 事件"
    # 不许再退回"靠沉默猜"
    assert "asyncio.wait_for(proc.stdout.readline()" not in api, \
        "又在用读超时猜收尾了 —— 中途的正常停顿会被误判"


def test_前端认收尾事件且新步骤会撤掉它():
    """后端发了前端不认，等于没发（第一版就是这样：事件发出去，面板照旧「等待中」）。"""
    jsx = (Path(__file__).resolve().parents[2]
           / "frontend/src/pages/cases/CaseDetail.jsx").read_text(encoding="utf-8")
    assert "currentEvent === 'finishing'" in jsx, "前端不认 finishing 事件"
    assert "setFinishingMsg" in jsx
    # 新步骤到来要撤掉提示 —— 否则一旦误报就永远挂着
    i = jsx.index("currentEvent === 'step_start'")
    assert "setFinishingMsg(null)" in jsx[i:i + 400], "来了新步骤没撤掉收尾提示"
