"""关联 bug + 标签 —— 记住「这条用例发现过什么 bug、什么时候验回来的」。

平台不做缺陷系统：真单子在 GitHub / Jira / 群里。这里存的是**映射 + 痕迹**。

    关联 open   发现 bug，这条红的原因不在用例。批量回归跳过它（跑了只是刷红）
        │
        │  git 上那条 issue 关闭 / 人告知修好了 → CC 回来调
        ▼
    标 fixed    **调通之后**才标。这条关联从此是历史记录，永久留着
        │
        └─ 没调通 → 留在 open，补一句 note 说清现在卡在哪

**fixed 不是"据说修好了"，是"我回来调过、通了"。** 中间那段（issue 关了但还没调）
它就该留在 open —— 那正是"还没验回来"的意思。

**为什么不清空**：清掉就看不出这条用例曾经发现过 bug。那是这份数据最值钱的部分 ——
哪些用例真的抓到过问题、抓到过几次，是评估用例价值的唯一依据。
传 `[]` 只用于**关联错了**（挂到了不相干的 bug 上），不是正常的结束方式。

平台任何路径都不会自己改 status：判定 bug 死活、判定验没验过，都是人和 CC 的事。
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.core.exceptions import ValidationError

OPEN = "open"
FIXED = "fixed"
_STATUSES = (OPEN, FIXED)
MAX_REFS = 20
MAX_TAGS = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_tags(raw) -> list[str] | None:
    """标签：去空、去重、保序。空列表归一成 None（列表页少一个空数组分支）。"""
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValidationError(code="INVALID_TAGS", message="tags 要是字符串数组")
    out: list[str] = []
    for t in raw:
        if not isinstance(t, str):
            raise ValidationError(code="INVALID_TAGS", message="tags 里只能是字符串")
        t = t.strip()
        if not t:
            continue
        if len(t) > 32:
            raise ValidationError(code="INVALID_TAGS", message=f"标签「{t[:10]}…」超过 32 字")
        if t not in out:
            out.append(t)
    if len(out) > MAX_TAGS:
        raise ValidationError(code="INVALID_TAGS", message=f"标签最多 {MAX_TAGS} 个")
    return out or None


def normalize_bug_refs(raw, previous=None) -> list[dict] | None:
    """校验并补齐关联 bug。`previous` 是库里那一份，用来判断状态**变没变**。

    只在状态真的变了时才动 updatedAt —— 否则每次保存用例（改个标题）都会
    把时间戳刷新一遍，「什么时候标的 fixed」这条线索就没了。
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValidationError(code="INVALID_BUG_REFS", message="bugRefs 要是数组")
    prev = {r.get("ref"): r for r in (previous or []) if isinstance(r, dict)}
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):          # 只给单号也收，最常见的写法
            item = {"ref": item}
        if not isinstance(item, dict):
            raise ValidationError(code="INVALID_BUG_REFS", message="每条要么是单号字符串，要么是对象")
        ref = str(item.get("ref") or "").strip()
        if not ref:
            raise ValidationError(code="INVALID_BUG_REFS", message="每条都要有 ref（单号或一句话）")
        if len(ref) > 200:
            raise ValidationError(code="INVALID_BUG_REFS", message="ref 最长 200 字")
        status = str(item.get("status") or OPEN).strip().lower()
        if status not in _STATUSES:
            raise ValidationError(code="INVALID_BUG_REFS",
                                  message=f"status 只能是 {OPEN} / {FIXED}，收到「{status}」")
        url = (item.get("url") or "").strip() or None
        if url and not url.startswith(("http://", "https://")):
            raise ValidationError(code="INVALID_BUG_REFS", message="url 要以 http(s):// 开头")
        note = (item.get("note") or "").strip() or None
        if note and len(note) > 500:
            note = note[:500]
        if ref in seen:                    # 同一个单号写两遍，后写的算
            out = [r for r in out if r["ref"] != ref]
        seen.add(ref)

        old = prev.get(ref)
        changed = old is None or old.get("status") != status
        rec = {"ref": ref, "status": status,
               "updatedAt": _now() if changed else (old.get("updatedAt") or _now())}
        if url:
            rec["url"] = url
        if note:
            rec["note"] = note
        if status == FIXED:
            rec["fixedAt"] = _now() if changed else (old.get("fixedAt") or _now())
        out.append(rec)
    if len(out) > MAX_REFS:
        raise ValidationError(code="INVALID_BUG_REFS", message=f"关联 bug 最多 {MAX_REFS} 条")
    return out or None


def _refs(case) -> list[dict]:
    return [r for r in (getattr(case, "bug_refs", None) or []) if isinstance(r, dict)]


def blocked_by_bug(case) -> bool:
    """还卡着：至少一条 open。"""
    return any(r.get("status", OPEN) == OPEN for r in _refs(case))


def has_fixed_bug(case) -> bool:
    """这条用例**发现过 bug 且已经验回来了**（有 fixed、没有 open）。

    它是痕迹，不是待办：列表上灰着显示，不催任何人。
    「哪些用例真抓到过问题」就是按它筛。
    """
    refs = _refs(case)
    if not refs or blocked_by_bug(case):
        return False
    return any(r.get("status") == FIXED for r in refs)
