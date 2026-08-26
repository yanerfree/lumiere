"""MCP instructions 的自洽守卫。

instructions 是 CC 一连上就读的那一份（不用人粘贴），所以它是纪律的**主份** ——
接入指令里的副本 2026-08-25 删了，理由见 test_mcp_profiles.py 里那几条。
主份的风险跟着变了：不再是"会不会被人漏掉粘贴"，而是**它自己会长出矛盾**。

它一万字、按时间层层追加，最后一段是最早写的。功能改了以后，新纪律加在前面，
旧段落留在后面照旧说着老话 —— 两段都在同一次连接里发给 CC，CC 只能挑一段听，
而它挑哪一段没人管得着。这里钉的就是这类矛盾，不是文字风格。
"""


def test_指令里不许再留凭文档推页面的退路():
    """曾经的原话：「如果找不到前端代码，就从 API 定义推断页面结构，
    但必须在步骤中标注"待确认"」。

    它和 ①「只有一条路：先活体验证」、⑤「平台不再提供凭文档造」正面顶牛，
    而且它给的是一条**更省力**的路 —— 两句话摆在一起，省力那句赢。
    "标注待确认"听着像有兜底，实际没有：没人回来确认，那条用例就以
    "看着像写好了"的样子躺在库里，比明明白白欠着一维坏得多。
    """
    from app.mcp import mcp

    ins = mcp.instructions
    for banned in ("推断页面结构", "必须按以下流程执行"):
        assert banned not in ins, f"指令里又出现了「{banned}」—— 和 ①/⑤ 冲突"
    # 正解还在：连不上就欠着，别编
    assert "target_level=spec" in ins
    assert "不再提供" in ins


def test_指令给出每轮的入口工具():
    """判据在工具自己身上，但**"该在什么时候调它"只有指令说得了**。

    lum_next_duty / lum_check_deliverable 这类不是查询工具，CC 不会自己想到调 ——
    tools/list 里躺着 57 个名字，没人告诉它"每轮先问该干什么"，它就按自己的
    习惯开工，于是归因队列、失败复跑、交付门禁全靠人在旁边提醒。
    """
    from app.mcp import mcp

    ins = mcp.instructions
    for tool in ("lum_next_duty", "lum_check_deliverable", "lum_module_checkup",
                 "lum_check_branch"):
        assert tool in ins, f"指令里没提 {tool} —— CC 不会自己想到调它"


def test_指令点名的工具都还活着():
    """下线一个工具时，最容易漏的就是这一万字里的名字。

    lum_generate_api_test 摘除时就漏在几个地方过。指名一个不存在的工具比不提它
    更坏：CC 会先试着调、失败，然后自己找替代路子 —— 那正是分档要挡的岔路。
    """
    import re

    from app.mcp import TOOL_CATALOG, mcp

    alive = {t["name"] for t in TOOL_CATALOG}
    named = set(re.findall(r"lum_[a-z_]+", mcp.instructions))
    # 下线的工具允许作为历史交代出现（"原 lum_generate_api_test 已下线"），
    # 靠"下线/摘除"这类字样自证；这里只揪没有交代的裸名字。
    dead = sorted(n for n in named - alive
                  if not re.search(rf"{n}[^\n]{{0,20}}(已下线|下线|摘除)", mcp.instructions))
    assert not dead, f"指令点名了不存在的工具：{dead}"


def test_指令别再长回去():
    """一万字已经在 CC 的注意力边缘。

    上限不是为了好看：这份东西每次连接整份发出去，长到一定程度 CC 就只读开头
    几段 —— 那时候写在后面的纪律和删掉没区别，还骗过了所有"某某在不在指令里"
    的守卫。要加新判据，先想清楚能不能放进工具描述或返回值里。
    """
    from app.mcp import mcp

    assert len(mcp.instructions) < 11500, (
        f"instructions 已 {len(mcp.instructions)} 字 —— 新判据优先放工具描述/返回值"
    )
