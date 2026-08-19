"""关联 bug + 标签 —— 回答两个问题：这条为什么红、什么时候能继续。

平台不做缺陷系统：真单子在 GitHub / Jira / 群里。这里只存**指针 + 开关**。

状态机就一条线，中间没有人工判定 bug 死活的余地：

    open ──人/CC 标 fixed──▶ fixed（列表显示「待重跑」）
                                  │
                        regression 跑绿 ──▶ 自动摘掉关联（回到正常）
                                  │
                        跑红 ──▶ 关联留着，说明 bug 其实没修好

**为什么跑绿要自动摘**：靠 CC 记得回来清，第一次忘了就永远挂着一条假阻塞。
摘掉的判据是执行事实（跑绿），跟「状态由执行推进」那条红线同源。
**为什么不自动把 open 标成 fixed**：那是判定 bug 死活，平台没有依据。
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


def retest_pending(case) -> bool:
    """可以继续了：一条 open 都没有，但有标了 fixed 的还没跑绿过。

    这就是「怎么知道这条用例可以继续」的那个信号 —— CC 的 check_branch
    和列表都读它，不各算一遍。
    """
    refs = _refs(case)
    if not refs or blocked_by_bug(case):
        return False
    return any(r.get("status") == FIXED for r in refs)


def clear_fixed_refs(case) -> int:
    """跑绿时摘掉已修的关联，返回摘了几条。open 的一条都不动。"""
    refs = _refs(case)
    if not refs:
        return 0
    keep = [r for r in refs if r.get("status", OPEN) == OPEN]
    if len(keep) == len(refs):
        return 0
    case.bug_refs = keep or None
    return len(refs) - len(keep)
