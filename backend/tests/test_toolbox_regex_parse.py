"""工具箱「AI 生成正则」的解析封样。

踩到的（有截图）：正则输入框里被填进了**一整个 JSON 文档**
`{"regex": "^1[3-9]\\d{9}$", "flags": "", ...}`，页面报"语法错误"，
用户完全不知道发生了什么。

根因：正则里几乎必然出现 `\\d` `\\w` `\\s`，而它们在 JSON 字符串里是非法转义。
模型少写一个反斜杠 `json.loads` 就炸，而原来的兜底是**把整段原文塞进 regex 字段**。
更糟的是它间歇发作 —— 模型偶尔规规矩矩写 `\\\\d`，那次就正常。

所以钉死一条底线：**任何情况下都不许把整段 JSON 当成正则返回**。
"""
from app.api.toolbox import parse_regex_payload as P


def test_规规矩矩的json():
    d = P('{"regex": "^\\\\d{11}$", "flags": "g", "explanation": "手机号"}')
    assert d["regex"] == "^\\d{11}$"
    assert d["flags"] == "g"


def test_模型少写反斜杠也要能解出来():
    """这是最常见的一种，也是当初翻车的那一种。"""
    d = P('{"regex": "^1[3-9]\\d{9}$", "flags": "", "explanation": "手机号"}')
    assert d["regex"] == "^1[3-9]\\d{9}$"
    assert not d["regex"].startswith("{")


def test_带代码围栏():
    d = P('```json\n{"regex": "^\\d+$", "flags": "", "explanation": "数字"}\n```')
    assert d["regex"] == "^\\d+$"


def test_json结构本身坏了也能把regex抠出来():
    d = P('{"regex": "^\\d{4}-\\d{2}$", "flags": "" "explanation": 缺引号}')
    assert d["regex"] == "^\\d{4}-\\d{2}$"


def test_只回一行光秃秃的正则也认():
    d = P("^[a-z]+@[a-z]+\\.com$")
    assert d["regex"] == "^[a-z]+@[a-z]+\\.com$"


# ── 底线：绝不把整段 JSON 当正则 ──

def test_解不出来时宁可报错也不返回整段原文():
    """这是这次修的核心。返回 None → 接口报"没解出来"，页面提示重试；
    而不是把一整个 JSON 填进正则框让人对着"语法错误"发呆。"""
    d = P('{"foo": "bar", "baz": 1}')
    assert d is None


def test_空内容返回None():
    assert P("") is None
    assert P(None) is None


def test_任何分支都不会返回以大括号开头的正则():
    for raw in (
        '{"regex": "^1[3-9]\\d{9}$"}',
        '{"regex": "^\\\\d+$", "flags": "g"}',
        '```json\n{"regex": "\\w+"}\n```',
        '{"regex": "a", "explanation": "带\\"引号\\"的说明"}',
    ):
        d = P(raw)
        assert d is not None, raw
        assert not d["regex"].lstrip().startswith("{"), (raw, d["regex"])


def test_regex为空的json不算解出来():
    """有 JSON 但没给正则，等于没生成成功，别拿空串糊弄。"""
    assert P('{"regex": "", "flags": "g"}') is None
