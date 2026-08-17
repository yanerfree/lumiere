"""列表上显示的字段，编辑弹窗里必须都能看到。

**这条被指出过三次**，所以做成机制而不是再改一次页面：
「列表看到一个值、点进去找不着」是最让人抓狂的一类不一致 —— 人会以为自己眼花，
或者以为功能坏了。

不可编辑的字段（派生值、履历字段）**也要在弹窗里以只读形式出现并说明为什么**，
不能干脆不显示 —— 「不显示」和「不能改」在用户那里是两件事：
前者让人找，后者让人放心。

判据是列头文字能在弹窗那段 JSX 里找到。「操作」列除外（那是按钮不是字段）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "frontend/src"

# 页面 → (列定义所在变量, 弹窗 JSX 的起始锚点)
PAGES = [
    ("pages/settings/I18nMessages.jsx", "const columns = [", "<Modal"),
]
# 这些列头不是字段：按钮列、纯序号
_NOT_FIELD = {"操作", "序号", "#"}


def _titles(block: str) -> list[str]:
    return [t for t in re.findall(r"title:\s*'([^']+)'", block) if t not in _NOT_FIELD]


@pytest.mark.parametrize("rel,cols_anchor,modal_anchor", PAGES)
def test_列表字段在编辑弹窗里都有入口(rel, cols_anchor, modal_anchor):
    src = (ROOT / rel).read_text(encoding="utf-8")
    cols = src[src.index(cols_anchor):src.index(modal_anchor)]
    modal = src[src.index(modal_anchor):]
    missing = []
    for t in _titles(cols):
        # 列头可能带语种后缀（「中文 (zh)」），弹窗里同名即可；取前两个字做宽松匹配
        stem = t.split(' ')[0]
        if t not in modal and stem not in modal:
            missing.append(t)
    assert not missing, (
        f"{rel}：列表上有「{'、'.join(missing)}」，编辑弹窗里找不到 —— "
        f"人会在列表看到值、进去找不着。不可编辑的也要只读显示并说明为什么。")


def test_字段不许只在编辑态显示():
    """**上一版只在编辑态显示，新建时又不见了，等于只修了一半。**

    这条守卫我写坏过两次，记下来：
      · 第一版用 `modal[i-400:i].split("<Form.Item")[-1]` 猜上下文 —— 什么都没查。
      · 第二版数 `{editing && (` 和 `)}` 的个数比 —— `)}` 在 JSX 里到处都是，
        closes 永远远大于 opens，恒真。
    **两次变异都没变红，而我当时都当成"通过"了。变异不红 = 守卫坏了，不是代码对了。**

    现在按 JSX 结构判：从每个 `{editing && (` 起，用括号配平找出它真正的作用域，
    落在里面的 label 就是"只有编辑态才显示"。
    """
    src = (ROOT / "pages/settings/I18nMessages.jsx").read_text(encoding="utf-8")
    modal = src[src.index("<Modal"):]

    # 找出所有 {editing && ( ... )} 的字符区间
    guarded: list[tuple[int, int]] = []
    for m in re.finditer(r"\{\s*editing\s*&&\s*\(", modal):
        depth, i = 0, m.end() - 1          # 指向那个 (
        while i < len(modal):
            if modal[i] == "(":
                depth += 1
            elif modal[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        guarded.append((m.start(), i))

    for label in ("模块", "来源", "分类", "中文 (zh)", "英文 (en)", "说明"):
        at = modal.index(f'label="{label}"')
        inside = [(a, b) for a, b in guarded if a < at < b]
        assert not inside, \
            f"「{label}」落在 {{editing && …}} 里面 —— 新建时看不到，等于只修了一半"


def test_列表字段都能改():
    """**列表上显示的字段，编辑时要能改。**

    这条走过三步：先是「模块/来源」在弹窗里压根没有 → 我补成只读 →
    仍然不对：「来源为什么不能编辑」。
    对的：列表上看得到却改不了，比能改更让人困惑 —— 人会以为页面坏了，
    而且分错了没有纠正的路。只读只适合真正不可变的东西（id、创建时间）。

    所以两个都是可编辑控件，不许再出现 disabled。
    """
    src = (ROOT / "pages/settings/I18nMessages.jsx").read_text(encoding="utf-8")
    modal = src[src.index("<Modal"):]
    for label in ("模块", "来源"):
        i = modal.index(f'label="{label}"')
        seg = modal[i:i + 420]
        assert "disabled" not in seg, f"「{label}」又变成只读了 —— 列表上看得到就该能改"
        assert "name=" in modal[max(0, i - 90):i + 20], f"「{label}」没绑表单字段，改了存不下去"


def test_模块是存的字段不是从键推导的():
    """派生值放在列表上，人默认它能改、实际改不了；键写错了它跟着错，
    而该改的是键。所以存字段：导入时预填一次，之后人和 CC 都能改。"""
    from app.models.i18n_message import ProjectI18nMessage
    assert "module" in ProjectI18nMessage.__table__.c, "module 没落成列，还是在前端算"
    src = (ROOT / "pages/settings/I18nMessages.jsx").read_text(encoding="utf-8")
    assert "const moduleOf = (r) => r?.module" in src, "moduleOf 还在从键算"


def test_说明栏引导写页面位置():
    """用户要的是「一眼看出这条文案在哪个页面的什么地方」，而且明确说了
    「你写到说明里面不就行了」。所以 placeholder 和 tooltip 都要给出路径格式，
    不能只写「可选」。"""
    src = (ROOT / "pages/settings/I18nMessages.jsx").read_text(encoding="utf-8")
    modal = src[src.index("<Modal"):]
    i = modal.index('name="description"')
    seg = modal[i:i + 700]
    assert "›" in seg, "说明栏没给出路径格式的例子"
    assert "位置" in seg, "说明栏没说清它是干什么的"
