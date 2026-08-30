"""项目须知的 200 字上限 —— 拒得对，还要**指对路**。

2026-08-30 的实测：外部 CC 想把一份 skill 正文推上来，撞了这条上限，
拒绝信息只说了"请你自己压"，于是它就真压了 —— 一份正文压成两条
「去看某某」的指路条目。指针留下了，正文没有任何地方收着它。

**这不是上限的错，是拒绝信息的错。** 上限管的是"这张表里该放什么"，
撞上它的两种内容出路完全不同（多件事挤一条 → 拆；本来就是一份规范 → 换个家）。
只说"自己压"就是把两种内容当成一种，教它丢东西。

所以这几条钉的不是"能不能拒"，是**拒的时候有没有把家指出来**。
"""
from __future__ import annotations

import pytest

from app.mcp.tools.project_notes import (MAX_CONTENT, _looks_like_a_spec,
                                         add_project_note)

# 长度校验在任何一次库访问之前，所以这几条根本走不到 session ——
# 传 None 反而是**更硬的断言**：万一哪天有人把校验挪到查库之后，
# 这里会当场 AttributeError 而不是安静地通过。
_NO_DB = None
_PID = "00000000-0000-0000-0000-000000000000"


async def _reject(content: str, **kw) -> dict:
    got = await add_project_note(_NO_DB, _PID, "标题", content, **kw)
    assert "error" in got, "超限必须拒，不能截断入库"
    return got


@pytest.mark.asyncio
async def test_超限直接拒不截断():
    got = await _reject("字" * (MAX_CONTENT + 1))
    assert str(MAX_CONTENT) in got["error"], "得告诉它上限是多少，不然只能试"


@pytest.mark.asyncio
async def test_拒的时候必须点名长文的家():
    """光说"自己压"就是在教它丢东西 —— 这条盯的就是那句话不能再单独出现。"""
    got = await _reject("字" * (MAX_CONTENT + 50))
    assert "lum_push_skill" in got["whereLongFormGoes"]


@pytest.mark.asyncio
async def test_这把Key够不着那个工具时也不许压():
    """指路指向一个够不着的工具，等于没指 —— 所以必须把"够不着怎么办"一起写上。

    lum_push_skill 只在「Skill 取用与共享」那一档里，而写须知的 CC 多半拿的是
    全链路/写用例那一档 —— **够不着才是常态**，不是边角情况。
    """
    got = await _reject("字" * 300)
    hint = got["ifPushSkillNotInScope"]
    assert "Skill 取用与共享" in hint, "得说清该去勾哪一档"
    assert "多选" in hint, "不说这句，人会以为勾了新档就丢了现在这档，于是不敢动"


@pytest.mark.asyncio
async def test_规范形状的内容会被点破不是须知():
    spec = (
        "## 提 issue 的规矩\n"
        "- 走 /issue，不是 write-issue —— 触发词重叠，容易挑错入口，挑错了产出的单子"
        "缺段落，修复方看不出少了什么\n"
        "- 修复后验证步骤不能省：缺了它，修复方按其余段落交付就算可关，而没人回来验\n"
        "- 审批/权限类强制 who×whom matrix 数据取证：谁对谁做了什么，一格一格填出来，"
        "只写「越权被拦住了」是没有证据的\n"
        "- 复现步骤按「点哪儿 → 看见什么」写，别写成接口调用 —— 修复方多半不看接口\n"
    )
    assert len(spec) > MAX_CONTENT, "样例本身得真超限，否则测的是另一条路"
    got = await _reject(spec)
    assert "thisIsNotANote" in got, "这种形状压根不属于这张表，得当场说破"


@pytest.mark.asyncio
async def test_一堆事实挤一条给的是拆不是丢():
    """事实型的超限内容不该被赶去写 skill，它的出路是拆 —— 两条出路别串味。"""
    got = await _reject("这个接口 404 有两种，一种是上游的一种是网关无路由的。" * 12)
    assert "thisIsNotANote" not in got
    assert "拆成两条" in got["howTo"]


def test_形状判据_规范一侧():
    assert _looks_like_a_spec("---\nname: issue\n---\n正文")   # SKILL.md 全文
    assert _looks_like_a_spec("步骤：\n1. 先 A\n2. 再 B\n3. 最后 C")
    assert _looks_like_a_spec("说明\n```bash\nmake test\n```")


def test_形状判据_须知一侧():
    """一条正经须知就是一两句话 —— 误判成规范会给出莫名其妙的建议。"""
    assert not _looks_like_a_spec(
        "这个接口 404 有两种：上游的 404 和网关无路由的 404，只断状态码会误判。")
    assert not _looks_like_a_spec("offline 会连带把 enabled 置 false，\nreactivate 再置回 true。")


@pytest.mark.asyncio
async def test_review_feedback_不给外部写():
    got = await add_project_note(_NO_DB, _PID, "标题", "短的", category="review_feedback")
    assert "error" in got


def test_工具描述里的字数和代码里的上限对得上():
    """描述说 200、代码改成 500 的话，CC 会按描述压，压完还是被拒 —— 而它不知道为什么。"""
    from app.mcp import TOOL_CATALOG

    desc = next(t["description"] for t in TOOL_CATALOG if t["name"] == "lum_add_project_note")
    assert f"{MAX_CONTENT} 字以内" in desc
    assert "lum_push_skill" in desc, "工具描述这一层也得指路 —— 有人是先读描述才决定往哪写的"
