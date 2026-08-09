"""i18n 采集器封样：JS 驼峰和 Python snake_case 两种写法都必须认。

背景：这个采集器上线时只写了 getByRole 这类驼峰规则，而平台上**所有**脚本都是
Python（回推通道走 pytest + playwright.sync_api，平台侧生成早已封存）。
实测扫 4 个脚本 added=0 —— 页面上「扫描脚本采集」按了等于没按，从上线起就没生效过。
"""
from app.services.i18n_harvest_service import extract_copy_literals

PY_SCRIPT = '''
from playwright.sync_api import Page, expect

def _login(page: Page):
    page.get_by_placeholder("用户名").fill(ADMIN_USERNAME)
    page.get_by_placeholder("密码").fill(ADMIN_PASSWORD)
    page.get_by_role("button", name="登 录").click()

def test_x(page: Page):
    page.get_by_role("button", name="创建项目").first.click()
    page.get_by_role("button", name="删除").click()
    page.get_by_text("已下线", exact=True)
    page.get_by_label("服务名称").fill("x")
    page.get_by_title("刷新").click()
    page.get_by_text(PROJ_NAME, exact=True)          # 变量不是文案，不该被收
    page.get_by_role("button", name="Submit").click()  # 纯英文不该被收
'''

TS_SCRIPT = """
await page.getByRole('button', { name: '导入' }).click()
await page.getByPlaceholder('请输入用户名').fill('x')
await page.getByText('登录成功').isVisible()
"""


def _texts(script):
    return {x["text"] for x in extract_copy_literals(script)}


def _cat(script, text):
    return next(x["category"] for x in extract_copy_literals(script) if x["text"] == text)


def test_python写法能抽出来():
    got = _texts(PY_SCRIPT)
    assert {"登 录", "创建项目", "删除", "用户名", "密码", "已下线", "服务名称", "刷新"} <= got


def test_python写法的分类正确():
    assert _cat(PY_SCRIPT, "登 录") == "button"
    assert _cat(PY_SCRIPT, "用户名") == "placeholder"
    assert _cat(PY_SCRIPT, "服务名称") == "label"
    assert _cat(PY_SCRIPT, "已下线") == "text"
    assert _cat(PY_SCRIPT, "刷新") == "title"


def test_变量和纯英文不收():
    got = _texts(PY_SCRIPT)
    assert "PROJ_NAME" not in got
    assert "Submit" not in got


def test_js驼峰写法仍然认():
    """改成兼容两种写法，不能把原来的 TS 支持弄丢。"""
    got = _texts(TS_SCRIPT)
    assert {"导入", "请输入用户名", "登录成功"} <= got
    assert _cat(TS_SCRIPT, "导入") == "button"
