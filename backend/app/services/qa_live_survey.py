"""QA 域评审「真跑页面对流量」的编排层：把散在各处的纯函数接成一趟能跑的活。

**这一层只搬运和合成，一个判据都不自己写。** 判据各有自己的家：

  · 选择器怎么解析、命中怎么分档   → `qa_selectors`
  · 谁能主爬、浅扫给谁             → `qa_survey_guard`
  · 路由表拉不到算什么             → `qa_route_table`
  · 三本账怎么对、五类缺口怎么算   → `qa_coverage_reconcile`
  · 哈希/复用判据                  → `qa_survey_cache`

在这里再写一遍判断，就会出现「两套判据，而页面上看不出用的是哪一套」。

两条贯穿全模块的纪律：

1. **缺信号不是一个结论。** 每一处「没拿到」都得带着自己的名字往下走：
   路由表拉不到 ⇒ 哈希是 `None`（不是「空表的哈希」）；构建指纹没量到 ⇒ 空串
   （不伪造一个 —— 伪造的话两趟「都没量到」会哈希成「没变过」）；
   选择器一页都没探成 ⇒ `probed=False`（不是把几百条报成「没见到」）。

2. **凭证只在进程内传。** `prepare()` 合出来的 `envVars` 里是真账号密码，
   它只喂给爬取那一趟；任何要出网的形状都必须先过 `public_plan()`。
"""
from __future__ import annotations

import re

from app.services import qa_catalog
from app.services.qa_coverage_reconcile import (
    build_group_index,
    compute_gaps,
    page_applicability,
    propose_rows,
)
from app.services.qa_route_table import fetch_route_table, route_table_note
from app.services.qa_selectors import parse_selectors, probe_payload, roll_up
from app.services.qa_survey_cache import route_table_hash
from app.services.qa_survey_guard import pick_main_crawl_role, shallow_scan_roles

# 认选择器表就认这一句话。**不认文件名** —— `ui/support/selectors.ts` 是 uag-qa
# 一家的摆法，写死文件名下一个 QA 仓就得改代码（同 `detect_catalog_path` 的理由）。
SELECTOR_MARKER = "export const sel"

# `node_modules` 必须排掉：实测那个仓里 `playwright-core/types/types.d.ts` 正文
# 也有这一句，不排就命中两个文件、当场判「认不出」。
# 两道保险是**故意**的（这里排掉 + 下面再按路径滤一遍）：`:!` 这个 pathspec magic
# 万一在某个 git 版本上不生效，git 会把它当普通文件名去找 —— 结果是 0 命中，
# 那是 fail-closed，兜得住；反过来漏进 node_modules 才是静默出错。
_SELECTOR_PATHSPEC = ["*.ts", ":!*node_modules/*"]

# helper 库：`lib/*.sh`。Q 侧端点的绝大半封在这些文件里，少读一个就是一批**假**缺口。
_LIB_SH = re.compile(r"(?:^|/)lib/[^/]+\.sh$")

# 读脚本正文的条数上限。**这不是 LLM 的字节预算**（`qa_catalog_review` 里那两个常量是
# 给 prompt 用的，别拿来复用）：对账是纯集合运算，截断一个脚本等于删掉它真的打过的
# 端点，产出的是一批**看起来完全正常**的 G1/G3。所以这里只挡病态仓库
# （实测那个仓 385 个 `.sh`），挡到了要出声。
Q_MAX_SCRIPTS = 1500


# ── 选择器表：在哪、读出来什么 ──────────────────────────────────────

def locate_selector_table(repo, ref: str, hint: str = "") -> str:
    """选择器表是仓库里的哪个文件。**认内容不认文件名。**

    填了 `hint` 就必须命中：找不到直接报错，**不回退自动识别** ——
    回退等于拿另一个文件的解析结果，挂着你填的那个路径显示。

    自动识别认出 0 个或 2 个都报错，不挑一个。挑一个的后果不是「可能挑错」，
    是「挑错了也照样出一份完整报告」：另一个文件里的键当然一条都探不到，
    报出来是几百条「你的选择器都失效了」。
    """
    if hint:
        if qa_catalog._show(repo, ref, hint) is None:
            raise ValueError(
                f"QA 仓的 {ref} 上没有 {hint} —— 选择器表路径填错了？留空就自动认。")
        return hint

    hits: list[str] = []
    for line in qa_catalog._grep(repo, ref,
                                 ["-l", "--fixed-strings", SELECTOR_MARKER],
                                 _SELECTOR_PATHSPEC):
        # `git grep -l <ref>` 每行是 `<ref>:<path>`
        path = line.split(":", 1)[1] if ":" in line else line
        if "node_modules/" in path:
            continue
        if path not in hits:
            hits.append(path)

    if not hits:
        raise ValueError(
            f"QA 仓的 {ref} 上找不到公共选择器表（按内容认：`.ts` 里有 "
            f"`{SELECTOR_MARKER}`）。他那套 UI 脚本如果不走公共表，"
            f"这一趟就没有可验的选择器清单。")
    if len(hits) > 1:
        raise ValueError(
            f"认出了 {len(hits)} 份公共选择器表（{'、'.join(hits)}）—— 不替你挑一个："
            f"挑错了也会照样出一份完整报告，而里面每条「没见到」都是假的。")
    return hits[0]


def load_selector_table(project_id: str, cfg: dict) -> dict:
    """读 + 解析选择器表 → `{path, ref, parsed}`。**阻塞（git），请在线程里跑。**

    `cfg["selectorPath"]` 是覆盖项。**配置弹窗今天没有这一格**
    （`schemas/project.QaRepoConfig` 里也没这个字段），留这条读法是为了自动识别
    认错时有地方改；真要用得先给那个 schema 加字段。
    """
    repo = qa_catalog._repo_dir(str(project_id))
    ref, _branch = qa_catalog._resolve_ref(repo, cfg.get("branch") or "")
    path = locate_selector_table(repo, ref, (cfg.get("selectorPath") or "").strip())
    text = qa_catalog._show(repo, ref, path)
    if text is None:
        # `locate_selector_table` 刚确认过它在 —— 走到这儿说明 git 那边出事了
        raise ValueError(f"读不到 {path}（{ref}）")
    return {"path": path, "ref": ref, "parsed": parse_selectors(text)}


def page_paths(parsed: dict) -> dict:
    """选择器表的 `routes` → 这一趟要爬的页面清单。

    只认**静态字符串**那些。参数化的（`routes.teamDetail = (id) => …`）一条都不爬，
    也**不给它编一个 id**：编出来的 id 打开的是一个「这个东西不存在」的页面，
    而它的控件账本和 P 边照样会被记下来 —— 长得跟一个正常的空页面一模一样。
    **少爬看得见（`skipped` 有数），爬错看不见。**

    不按名字筛（`/login`、带 `?tab=` 的那些照爬）：按名字筛就是在猜哪一页
    「不该爬」，而只读五层本来就在管「进去之后不许动什么」。
    """
    routes = (parsed or {}).get("routes") or {}
    paths: list[str] = []
    dropped: list[str] = []
    for key in sorted(routes):
        raw = str(routes[key] or "").strip()
        if not raw.startswith("/"):
            # 不是站内路径（外链、锚点、空串）—— 不猜它该拼成什么样
            dropped.append(f"{key}={raw or '(空)'}")
            continue
        if raw not in paths:
            paths.append(raw)
    skipped = sorted((parsed or {}).get("routeTemplates") or [])

    declarations: list[str] = []
    if skipped:
        declarations.append(
            "%d 条参数化路由没爬（`routes.xxx = (id) => …`）：打开它要一个真实 id，"
            "而编一个 id 打开的是「不存在」页 —— 那一页的控件账本和 P 边跟一个正常的"
            "空页面长得一样。名单：%s" % (len(skipped), "、".join(skipped)))
    if dropped:
        declarations.append(
            "%d 条路由值不是站内路径（不以 `/` 开头），没爬：%s"
            % (len(dropped), "、".join(dropped)))
    return {"paths": paths, "skipped": skipped, "dropped": dropped,
            "declarations": declarations}


# ── 角色：环境变量里配了谁 ──────────────────────────────────────────

_USER_SUFFIX = "_USERNAME"
_PASS_SUFFIX = "_PASSWORD"


def roles_from(env_vars: dict) -> dict:
    """环境变量 → 这一趟能用的角色名单。

    约定跟 `qa_page_survey_crawl._role_credentials` **反着来**：
    `<PREFIX>_USERNAME` + `<PREFIX>_PASSWORD` 两样齐了才算一个角色，
    角色名 = `PREFIX` 小写、下划线换横线（`AUDITOR_*` → `auditor`，
    `TEAMB_ADMIN_*` → `teamb-admin`）。**是前缀，不是账号名**：实测那个环境里
    `AUDITOR_USERNAME` 的值是 `qa-auditor`，拿值当角色名的话取凭证会查不到。

    两处刻意：

    1. **只配了一半的不算角色，但要记名字。** 不记的话「这个环境没配只读账号」
       和「配了用户名忘了密码」在页面上是同一句「没有只读账号」，
       而后者是补一格就能跑的。
    2. **反推回去对不上的也不算。** `Auditor_USERNAME` 会推出角色 `auditor`，而爬取
       那边照约定去找的是 `AUDITOR_USERNAME` —— 大小写不一样，找不到，于是这个角色
       被静默跳过（只在账本的 `rolesSkipped` 里留一行）。在这里判出来，是把一次
       静默跳过换成一句看得见的话。
    """
    env = {str(k): v for k, v in (env_vars or {}).items()}
    roles: list[str] = []
    incomplete: list[str] = []
    mismatched: list[str] = []

    for key in sorted(env):
        if not key.endswith(_USER_SUFFIX):
            continue
        prefix = key[: -len(_USER_SUFFIX)]
        if not prefix:
            continue
        role = prefix.lower().replace("_", "-")
        if role.replace("-", "_").upper() != prefix:
            mismatched.append(key)
            continue
        if not str(env.get(key) or "").strip():
            incomplete.append(f"{role}（{key} 是空的）")
            continue
        if not str(env.get(prefix + _PASS_SUFFIX) or "").strip():
            incomplete.append(f"{role}（缺 {prefix}{_PASS_SUFFIX}）")
            continue
        if role not in roles:
            roles.append(role)

    declarations: list[str] = []
    if incomplete:
        declarations.append(
            "%d 个角色只配了一半，这一趟没用上：%s"
            % (len(incomplete), "、".join(incomplete)))
    if mismatched:
        declarations.append(
            "%d 个变量名的大小写/分隔符不合约定（要 `AUDITOR_USERNAME` 这种全大写"
            "下划线），爬取那边按约定去找会找不到，所以这里不当角色算：%s"
            % (len(mismatched), "、".join(mismatched)))
    return {"roles": roles, "incomplete": incomplete, "mismatched": mismatched,
            "declarations": declarations}


# ── 起跑前的准备（在请求里跑，错就当场 4xx） ────────────────────────

async def prepare(*, session, project_id, cfg: dict, env) -> dict:
    """合出这一趟的执行计划。**故意放在请求里同步跑**，不丢进后台。

    配置类的错（没 BASE_URL、没只读账号、认不出选择器表）应当**立刻**变成一句
    人话回给页面。丢进后台的话，人看到的是一条转了十几秒然后 failed 的任务 ——
    同一句话，晚十几秒，还得再点开一层才看得见。

    这里只有一件事是「拉」而不是「读」：路由表（打被测 BFF 的 `/api/docs/routes`）。
    它拉不到不阻断 —— `route_table_note` 会把「本轮无路由表，G2 未验证」这句话准备好，
    而 G2 停在 0 的那种沉默才是要防的。
    """
    import anyio

    from app.services.variable_service import build_run_env

    env_vars = await build_run_env(session, env.id)
    base_url = str(env_vars.get("BASE_URL") or "").strip().rstrip("/")
    if not base_url:
        raise ValueError(
            f"环境「{env.name}」里没有 BASE_URL —— 页面枚举必须知道爬哪个环境，"
            f"不猜、不用默认值。去「项目设置 → 环境」配上再来。")

    role_info = roles_from(env_vars)
    # 没只读账号就**不开爬**（判据在 `qa_survey_guard`，它会把现在配了谁一起说出来）
    main_role = pick_main_crawl_role(role_info["roles"])

    selector = await anyio.to_thread.run_sync(
        lambda: load_selector_table(str(project_id), cfg))
    pages = page_paths(selector["parsed"])
    if not pages["paths"]:
        raise ValueError(
            f"{selector['path']} 里一条静态路由都没有（参数化的 "
            f"{len(pages['skipped'])} 条不爬，理由见报告声明）—— 没有页面可爬，"
            f"这一趟不起。")

    table = await fetch_route_table(base_url)
    note = route_table_note(table)

    declarations = list(role_info["declarations"]) + list(pages["declarations"])
    if note["declaration"]:
        declarations.append(note["declaration"])
    # **构建指纹今天没有生产者**（`grep -rn build_fingerprint app/` 全在消费侧）。
    # 空串往下走 ⇒ `survey_key` 返回 `None` ⇒ 这一趟一定重爬、也不会被下一趟复用。
    # 多爬一趟的代价看得见；伪造一个指纹的代价看不见 —— 两趟「都没量到」会哈希成
    # 「没变过」，于是一趟根本没量过构建的运行命中上一趟的缓存。
    declarations.append(
        "没有构建指纹（平台还没有哪一处在产出它），所以**这一趟一定重爬**，也不会被"
        "下一趟复用 —— 不是「构建没变」，是「我们没量」。")

    return {
        "baseUrl": base_url,
        "envId": str(env.id),
        "envName": env.name or "",
        "roles": role_info["roles"],
        "mainRole": main_role,
        "shallowRoles": shallow_scan_roles(role_info["roles"]),
        "pagePaths": pages["paths"],
        "pagesSkipped": pages["skipped"],
        "selectorPath": selector["path"],
        "selectorRef": selector["ref"],
        "selectorParsed": selector["parsed"],
        "selectorProbe": probe_payload(selector["parsed"]),
        "routeTable": table,
        "routeTableNote": note,
        # 拉不到 ⇒ `route_table_hash` 是 `None`，而落库那格要字符串，转空串。
        # **空串和「空表的哈希」不是一回事**，前者是缺信号（见那个函数的文档）。
        "routeTableHash": route_table_hash(table) or "",
        "buildFingerprint": "",
        "envVars": env_vars,
        "counters": {
            "pages": len(pages["paths"]),
            "pagesSkipped": len(pages["skipped"]),
            "pagesDropped": len(pages["dropped"]),
            "roles": len(role_info["roles"]),
            "rolesIncomplete": len(role_info["incomplete"]),
            "selectorKeys": selector["parsed"]["counters"]["keys"],
            "selectorProbeable": selector["parsed"]["counters"]["probeable"],
            "routeCount": note["routeCount"],
            "routeGroups": note["groupCount"],
            "routeUnreadable": note["unreadableCount"],
            # 「形状不认识」和「认识但不是端点」分两格 —— 见 qa_route_table 模块头
            "routeSkipped": note["skippedCount"],
        },
        "declarations": declarations,
    }


# 出网前扔掉的几格。`envVars` 是真凭证；另外三格是几十上百 KB 的中间产物
# （页面要的是它们的计数，都在 `counters` 里）。
_PRIVATE = ("envVars", "selectorParsed", "selectorProbe", "routeTable")


def public_plan(plan: dict) -> dict:
    """能回给页面的那一份。**唯一的出网闸门，别在别处另开一条。**

    `envVars` 里是**完整可用的账号密码**（不是脱敏过的）。这里整格扔掉，理由同
    `qa_survey_guard.DROP_HEADERS`：留一个打了星号的键，下一个人加一行日志就又出去了。
    """
    return {k: v for k, v in (plan or {}).items() if k not in _PRIVATE}


# ── 跑完之后：选择器报告 + 三边对账 ────────────────────────────────

def selector_report(parsed: dict, ledger: dict | None) -> dict:
    """账本里的探测结果 → 四档报告。**`probed` 只认「至少探成了一页」。**

    写成「我们打算探」就错了：一趟里每次 `evaluate` 都失败（页面没开起来、脚本被
    CSP 拦了）时，报告会说他几百条选择器「都没见到」—— 那是关于**我们这一趟**的
    事实，不是关于他选择器的事实。
    """
    acc = ((ledger or {}).get("selectorProbe") or {})
    return roll_up(parsed, acc, probed=bool(acc.get("pages")))


def load_q_side(project_id: str, cfg: dict, catalog: dict) -> dict:
    """Q 侧账本：清单里每条场景的脚本正文 + helper 库 + 归属索引。

    **阻塞（git），请在线程里跑。**

    两处不能省：

    1. **清单要重读一遍。** `cached_read` 组装出来的 `domains` 留了 `groups` 却丢了
       `groupsRaw`，而 `build_group_index` 的前缀归属（按 `/api/public/v1/*` 那种划的）
       只在 `groupsRaw` 里。少了它那些域会落进 `unresolved` ——
       而「归不了属」绝不能渲染成「0 缺口」。
    2. **脚本正文不设字节预算。** 见 `Q_MAX_SCRIPTS` 那段。
    """
    repo = qa_catalog._repo_dir(str(project_id))
    ref, _branch = qa_catalog._resolve_ref(repo, cfg.get("branch") or "")

    catalog_path = ((catalog or {}).get("repo") or {}).get("catalogPath") or ""
    catalog_text = qa_catalog._show(repo, ref, catalog_path) if catalog_path else None
    if catalog_text is None:
        # `cached_read` 刚读过它（读不到会抛 GitError）—— 这里读不到只能是中间换了 ref。
        # **不降级成空索引**：空索引会让每一条端点都「归不了属」，而那份报告看起来跟
        # 「这个仓一条都没归上」一模一样。
        raise ValueError(
            f"重读清单失败：{ref} 上没有 {catalog_path or '(没有清单路径)'}")
    _scen, domain_meta, _issues = qa_catalog.parse_catalog(catalog_text)
    index = build_group_index(domain_meta)

    scenarios = list((catalog or {}).get("scenarios") or [])
    claimed = {s.get("domain") for s in scenarios if s.get("domain")}

    # 同一个脚本常常覆盖同域好几条场景 —— 按路径读一次，按 (域, 场景) 摊开
    cache: dict[str, str | None] = {}
    rows: list[dict] = []
    unreadable: list[str] = []
    truncated = 0
    for s in scenarios:
        for c in s.get("scripts") or []:
            path = c.get("path") or ""
            if not path:
                continue
            if path not in cache:
                if len(cache) >= Q_MAX_SCRIPTS:
                    truncated += 1
                    continue
                cache[path] = qa_catalog._show(repo, ref, path)
                if cache[path] is None:
                    unreadable.append(path)
            text = cache.get(path)
            if text is None:
                continue
            rows.append({"domain": s.get("domain") or "",
                         "scenarioId": s.get("id") or "",
                         "path": path, "text": text})

    helper_lib: dict[str, str] = {}
    lib_unreadable: list[str] = []
    for path in qa_catalog._ls_tree(repo, ref):
        if not _LIB_SH.search(path):
            continue
        text = qa_catalog._show(repo, ref, path)
        if text is None:
            lib_unreadable.append(path)
            continue
        helper_lib[path] = text

    declarations: list[str] = []
    if unreadable:
        declarations.append(
            "%d 个脚本读不出来，它们打过的端点这一趟一律算没人测（会虚报 G1/G3）：%s"
            % (len(unreadable), "、".join(sorted(unreadable)[:10])))
    if lib_unreadable:
        declarations.append(
            "%d 个 helper 库读不出来（%s）—— 里面封的端点在 Q 侧整个消失，缺口会虚高。"
            % (len(lib_unreadable), "、".join(sorted(lib_unreadable))))
    if truncated:
        declarations.append(
            "脚本数超过上限 %d，有 %d 处引用没读（它们打过的端点会被算成没人测）。"
            "这不是预算问题，是仓库大得反常，去看一眼再调上限。"
            % (Q_MAX_SCRIPTS, truncated))
    if index["unresolved"]:
        declarations.append(
            "%d 个域的归属规则没读懂（第三列有内容，但既没组名也没路径前缀）：%s"
            " —— 它们的缺口数**不是 0，是没算**。"
            % (len(index["unresolved"]), "、".join(index["unresolved"])))

    return {
        "scripts": rows,
        "helperLib": helper_lib,
        "index": index,
        "scenarios": scenarios,
        "claimedDomains": claimed,
        "counters": {
            "scenarios": len(scenarios),
            "scriptRefs": len(rows),
            "scriptFiles": len([p for p, t in cache.items() if t is not None]),
            "scriptsUnreadable": len(unreadable),
            "helperLibFiles": len(helper_lib),
            "helperLibUnreadable": len(lib_unreadable),
            "claimedDomains": len(claimed),
            "domainsUnresolved": len(index["unresolved"]),
        },
        "declarations": declarations,
    }


def reconcile(*, plan: dict, ledger: dict | None, items: list | None,
              page_edges: list | None, q: dict,
              page_survey_available: bool) -> dict:
    """三边对账 + 提案 + 逐域适用性。**判据全在 `qa_coverage_reconcile` 里。**

    `page_survey_available=False` 的那一趟（爬崩了）**照样要走这里** —— 走进去之后
    `compute_gaps` 会自己声明「只剩 G2，那等于一个更慢的 route-drift」。直接跳过对账
    的话，页面上只会看到一条 failed，而「这一趟没有验任何新维度」这句话不会有人说。

    `controlsClicked` 用 `.get`（不给默认 0）：`None` = 这趟没记过，
    `0` = 记过、确实一个都没点。无向枚举永远是后者，但两者的声明不一样。
    """
    led = ledger or {}
    table = plan.get("routeTable") or {}
    gaps = compute_gaps(
        page_items=list(items or []),
        routes=list(table.get("routes") or []),
        scripts=q["scripts"],
        index=q["index"],
        claimed_domains=q["claimedDomains"],
        route_table_available=bool(table.get("available")),
        page_survey_available=page_survey_available,
        # 空串 ⇒ `edge_ok` 对 `static` 边一律 fail-closed（见那个函数）
        build_fingerprint=plan.get("buildFingerprint") or None,
        helper_lib=q["helperLib"],
        page_edges=page_edges,
        controls_clicked=led.get("controlsClicked"),
    )
    proposals = propose_rows(gaps=gaps, scenarios=q["scenarios"])
    applicability = page_applicability(
        scenarios=q["scenarios"],
        page_domains=gaps.get("pageDomains"),
        page_survey_available=page_survey_available)

    # 声明汇总：计划的 + Q 侧的 + 对账的。**按序去重**，页面直接渲染这一份 ——
    # 三处各渲染一份的话，读的人得自己拼出「这一趟少验了什么」。
    seen: set[str] = set()
    declarations: list[str] = []
    for line in (list(plan.get("declarations") or [])
                 + list(q.get("declarations") or [])
                 + list(gaps.get("declarations") or [])):
        if line and line not in seen:
            seen.add(line)
            declarations.append(line)

    return {"gaps": gaps, "proposals": proposals, "applicability": applicability,
            "declarations": declarations}
