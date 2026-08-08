"""断言指纹（B5）—— 防"改到绿了但测试死了"。

这套架构最大也最隐蔽的失败模式：**CC 会"改得太好"。**
给它"让测试变绿"的目标，它一定能做到 —— 加 waitForTimeout、放宽断言、
try/except 吞异常、条件 skip。每一条都能变绿，每一条都在销毁这条测试存在的理由。
**这个失败不会以"报错"出现，它会以"一切正常"出现。**

⚠ 但"断言强度"**做不到可靠硬拦截**（评审裁定，Amelia 的技术否决）：
Playwright 断言形态太多（expect().to_be_visible() / assert / wait_for_selector 都算），
一条 to_contain_text 拆成两条数量还涨了但强度反而降了。误拦会逼 CC 想办法绕
（把断言拆成两条凑数），比不拦更糟。

所以只做两件能可靠判的：
- **硬拦截一条**：断言总数为 0。100% 可判，也是最常见的作弊路径
  （"跑通了但什么都不验证"）
- **其余软警告 + 记账**：存指纹，回推时把变化告诉 CC 和人，让退化**可见**，
  不自动拦。看得见就治得住，看不见才是真危险。
"""
from __future__ import annotations

import re

# 按"强度"分档。强 = 断言了具体内容；弱 = 只断言存在性/状态码。
# 这个分档只用于**提示**，不用于拦截 —— 见模块开头的技术否决。
_PATTERNS: dict[str, list[re.Pattern]] = {
    "text": [  # 断了具体文案/值 —— 最强
        re.compile(r"\.to_have_text\(|\.toHaveText\("),
        re.compile(r"\.to_contain_text\(|\.toContainText\("),
        re.compile(r"\.to_have_value\(|\.toHaveValue\("),
        re.compile(r"\.to_have_url\(|\.toHaveURL\("),
        re.compile(r"\.to_have_attribute\(|\.toHaveAttribute\("),
    ],
    "count": [  # 断了数量 —— 较强
        re.compile(r"\.to_have_count\(|\.toHaveCount\("),
    ],
    "status": [  # 断了状态码
        re.compile(r"status_code\s*==|\.status\s*==|\.to_have_status\(|resp\.status"),
    ],
    "visible": [  # 只断存在/可见 —— 弱
        re.compile(r"\.to_be_visible\(|\.toBeVisible\("),
        re.compile(r"\.to_be_enabled\(|\.toBeEnabled\("),
        re.compile(r"\.to_be_checked\(|\.toBeChecked\("),
    ],
    "bare_assert": [  # 裸 assert —— 强度取决于表达式，单独一档
        re.compile(r"^\s*assert\s+", re.M),
    ],
}

_STRENGTH = {"text": 3, "count": 3, "status": 2, "bare_assert": 2, "visible": 1}

# 只要出现就该提醒的写法 —— 它们是"改到绿"最常见的几种手法
_SMELLS = [
    (re.compile(r"wait_for_timeout\(|waitForTimeout\("),
     "用了固定等待 —— 换台机器就会偶发。改成条件等待（expect(...).to_be_visible / wait_for_url）"),
    (re.compile(r"except\s*(Exception)?\s*:\s*(pass|\.\.\.)|catch\s*\([^)]*\)\s*\{\s*\}"),
     "有 try/except 吞异常 —— 失败会被静默咽掉，测试永远绿"),
    (re.compile(r"pytest\.skip\(|test\.skip\(|@pytest\.mark\.skip"),
     "有 skip —— 跳过的用例在报告里不算失败，等于悄悄少测了一块"),
]


def build(content: str) -> dict:
    """算一份断言指纹。"""
    buckets = {k: 0 for k in _PATTERNS}
    for kind, pats in _PATTERNS.items():
        for p in pats:
            buckets[kind] += len(p.findall(content))
    total = sum(buckets.values())
    score = sum(buckets[k] * _STRENGTH[k] for k in buckets)
    smells = [msg for p, msg in _SMELLS if p.search(content)]
    return {"total": total, "byKind": buckets, "strengthScore": score, "smells": smells}


def diff_warnings(old: dict | None, new: dict) -> list[str]:
    """新旧指纹对比，产出**软警告**（不拦）。"""
    warns = [f"⚠ {s}" for s in new.get("smells", [])]
    if not old:
        return warns

    o_total, n_total = old.get("total", 0), new.get("total", 0)
    o_score, n_score = old.get("strengthScore", 0), new.get("strengthScore", 0)

    if n_total < o_total:
        warns.append(
            f"⚠ 断言条数变少了：{o_total} → {n_total}。如果是合并/重构就忽略；"
            "如果是为了让它变绿而删断言，那是把测试改死了，比红着更糟"
        )
    if n_score < o_score:
        ob, nb = old.get("byKind", {}), new.get("byKind", {})
        moved = [f"{k} {ob.get(k, 0)}→{nb.get(k, 0)}" for k in nb if nb.get(k, 0) != ob.get(k, 0)]
        warns.append(
            f"⚠ 断言强度下降（{o_score} → {n_score}）：{'，'.join(moved)}。"
            "典型退化是把 to_have_text 换成 to_be_visible —— 只验有个东西在，不验内容对不对"
        )
    return warns
