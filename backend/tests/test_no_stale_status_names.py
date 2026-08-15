"""废弃的状态名不许留在活的逻辑和给 CC 看的说明里。

三态改造（草稿/调试中/完成 + 用例级审核标签）之后，`not_started`、`pending_review`、
`executable` 这三个态没了。但有三处**活代码**还在读它们，一直没人发现 ——
因为它们不报错，只是永远匹配不上：

1. check_deliverable 的 `waiting_human` 判 `== "pending_review"` → 恒为空，
   「等人审」这个信号在交付门禁里彻底失效。
2. 同一函数的 `not_ready` 判 `("not_started", "draft", "debugging")` 且**排除 manual**
   → 手动维度还在「调试中」也照样判可交付。实测 TC-FWGL-00002 就是这样：
   manual=debugging、进不了「待审」，门禁却说它可交付。
3. tb_list_cases 的工具说明里写着可选值是
   `not_started/draft/debugging/pending_review/executable` ——
   **CC 直接读这段文字**，拿废弃的值去过滤，永远查不到东西，还以为是真没有。

判词里还在说「维度是待发布，进回归还要人在列表上点发布到回归」—— 那个环节已经
不存在了，CC 照着去找按钮只会找不到。

这类 bug 的形状和这个项目栽过的另外几次一样：**不报错、静默失效、只能靠人对着
数据看才发现**。所以钉死。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "app"
STALE = ("not_started", "pending_review", "executable")

# 这些文件里出现旧态名一定是活逻辑或对外文案，不是历史注释
FILES = [
    "mcp/tools/deliverable.py",
    "mcp/tools/plans.py",
    "mcp/__init__.py",
    "services/script_run_service.py",
    "api/cases.py",
]


def _strip_comments_and_docstrings(src: str) -> str:
    """只看会被执行/会被 CC 读到的部分。

    模型注释里保留旧态名是**对的** —— 那是在解释为什么删掉它们。
    但 mcp/__init__.py 里的 description 是给 CC 看的文案，不是注释，必须留下。
    """
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    src = re.sub(r"^\s*#.*$", "", src, flags=re.M)
    return src


@pytest.mark.parametrize("rel", FILES)
def test_活逻辑里没有废弃状态名(rel):
    p = ROOT / rel
    if not p.exists():
        pytest.skip(f"{rel} 不存在")
    body = _strip_comments_and_docstrings(p.read_text(encoding="utf-8"))
    # 只盯**状态值字面量**。函数名 `_not_executable` 之类不算 ——
    # 一刀切匹配子串会把它误报成 bug，然后这条守卫就会被人调松或删掉。
    hits = [s for s in STALE if re.search(rf"""['"]{s}['"]""", body)]
    assert not hits, f"{rel} 里还在用废弃状态 {hits} —— 它不报错，只会永远匹配不上"


def test_交付门禁按三态判():
    src = (ROOT / "mcp/tools/deliverable.py").read_text(encoding="utf-8")
    assert 'dim_status.get(d) in ("draft", "debugging")' in src, "not_ready 没按三态判"
    assert 'case.review_status == "pending"' in src, \
        "「等人审」应该读用例级审核标签，不是维度态"


def test_手动维度不许被排除在状态检查外():
    """排除 manual 的后果：手动维度停在「调试中」→ 进不了「待审」→ 但门禁说可交付。
    人只会看到这条一直不冒头，不知道卡在哪。"""
    src = (ROOT / "mcp/tools/deliverable.py").read_text(encoding="utf-8")
    i = src.index("not_ready = ")
    seg = src[i:i + 200]
    assert 'd != "manual"' not in seg, "not_ready 又把 manual 排除了"


def test_判词不再提已经不存在的发布环节():
    src = (ROOT / "mcp/tools/deliverable.py").read_text(encoding="utf-8")
    body = _strip_comments_and_docstrings(src)
    assert "发布到回归" not in body, "判词还在让人去点一个已经不存在的按钮"
    assert "审核不挡回归" in body, "没说清审核不挡回归 —— CC 会以为还卡着人工闸口"
