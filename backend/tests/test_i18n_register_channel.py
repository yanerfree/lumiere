"""国际化词典的登记通道 —— 纪律要求走 t()，却没有地方登记词条。

外部 CC 反馈第五条：「平台让我去国际化词典登记，但 MCP 42 个工具里没有对应工具，
我只能把键值整理成表交给人工。」

**一条只能靠人工转抄才能遵守的纪律，等于没有这条纪律。**
"""
from __future__ import annotations

import pytest

from app.mcp.tools.sync import upsert_i18n_terms


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    """按 key 查一条、没有就 add 的最小假 session。"""

    def __init__(self, existing=None):
        self.existing = existing or {}
        self.added = []
        self.commits = 0

    async def execute(self, stmt):
        key = next((p.right.value for p in stmt._where_criteria
                    if getattr(p, "right", None) is not None
                    and isinstance(getattr(p.right, "value", None), str)), None)
        row = self.existing.get(key)
        return _Res([row] if row else [])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


_PID = "11111111-1111-1111-1111-111111111111"


@pytest.mark.asyncio
async def test_登记键值三件套():
    s = _Session()
    r = await upsert_i18n_terms(s, _PID, [
        {"key": "services.action.more", "zh": "更多", "en": "More", "category": "button"}])
    assert r["created"] == ["services.action.more"]
    row = s.added[0]
    assert row.translations == {"zh-CN": "更多", "en-US": "More"}
    assert row.category == "button" and row.source == "manual"


@pytest.mark.asyncio
async def test_中文当键时中文自动补上():
    """中文键的中文就是它自己。不补的话 harvest 的反查（zh → key）对不上，
    而且 load_locale_table 只注入有译文的行，只填 en 的行反查不到中文。"""
    s = _Session()
    await upsert_i18n_terms(s, _PID, [{"key": "服务名已存在", "en": "Service name exists"}])
    assert s.added[0].translations["zh-CN"] == "服务名已存在"


@pytest.mark.asyncio
async def test_没有en译文要说出来():
    """登记了不等于能测英文 —— 只有 zh 的词条在英文环境下 t() 照样退回中文。"""
    s = _Session()
    r = await upsert_i18n_terms(s, _PID, [{"key": "common.save", "zh": "保存"}])
    assert r["missingEn"] == ["common.save"]
    assert "英文" in r["message"]


@pytest.mark.asyncio
async def test_key必填且逐条报错不整批挂():
    s = _Session()
    r = await upsert_i18n_terms(s, _PID, [{"zh": "保存"}, {"key": "common.ok", "en": "OK"}])
    assert r["status"] == "partial" and r["errors"][0]["index"] == 0
    assert r["created"] == ["common.ok"], "一条错不该带走另一条"


@pytest.mark.asyncio
async def test_空数组直接说清楚():
    assert "error" in await upsert_i18n_terms(_Session(), _PID, [])


def test_工具注册了并且进了UI脚本档():
    """写 UI 脚本那档必须有它 —— 文案纪律就在那一档里要求的。
    接口断言的 ${T:} 用的是同一份词典，所以 live 档也要有。"""
    from app.mcp import TOOL_CATALOG
    from app.mcp.profiles import PROFILES

    assert "lum_upsert_i18n_terms" in {t["name"] for t in TOOL_CATALOG}
    for key in ("uiscript", "live"):
        p = next(x for x in PROFILES if x["key"] == key)
        assert "lum_upsert_i18n_terms" in p["tools"], key


def test_规范里指向这个通道并说清两种查不到的后果():
    """键查不到返回键名 → 选择器必挂（假红，排查的人会误判成产品缺陷）；
    中文查不到返回中文 → 不挂。两者后果不同，不写清就没法自己判断该用哪种。"""
    from app.mcp.tools.sync import _SPEC_UI_SCRIPT as spec
    assert "lum_upsert_i18n_terms" in spec
    assert "退回中文" in spec and "找不到元素" in spec
    assert "TEST_LANGUAGE=en" in spec, "没说本地怎么跑英文，纪律在本地就验不了"


def test_规范要求优先断稳定错误码():
    """活体跑回推链路时撞到的：那个 409 的 message 是英文（没走 i18n）**还拼了动态服务名**，
    等值断言必挂，套 ${T:} 也白搭（词典里没有它，返回原文照样对不上）。
    而它同时回了 error.code=SERVICE_NAME_CONFLICT —— 那才是该断的东西。
    不写清顺序，CC 会一路给不该本地化的东西登记词条。
    """
    from app.mcp.tools.sync import _SPEC_API_SCENARIO as spec
    assert "错误码 > `${T:}` 文案 > contains 片段" in spec
    assert "error.code" in spec
