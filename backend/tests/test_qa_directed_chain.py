"""§12「有向链路」的封样：账本每一格都摆着、断点只记第一个、残留分两种。

这份测试盯的是**账本的形状**，不是某个产品的按钮名。会红的典型改动：
把只在发生时才出现的键从 `new_chain` 里删掉（页面上「没发生」和「没记过」
就分不开了）、把两种残留合成一句「有残留」、把 `note_fact` 改成打断链。
"""
import pytest

from app.services.qa_directed_chain import (
    BREAKPOINTS,
    CHAIN_FACTS,
    CHAIN_STEPS,
    PROBE_PREFIX,
    chain_declarations,
    field_kind,
    finish_chain,
    is_mine,
    looks_like_probe,
    matches_purpose,
    new_chain,
    new_probe_tag,
    note_breakpoint,
    note_fact,
    note_step,
    note_write,
    pick_control,
    plan_fill,
    residue_findings,
    summarize_chains,
    value_for,
)


def _chain(tag="qa-probe-x"):
    return new_chain("/services", tag)


class Test自己造的数据认得出来:
    def test_前缀在标签里(self):
        tag = new_probe_tag()
        assert tag.startswith(PROBE_PREFIX)
        assert looks_like_probe(f"名字：{tag}")

    def test_只认自己这一趟的标签(self):
        mine, other = new_probe_tag("aa"), new_probe_tag("bb")
        assert is_mine(f"row {mine}", mine)
        assert not is_mine(f"row {other}", mine)

    def test_别人的行一个字都不许认成自己的(self):
        assert not looks_like_probe("生产订单 20260904")


class Test账本每一格都摆着:
    def test_新链就有全部键(self):
        c = _chain()
        for key in ("steps", "writes", "breakpoint", "breakpointDetail",
                    "created", "deleteTried", "deleted", "unlockedPaths",
                    "unfillable", "facts", "residue", "residueKind",
                    # §14.1 的四样 + 「探过没有」
                    "hints", "states", "surface", "sections", "cells",
                    "probed"):
            assert key in c, key

    def test_打错的环名当场拒(self):
        c = _chain()
        note_step(c, "verify", ok=True)
        with pytest.raises(ValueError):
            note_step(c, "verfiy", ok=True)

    def test_环名清单就是这七个(self):
        assert CHAIN_STEPS == ("create", "list", "detail", "edit",
                               "verify", "delete", "confirm")


class Test断点只记第一个:
    def test_后来的断点不覆盖(self):
        c = _chain()
        note_breakpoint(c, "no_form", detail="点了没弹层")
        note_breakpoint(c, "delete_failed", detail="删不掉")
        assert (c["breakpoint"], c["breakpointDetail"]) == ("no_form", "点了没弹层")

    def test_不是断点的一律拒(self):
        with pytest.raises(ValueError):
            note_breakpoint(_chain(), "no_such_thing")

    def test_每个断点都写了归谁(self):
        for kind, meta in BREAKPOINTS.items():
            assert meta["owner"] in ("ours", "product", "finding", "fact",
                                     "unknown"), kind
            assert meta["why"]

    def test_删不掉归产品不归我们(self):
        # 归给自己就没人去查产品那一半 —— 这是最值钱的那一类发现。
        assert BREAKPOINTS["delete_failed"]["owner"] == "product"


class Test事实不打断链:
    def test_找不到编辑入口照样算走完(self):
        c = _chain()
        c["created"] = True
        c["deleted"] = True
        note_fact(c, "no_edit_entry", detail="行内只有查看")
        finish_chain(c)
        assert c["completed"] is True
        assert c["residue"] is False

    def test_同一条事实不重复记(self):
        c = _chain()
        note_fact(c, "no_detail_entry")
        note_fact(c, "no_detail_entry")
        assert len(c["facts"]) == 1

    def test_不是事实的一律拒(self):
        with pytest.raises(ValueError):
            note_fact(_chain(), "no_form")      # 那是断点，不是事实

    def test_事实和断点是两本(self):
        assert not set(CHAIN_FACTS) & set(BREAKPOINTS)


class Test写请求记账:
    def test_2xx算成功且不留响应体(self):
        c = _chain()
        note_write(c, method="post", path="/api/svc", status=201, body="{...}")
        row = c["writes"][0]
        assert (row["method"], row["ok"]) == ("POST", True)
        assert "error" not in row

    def test_非2xx留报错原文且截断(self):
        c = _chain()
        note_write(c, method="POST", path="/api/svc", status=422, body="x" * 500)
        row = c["writes"][0]
        assert row["ok"] is False
        assert len(row["error"]) == 300

    def test_状态码读不出来不算成功(self):
        c = _chain()
        note_write(c, method="POST", path="/api/svc", status=None)
        assert c["writes"][0]["ok"] is False


class Test残留分两种:
    def test_造了没试删是我们的欠账(self):
        c = _chain()
        c["created"] = True
        finish_chain(c)
        assert c["residueKind"] == "residue_not_cleaned"

    def test_试了删没删掉是产品的(self):
        c = _chain()
        c["created"], c["deleteTried"] = True, True
        finish_chain(c)
        assert c["residueKind"] == "cleanup_failed"

    def test_两种残留在清单里各自成条(self):
        a, b = _chain("qa-probe-a"), _chain("qa-probe-b")
        a["created"] = True
        b["created"], b["deleteTried"] = True, True
        finish_chain(a)
        finish_chain(b)
        kinds = [f["kind"] for f in residue_findings([a, b])]
        assert kinds == ["residue_not_cleaned", "cleanup_failed"]

    def test_没造过东西就没有残留(self):
        c = _chain()
        finish_chain(c)
        assert residue_findings([c]) == []


class Test计数每一格都渲染:
    def test_一条链都没有时六个断点键一个不少(self):
        s = summarize_chains([])
        assert set(s["chainBreakpoints"]) == set(BREAKPOINTS)
        assert set(s["chainFacts"]) == set(CHAIN_FACTS)
        assert s["chainsAttempted"] == 0

    def test_写请求数分总数和被拒数(self):
        c = _chain()
        note_write(c, method="POST", path="/a", status=201)
        note_write(c, method="POST", path="/b", status=422)
        s = summarize_chains([c])
        assert (s["chainWrites"], s["chainWritesFailed"]) == (2, 1)


class Test声明说的是没量到不是没有:
    def test_一条链都没跑就说清楚这一维没量(self):
        lines = chain_declarations(summarize_chains([]))
        assert len(lines) == 1
        assert "不是 0，是没量" in lines[0]

    def test_按钮是灰的要说是这个角色量不到(self):
        # 「没有这个功能」和「这个角色不让建」处置完全不同：前者去问对方的
        # 清单，后者换个账号才量得到。合成一句就没法处置。
        lines = chain_declarations(summarize_chains([]), create_disabled=11,
                                   main_role="auditor")
        assert len(lines) == 1
        assert "灰的" in lines[0] and "auditor" in lines[0]
        assert "这个角色量不到" in lines[0]

    def test_没灰按钮时还是那句没量(self):
        lines = chain_declarations(summarize_chains([]), create_disabled=0)
        assert "不是 0，是没量" in lines[0]

    def test_开了没建成也要出声(self):
        c = _chain()
        note_breakpoint(c, "no_form")
        finish_chain(c)
        lines = chain_declarations(summarize_chains([c]))
        assert any("一条也没建成" in x for x in lines)


class Test填表判据跟产品无关:
    def test_验证码不许猜(self):
        got = plan_fill([{"isField": True, "label": "验证码", "id": "code",
                          "fieldType": "text", "required": True}], "qa-probe-x")
        assert got["fills"] == []
        assert [u["label"] for u in got["unfillable"]] == ["验证码"]
        # 有必填项填不出来 ⇒ 这张表单提交不了，那才是断点
        assert got["blocked"] is True

    def test_锚不住的框不凭序号编一个(self):
        got = plan_fill([{"isField": True, "label": "名称",
                          "fieldType": "text", "required": True}], "qa-probe-x")
        assert [u["kind"] for u in got["unfillable"]] == ["unanchorable"]

    def test_tag必须落进表单否则算填不了(self):
        # 建出来认不出、删不掉，比不建更坏 —— 所以 `blocked`。
        got = plan_fill([{"isField": True, "label": "数量", "id": "n",
                          "fieldType": "number", "required": True}], "qa-probe-x")
        assert got["tagPlaced"] is False
        assert got["blocked"] is True

    def test_必填抽不到就把能填的都填上(self):
        rows = [{"isField": True, "label": "名称", "id": "a", "fieldType": "text"},
                {"isField": True, "label": "备注", "id": "b", "fieldType": "text"}]
        got = plan_fill(rows, "qa-probe-x")
        assert got["requiredSeen"] is False
        assert len(got["fills"]) == 2 and got["blocked"] is False

    def test_日期取未来(self):
        assert value_for(field_kind({"fieldType": "date"}), "t").startswith("20")

    def test_按角色挑控件不认产品名(self):
        items = [{"label": "新建服务", "role": "button"},
                 {"label": "导出", "role": "button"}]
        assert matches_purpose("新建服务", "button", "create")
        assert pick_control(items, "create") is items[0]

    def test_开关和勾选框不算按钮(self):
        assert not matches_purpose("启用", "switch", "create")
