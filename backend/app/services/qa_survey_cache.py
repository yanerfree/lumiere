"""S7.8 增量缓存键（架构 AD-8）。纯函数：零 IO、零模型、不碰 session。

三档增量，键的形状写死在这里，省得实现时各写一套：

```
survey 缓存键   = (project_id, env_id, build_fingerprint)
对账缓存键      = survey 缓存键 + route_table_hash + qa_commit_sha
```

- QA 仓 commit 变 ⇒ 只有对账键变 ⇒ **不重爬**，只重算（秒级）
- `route_table_hash` 变 ⇒ 只重算 R 侧与 G2
- `build_fingerprint` 变 ⇒ **重爬**（没有上一趟也一样：首次必须整站）

**这个模块省下来的是「对别人的测试环境再爬一趟」。** 所以判错一次的代价不是慢，
是**把上一趟的事实当成这一趟的结论端出去** —— 而页面上两者长得一模一样。
于是本模块所有的保守都朝同一个方向：

- 拿不准 ⇒ **重爬**。多花一趟，代价看得见。
- 绝不「信号拿不到就当它没变」。省下一趟，换来一个陈旧结论冒充新鲜结论 ——
  和 S7.7 的「没探到不是看不见」、洞四的「没读到不是读过了」是同一个形状：
  **缺信号不是一个结论。**

复用了就必须说出来：`plan_reuse()` 的 `summary` 把「哪一趟、什么时候、什么指纹、
什么终态」焊进同一句话。§7 原话 —— **复用缓存却不说，就是把陈旧事实伪装成新鲜结论。**
"""
from __future__ import annotations

import hashlib

# 键的**定义**版本。定义一改（多一格、少一格、换了归一化写法），
# 这个数就得跟着涨 —— 否则新定义算出来的键会跟旧缓存里那些**长得一样合法**，
# 于是按新口径判「没变」，复用的却是按旧口径攒的东西。同 `DIM_SPEC` 的纪律。
KEY_SPEC = 1

# 可以拿来复用的终态。`partial` 在列 —— 它缺的那部分在对账那边已经算「没验证」
# （S7.5/S7.7 的三态），不会被当成「功能没了」；而把 `partial` 排除在外的代价
# 是：一个角色少配了凭证的环境**永远命中不了缓存**，于是每次对账都去重爬
# 别人的测试环境。那是朝错误方向的保守。
# **但复用它必须在结论里带上终态**（见 `_summary`）。
REUSABLE = ("done", "partial")

# 这两个终态一律不复用：
# - `failed`：压根没爬到东西，没有可复用的事实。
# - `dirty`：只读爬完、环境里的数却变了。这一趟最该被人看的就是「我们动了什么」，
#   拿它当底再端出一个正常结论，等于**把那面红旗洗掉**。
NEVER_REUSE = ("failed", "dirty")

CRAWL = "crawl"
RECOMPUTE = "recompute"
REUSE = "reuse"

R_SIDE = "routeTable"
Q_SIDE = "qaCatalog"


def _digest(*parts: str) -> str:
    """逐段**带长度前缀**再哈希。

    直接拿分隔符拼的话，`("a", "", "b")` 和 `("a", "b", "")` 拼出来是同一串 ——
    两个不同的（项目, 环境, 指纹）组合共用一个键。而这里的键管的是
    「要不要再去爬别人的环境」，撞键的后果是**把 A 环境的爬取当成 B 环境的结论**。
    """
    blob = "".join(f"{len(p)}:{p}\x1f" for p in parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def route_table_hash(table) -> str | None:
    """路由表 → 稳定哈希；**拉不到返回 `None`，不是「空表的哈希」。**

    空表的哈希是个合法的值，它跟上一轮"也没拉到"的那个值**相等** ——
    于是连着两轮拉不到路由表，会推出「路由表没变，R 侧不用重算」，
    而事实是这两轮**谁都没看过路由表**（S7.2 那句「本轮无路由表，G2 未验证」
    说的就是它）。

    拉到了但确实是空表 ⇒ 照常出哈希：**那是观测到的事实**，不是缺信号。

    组名原样进哈希（不归一化）：归一是对账那侧的事，在这里归一，
    「组名改了写法」这个信号就再也不会让 R 侧重算。
    """
    t = table or {}
    if not t.get("available"):
        return None
    rows = sorted(_digest("route", str(r.get("group") or ""), str(r.get("method") or ""),
                          str(r.get("path") or ""))
                  for r in (t.get("routes") or []))
    # 读不出来的那几条也进哈希：它们变了，R 侧算出来的东西就跟着变。
    # 重算是秒级的，往「多算一次」偏没有代价 —— 往「少算一次」偏才有。
    bad = sorted(str(x) for x in (t.get("unreadable") or []))
    return _digest(f"spec{KEY_SPEC}", "routes", *rows, "unreadable", *bad)


def survey_key(*, project_id, env_id, build_fingerprint) -> str | None:
    """三样齐了才有键；**缺一样返回 `None`**。

    `None` 不是一个键，是「这一趟没有可复用的身份」。

    ⚠ 最容易写错的一处：把缺的那格当成空串照样出一个键。那样
    「指纹没量到」和「指纹跟上次一样」会**哈希到同一个值**，
    一趟没量到构建的运行就命中了上一趟的缓存 ——
    「我们没能确认构建没变」被记成了「构建没变」。这正是洞四的形状。

    `env_id` 也必填：环境为空 ⇒ 说不清这份爬取是打在哪台机器上的，
    而它爬的是**别人的测试环境**，认错环境不是"少一次缓存命中"那个量级的错。
    """
    parts = [str(project_id or "").strip(), str(env_id or "").strip(),
             str(build_fingerprint or "").strip()]
    if not all(parts):
        return None
    return _digest(f"spec{KEY_SPEC}", "survey", *parts)


def reconcile_key(*, survey_key: str | None, route_table_hash: str | None,
                  qa_commit_sha) -> str | None:
    """对账键 = survey 键 + 路由表哈希 + QA 仓 commit。缺一格同样返回 `None`。

    它比 survey 键多的那两格，**变了只要重算、不用重爬** —— 这就是 AD-8 想省下的
    那一趟。但"缺"和"变"依旧是两回事：缺格子的时候连"要不要重算"都判不了，
    只能重算（见 `plan_reuse`），不能顺势推成「没变」。
    """
    sha = str(qa_commit_sha or "").strip()
    rt = str(route_table_hash or "").strip()
    if not survey_key or not rt or not sha:
        return None
    return _digest(f"spec{KEY_SPEC}", "reconcile", survey_key, rt, sha)


def previous_of(survey, *, qa_commit_sha="") -> dict:
    """上一趟的 ORM 行 → `plan_reuse` 认的那个形状。

    有这个函数，是因为下一个接手的人**只会照着字段名手抄一遍**，
    而抄错 `status`（比如把 `dirty` 当成"爬完了"）不会有任何东西报错。
    `qa_commit_sha` 不在这张表上（它属于对账那一侧的记录），所以单独传。
    """
    if survey is None:
        return {}
    started = getattr(survey, "started_at", None)
    return {
        "surveyId": str(getattr(survey, "id", "") or ""),
        "status": str(getattr(survey, "status", "") or ""),
        "crawledAt": "" if started is None else str(started),
        "projectId": str(getattr(survey, "project_id", "") or ""),
        "envId": str(getattr(survey, "env_id", "") or ""),
        "buildFingerprint": str(getattr(survey, "build_fingerprint", "") or ""),
        "routeTableHash": str(getattr(survey, "route_table_hash", "") or ""),
        "qaCommitSha": str(qa_commit_sha or ""),
    }


def plan_reuse(*, previous: dict | None, current: dict) -> dict:
    """这一轮该重爬、该重算、还是原样复用。**只判，不执行，不碰任何 IO。**

    `current` 收 `projectId` / `envId` / `buildFingerprint` / `routeTableHash`
    / `qaCommitSha`；`previous` 是 `previous_of()` 的产物（`None` = 没有上一趟）。

    返回里**一个布尔都没有**（同 S7.7）：`{"cached": true}` 这种字段一旦存在，
    页面上就会有人只渲染它 —— 而"复用了"和"复用的是哪一趟、什么时候爬的"
    分开之后，前者就是个绿勾。要么带着出处一起渲染，要么什么都别渲染。
    """
    cur = dict(current or {})
    prev = dict(previous or {})
    reasons: list[str] = []
    recompute: list[str] = []

    key = survey_key(project_id=cur.get("projectId"), env_id=cur.get("envId"),
                     build_fingerprint=cur.get("buildFingerprint"))
    rt = str(cur.get("routeTableHash") or "").strip()
    sha = str(cur.get("qaCommitSha") or "").strip()

    def _out(action: str) -> dict:
        return {
            "action": action,
            "recompute": recompute,
            "reasons": reasons,
            "surveyKey": key or "",
            "reconcileKey": reconcile_key(survey_key=key, route_table_hash=rt,
                                          qa_commit_sha=sha) or "",
            "provenance": _provenance(action, prev),
            "summary": _summary(action, recompute, reasons, prev),
        }

    # —— 第一档：还能不能用上一趟的爬取 ——
    if key is None:
        reasons.append("项目/环境/构建指纹缺一格，这一趟没有可复用的身份 —— 重爬")
        return _out(CRAWL)
    if not prev:
        reasons.append("没有上一趟：首次必须整站")
        return _out(CRAWL)
    status = str(prev.get("status") or "")
    if status in NEVER_REUSE:
        reasons.append(f"上一趟终态 {status}，不作数"
                       + ("：那一趟动过环境，最该看的是我们改了什么"
                          if status == "dirty" else "：那一趟没爬到东西"))
        return _out(CRAWL)
    if status not in REUSABLE:
        reasons.append(f"上一趟终态 {status or '未知'} 不在可复用之列 —— 重爬")
        return _out(CRAWL)
    prev_key = survey_key(project_id=prev.get("projectId"), env_id=prev.get("envId"),
                          build_fingerprint=prev.get("buildFingerprint"))
    if prev_key != key:
        reasons.append("构建指纹（或项目/环境）跟上一趟不是同一个 —— 重爬")
        return _out(CRAWL)

    # —— 第二档：爬取可以复用，只看对账那两格 ——
    if not rt:
        # 缺 ≠ 没变。这一轮没拉到路由表，就不许推出「路由表没变」。
        recompute.append(R_SIDE)
        reasons.append("本轮没拉到路由表，断不了它有没有变 —— R 侧重算，G2 记未验证")
    elif rt != str(prev.get("routeTableHash") or ""):
        recompute.append(R_SIDE)
        reasons.append("路由表变了 —— 只重算 R 侧与 G2，不重爬")
    if not sha:
        recompute.append(Q_SIDE)
        reasons.append("拿不到 QA 仓 commit，断不了清单有没有变 —— Q 侧重算")
    elif sha != str(prev.get("qaCommitSha") or ""):
        recompute.append(Q_SIDE)
        reasons.append("QA 仓 commit 变了 —— 只重算，不重爬")

    if not recompute:
        reasons.append("三格都没变 —— 原样复用上一趟的结论")
        return _out(REUSE)
    return _out(RECOMPUTE)


def _provenance(action: str, prev: dict) -> dict:
    """这份结论是**哪一趟爬取**产出的。重爬的那一轮也照样出这一节，
    值是空的 —— 只在复用时才出现的出处，和"没记过出处"长得一模一样。
    """
    if action == CRAWL:
        return {"source": "freshCrawl", "surveyId": "", "crawledAt": "",
                "buildFingerprint": "", "surveyStatus": ""}
    return {"source": "reusedSurvey",
            "surveyId": str(prev.get("surveyId") or ""),
            "crawledAt": str(prev.get("crawledAt") or ""),
            "buildFingerprint": str(prev.get("buildFingerprint") or ""),
            "surveyStatus": str(prev.get("status") or "")}


def fresh_provenance() -> dict:
    """**压根没做过复用判断**的那一轮该记的出处（比如上一趟没查着、直接开爬）。

    单独开一个口子而不是让调用方现拼一个字典：形状得跟 `plan_reuse` 重爬那一支
    **逐格一样**。少一格，下游一句 `prov["surveyStatus"]` 就 KeyError，
    而修它的人多半顺手改成 `.get(...)` —— 从那以后「没有出处」和「出处是空的」
    再也分不开了。
    """
    return _provenance(CRAWL, {})


def _summary(action: str, recompute: list, reasons: list, prev: dict) -> str:
    """一句话，**出处和结论焊在一起**。

    复用那一支必须带齐「哪一趟 + 什么时候 + 什么指纹 + 什么终态」：
    少了时间和指纹，页面上就是一句"已复用缓存"，看的人无从判断它是几天前的；
    少了终态，一趟 `partial` 会渲染得跟整站爬完一模一样 ——
    而它本来就缺着一部分页面。
    """
    if action == CRAWL:
        return "本轮重爬（不复用任何既有爬取）：" + "；".join(reasons) + "。"
    fp = str(prev.get("buildFingerprint") or "")
    head = ("本轮**没有重爬**，用的是 "
            f"{prev.get('crawledAt') or '时间未记录'} 那一趟"
            f"（survey {prev.get('surveyId') or '未记录'}，"
            f"构建指纹 {fp or '未记录'}，终态 {prev.get('status') or '未记录'}）")
    tail = ("；本轮重算：" + "/".join(recompute)) if recompute else "；本轮什么都没重算"
    return f"{head}{tail}。理由：" + "；".join(reasons) + "。"
