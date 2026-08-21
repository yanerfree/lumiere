"""跑完有没有把造的数据清干净（review-spec §3 第 8 项、§12-11）。

原方案是做一张「脏数据账本」，**撤了**（§10 ③）：正常脚本跑完就该自己收拾干净，
留垃圾只有两种可能 —— 脚本没写清理，或者删不掉（那是被测系统的 bug）。
做账本等于给"不清理"提供了一个正规的存放处。改成审核的一项检查：

| 现象 | 结论 |
|---|---|
| 造了、一次删都没试过 | **脚本没清理** → 打回脚本 |
| 删了但没删掉（DELETE 非 2xx） | **删不掉是 bug** → 开失败单，不打回用例 |
| 造了也删了 | 干净，不报 |

## 判据：只看这次执行的真实流量，不猜

配对靠 URL 归一后的**集合**：`POST /api/v1/services` 造出来的东西，
清理必然是 `DELETE /api/v1/services/{id}`（或同集合下的软删 `PUT .../{id}/archive`）。
两边都归一成 `/api/v1/services`，比集合就行 —— 不需要从响应体里抠 id，
那个每个接口长得都不一样，抠不准反而制造假结论。

## 反例（什么时候会冤枉人）

1. **这条用例就是在验"建完要能留下"**（注册、下单），本来就不该删。
   出口：提示里写清"如果这条就是要验留存，忽略"。这也是它只能是 major、
   不能是 blocker 的原因 —— 存在合法写法（RULES.md ①③）。
2. **清理走的是后台 API / SQL，不经浏览器**。出口：同上，提示里说明。
3. **数据是共享夹具，多条用例复用**。出口：同上。

所以这条永远是 major + 带出口，不硬拦。
"""
from __future__ import annotations

import re

from app.services.review.traffic_diff import norm

_WRITE = ("POST", "PUT", "PATCH")
# 软删也算清理：把状态改回去、归档、下线
_SOFT_DELETE = re.compile(r"/(archive|disable|deactivate|offline|revoke|cancel|restore)\b", re.I)
# 这些 POST 不是"造数据"：登录、搜索、导出、批量查询
_NOT_CREATE = re.compile(
    r"/(login|logout|refresh|token|search|query|export|import|preview|validate|check|"
    r"batch-get|_search|upload)\b", re.I)


# 路径末尾的动作词：`POST /x/{id}/publish` 是动作，不是创建
_ACTION_TAIL = re.compile(
    r"^(publish|unpublish|archive|restore|enable|disable|activate|deactivate|"
    r"online|offline|approve|reject|submit|revoke|cancel|retry|refresh|sync|"
    r"start|stop|pause|resume|clone|copy|move|reset)$", re.I)
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _id_like(seg: str) -> bool:
    """这一段是资源 id 吗。

    **不能只认 UUID 和纯数字**：实测 `DELETE /api/v1/services/abc-123` 里的
    `abc-123` 两样都不是，于是它的集合被算成 `/api/v1/services/abc-123`，
    跟 `POST /api/v1/services` 对不上 —— 结果"造了也删了"照样报残留。
    反过来也不能太宽：`v1`、`v2` 这种版本段带数字但不是 id，
    误伤它会把 `/api/v1/services` 砍成 `/api`，所有集合糊成一个。
    所以要求「够长 + 带分隔符 + 带数字」三条同时成立。
    """
    s = (seg or "").strip()
    if not s:
        return False
    if s == "{id}" or s.isdigit() or _UUID.match(s):
        return True
    if len(s) >= 12 and re.fullmatch(r"[0-9a-f]+", s, re.I):        # 长 hex
        return True
    return len(s) >= 6 and any(c.isdigit() for c in s) and ("-" in s or "_" in s)


def _collection(key: str) -> str | None:
    """归到「集合」这一层，创建和删除才配得上对。

    `POST /api/v1/services` → `/api/v1/services`
    `DELETE /api/v1/services/{id}` → `/api/v1/services`
    `DELETE /api/v1/services/abc-123` → `/api/v1/services`
    `PUT /api/v1/services/abc-123/archive` → `/api/v1/services`
    """
    if not key or " " not in key:
        return None
    segs = [s for s in key.split(" ", 1)[1].split("/") if s]
    while segs and (_id_like(segs[-1]) or _ACTION_TAIL.match(segs[-1])
                    or _SOFT_DELETE.match("/" + segs[-1])):
        segs.pop()
    return ("/" + "/".join(segs)) if segs else None


def _is_create(key: str, url: str) -> bool:
    """这个 POST 是「造了一条数据」吗。

    不算的：登录/搜索/导出这类（`_NOT_CREATE`）、打在某个实例上的动作
    （`POST /x/{id}/publish`）—— 后者改的是已有对象，不留新垃圾。
    """
    if _NOT_CREATE.search(url):
        return False
    segs = [s for s in key.split(" ", 1)[1].split("/") if s]
    if not segs:
        return False
    return not (_id_like(segs[-1]) or _ACTION_TAIL.match(segs[-1]))


def analyze(captured: list) -> list[dict]:
    """入参是这次执行抓到的流量。没抓到流量就不下任何结论。"""
    if not captured:
        return []

    created: dict[str, int] = {}        # 集合 → 成功创建了几个
    delete_ok: dict[str, int] = {}      # 集合 → 成功删掉几个
    delete_bad: dict[str, list] = {}    # 集合 → 删失败的状态码

    for q in captured:
        if not isinstance(q, dict):
            continue
        method = str(q.get("method") or "").upper()
        url = str(q.get("url") or "")
        status = q.get("status")
        key = norm(url, method)
        if not key:
            continue
        coll = _collection(key)
        if not coll:
            continue
        ok = isinstance(status, int) and 200 <= status < 300

        if method == "POST" and _is_create(key, url):
            # 只认"打到集合根上的 POST"是创建；`POST /x/{id}/action` 是动作不是创建
            if ok:
                created[coll] = created.get(coll, 0) + 1
        elif method == "DELETE" or (method in _WRITE and _SOFT_DELETE.search(url)):
            if ok:
                delete_ok[coll] = delete_ok.get(coll, 0) + 1
            elif status is not None:
                delete_bad.setdefault(coll, []).append(status)

    out: list[dict] = []

    # ① 造了、一次都没试着删
    leaked = [c for c, n in created.items() if n > 0 and not delete_ok.get(c) and c not in delete_bad]
    if leaked:
        detail = "、".join(f"{c}（造了 {created[c]} 个）" for c in leaked[:5])
        out.append({
            "kind": "residue_not_cleaned", "severity": "major", "where": "run",
            "detail": f"这次跑完，造的数据**一次清理都没发起过**：{detail}。"
                      f"留下的垃圾会让下一次跑撞上重名、也会让"
                      f"「列表里有几条」这类断言越跑越不准。脚本末尾补上清理。"
                      f"**如果这条用例就是要验「建完能留下」**（注册、下单这类），"
                      f"或者清理走的是后台接口/SQL 不经浏览器，忽略这条。",
        })

    # ② 试着删了但没删掉 —— 这是被测系统的 bug，不是用例的错
    if delete_bad:
        detail = "、".join(f"{c}（{'/'.join(str(s) for s in sorted(set(v))[:3])}）"
                           for c, v in list(delete_bad.items())[:5])
        out.append({
            "kind": "cleanup_failed", "severity": "minor", "where": "run",
            "detail": f"脚本发起了清理但**没删掉**：{detail}。"
                      f"清理写了却删不掉，是被测系统这边的问题 —— "
                      f"这条用例本身不因此被打回，已按失败跟进处理。",
        })

    return out
