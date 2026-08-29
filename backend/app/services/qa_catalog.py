"""QA 仓场景清单 —— **只读**。

QA 仓是别人的仓库（黑盒验收仓：只有用例脚本，没有产品代码）。平台对它的全部动作
只有三个：`clone --bare`、`fetch`、`git show`。**不写、不 push、不建 worktree、
不 checkout 工作区**，也不要求对方仓库为我们改任何东西（不加字段、不加文件、不加钩子）。
新增能力时请守住这条：这个模块里出现任何写远端的 git 子命令都是 bug。

配置只有仓库地址是必须的：分支留空跟仓库默认分支走、清单路径留空按内容找、
脚本范围留空用 `git grep -l @scenario` 捞 —— 三项都是"认错了才需要填"的覆盖项。

读什么：
  1. 清单文件（自动认：场景行最多的那份 .md）—— 场景的**分母**：
     哪些场景应该存在、优先级/风险/层级各是什么、是已覆盖(✅)/待补(⬜)/已废弃(❌)。
  2. 用例脚本头部的声明 —— 场景的**分子**：`@scenario`(可多个) / `@tier` / `@known-bug`。

为什么要读两边而不是只读清单：清单的"状"列是人手维护的，脚本头是机器可校验的。
两边对不上正是最该看见的信息（清单说 ✅ 但没有脚本声明它 = 清单说谎），
所以这里如实呈现两边，不做"以清单为准"的抹平。
"""
import logging
import re
import shutil
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from app.config import settings
from app.services.git_service import GitError, _run_git, ensure_bare_repo, fetch_origin

logger = logging.getLogger(__name__)

# 风险分 R = 概率(1–3) × 影响(1–3)，取值 1–9（口径来自 QA 清单自己的「列的含义」一节）。
# ≥6 算高风险：实测 uag-qa 的取值只落在 {2,4,6,9} 上，6 正好是"概率或影响有一头拉满"。
HIGH_RISK = 6

# 场景 ID 的形状：两到六位大写字母 + 短横 + 两三位数字。清单行和文件头共用这一套。
_ID_RE = re.compile(r"[A-Z][A-Z0-9]{1,5}-\d{2,3}")
# 清单行：| SMK-01 | 场景描述 | P0 | 6 | smoke | ✅ |
# 域码不写死（别的项目有别的域码），只要求"两到六位大写字母 + 短横 + 两三位数字"。
_ROW_RE = re.compile(
    r"^\|\s*(?P<id>[A-Z][A-Z0-9]{1,5}-\d{2,3})\s*\|(?P<rest>.*)\|\s*$"
)
# 域码表行：| `SMK` | 冒烟 | Health, Docs, System |
# **第三列必须捕获**：它是「API 组 → 域码」映射的**唯一**出处，丢了它，
# 「这个端点归哪个域」就只剩下猜。第三列写成可选组而不是硬要求 —— 老清单
# 只有两列，硬要求会让整张域码表一行都读不进来（然后所有域名变空字符串，
# 页面上看着像"域码表没写"，其实是正则多要了一列）。
_DOMAIN_RE = re.compile(
    r"^\|\s*`(?P<code>[A-Z][A-Z0-9]{1,5})`\s*\|\s*(?P<name>[^|]+?)\s*\|"
    r"(?:\s*(?P<groups>[^|]*?)\s*\|)?"
)
# 组名的形状：**大写字母开头**，可带数字和内部短横（`Health`、`MCP-Tools`、`Root`）。
# 大写这条不是洁癖：第三列里混着路径前缀（`PUB` 那行写的是「按路径前缀
# `/api/public/v1/*` 划定」），不要求大写的话 `api` / `public` / `v1` 三个
# 路径段会被当成组名 —— 造出三个对不上任何路由的组，然后报「这几个组没人打过」。
_GROUP_TOKEN_RE = re.compile(r"[A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*")
# 「看着像场景行、却没解析成」的首列形状。故意放宽（小写、一位数字、下划线、
# 中文破折号都算），因为漏一行 = 少一条场景**而且永远不报错**；多报一行只是
# 让人回清单里瞄一眼。域码表（`SMK`）、统计表（层级名）、分隔行（---）都不带
# 「短横 + 数字」，不会被它捞进来。
_LOOSE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,5}\s*[-–—_]\s*\d{1,3}$")
# 文件头声明，兼容 `#` 和 `//` 两种注释符（bash 用例 / Playwright 用例）
_HEADER_LINES = 25


def _decl_re(tag: str) -> re.Pattern:
    return re.compile(rf"^\s*(?:#|//)\s*@{tag}\s+(?P<val>.+?)\s*$")


_SCENARIO_RE = _decl_re("scenario")
_TIER_RE = _decl_re("tier")
_KNOWN_BUG_RE = _decl_re("known-bug")


def cache_root() -> Path:
    """本地 bare 缓存根目录。"""
    if settings.qa_repo_cache_dir:
        return Path(settings.qa_repo_cache_dir)
    # backend/ 根目录下，和 app/ 同级
    return Path(__file__).resolve().parents[2] / ".qa-repos"


def _repo_dir(project_id: str) -> Path:
    return cache_root() / f"{project_id}.git"


# ---- 解析 ----

def _first_cell(line: str) -> str:
    """表格行的首列，剥掉反引号和加粗号。`| **AUT-01** |` → `AUT-01`。"""
    parts = line.split("|")
    return parts[1].strip().strip("`*").strip() if len(parts) > 1 else ""


def _group_cell(raw: str) -> tuple[list[str], str]:
    """域码表第三列 → (组名列表, 认不出来的那部分原文)。

    **原样返回，不归一化**（大小写/单复数归一是对账那一侧的事，见
    `qa_coverage_reconcile`）—— 在这里就归一的话，页面上再也显示不出
    清单原文写的是什么，而"清单把 `Tags` 改成了 `Tag`"正是要看见的信号。
    """
    text = (raw or "").replace("**", " ").replace("`", " ")
    groups: list[str] = []
    leftover: list[str] = []
    for chunk in re.split(r"[,，、;；]", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        if _GROUP_TOKEN_RE.fullmatch(chunk):
            if chunk not in groups:
                groups.append(chunk)
            continue
        # 混了散文的那一段（`外加 Templates`）：**捞出组名，同时把整段记账**。
        # 两个方向都得走 —— 只捞不记账，`PUB` 的路径前缀规则就静默消失了；
        # 只记账不捞，`Templates` 这个真组就丢了，而丢组是**不会红**的那一种错。
        leftover.append(chunk)
        for m in _GROUP_TOKEN_RE.finditer(chunk):
            if m.group(0) not in groups:
                groups.append(m.group(0))
    return groups, " ".join(leftover).strip()


def parse_catalog(text: str) -> tuple[list[dict], dict[str, dict], dict]:
    """解析清单 markdown。返回 (场景行, 域码->{name,groups,groupsRaw}, 读不进来的行)。

    只认「场景清单」正文里的行；统计段里那张"已实现清单"表首列是层级不是 ID，
    天然不会命中 _ROW_RE，所以不需要额外切段。

    ⚠ 第三个返回值是**这次悄悄少读了什么**，必须一路带到页面上。少一行的后果是
    「那条场景不存在」—— 覆盖率不会掉、缺口不会涨、门禁不会红，谁都发现不了。
    两类：首列像 ID 但整行没解析成（漏了尾部的 `|`、破折号打成 `–`、大小写写错），
    以及同一个 ID 出现两次（保留第一条，第二条的内容整行丢掉）。
    """
    scenarios: list[dict] = []
    domains: dict[str, dict] = {}
    groups_unreadable: list[dict] = []
    seen: set[str] = set()
    unparsed: list[dict] = []
    duplicates: list[str] = []

    for lineno, line in enumerate(text.splitlines(), 1):
        dm = _DOMAIN_RE.match(line)
        if dm:
            code = dm.group("code")
            if code not in domains:
                raw = (dm.group("groups") or "").strip()
                groups, leftover = _group_cell(raw)
                domains[code] = {"name": dm.group("name").strip(),
                                 "groups": groups, "groupsRaw": raw}
                if leftover:
                    groups_unreadable.append({"code": code, "raw": leftover[:160]})
            continue

        m = _ROW_RE.match(line)
        if not m:
            if line.lstrip().startswith("|") and _LOOSE_ID_RE.match(_first_cell(line)):
                unparsed.append({"line": lineno, "raw": line.strip()[:160]})
            continue
        sid = m.group("id")
        if sid in seen:
            # 同一个 ID 在清单里出现两次是清单自己的问题，这里保留第一条、不静默合并
            if sid not in duplicates:
                duplicates.append(sid)
            continue
        seen.add(sid)
        cols = [c.strip() for c in m.group("rest").split("|")]
        # cols = [场景, P, R, 层, 状]，列数不足就按缺省补空，别因为格式差一列整份读不出来
        title = cols[0] if len(cols) > 0 else ""
        priority = cols[1] if len(cols) > 1 else ""
        risk_raw = cols[2] if len(cols) > 2 else ""
        tier = cols[3] if len(cols) > 3 else ""
        # 已废弃的行「执行层」填的是占位破折号（实测 8 条，全是 ❌）。原样留着，
        # 筛选下拉里就会多出一个「— （—）」的选项 —— 看着像脏数据，其实是"没有层"。
        if tier.strip("`").strip() in {"—", "–", "-", "/", "N/A", "n/a", "无"}:
            tier = ""
        state_raw = cols[4] if len(cols) > 4 else ""

        if "❌" in state_raw:
            state = "deprecated"
        elif "✅" in state_raw:
            state = "covered"
        else:
            state = "gap"
        # 状态列里除了符号还常挂一句话（`@known-bug GL#531`、"待补 testid"），留着
        state_note = state_raw.replace("✅", "").replace("⬜", "").replace("❌", "").strip()
        state_note = state_note.strip("`").strip()

        scenarios.append({
            "id": sid,
            "domain": sid.rsplit("-", 1)[0],
            "title": title,
            "priority": priority.upper() if re.fullmatch(r"[Pp][0-3]", priority) else priority,
            "risk": int(risk_raw) if risk_raw.isdigit() else None,
            "tier": tier.strip("`"),
            "state": state,
            "stateNote": state_note,
        })

    return scenarios, domains, {"unparsedRows": unparsed, "duplicateIds": duplicates,
                                "domainGroupsUnreadable": groups_unreadable}


def domain_index(domains: dict[str, dict]) -> dict[str, set[str]]:
    """`{组名: {域码, ...}}` —— **值必须是集合**。

    域码表里一个组会同时属于好几个域，这是清单**故意**这么写的：
    `PUB` 按路径前缀划定、和 `TEM/PRV/AGT/MCP` 重叠，`Root` 组同属 `SMK/MCP/SEC`。
    写成 `dict[str, str]` 的话后一个域把前一个覆盖掉，**一个字都不会报错**，
    而对账那边算出来的缺口从此少一整个域 —— 少算的缺口不会红，没人发现得了。
    """
    out: dict[str, set[str]] = {}
    for code, meta in (domains or {}).items():
        for g in meta.get("groups") or []:
            out.setdefault(g, set()).add(code)
    return out


def _scenario_ids(value: str) -> list[str]:
    """`@scenario AUT-01 AUT-02   ← 第一个是主场景` → ['AUT-01', 'AUT-02']。

    只认形如 ID 的 token，遇到第一个不像 ID 的就停 —— 行尾常跟着中文说明，
    整行 split() 会把说明也当成场景 ID，然后在页面上冒出一堆"孤儿脚本"。
    """
    ids: list[str] = []
    for token in value.split():
        if not _ID_RE.fullmatch(token):
            break
        ids.append(token)
    return ids


def parse_case_header(text: str) -> dict:
    """从用例文件前 25 行取 @scenario / @tier / @known-bug。"""
    ids: list[str] = []
    tier = ""
    bugs: list[str] = []
    for line in text.splitlines()[:_HEADER_LINES]:
        m = _SCENARIO_RE.match(line)
        if m:
            ids.extend(_scenario_ids(m.group("val")))
            continue
        m = _TIER_RE.match(line)
        if m and not tier:
            tier = m.group("val").strip()
            continue
        m = _KNOWN_BUG_RE.match(line)
        if m:
            bugs.append(m.group("val").strip())
    return {"ids": ids, "tier": tier, "knownBugs": bugs}


@lru_cache(maxsize=64)
def _glob_to_re(pattern: str) -> re.Pattern:
    """把 `api/**/*.sh` 这类 glob 编译成正则。

    不用 fnmatch：它的 `*` 会跨 `/`，于是 `api/**/*.sh` 既match 不到 `api/x.sh`
    （硬要求中间那个斜杠），又会把 `*` 当成 `**` 用，两头都不对。这里按 glob 的
    通常语义来：`**` 跨目录、`*` 不跨、`/**/` 允许零层目录。
    """
    out = ["^"]
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if pattern.startswith("/**/", i):
            out.append("/(?:.*/)?")
            i += 4
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif ch == "*":
            out.append("[^/]*")
            i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def _match_globs(path: str, globs: list[str]) -> bool:
    return any(_glob_to_re(g).match(path) for g in globs)


# ---- git 只读访问 ----

def _show(repo: Path, ref: str, file_path: str) -> str | None:
    try:
        return _run_git(["--git-dir", str(repo), "show", f"{ref}:{file_path}"])
    except GitError:
        return None


def _ls_tree(repo: Path, ref: str) -> list[str]:
    out = _run_git(["--git-dir", str(repo), "ls-tree", "-r", "--name-only", ref])
    return [line for line in out.splitlines() if line]


def _grep(repo: Path, ref: str, args: list[str], pathspec: list[str] | None = None) -> list[str]:
    """git grep（只读，在 tree 对象上搜，不需要工作区）。没命中时 git 返回 1，当空结果。

    ref 必须排在 `--` 前面 —— 放到 pathspec 那一侧的话 git 会把它当文件名找，
    结果是"一条都没搜到"而不是报错（清单自动识别就这么静默瞎过一次）。
    """
    cmd = ["--git-dir", str(repo), "grep", *args, ref]
    if pathspec:
        cmd += ["--", *pathspec]
    try:
        out = _run_git(cmd)
    except GitError:
        return []
    return [line for line in out.splitlines() if line]


def detect_catalog_path(repo: Path, ref: str) -> str:
    """没配清单路径时自己找：仓库里场景行最多的那份 markdown。

    不猜文件名（别的 QA 仓不会叫 test-scenario-catalog.md），只认内容：
    一行以 `| 域码-数字 |` 开头才算场景行。行数太少的（README 里举个例子）不算。
    """
    counts: list[tuple[int, str]] = []
    # git grep 用的是 POSIX 正则，和 _ROW_RE 那套写法不通用，这里单写一份等价的
    for line in _grep(repo, ref, ["-c", "-E", r"^\|[[:space:]]*[A-Z][A-Z0-9]{1,5}-[0-9]{2,3}[[:space:]]*\|"], ["*.md"]):
        # 形如 `HEAD:docs/x.md:357`
        try:
            _, path, n = line.rsplit(":", 2) if line.count(":") >= 2 else (None, None, None)
            counts.append((int(n), path))
        except (ValueError, TypeError):
            continue
    counts = [c for c in counts if c[0] >= 5]
    if not counts:
        raise GitError(
            "没在这个仓库里找到场景清单：没有哪份 .md 带够 `| ID | 场景 | … |` 这样的表格行。"
            "确认分支对不对，或在「高级」里手填清单文件路径。"
        )
    counts.sort(key=lambda c: (-c[0], c[1]))
    return counts[0][1]


# 模板/示例文件也会带 `@scenario XXX-01`（uag-qa 的 .claude/skills/.../case.sh.tmpl 就是），
# 但它不是用例：算进去会平白多出一条"声明了清单外 ID 的脚本"。
_TEMPLATE_SUFFIXES = (".tmpl", ".template", ".example", ".sample", ".dist")
_TEMPLATE_DIRS = {"templates", "template", "examples", "example"}


def _looks_like_template(path: str) -> bool:
    if path.endswith(_TEMPLATE_SUFFIXES):
        return True
    return bool(_TEMPLATE_DIRS & set(path.split("/")[:-1]))


def discover_case_files(repo: Path, ref: str, catalog_path: str) -> list[str]:
    """没配 glob 时自己找：所有声明了 @scenario 的文件。

    比配目录 glob 准 —— 脚本挪目录不用改配置，也不会把没声明场景的支持库当成用例。
    排除三类：清单本身、.md（文档里讲这套约定时会原样引用 `# @scenario`）、
    模板/示例文件（占位 ID 会变成假的孤儿脚本）。
    真要把模板当用例看，去「高级」里填 glob —— 填了就完全按 glob 来。
    """
    files: list[str] = []
    for line in _grep(repo, ref, ["-l", "-F", "@scenario"]):
        path = line.split(":", 1)[1] if ":" in line else line
        if path.endswith(".md") or path == catalog_path or _looks_like_template(path):
            continue
        files.append(path)
    return sorted(set(files))


def _pick_ref(repo: Path, branch: str) -> str | None:
    """同一个分支名的两个 ref，按新鲜度取：`refs/remotes/origin/*` 优先。

    `refs/heads/*` 只是 clone 那一刻的快照 —— fetch 的 refspec 是
    `+refs/heads/*:refs/remotes/origin/*`，只写 remotes，永远碰不到它。
    留着它兜底是因为第一次 clone 之后不 fetch（`sync_and_read` 会跳过），
    那一趟 remotes 还是空的。
    """
    for candidate in (f"refs/remotes/origin/{branch}", f"refs/heads/{branch}"):
        try:
            _run_git(["--git-dir", str(repo), "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"])
            return candidate
        except GitError:
            continue
    return None


def _resolve_ref(repo: Path, branch: str) -> tuple[str, str]:
    """分支名 → (ref, 实际读的分支名)。

    留空 = 跟这个仓库自己的默认分支走（bare 仓的 HEAD 指的就是它）。**不默认 main**：
    猜错了页面会说"找不到分支 main"，而人根本没填过分支，只会以为是仓库地址填错了。

    填了分支就必须命中：找不到就报错，不再悄悄回退 HEAD —— 回退等于拿别的分支的数据
    挂着你填的分支名显示，比报错难查得多。

    ⚠ **留空这条路也必须走 `_pick_ref`，不能直接读 HEAD。** bare 仓的 HEAD 指向
    `refs/heads/<默认分支>`，而 fetch 只写 `refs/remotes/origin/*` —— 读 HEAD 拿到的
    永远是第一次 clone 那一刻的快照：fetch 成功了、页面数字一动不动，"拉取最新"还报成功。
    偏偏"分支留空"是文档推荐的用法，等于默认踩坑（填了分支名反而躲过去了）。
    2026-08-26 实测：uag-qa 缓存里 refs/heads/main 落后 origin/main 16 个提交，
    覆盖率少算 6 个点（47% vs 53%）、缺口多报 26 条。
    """
    if not branch:
        try:
            name = _run_git(["--git-dir", str(repo), "symbolic-ref", "--short", "HEAD"])
        except GitError:
            name = ""              # 游离 HEAD：能读，只是没名字
        if not name:
            return "HEAD", "HEAD"
        return _pick_ref(repo, name) or "HEAD", name

    ref = _pick_ref(repo, branch)
    if ref is None:
        raise GitError(f"QA 仓里没有分支 {branch}（分支留空就跟仓库默认分支走）")
    return ref, branch


def _last_fetch_at(repo: Path) -> str | None:
    """上一次真从远端抓过是什么时候。**不是提交时间** —— 两个都要有，缺一个就没法判新鲜度：
    提交时间旧可能是 QA 那边本来就没动，拉取时间旧才是"这页过期了"。

    不额外存状态：`FETCH_HEAD` 是 git 每次 fetch 都重写的文件（哪怕这次没有新提交），
    它的 mtime 就是答案；还没 fetch 过就退回 `HEAD` 的 mtime（= clone 时间）。
    **别用 config 的 mtime**：`ensure_bare_repo` 每次都会写一遍 config，那个时间永远是"刚刚"。
    """
    for name in ("FETCH_HEAD", "HEAD"):
        try:
            ts = (repo / name).stat().st_mtime
        except OSError:
            continue
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    return None


def _drop_stale_cache(repo: Path, url: str) -> None:
    """缓存目录按项目建，改了仓库地址就得丢掉重来。

    不丢的话 `ensure_bare_repo` 看见 HEAD 存在就直接复用，之后 fetch 的还是老仓库 ——
    页面上仓库地址已经改了、数据还是旧的那家，属于查不出来的那种错。
    """
    if not (repo / "HEAD").exists():
        return
    try:
        current = _run_git(["--git-dir", str(repo), "config", "--get", "remote.origin.url"])
    except GitError:
        current = ""
    if current != url:
        logger.info("qa repo url changed (%s -> %s), dropping cache %s", current, url, repo)
        shutil.rmtree(repo, ignore_errors=True)


def sync_and_read(project_id: str, cfg: dict, do_fetch: bool = True) -> dict:
    """把 QA 仓抓到本地只读缓存并解析。阻塞调用，请在线程里跑。"""
    repo = _repo_dir(project_id)
    url = cfg["url"]

    _drop_stale_cache(repo, url)
    first_time = ensure_bare_repo(url, repo)
    if do_fetch and not first_time:
        fetch_origin(repo, repo / "qa-catalog.lock")

    ref, branch_name = _resolve_ref(repo, cfg.get("branch") or "")
    commit_sha = _run_git(["--git-dir", str(repo), "rev-parse", ref])
    commit_date = _run_git(["--git-dir", str(repo), "log", "-1", "--format=%cI", ref])
    commit_subject = _run_git(["--git-dir", str(repo), "log", "-1", "--format=%s", ref])

    # 清单路径和脚本范围都能自己认出来，配置里填了就当覆盖用（认错了才需要填）
    catalog_path = cfg.get("catalogPath") or ""
    catalog_auto = not catalog_path
    if catalog_auto:
        catalog_path = detect_catalog_path(repo, ref)

    catalog_text = _show(repo, ref, catalog_path)
    if catalog_text is None:
        raise GitError(f"QA 仓的 {branch_name} 分支上没有 {catalog_path}")

    scenarios, domain_names, catalog_issues = parse_catalog(catalog_text)

    globs = cfg.get("caseGlobs") or []
    if globs:
        files = [p for p in _ls_tree(repo, ref) if _match_globs(p, globs)]
    else:
        files = discover_case_files(repo, ref, catalog_path)

    cases: list[dict] = []
    for path in files:
        content = _show(repo, ref, path)
        if content is None:
            continue
        header = parse_case_header(content)
        if not header["ids"]:
            continue  # 没声明 ID 的文件不是用例（支持库/夹具）
        cases.append({"path": path, **header})

    return _assemble(scenarios, domain_names, cases, catalog_issues, {
        "url": url,
        "branch": branch_name,
        "branchAuto": not (cfg.get("branch") or ""),
        "catalogPath": catalog_path,
        "catalogAuto": catalog_auto,
        "caseDiscovery": "glob" if globs else "grep",
        "commitSha": commit_sha,
        "commitShort": commit_sha[:9],
        "commitDate": commit_date,
        "commitSubject": commit_subject,
        "fetchedAt": _last_fetch_at(repo),
        "caseFiles": len(files),
    })


def _assemble(scenarios: list[dict], domain_meta: dict[str, dict], cases: list[dict],
              catalog_issues: dict, repo_meta: dict) -> dict:
    by_id = {s["id"]: s for s in scenarios}
    for s in scenarios:
        s["scripts"] = []
        s["knownBugs"] = []
        s["domainName"] = (domain_meta.get(s["domain"]) or {}).get("name", "")

    orphan_scripts: list[dict] = []
    for c in cases:
        unknown = []
        for i, sid in enumerate(c["ids"]):
            target = by_id.get(sid)
            if target is None:
                unknown.append(sid)
                continue
            target["scripts"].append({
                "path": c["path"],
                "tier": c["tier"],
                # 第一个 @scenario 是主 ID，决定文件放哪个目录（QA 仓的规矩）
                "primary": i == 0,
            })
            for b in c["knownBugs"]:
                if b not in target["knownBugs"]:
                    target["knownBugs"].append(b)
        if unknown:
            orphan_scripts.append({"path": c["path"], "ids": unknown})

    # 清单说 ✅ 但没有任何脚本声明它 —— QA 仓的 check-coverage.sh 管这叫「抓清单说谎」
    for s in scenarios:
        s["claimedButUncovered"] = s["state"] == "covered" and not s["scripts"]

    total = len([s for s in scenarios if s["state"] != "deprecated"])
    covered = len([s for s in scenarios if s["state"] == "covered"])
    gap = len([s for s in scenarios if s["state"] == "gap"])
    deprecated = len([s for s in scenarios if s["state"] == "deprecated"])

    by_priority: dict[str, dict] = {}
    for s in scenarios:
        if s["state"] == "deprecated":
            continue
        p = s["priority"] or "—"
        slot = by_priority.setdefault(p, {"total": 0, "covered": 0, "gap": 0})
        slot["total"] += 1
        slot["covered" if s["state"] == "covered" else "gap"] += 1

    domains: list[dict] = []
    for code in sorted({s["domain"] for s in scenarios}):
        rows = [s for s in scenarios if s["domain"] == code and s["state"] != "deprecated"]
        gaps = [s for s in rows if s["state"] == "gap"]
        meta = domain_meta.get(code) or {}
        domains.append({
            "code": code,
            "name": meta.get("name", ""),
            # 第三列原样带出来：对账要用，页面上也要看得见清单写的是什么
            "groups": meta.get("groups") or [],
            "total": len(rows),
            "covered": len([s for s in rows if s["state"] == "covered"]),
            # 页面按缺口排序找「黑洞域」，P0 缺口决定先啃哪个
            "gap": len(gaps),
            "p0Gap": len([s for s in gaps if s["priority"] == "P0"]),
        })

    known_bug_scenarios = [s["id"] for s in scenarios if s["knownBugs"]]
    # 「N 条场景挂着缺陷」和「一共几个缺陷单」是两个数：一个缺陷能压住好几条场景
    # （实测 uag-qa 的 F-5 一个号挂在 4 个文件上）。只报前者会让人以为缺陷有 N 个，
    # 把「等 3 个 bug 修完」误读成「等 12 个 bug 修完」。
    # 取值规则不写死任何仓库的号段：`@known-bug <号> <说明>`，第一个 token 就是号。
    # 顺手按缺陷号归一遍：页面要能回答「那 8 条到底在等哪一个单子」，
    # 光给个总数还是得回仓里 grep
    bug_index: dict[str, list[str]] = {}
    for s in scenarios:
        for b in s["knownBugs"]:
            parts = b.split()
            ref = parts[0].strip("`") if parts else ""
            if not ref:
                continue
            holds = bug_index.setdefault(ref, [])
            if s["id"] not in holds:
                holds.append(s["id"])
    bug_refs = [{"ref": r, "scenarios": ids} for r, ids in bug_index.items()]
    lying = [s["id"] for s in scenarios if s["claimedButUncovered"]]
    # 「已覆盖」里有一批是明知跑出来是红的 —— 不点出来的话覆盖率是虚高的
    covered_with_bugs = [s["id"] for s in scenarios if s["state"] == "covered" and s["knownBugs"]]
    # QA 清单 §1.1：「P 和 R 是两条独立的轴。一条 P3 场景评出 R=8，
    # 那是『回去重新审优先级』的信号」——把这条体检替他做了
    risk_mismatch = [
        s["id"] for s in scenarios
        if s["state"] != "deprecated" and (s["risk"] or 0) >= HIGH_RISK
        and s["priority"] in ("P2", "P3")
    ]

    return {
        "repo": repo_meta,
        "summary": {
            "total": total,
            "covered": covered,
            "gap": gap,
            "deprecated": deprecated,
            "scripts": len(cases),
            "knownBugScenarios": len(known_bug_scenarios),
            "knownBugRefs": len(bug_refs),
            "coveredWithBugs": len(covered_with_bugs),
            "claimedButUncovered": len(lying),
            "orphanScripts": len(orphan_scripts),
            "riskMismatch": len(risk_mismatch),
            # 这两个是「这次少读了多少」，0 也要出现在页面上：只在非 0 时冒出来的指标，
            # 跟「没算过」长得一模一样
            "unparsedRows": len(catalog_issues.get("unparsedRows") or []),
            "duplicateIds": len(catalog_issues.get("duplicateIds") or []),
            "byPriority": by_priority,
        },
        "domains": domains,
        "scenarios": scenarios,
        "orphanScriptList": orphan_scripts,
        "knownBugRefList": bug_refs,
        "catalogIssues": catalog_issues,
    }


# ---- 内存缓存：同一个 commit 不重复解析 ----
_CACHE: dict[str, tuple[str, dict]] = {}


def cached_read(project_id: str, cfg: dict, refresh: bool) -> dict:
    """refresh=True 才打远端；否则本地缓存有就直接用。"""
    # glob 也进 key：只改脚本范围时 commit 没变，不进 key 的话缓存会原样顶回旧结果
    key = "|".join([
        str(project_id), cfg.get("url") or "", cfg.get("branch") or "",
        cfg.get("catalogPath") or "", ",".join(cfg.get("caseGlobs") or []),
    ])
    repo = _repo_dir(project_id)
    if not refresh and (repo / "HEAD").exists() and key in _CACHE:
        try:
            ref, _ = _resolve_ref(repo, cfg.get("branch") or "")
            sha = _run_git(["--git-dir", str(repo), "rev-parse", ref])
            cached_sha, data = _CACHE[key]
            if cached_sha == sha:
                return data
        except GitError:
            pass

    data = sync_and_read(project_id, cfg, do_fetch=refresh)
    _CACHE[key] = (data["repo"]["commitSha"], data)
    return data


# ---- 打开某个文件看内容（只读，git show）----

# 脚本一般几 KB。设上限是防"点开一个 3MB 的夹带文件把浏览器卡死"，
# 不是防越权 —— 真正管越权的是下面那份白名单。
MAX_FILE_BYTES = 200_000


def readable_paths(data: dict) -> set[str]:
    """这一页允许点开的文件 = **本次解析真的引用到的那些**。

    白名单从已经算好的数据里现取，不做 `..`/绝对路径之类的清洗：清洗是黑名单思路，
    漏一个写法就等于把别人仓库里的任意文件（比如 CI 里那份密钥模板）变成可读接口。
    页面上没出现过的路径，这里一律不给。
    """
    paths = {c["path"] for s in data.get("scenarios") or [] for c in (s.get("scripts") or [])}
    paths.update(x["path"] for x in data.get("orphanScriptList") or [])
    catalog = (data.get("repo") or {}).get("catalogPath")
    if catalog:
        paths.add(catalog)
    return paths


def read_file(project_id: str, cfg: dict, path: str) -> dict:
    """读 QA 仓里某个文件的内容（`git show <ref>:<path>`）。阻塞调用，请在线程里跑。"""
    data = cached_read(project_id, cfg, refresh=False)
    catalog_path = (data.get("repo") or {}).get("catalogPath") or ""
    if path not in readable_paths(data):
        raise GitError(f"这个文件不在清单引用的范围里：{path}")

    repo = _repo_dir(project_id)
    ref, _ = _resolve_ref(repo, cfg.get("branch") or "")
    text = _show(repo, ref, path)
    if text is None:
        raise GitError(f"QA 仓里读不到 {path}（清单引用了它，但这个 commit 上没有这个文件）")

    raw = text.encode("utf-8")
    truncated = len(raw) > MAX_FILE_BYTES
    if truncated:
        text = raw[:MAX_FILE_BYTES].decode("utf-8", "ignore")
    return {
        "path": path,
        "content": text,
        "lines": text.count("\n") + 1,
        "bytes": len(raw),
        "truncated": truncated,
        "commitSha": data["repo"]["commitSha"],
        # 抽屉标题上要显示"这个脚本自己声明覆盖了哪几条"——跟清单对不对得上，
        # 点开的人第一眼就想知道
        "header": {} if path == catalog_path else parse_case_header(text),
    }
