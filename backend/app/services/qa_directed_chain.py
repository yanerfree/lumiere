"""有向链路：**造一条自己的数据，再在它身上把这个域走完**（需求 §12）。

判据全在这里 —— **纯函数，零 IO，零模型，零业务名词**。它不认「团队」
「适配器」「提示词模板」，只认两件事：「这个控件的文案是干什么的」和
「这一行的文本里有没有我刚才那个前缀」。换个域、换个产品照样成立。

## 为什么非做不可

无向枚举的天花板是**「页面此刻长什么样」**，而绝大多数功能**在空列表上
根本不存在**：没有一条数据，「编辑 / 详情 / 删除」一个都不渲染，详情页和
子标签页进都进不去，**一个写接口都观测不到**。上一趟量出来 668 条页面级边
全是 GET，而报告里那 120 条 G2 大头正是写接口 —— 「他没测」和
「我们没让页面发出来过」在那一格里是**同一个数**。

## 凭什么允许它写

§1.3 立的「爬虫不许点写按钮」是对的，但**理由**是「它不知道自己造了什么、
也清理不掉」—— 不是「环境归谁」。所以反过来：

> 自己亲手造出来的那一条数据，我们知道它是什么、知道它的名字、也知道怎么删。
> **在它身上做的任何操作都是有向的。**

于是两条判据（都跟域和产品无关）：

1. **新建**：每一页最多走一次全程。造出来的东西名字带**固定前缀 + 随机尾巴**
   （`qa-probe-7f3a`）—— 这是它后面能被认出来、也能被清掉的**唯一凭据**。
2. **只在自己造的那一行上继续**：列表里匹配那个前缀的行，它上面的
   编辑 / 详情 / 停用 / 删除都可以点。**别人的数据、系统预置的数据，
   一个按钮都不碰** —— 那部分仍然走无向那套规矩。

## 这里最容易出的错，方向是一样的：把「没做到」写成「没有」

- 表单填不出来（要验证码 / 要上传 / 依赖另一条数据）**是一条明账，不是一个 0**。
  猜不出必填项就悄悄跳过，账本上和「这一页没有新建功能」一模一样，
  而报告会显得非常干净。
- 断点必须**逐格分开**（`BREAKPOINTS`）：「找不到表单」「提交被服务端拒了」
  「建成了但列表里找不到自己那一行」是三件完全不同的事，
  塌成一句「这个域没有新建功能」等于把三条发现一起丢掉。
- **删不掉是最值钱的那种发现**（产品 bug），而且**同时必须报环境残留** ——
  两件事都要说，只报一件就会有人把它当成"下次再说"。
"""
from __future__ import annotations

import secrets

from app.services.qa_survey_guard import word_hit

# ── 自己那条数据的凭据 ────────────────────────────────────────────────────

# 固定前缀。**不许配置成可改的**：它是"这条数据是我们造的"的唯一判据，
# 改一次，上一趟的残留就再也认不出来了（认不出的残留 = 别人的数据 = 谁都不敢删）。
PROBE_PREFIX = "qa-probe"

# 随机尾巴的字节数（2 → 4 个 hex 字符）。够短，塞得进大多数「名称」框的长度限制；
# 够长，同一天跑几十趟不会撞。**撞了的后果是拿别人那趟的行去做写操作**，
# 所以宁可长一点也不能省 —— 但真正防撞的是"只在自己这趟的完整 tag 上动手"。
TAG_TAIL_BYTES = 2


def new_probe_tag(tail: str = "") -> str:
    """`qa-probe-7f3a` —— 这一趟的凭据。`tail` 只给测试用。

    **全小写 + 只有字母数字和一个短横**：很多产品的「名称/编码」字段有
    格式校验（不许空格、不许大写、不许中文）。凭据一旦被格式校验挡下来，
    整条链断在第一步，而断的原因看起来像"这个产品没有新建功能"。
    """
    return f"{PROBE_PREFIX}-{tail or secrets.token_hex(TAG_TAIL_BYTES)}"


def is_mine(text: str, tag: str) -> bool:
    """这段文本（列表里的一行）是不是**这一趟**造的那条。

    判据在**文本前缀**上，不在按钮名字上 —— 名字随产品变，前缀不变。
    `tag` 空一律 False：空 tag 会让 `in` 恒真，那等于把整张列表都认成自己的。
    """
    if not tag or not text:
        return False
    return tag.lower() in text.lower()


def looks_like_probe(text: str) -> bool:
    """看着像**某一趟**探测造的（前缀对，尾巴不是这一趟的）。

    只用来**报残留**，不用来授权写操作 —— 上一趟（或另一个人同时在跑的那趟）
    的行不许碰：正在跑的那条被我们删掉，对方会在"建成功但找不到自己那一行"
    那一格看到一条查不出原因的假发现。
    """
    if not text:
        return False
    return f"{PROBE_PREFIX}-" in text.lower()


# ── 控件干什么用的：链路自己的词表 ───────────────────────────────────────
#
# 和 `qa_survey_guard.classify_control` 不是一回事，别合：
# 那套答的是「点下去会不会写」（无向枚举用它避险），这套答的是
# 「我要找的那个按钮是哪一个」（有向链路用它认路）。合成一套的话，
# 「保存」既要判成"危险别点"又要判成"就是要点它"，必然有一头是错的。
#
# 匹配规则借 `word_hit`（ASCII 认词边界、中文认子串）—— **不许再写一套**：
# 一分叉，`Created At` 那种表头就会重新被认成「新建按钮」。
_PURPOSE_WORDS: dict[str, tuple[str, ...]] = {
    # 「新建」这一档**故意不含「编辑」「配置」**：那些也是开层按钮，
    # 但点开的是别人那条数据的表单 —— 有向链路的第一步只能是造自己的。
    "create": ("新建", "创建", "添加", "新增", "new", "create", "add"),
    # 层里的那一下。「确定」放在这儿也放在 confirm 里 —— 两处都可能是它，
    # 用哪一个由调用方按当时在找什么决定（见 `matches_purpose` 的说明）。
    "submit": ("保存", "提交", "确定", "确认", "完成", "save", "submit", "ok",
               "confirm", "done", "create", "新建", "创建"),
    # 二次确认框上的那一下。**这是唯一允许点「确认删除」的地方** ——
    # 前提是我们自己刚点的删除、删的是自己那一行。
    "confirm": ("确定", "确认", "是", "删除", "ok", "yes", "confirm", "delete"),
    "edit": ("编辑", "修改", "更新", "edit", "modify", "update"),
    "delete": ("删除", "移除", "delete", "remove"),
    "detail": ("详情", "查看", "detail", "view", "detail"),
}

PURPOSES = tuple(_PURPOSE_WORDS)

# 角色本身就说明它不是那个按钮：开关点一下是改状态，不是提交表单。
# （同 `classify_control` 的纪律：**角色先于文案**。）
_NOT_A_BUTTON_ROLES = ("switch", "checkbox", "radio", "slider", "spinbutton")


def matches_purpose(label: str, role: str = "", purpose: str = "") -> bool:
    """这个控件是不是**我现在要找的那一个**。

    刻意做成「按目的问」而不是「给控件分个类」：`确认删除` 同时命中
    `confirm` 和 `delete`，做成单一分类就得排个优先级，而排出来的那个顺序
    在"找删除按钮"和"找确认按钮"两个场合里必然有一个是错的。
    按目的问，两个场合各自都对。
    """
    words = _PURPOSE_WORDS.get(purpose or "")
    if not words:
        return False
    if (role or "").strip().lower() in _NOT_A_BUTTON_ROLES:
        return False
    text = (label or "").strip()
    if not text:
        return False
    return any(word_hit(text.lower(), w) for w in words)


def pick_control(items, purpose: str, *, allow_disabled: bool = False):
    """从一堆枚举出来的控件里挑出**这个目的**的那一个。返回原始行或 `None`。

    `allow_disabled=True` 用来分开两件事：**没有这个入口** 和
    **入口在但是灰的**。前者是「这个对象确实不可编辑」（记成事实），
    后者是「当前状态下不让编辑」（那是一条业务规则，更值钱）——
    合成一个 `None` 就永远分不出来。

    按 `_PURPOSE_WORDS` 的**词序**挑：先命中的词优先，同一个词内按枚举顺序。
    不按 DOM 顺序挑第一个 —— 那会让「批量删除」抢在行内「删除」前面。
    """
    words = _PURPOSE_WORDS.get(purpose or "")
    if not words:
        return None
    for w in words:
        for raw in items or []:
            if raw.get("isField"):
                continue
            if not allow_disabled and raw.get("disabled"):
                continue
            role = (raw.get("role") or "").strip().lower()
            if role in _NOT_A_BUTTON_ROLES:
                continue
            label = (raw.get("label") or "").strip()
            if label and word_hit(label.lower(), w):
                return raw
    return None


# ── 表单：填什么、填不出来的记明账 ───────────────────────────────────────

# 填不出来的原因。**每一种都得有名字** —— 统称"填不出来"的话，
# 「要验证码」（永远填不出来，认了）和「下拉是空的」（可能是依赖没造，
# 是一条发现）会被一起当成我们的欠账。
UNFILLABLE = {
    "upload": "要上传文件",
    "captcha": "要验证码",
    "unanchorable": "定位不到这个框（没有 testid/id/name/placeholder）",
    "interactive": "要在页面上交互才能选（下拉/日期选择器）",
    "fill_failed": "填不进去（框被别的东西遮住 / 是自定义组件不吃 fill）",
    "no_option": "下拉点开之后一个选项都没有（它依赖的数据可能压根没造出来）",
}

_CAPTCHA_WORDS = ("验证码", "captcha", "verify code", "verification")

# 填进去的值。**全是常量，不猜业务** —— 猜出来的值被服务端拒掉，
# 报出来是 `submit_failed`，那一格恰好会带上服务端的原文，比我们猜得准。
_NUMBER = "1"
_DATE = "2030-01-01"        # 故意取未来：「不得早于今天」这类校验能过
_TIME = "12:00"
_TEL = "13000000000"


def field_kind(field) -> str:
    """这个框是什么类型的框。判据只用**标记**（type/tagName/role），不用文案。

    只有一处例外：验证码。它在标记上就是个普通文本框，认不出来的话
    我们会往里填 `qa-probe-7f3a`，然后拿一个 400 去报「提交失败」——
    那是一条**假发现**（服务端没错，是我们填了个假验证码）。
    """
    if field.get("disabled") or field.get("readonly"):
        return "skip"
    ftype = (field.get("fieldType") or "").strip().lower()
    role = (field.get("role") or "").strip().lower()
    label = (field.get("label") or "").strip().lower()
    if any(word_hit(label, w) for w in _CAPTCHA_WORDS):
        return "captcha"
    if ftype == "file":
        return "upload"
    if ftype == "select" or role in ("select", "combobox"):
        return "select"
    if ftype in ("number", "range"):
        return "number"
    if ftype in ("date", "datetime-local", "month", "week"):
        return "date"
    if ftype == "time":
        return "time"
    if ftype == "tel":
        return "tel"
    if ftype == "email":
        return "email"
    if ftype == "url":
        return "url"
    if ftype == "password":
        return "password"
    return "text"


def value_for(kind: str, tag: str) -> str:
    """这个类型的框填什么。`""` = 填不出来 / 要在页面上交互。

    文本框一律填 `tag` 本身（不是"tag + 一句好看的描述"）：那一串是
    后面认出自己那一行的**唯一**凭据，混了别的字进去，
    列表里被截断显示（`qa-pro…`）就再也匹配不上。
    """
    if kind == "text":
        return tag
    if kind == "email":
        return f"{tag}@example.invalid"
    if kind == "url":
        return f"https://{tag}.example.invalid"
    if kind == "password":
        # 大小写 + 数字 + 符号：绝大多数强度校验的最小公倍数。
        return f"{tag}-Aa1!"
    if kind == "number":
        return _NUMBER
    if kind == "date":
        return _DATE
    if kind == "time":
        return _TIME
    if kind == "tel":
        return _TEL
    return ""


def edit_value(tag: str) -> str:
    """编辑那一步往文本框里填什么。

    **必须还带着 `tag`**：改完认不出来是自己那一行，后面的删除就没法做，
    于是一条改过名的残留永远留在环境里，而账本上写的是"删掉了"。
    所以是**加后缀**，不是换一个新名字。
    """
    return f"{tag}-e"


def field_selector(field) -> str:
    """把这个框锚住。锚不住返回 `""` —— **不凭序号编一个**。

    顺序就是稳定性顺序（testid > id > name > placeholder > aria-label），
    同 `ui_selector_render.infer_kind` 那套等级。带双引号的属性值直接放弃：
    转义一歪会锚到别的框上，而"填错了框"报出来是提交失败，查不到原因。
    """
    for attr, key in (("data-testid", "testid"), ("id", "id"),
                      ("name", "name"), ("placeholder", "placeholder"),
                      ("aria-label", "ariaLabel")):
        val = (field.get(key) or "").strip()
        if val and '"' not in val and "\\" not in val:
            return f'[{attr}="{val}"]'
    return ""


def plan_fill(fields, tag: str) -> dict:
    """一张表单的字段 → **填哪些、怎么填、哪些填不出来**。

    两条判据：

    1. **必填抽得到就只填必填**（外加"保证 tag 至少落在一个文本框里"）——
       少碰一个框少一份猜错格式的风险。
    2. **必填一个都抽不到就把能填的都填上**。`required` 靠 `required` /
       `aria-required` 抽，而**很多产品用样式类标必填**（红星画在 label 上）。
       抽不到时只填一个框，提交必然 400，而那条 400 长得像"我们填错了值"，
       实际是"我们少填了三个框"。

    `blocked` = **有必填项填不出来**，那才是断点 `form_unfillable`。
    非必填填不出来只记明账不断链 —— 记着是为了下次能问「这个域有几张表单
    我们压根填不了」，那是我们的欠账清单，不是产品的缺口。
    """
    rows = [f for f in (fields or []) if f.get("isField")]
    required_seen = any(f.get("required") for f in rows)
    fills: list[dict] = []
    unfillable: list[dict] = []
    tag_placed = False

    for f in rows:
        kind = field_kind(f)
        if kind == "skip":
            continue
        label = (f.get("label") or "").strip()
        required = bool(f.get("required"))
        sel = field_selector(f)
        if not sel:
            unfillable.append({"label": label, "kind": "unanchorable",
                               "why": UNFILLABLE["unanchorable"],
                               "required": required})
            continue
        if kind in ("upload", "captcha"):
            unfillable.append({"label": label, "kind": kind,
                               "why": UNFILLABLE[kind], "required": required})
            continue
        if kind == "select":
            # 下拉不是"填"，是"点开挑一个"。值只能在页面上现场拿 ——
            # 这里出一格 `interactive`，由驱动那边去点；点开发现一个选项
            # 都没有的，才是"依赖的数据没造出来"，那一格由驱动补。
            fills.append({"label": label, "kind": "select", "value": "",
                          "required": required, "selector": sel})
            continue
        value = value_for(kind, tag)
        if not value:
            unfillable.append({"label": label, "kind": "interactive",
                               "why": UNFILLABLE["interactive"],
                               "required": required})
            continue
        if required or not required_seen:
            fills.append({"label": label, "kind": kind, "value": value,
                          "required": required, "selector": sel})
            tag_placed = tag_placed or (tag in value)

    if not tag_placed:
        # tag 一个字都没落到表单里 ⇒ 建出来的东西认不出来也删不掉。
        # 补一个文本框（第一个能填的），**宁可多碰一个框**：
        # 认不出自己造的数据比填错一个可选框严重得多。
        for f in rows:
            if field_kind(f) != "text":
                continue
            sel = field_selector(f)
            if not sel or any(x["selector"] == sel for x in fills):
                continue
            fills.append({"label": (f.get("label") or "").strip(),
                          "kind": "text", "value": tag,
                          "required": bool(f.get("required")), "selector": sel})
            tag_placed = True
            break

    return {"fills": fills, "unfillable": unfillable,
            "requiredSeen": required_seen, "tagPlaced": tag_placed,
            # 有必填项填不出来 = 这张表单我们提交不了。
            # **tag 落不进去也算 blocked**：建出来认不出、删不掉，
            # 那比不建更坏（直接留一条永久残留）。
            "blocked": bool([u for u in unfillable if u["required"]])
                       or not tag_placed}


# ── 一条链走到哪儿、断在哪儿 ─────────────────────────────────────────────

# 顺序是固定的。每一环**都记它发出去的请求** —— 那才是拿去和 QA 脚本对账的东西。
CHAIN_STEPS = ("create", "list", "detail", "edit", "verify", "delete", "confirm")

# 断点逐格分开（需求 §12.3 那张表）。`owner` 说这一格是谁的问题 ——
# 混着看会让「我们没认出层」和「产品删不掉」排在同一个待办里。
#
#   ours    我们的欠账（要么补能力，要么认了并留痕）
#   product 产品缺陷（`delete_failed` 是最值钱的那一种）
#   finding 这本身是一条发现（不一定是缺陷，但值得去问）
#   fact    就是这样，不当缺口
#   unknown 分不出是谁的 —— **不许默认归给自己**，归了就没人去查产品那一半
BREAKPOINTS: dict[str, dict] = {
    "no_form": {"label": "找不到表单", "owner": "unknown",
                "why": "点了「新建」没弹层也没跳页 —— 可能是产品的死按钮，"
                       "也可能是我们没认出它的层"},
    "form_unfillable": {"label": "表单填不出来", "owner": "ours",
                        "why": "有必填项猜不出（依赖另一条数据 / 要验证码 / "
                               "要上传文件）。**是明账不是 0**"},
    "submit_failed": {"label": "提交被拒", "owner": "ours",
                      "why": "服务端 4xx/5xx —— 填的值不合规。"
                             "报错原文是最好的线索，一并记下"},
    "row_not_found": {"label": "建成了但找不到自己那一行", "owner": "finding",
                      "why": "列表没刷新 / 分页在后面 / 需要审批才可见 —— "
                             "**这本身就是一条发现**"},
    "row_unscoped": {"label": "找到了那一行但圈不出它的范围", "owner": "ours",
                     "why": "文本在页面上，但认不出它属于哪一行（表格结构没见过）。"
                            "**这时一个写按钮都不许点** —— 点了可能删的是别人那条"},
    "delete_failed": {"label": "删不掉", "owner": "product",
                      "why": "自己造的数据删不了 —— **产品 bug，而且是最值钱的"
                             "那种发现**；同时必须报环境残留"},
}


# 「记成事实，不当缺口」的那一档（需求 §12.3 对「找不到编辑入口」的原话）。
# **和断点分开两本**：断点是"链断在这儿了"，事实是"链照着走完了，只是这个
# 对象没有这个入口"。塞进 `BREAKPOINTS` 的话，一条走完并且清理干净的链会因为
# 「没有编辑按钮」被算成没走完 —— 然后残留那一格开始报假账。
CHAIN_FACTS: dict[str, dict] = {
    "no_edit_entry": {"label": "找不到编辑入口",
                      "why": "这个对象确实不可编辑，也可能编辑藏在详情页里。"
                             "**不当缺口** —— 但要能看见，不然"
                             "「这个域没有编辑功能」谁都不会去核"},
    "no_delete_entry": {"label": "找不到删除入口",
                        "why": "自己造的东西没法从页面上删掉。产品上可能就是这样"
                               "（只能停用），但**这一条必然留残留** —— "
                               "残留清单里会有它"},
    "no_detail_entry": {"label": "找不到详情入口",
                        "why": "列表页就是全部，没有下一层。那么"
                               "「详情页/子标签页没覆盖」这句话对这个域不成立"},
}


def new_chain(page_path: str, tag: str) -> dict:
    """开一条链的账。**每一格都先摆在这儿，0 也摆** —— 只在发生时才出现的键，
    在页面上和「没记过」长得一模一样。
    """
    return {"page": page_path, "tag": tag,
            "steps": [], "writes": [],
            # 断点只记**第一个**：链是有序的，断了后面就没走过，
            # 后面那些格子写"失败"是一句我们没验证过的话。
            "breakpoint": "", "breakpointDetail": "",
            "created": False, "deleteTried": False, "deleted": False,
            "unlockedPaths": [], "unfillable": [], "facts": [],
            "residue": False, "residueKind": "",
            # §14.1 每点一步要落**四样**（规则 / 状态 / 动作面 / 结构），
            # 这里只是账本上的四个格子 —— 判据全在 `qa_domain_map`，
            # 那边零业务名词、换个产品照样成立。
            #
            # `hints` 存**原文**（提示原文就是规则本身，压成 pass/fail 就查不回来了）；
            # `states` 是我们那一行的状态文本按时间顺序；
            # `surface` 是每一步枚举到的能点的东西（带「属于哪一行/哪一层」）；
            # `sections` 是每一步的区块标题（差集 = 建完才出现的结构）;
            # `cells` 是列表每行的单元格文本（拿来**数**这个对象一共有几种状态）。
            "hints": [], "states": [], "surface": [], "sections": [],
            "cells": [],
            # 哪几层我们**真的去枚举过**。它和「枚举到了几个」是两件事：
            # 探过、一个都没有 = 这个产品这一层就是空的（一条事实）；
            # 压根没探 = 我们的欠账（§15.3 的 `unreached`）。
            # 合成一个数之后，后者会伪装成前者。
            "probed": []}


def note_step(chain: dict, step: str, *, ok: bool, detail: str = "",
              control: str = "") -> dict:
    """记一环。`step` 不在 `CHAIN_STEPS` 里的一律拒 —— 打错一个字
    （`verfiy`）会安静地多出一环，而"多出来的环"没人对得上。

    `control` 是**这一环点的是哪个控件**（§13.6 那张表的「页面上点哪儿」一列）。
    空串 = 这一环没点任何东西（回列表确认那种），不是"没记"。
    """
    if step not in CHAIN_STEPS:
        raise ValueError(f"不是链上的环：{step}")
    chain["steps"].append({"step": step, "ok": bool(ok), "detail": detail,
                           "control": control})
    return chain


def note_breakpoint(chain: dict, kind: str, *, detail: str = "") -> dict:
    """记断点。**只记第一个**，后来的一律忽略（不是覆盖）。"""
    if kind not in BREAKPOINTS:
        raise ValueError(f"不是断点：{kind}")
    if not chain["breakpoint"]:
        chain["breakpoint"] = kind
        chain["breakpointDetail"] = detail
    return chain


def note_fact(chain: dict, kind: str, *, detail: str = "") -> dict:
    """记一条事实。**不打断链**（和 `note_breakpoint` 唯一的区别，也是全部区别）。"""
    if kind not in CHAIN_FACTS:
        raise ValueError(f"不是链上的事实：{kind}")
    if kind not in [f["kind"] for f in chain["facts"]]:
        chain["facts"].append({"kind": kind, "detail": detail})
    return chain


def note_write(chain: dict, *, method: str, path: str, status,
               body: str = "") -> dict:
    """记一条链上发出去的写请求。**这是 §12.5 那句「P 边第一次会有写接口」
    的实体** —— 没有它，这一批做完报告上还是 668 条全 GET。

    `body` 只在**非 2xx** 时留，而且截断：服务端的报错原文是最好的线索，
    但 2xx 的响应体里是被测环境的业务数据，一个字都不该抄进我们的账本。
    """
    ok = isinstance(status, int) and 200 <= status < 300
    row = {"method": (method or "").upper(), "path": path or "",
           "status": status, "ok": ok}
    if not ok and body:
        row["error"] = body[:300]
    chain["writes"].append(row)
    return chain


def finish_chain(chain: dict) -> dict:
    """收尾：算 `completed` 和**残留**。

    残留分两种，跟 `review/residue.py` 一套词：
      · `residue_not_cleaned` —— 造了、**一次删都没试过**（我们的：链断在前面了）
      · `cleanup_failed` —— 删了没删掉（产品的：`delete_failed`）
    两者的处置完全不同，合成一句「有残留」就分不出该找谁。
    """
    chain["completed"] = bool(chain["created"] and chain["deleted"]
                              and not chain["breakpoint"])
    chain["residue"] = bool(chain["created"] and not chain["deleted"])
    if chain["residue"]:
        chain["residueKind"] = ("cleanup_failed" if chain["deleteTried"]
                                else "residue_not_cleaned")
    else:
        chain["residueKind"] = ""
    return chain


def summarize_chains(chains) -> dict:
    """一趟里所有链 → 计数。**每一格都渲染，0 也渲染。**

    `chainBreakpoints` 里 6 个断点键**一个不少**：只列出发生过的，
    那么「这一批一次都没断在删除上」和「我们压根没走到删除」在页面上一样。
    """
    rows = list(chains or [])
    counts = {k: 0 for k in BREAKPOINTS}
    facts = {k: 0 for k in CHAIN_FACTS}
    writes = writes_failed = unlocked = unfillable = 0
    for c in rows:
        bp = c.get("breakpoint") or ""
        if bp in counts:
            counts[bp] += 1
        for f in c.get("facts") or []:
            if f.get("kind") in facts:
                facts[f["kind"]] += 1
        for w in c.get("writes") or []:
            writes += 1
            if not w.get("ok"):
                writes_failed += 1
        unlocked += len(c.get("unlockedPaths") or [])
        unfillable += len(c.get("unfillable") or [])
    return {
        "chainsAttempted": len(rows),
        "chainsCreated": len([c for c in rows if c.get("created")]),
        "chainsCompleted": len([c for c in rows if c.get("completed")]),
        "chainsResidue": len([c for c in rows if c.get("residue")]),
        # 有向那一趟发出去的写请求数 / 其中被服务端拒掉的。
        # 前者是"这一维到底有没有量到东西"，后者掉不下来说明我们填得不对。
        "chainWrites": writes,
        "chainWritesFailed": writes_failed,
        # 建出一条数据之后**新解锁**的页（详情页、子标签页）。
        # 它是 §12.5「页数会涨一截」那句话的度量。
        "chainPagesUnlocked": unlocked,
        "chainFieldsUnfillable": unfillable,
        "chainBreakpoints": counts,
        # 事实那一本。**每个键都渲染** —— 同 `chainBreakpoints` 一个理由。
        "chainFacts": facts,
    }


def chain_meta() -> dict:
    """账本上那些 key 各自**叫什么、归谁**，跟着账本一起发给页面。

    为什么不让前端自己写一份对照表：那份表和这里一旦不同步，新加的断点
    在页面上会渲染成一个**没有名字的数**，而"没有名字"看起来像"没这一档"。
    键的唯一出处是这个文件，名字跟着键走。
    """
    return {
        "steps": list(CHAIN_STEPS),
        "breakpoints": {k: dict(v) for k, v in BREAKPOINTS.items()},
        "facts": {k: dict(v) for k, v in CHAIN_FACTS.items()},
    }


def residue_findings(chains) -> list[dict]:
    """残留清单。走 `review/residue.py` 同一套 `kind`/`severity`/`detail` 形状 ——
    **不新造一套词**：那边已经有人读得懂"造了没删"和"删不掉"的区别。
    """
    out: list[dict] = []
    not_cleaned = [c for c in chains or []
                   if c.get("residueKind") == "residue_not_cleaned"]
    failed = [c for c in chains or [] if c.get("residueKind") == "cleanup_failed"]
    if not_cleaned:
        out.append({
            "kind": "residue_not_cleaned", "severity": "major", "where": "chain",
            "tags": [c.get("tag") for c in not_cleaned],
            "detail": "有向链路造了 %d 条数据、**一次清理都没发起过**（链断在"
                      "删除之前）：%s。这些行会留在被测环境里，"
                      "下一趟「列表里有几条」这类判断会全部失真，"
                      "而失真的方向是**看起来更正常**。照前缀手工清一遍。"
                      % (len(not_cleaned),
                         "、".join(str(c.get("tag")) for c in not_cleaned[:5])),
        })
    if failed:
        out.append({
            "kind": "cleanup_failed", "severity": "major", "where": "chain",
            "tags": [c.get("tag") for c in failed],
            "detail": "有向链路发起了删除但**没删掉** %d 条：%s。"
                      "自己造的数据删不了是**产品缺陷**（不是我们的脚本问题），"
                      "同时这些数据现在是被测环境里的残留 —— 两件事都要处理。"
                      % (len(failed),
                         "、".join(str(c.get("tag")) for c in failed[:5])),
        })
    return out


def chain_declarations(summary: dict, *, create_disabled: int = 0,
                       main_role: str = "") -> list[str]:
    """这一趟有向链路**没验到什么**。声明是一等公民，不是附注。

    `create_disabled` 是「页面上有『新建』但它是灰的」的页数（主爬角色只读时
    这是常态）。**一条都没开**有两种完全不同的原因，合成一句就没法处置：
    没有这个功能 ⇒ 去问对方的清单；这个角色不让建 ⇒ 换个账号才量得到。
    """
    out: list[str] = []
    s = summary or {}
    if not s.get("chainsAttempted"):
        who = f"主爬角色（{main_role}）" if main_role else "主爬角色"
        if create_disabled:
            out.append("这一趟没跑有向链路：%d 个页面上「新建」是**灰的** —— "
                       "%s是只读账号，建不了东西。所以页面上的写操作这一维"
                       "**不是 0，是这个角色量不到**；要量得给这一段一个"
                       "能写的账号，别把它读成「这个域没有写功能」"
                       % (create_disabled, who))
        else:
            out.append("这一趟没跑有向链路（一条都没开）—— 页面上的写操作这一维"
                       "**不是 0，是没量**：P 边里只有 GET 是必然结果，"
                       "拿它去判「他没测写接口」会全是假缺口")
        return out
    if not s.get("chainsCreated"):
        out.append("有向链路开了 %d 条，**一条也没建成** —— 详情页/子标签页、"
                   "以及页面上的写接口这一维一格都没量到。断点分布见"
                   "`chainBreakpoints`，别读成「这个域没有这些功能」"
                   % s["chainsAttempted"])
    if s.get("chainFieldsUnfillable"):
        out.append("%d 处表单字段我们填不出来（要验证码 / 要上传 / 依赖另一条"
                   "数据）—— 这是**我们的欠账清单**，不是产品的缺口，"
                   "别当成「这些表单没有必填校验」" % s["chainFieldsUnfillable"])
    if s.get("chainsResidue"):
        out.append("有向链路留了 %d 条数据在被测环境里没清掉 —— 见残留清单，"
                   "别等下一趟（下一趟会看见自己上一趟造的东西）"
                   % s["chainsResidue"])
    if s.get("chainWritesFailed"):
        out.append("有向链路发出去的写请求有 %d 条被服务端拒了 —— 先看那几条的"
                   "报错原文（多半是我们填的值不合规），别直接当成产品缺陷"
                   % s["chainWritesFailed"])
    return out
