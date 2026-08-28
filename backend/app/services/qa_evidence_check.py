"""把模型给的 `evidence` 拿回脚本正文里搜一遍。**纯函数，零 IO，零模型。**

导出的那份 Markdown 里有一句承诺：「每条都能十秒内被否掉：`evidence` 是从脚本正文
原样抄的，grep 一下就知道我说得对不对。**这才是它能被信的理由，不是「AI 说的」。**」

那句话此前**没有任何东西在验证它**。而这个模块的全部意义就是抓「结论看起来有据、
依据其实没验过」—— 在自己身上留一句没验过的承诺，比不写这句话坏得多：
它把"可复核"这件事从一个可以检查的性质，变成了一句需要相信的话。

⚠ **只打标记，不删、不降 `severity`。**
- 删 ⇒ 丢了多少不可知，「一条没删」和「删了 8 条」在页面上长得一模一样 ——
  正是本模块要禁的那个形状。
- 降 `severity` ⇒ `severity` 说的是「对仓库有多糟」，回验说的是「我有多确信」，
  两个正交的轴合成一个，还会污染 `_SEV_RANK` 的排序。
"""

from __future__ import annotations

import re

# 归一化后短于这个长度就不算验过。`fi` / `done` / `}` / `set -e` 在任何一份 shell
# 脚本里都命中 —— 算通过等于把这道检查变成橡皮图章：**通过率 100%，信息量 0**。
MIN_EVIDENCE_CHARS = 8

#: 这三档都算「grep 得到」：每一行都能在它自己说的那份脚本里搜到。
PASS_STATES = ("verbatim", "reflowed", "stitched")

#: 六态 + `too_short`。渲染和统计都按这个顺序排，别按字典序。
STATES = ("verbatim", "reflowed", "stitched", "wrong-path",
          "unmatched", "too_short", "empty")

_STATE_CN = {
    "verbatim": "一字不差抄的",
    "reflowed": "只有换行/缩进变了",
    "stitched": "从正文几处拼起来的",
    "wrong-path": "判据是真的，但路径写错了",
    "unmatched": "在这一批脚本里搜不到",
    "too_short": "太短，搜到了也不算验过",
    "empty": "没给判据",
}


def state_cn(state: str) -> str:
    return _STATE_CN.get(state, state or "")


def _norm(text: str) -> str:
    """空白全部压成单个空格。

    模型抄正文时缩进和换行几乎必然会变（它是在写 JSON 字符串），
    拿原始文本做 exact match 等于**要求模型逐字节复刻缩进** —— 那种实现的
    "搜不到"里绝大多数是排版差异，不是编造。
    """
    return re.sub(r"\s+", " ", text or "").strip()


def _needles(evidence: str) -> list[str]:
    """判据逐行归一化。空行丢掉。"""
    return [n for n in (_norm(ln) for ln in (evidence or "").splitlines()) if n]


def _match(needles: list[str], hay_raw: str, whole_raw: str,
           hay_norm: str, whole_norm: str) -> str | None:
    """这段判据在这一份脚本里是什么形态。搜不到返回 `None`。

    三档都算搜到，区别只是「模型抄的时候动了多少」：
    - `verbatim`  一字不差，连缩进都没动
    - `reflowed`  归一化之后仍是**一整段连续**的，只有换行/缩进变了
    - `stitched`  每一行都在正文里，但不连续 —— 从几处拼起来的

    ★ 关键在于 `stitched` 必须算通过。真实的判据经常是「第 12 行的断言 +
    第 40 行的清理」拼在一起，中间隔着几十行 —— 任何"整块 exact match"的实现
    会把这类**真判据**判成编造，实测那是 27% 的假阳。
    修误报最容易的翻车方式是把真阳一起修掉；这一档反过来，
    **放松得不够就会把真判据打成编造**，然后没人再信这一列。
    """
    if not needles:
        return None
    if whole_raw and whole_raw in hay_raw:
        return "verbatim"
    if whole_norm and whole_norm in hay_norm:
        return "reflowed"
    if any(n not in hay_norm for n in needles):
        return None
    return "stitched"


def check_evidence(gaps: list[dict], batch_scripts: list[dict]) -> list[dict]:
    """给每条 gap 打上 `evidenceCheck`。原地改并返回同一个列表。

    `batch_scripts` 是**这一批**的脚本，不是全域的。

    这个参数名是**结构上的约束**，不是随手起的：调用点必须落在 `_one` 里、
    `parse_result` 之后，**不能挪到 merge 之后**。挪过去之后 A 批引用 B 批脚本
    正文的那种编造就查不出来了 —— 而那时类型、形状、绝大多数单测全都过得去，
    只有 `test_回验必须在合并之前` 会红，红起来还像是"测试写得太严"。
    想传全域进来就得改这个签名，那一改会被 review 看见。

    ⚠ **判据被截断（`evidenceTruncated`）不需要额外放水。** `_clip_lines` 要么切在
    行边界上（留下的都是完整行），要么在首行超长时硬切（留下的是那一行的前缀）——
    两种都还是正文的子串，照常搜得到。这里**故意不加**任何"截过就放松一档"的兜底：
    那种兜底会把真编造的短判据一起放过去。
    """
    by_path = {s.get("path"): (s.get("content") or "", _norm(s.get("content") or ""))
               for s in batch_scripts or []}
    for g in gaps or []:
        ev = g.get("evidence") or ""
        needles, whole = _needles(ev), _norm(ev)
        if not needles:
            g["evidenceCheck"] = "empty"
            continue
        if len(whole) < MIN_EVIDENCE_CHARS:
            g["evidenceCheck"] = "too_short"
            continue
        claimed = g.get("path")
        if claimed in by_path:
            raw, norm = by_path[claimed]
            if st := _match(needles, raw, ev, norm, whole):
                g["evidenceCheck"] = st
                continue
        # 它自己说的那份里搜不到 —— 再看是不是**路径写错了**。
        # 「判据是真的、位置指错了」和「判据是编的」是两件事，处置也不同：
        # 前者改一个字段就能用，后者整条不能信。混成一档等于把前者当废品扔了。
        for path, (raw, norm) in by_path.items():
            if path == claimed:
                continue
            if _match(needles, raw, ev, norm, whole):
                g["evidenceCheck"] = "wrong-path"
                g["evidenceFoundIn"] = path
                break
        else:
            g["evidenceCheck"] = "unmatched"
    return gaps or []


def evidence_stats(gaps: list[dict]) -> dict:
    """按状态数一遍。

    **从行本身数，不从回验那一步的返回值攒。** 页面列的是这些行，摘要里的数也从
    这些行来 —— 同一个来源就不可能对不上。分批时各批各回一份统计再相加，
    那两个数就有了两条独立的路径，哪天分歧了没人看得出来是哪边错。
    """
    rows = [g for g in (gaps or []) if isinstance(g, dict)]
    by = {s: sum(1 for g in rows if g.get("evidenceCheck") == s) for s in STATES}
    unknown = sum(1 for g in rows if g.get("evidenceCheck") not in STATES)
    return {
        "total": len(rows),
        # 存量结论里没有这个键。**不许当成"验过了"** —— 那正好是这一版要装的东西。
        "unchecked": unknown,
        "verified": sum(by[s] for s in PASS_STATES),
        "byState": by,
    }
