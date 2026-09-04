"""表单字段得进账本 —— 而且不能被当成「控件」来判。

上一趟真跑：1266 个可操作项里**一个输入框都没有**，看着像被测系统没有表单。
真相是枚举用的那句选择器只写了 `button/a/[role=…]/checkbox/radio`，
文本框、下拉框、多行文本压根不在里面。于是「这一页的表单覆盖了没」这个问题
连问都问不出来 —— 分母是 0，任何覆盖率都成立。

补进来之后有三条纪律，每条都对应一种「补了反而更糟」的写法：
① 字段**不过 `classify_control`**：那套词表判的是「点下去会不会写」；
② 字段**不进 G4/G5**：只读输入框不是「死按钮」，不挡会把真的死按钮淹掉；
③ 字段的文案**绝不取 `value`**：那是用户填进去的数据。
"""
from app.engine.surveys.qa_page_survey_crawl import _COLLECT_JS, collect_items
from app.services.qa_coverage_reconcile import compute_gaps


def _item(**kw):
    base = {"key": "", "page_path": "/x", "page_title": "", "anchor": "a",
            "anchor_kind": "testid", "label": "", "control_type": "read",
            "state": "enabled", "endpoints": []}
    base.update(kw)
    return base


class Test枚举那句选择器:
    def test_文本框下拉框多行文本都在里面(self):
        for frag in ("input:not([type=\"checkbox\"])", "select", "textarea",
                     '[role="combobox"]', '[contenteditable="true"]'):
            assert frag in _COLLECT_JS, f"{frag} 不在枚举选择器里"

    def test_按钮那一档一个都没丢(self):
        for frag in ("button", "a[href]", '[role="menuitem"]',
                     'input[type="checkbox"]', 'input[type="radio"]'):
            assert frag in _COLLECT_JS

    def test_字段的文案不许取_value(self):
        """`value` 是用户填进去的东西（用户名、密钥、备注）。

        取了就等于把被测环境的数据抄进我们的账本，还会顺着 diff 一路留档 ——
        HAR 那边凭据是**丢掉不是打码**，这里是同一条纪律。
        """
        field_branch = _COLLECT_JS.split("? (el.getAttribute('aria-label')")[1]
        field_branch = field_branch.split(":")[0]
        assert "value" not in field_branch
        assert "placeholder" in field_branch


class Test字段不走控件那套判定:
    def test_字段不算认不出来的控件(self):
        led: dict = {}
        rows = collect_items("/x", "", [
            {"label": "请输入名称", "role": "input", "id": "name", "isField": True},
        ], led)
        assert rows[0]["control_type"] == "field"
        assert led.get("controlsUnknown", 0) == 0, "字段污染了 controlsUnknown"
        assert led["fieldsSeen"] == 1

    def test_必填单独记一笔(self):
        led: dict = {}
        collect_items("/x", "", [
            {"label": "名称", "role": "input", "id": "n1", "isField": True,
             "required": True},
            {"label": "备注", "role": "textarea", "id": "n2", "isField": True},
        ], led)
        assert led["fieldsSeen"] == 2
        assert led["fieldsRequired"] == 1

    def test_只读字段算_present(self):
        rows = collect_items("/x", "", [
            {"label": "编号", "role": "input", "id": "code", "isField": True,
             "readonly": True},
        ], {})
        assert rows[0]["state"] == "present"

    def test_按钮照旧走词表(self):
        led: dict = {}
        rows = collect_items("/x", "", [
            {"label": "删除", "role": "button", "id": "del"},
            {"label": "查看", "role": "button", "id": "view"},
        ], led)
        assert [r["control_type"] for r in rows] == ["write", "read"]


class Test字段不进缺口:
    def test_只读字段不会变成_G5(self):
        """不挡的话每个只读输入框都是一条 G5「情报」，真正的死按钮会被淹掉。"""
        out = compute_gaps(
            page_items=[_item(control_type="field", state="present", anchor="f1"),
                        _item(control_type="read", state="present", anchor="btn")],
            page_edges=[], routes=[], index={"byDomain": {}, "unresolved": []},
            scripts=[], build_fingerprint="", controls_clicked=0)
        g5_anchors = [r["anchor"] for r in out["g5"]]
        assert not any("f1" in a for a in g5_anchors)
        assert any("btn" in a for a in g5_anchors), "真的死按钮还得报"

    def test_字段的数不许丢(self):
        """挡掉不等于当它不存在 —— 它是「表单覆盖了没」的分母。"""
        out = compute_gaps(
            page_items=[_item(control_type="field", state="enabled", anchor="f1"),
                        _item(control_type="field", state="present", anchor="f2")],
            page_edges=[], routes=[], index={"byDomain": {}, "unresolved": []},
            scripts=[], build_fingerprint="", controls_clicked=1)
        assert out["counters"]["fieldsSeen"] == 2
