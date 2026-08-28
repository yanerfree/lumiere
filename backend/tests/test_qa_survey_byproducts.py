"""页面枚举两个副产品的判定部分（S6.5）。

Test ID: qa-survey-byproducts-UT-001

这个文件守的是同一条纪律的第三次出现：**信号弱的时候不许下结论。**
- S6.4 是「没走到那页」不许写成「功能没了」；
- 这里是「这一趟只找到文案锚点」不许写成「前端把 testid 拿掉了」，
  以及「对不上这个模块的页面」不许拿整个产品的控件去凑。

两个方向都要钉住：降级规则各配一条**反向锚点**（真不一致必须报出来、
真对得上必须给得出控件），否则「一律不报」也能让全部测试变绿。

写库那半边在根级 `tests/integration/services/test_qa_survey_byproducts.py`。
"""
import pytest

from app.services import qa_survey_byproducts as b


def _item(page="/svc", anchor="new-btn", kind="testid", label="新建服务",
          title="服务管理", ctype="write", key=None) -> dict:
    return {"key": key or f"{page}::{anchor}", "page_path": page, "page_title": title,
            "anchor": anchor, "anchor_kind": kind, "label": label,
            "control_type": ctype, "state": "enabled"}


class Test爬到的锚点变成登记行:
    def test_稳定锚点登记成_active(self):
        c = b.candidates_from_items([_item()])[0]
        assert c["status"] == "active"
        assert c["selector"] == '[data-testid="new-btn"]'
        assert c["kind"] == "testid"

    def test_只能靠文案的登记成_gap_且不留选择器(self):
        """留着凑合的选择器，下一个人会直接拿去用，
        于是"去前端补 testid"永远不会发生。"""
        c = b.candidates_from_items([_item(anchor="批准", kind="text", label="批准")])[0]
        assert c["status"] == "gap"
        assert c["selector"] is None
        assert "data-testid" in c["gap_note"] and "MR" in c["gap_note"]

    @pytest.mark.parametrize("kind", ["", "style", "structure", "什么鬼"])
    def test_认不出的锚点一律落_gap(self, kind):
        """**往不稳的那边偏。** 认不出还登记成 active，等于往公共资产里塞一条
        空选择器 —— 替进脚本之后「不应出现」那类断言会集体假绿。"""
        c = b.candidates_from_items([_item(kind=kind)])[0]
        assert c["status"] == "gap" and c["selector"] is None

    def test_锚不住的控件不出行(self):
        assert b.candidates_from_items([_item(anchor="")]) == []

    def test_key_取锚点原值不取文案(self):
        """testid 锚的行，前端改文案 key 不能动 —— 动了脚本里那句
        `${SEL:...}` 当场失效，而且会再登记出一行重复的。"""
        a = b.candidates_from_items([_item(label="新建服务")])[0]["key"]
        c = b.candidates_from_items([_item(label="创建服务（新）")])[0]["key"]
        assert a == c == "svc.new-btn"

    def test_同一批跑两次给出同样的东西(self):
        rows = [_item(anchor="b"), _item(anchor="a"), _item(page="/env", anchor="c")]
        assert (b.candidates_from_items(rows)
                == b.candidates_from_items(list(reversed(rows))))

    def test_key_不超过登记表的字段长度(self):
        long = b.candidates_from_items([_item(page="/" + "a" * 300, anchor="x" * 300)])[0]
        assert len(long["key"]) <= 200


class Test选择器还原走同一套词表:
    """survey 存的是锚点**原值**（`new-btn`），登记表要的是选择器字面量
    （`[data-testid="new-btn"]`）—— 中间这次还原**必须走 `anchor_selector`**。
    照着 f-string 另拼一遍就是第二套词表，登记表的 `kind` 和 survey 的
    `anchor_kind` 一旦对不上，「爬到的与登记不符」那条待整改就永远报不准。
    """

    @pytest.mark.parametrize("kind,kw", [
        ("testid", {"testid": "new-btn"}),
        ("id", {"elem_id": "new-btn"}),
        ("text", {"text": "new-btn"}),
    ])
    def test_三档各还原各的且跟拼选择器那支一字不差(self, kind, kw):
        from app.services.ui_selector_render import anchor_selector, selector_of_item
        assert selector_of_item(anchor="new-btn", anchor_kind=kind) == anchor_selector(**kw)

    def test_认不出的_kind_返回空不猜(self):
        """返回空的方向是对的：`upsert_selectors` 硬拦空选择器，
        于是它只能落 `gap`。猜一个出来就成了 active，替进脚本之后
        「不应出现」那类断言集体假绿。"""
        from app.services.ui_selector_render import selector_of_item
        assert selector_of_item(anchor="x", anchor_kind="style") == ""
        assert selector_of_item(anchor="x", anchor_kind="") == ""


class Test爬到的与登记不符:
    def _existing(self, **kw):
        return {"svc.new-btn": {"selector": '[data-testid="new-btn"]', "status": "active",
                                "kind": "testid", "source": "manual", **kw}}

    def test_两边都是稳定抓手却不一样才报(self):
        d = b.disagreements(b.candidates_from_items([_item()]),
                            self._existing(selector='[data-testid="OLD"]'))
        assert len(d) == 1 and d[0]["爬到的"] == '[data-testid="new-btn"]'

    def test_一样就不报(self):
        assert b.disagreements(b.candidates_from_items([_item()]), self._existing()) == []

    def test_爬到的更弱不算不符(self):
        """登记的是 testid、这一趟只找到文案 —— 最可能是**这一趟没看清**
        （渲染时机、当前角色、列表空状态），不是前端把 testid 拿掉了。
        报出来人会去查一个不存在的改动，查两次这份清单就没人信了。"""
        cands = b.candidates_from_items([_item(anchor="新建服务", kind="text")])
        ex = {cands[0]["key"]: {"selector": '[data-testid="new-btn"]', "status": "active",
                                "kind": "testid", "source": "manual"}}
        assert b.disagreements(cands, ex) == []

    def test_这一趟没抓住的不报(self):
        """跟上一条同一条纪律，但单独钉住 **status** 这一半：
        候选行的 kind 明明是稳的、这一趟就是没抓住（status=gap）——
        那是"这趟没看清"，不是"前端改了"。上一条被 kind 那道守卫兜着，
        守卫叠守卫会让两道各自都没人测。"""
        cands = [{"key": "svc.new-btn", "selector": None,
                  "kind": "testid", "status": "gap"}]
        assert b.disagreements(cands, self._existing()) == []

    def test_登记着_gap_而爬到了抓手要报出来(self):
        """反方向的好消息：前端可能已经把 testid 补上了，
        该有人回来把这行改 active，被它卡住的用例才会自己冒出来。"""
        d = b.disagreements(b.candidates_from_items([_item()]),
                            self._existing(selector=None, status="gap"))
        assert len(d) == 1 and "补上" in d[0]["怎么回事"]

    def test_爬取自己登记的行不算不符(self):
        """它下一趟本来就会被原地更新，报出来是给人添一件不用做的事。"""
        assert b.disagreements(b.candidates_from_items([_item()]),
                               self._existing(selector='[data-testid="OLD"]',
                                              source="crawl")) == []

    def test_status_写着_active_但_kind_不稳的不报(self):
        """`disagreements` 是公共纯函数，谁都能喂它候选行。
        「active」和「kind 稳」是两件事，只信前者的话，哪天上游放松了
        这里就会拿样式类去跟人工登记的 testid 比，比一次报一条假的。"""
        cands = [{"key": "svc.new-btn", "selector": ".ant-btn-primary",
                  "kind": "style", "status": "active"}]
        assert b.disagreements(cands, self._existing()) == []

    def test_登记表里没有的不算不符(self):
        assert b.disagreements(b.candidates_from_items([_item()]), {}) == []


class Test模块对页面:
    def test_标题对得上就算这一页(self):
        assert b.module_pages("服务管理", [_item(title="服务管理")]) == {"/svc"}

    def test_模块名更长也算(self):
        """目录叫「服务管理·本租户」、页面标题只写「服务管理」—— 互为子串都算。"""
        assert b.module_pages("服务管理·本租户", [_item(title="服务管理")]) == {"/svc"}

    def test_对不上就一页都不给(self):
        """**认不出时返回空，不是"整个产品的控件"。** 把别的模块的按钮塞进来，
        产出的是「这个模块没测导出功能」这种查一次就发现不存在的假缺口。"""
        assert b.module_pages("订阅管理", [_item(title="服务管理")]) == set()

    @pytest.mark.parametrize("m", [None, "", "   "])
    def test_没给模块名不瞎猜(self, m):
        assert b.module_pages(m, [_item()]) == set()


class Test可操作项成行:
    def test_顺序排死(self):
        rows = [_item(anchor="b", label="B"), _item(anchor="a", label="A"),
                _item(page="/env", anchor="c", label="C")]
        assert b.observed_lines(rows) == b.observed_lines(list(reversed(rows)))

    def test_一样的行只出一次(self):
        assert len(b.observed_lines([_item(anchor="a"), _item(anchor="b")])) == 1

    def test_没文案就用锚点(self):
        assert "new-btn" in b.observed_lines([_item(label="")])[0]


class Test截断要说出来:
    """`scriptsRead` 数的是读到的份数、模型只看了一部分 —— 洞四就是这个病。
    这一列 S6.5 之后是自动读的，一个模块几百个控件很正常。"""

    def _cases(self):
        class C:
            case_code, title, steps = "TC-X-00001", "建服务", []
        return [C()]

    async def _prompt_of(self, monkeypatch, actions):
        from app.services.review import checkup

        seen = {}

        class R:
            content = '```json\n{"coverageGaps": []}\n```'

        async def fake(msgs, **kw):
            seen["user"] = msgs[-1]["content"]
            return R()

        monkeypatch.setattr(checkup.llm_client, "complete", fake)
        await checkup.coverage_gaps(None, self._cases(), object(), actions)
        return seen["user"]

    @pytest.mark.asyncio
    async def test_超过上限要在正文里说清楚(self, monkeypatch):
        from app.services.review.checkup import MAX_ACTIONS
        n = MAX_ACTIONS + 7
        user = await self._prompt_of(monkeypatch, [f"页 · 控件{i}" for i in range(n)])
        assert f"共 {n} 个可操作项" in user
        assert user.count("- 页 · 控件") == MAX_ACTIONS

    @pytest.mark.asyncio
    async def test_没超就不啰嗦(self, monkeypatch):
        user = await self._prompt_of(monkeypatch, ["页 · 控件A", "页 · 控件B"])
        assert "只列了前" not in user
        assert "控件B" in user


class Test登记入口只认两种来源:
    @pytest.mark.asyncio
    async def test_乱填_source_直接拒(self):
        """`source` 决定"能不能压过人工登记的行"，写错一个字就是静默越权。"""
        from app.mcp.tools.selectors import upsert_selectors
        out = await upsert_selectors(None, "not-a-uuid", [{"key": "a"}], source="auto")
        assert "error" in out and "source" in out["error"]
