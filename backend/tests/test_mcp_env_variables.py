"""tb_get_merged_variables 的封样。

实测：这个工具对**每一个环境**都直接报错
`structured_content must be a dict or None. Got list: [...]` ——
service 返回 list，而 FastMCP 要求结构化内容是 dict。
它的描述写着"排查『变量未解析』先查这里"，结果这个入口自己一直是坏的，
而且它就在 live / uiscript 两个主力档位里。

修的时候要连带脱敏：同族的 tb_list_global_data 一直脱敏，这条不脱
等于把一个崩溃换成一次凭证泄漏。
"""
import asyncio

from app.mcp.tools import environments as env_tools

ROWS = [
    {"key": "BASE_URL", "value": "http://10.0.0.1:8080", "source": "environment"},
    {"key": "ADMIN_PASSWORD", "value": "Admin@123456", "source": "environment"},
    {"key": "ADMIN_USERNAME", "value": "admin", "source": "environment"},
    {"key": "AUTH_TOKEN", "value": "eyJhbGciOi...", "source": "global"},
    {"key": "API_TIMEOUT", "value": "30", "source": "global"},
    {"key": "db_secret_key", "value": "s3cr3t", "source": "global"},
]


def _call(monkeypatch_rows=ROWS):
    async def fake(session, env_id):
        return monkeypatch_rows

    orig = env_tools.environment_service.get_merged_variables
    env_tools.environment_service.get_merged_variables = fake
    try:
        return asyncio.run(env_tools.get_merged_variables(
            None, "11111111-1111-1111-1111-111111111111"))
    finally:
        env_tools.environment_service.get_merged_variables = orig


def test_返回的是dict不是list():
    """这就是当初报错的根因 —— FastMCP 不收 list。"""
    out = _call()
    assert isinstance(out, dict)
    assert isinstance(out["variables"], list)
    assert out["total"] == len(ROWS)


def test_凭证类的值被脱敏():
    out = {v["key"]: v["value"] for v in _call()["variables"]}
    assert out["ADMIN_PASSWORD"] == "***"
    assert out["AUTH_TOKEN"] == "***"
    assert out["db_secret_key"] == "***"     # 小写也要盖住


def test_非凭证的值照常给出来():
    """脱敏不能把 BASE_URL 这种也盖了 —— 那 CC 就不知道打哪儿了。"""
    out = {v["key"]: v["value"] for v in _call()["variables"]}
    assert out["BASE_URL"] == "http://10.0.0.1:8080"
    assert out["API_TIMEOUT"] == "30"
    assert out["ADMIN_USERNAME"] == "admin"


def test_键名一个都不能少():
    """脱敏的是值不是键 —— CC 要的正是"能引用哪些键"。"""
    keys = {v["key"] for v in _call()["variables"]}
    assert keys == {r["key"] for r in ROWS}


def test_保留来源标记():
    """同名以环境为准，来源要能看出来，不然排查覆盖关系没依据。"""
    srcs = {v["key"]: v["source"] for v in _call()["variables"]}
    assert srcs["BASE_URL"] == "environment"
    assert srcs["API_TIMEOUT"] == "global"


def test_空环境不炸():
    out = _call([])
    assert out["total"] == 0 and out["variables"] == []


def test_返回里要写清怎么用():
    """不写的话，CC 很可能把值复制进脚本 —— 那正是变量纪律要禁的。"""
    assert "${" in _call()["usage"]
