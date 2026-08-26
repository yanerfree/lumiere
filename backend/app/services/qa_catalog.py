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
_DOMAIN_RE = re.compile(r"^\|\s*`(?P<code>[A-Z][A-Z0-9]{1,5})`\s*\|\s*(?P<name>[^|]+?)\s*\|")
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

def parse_catalog(text: str) -> tuple[list[dict], dict[str, str]]:
    """解析清单 markdown。返回 (场景行, 域码->域名)。

    只认「场景清单」正文里的行；统计段里那张"已实现清单"表首列是层级不是 ID，
    天然不会命中 _ROW_RE，所以不需要额外切段。
    """
    scenarios: list[dict] = []
    domains: dict[str, str] = {}
    seen: set[str] = set()

    for line in text.splitlines():
        dm = _DOMAIN_RE.match(line)
        if dm:
            domains.setdefault(dm.group("code"), dm.group("name").strip())
            continue

        m = _ROW_RE.match(line)
        if not m:
            continue
        sid = m.group("id")
        if sid in seen:
            # 同一个 ID 在清单里出现两次是清单自己的问题，这里保留第一条、不静默合并
            continue
        seen.add(sid)
        cols = [c.strip() for c in m.group("rest").split("|")]
        # cols = [场景, P, R, 层, 状]，列数不足就按缺省补空，别因为格式差一列整份读不出来
        title = cols[0] if len(cols) > 0 else ""
        priority = cols[1] if len(cols) > 1 else ""
        risk_raw = cols[2] if len(cols) > 2 else ""
        tier = cols[3] if len(cols) > 3 else ""
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

    return scenarios, domains


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


def _resolve_ref(repo: Path, branch: str) -> tuple[str, str]:
    """分支名 → (ref, 实际读的分支名)。

    留空 = 跟这个仓库自己的默认分支走（bare 仓的 HEAD 指的就是它）。**不默认 main**：
    猜错了页面会说"找不到分支 main"，而人根本没填过分支，只会以为是仓库地址填错了。

    填了分支就必须命中：找不到就报错，不再悄悄回退 HEAD —— 回退等于拿别的分支的数据
    挂着你填的分支名显示，比报错难查得多。
    """
    if not branch:
        try:
            name = _run_git(["--git-dir", str(repo), "symbolic-ref", "--short", "HEAD"])
        except GitError:
            name = "HEAD"          # 游离 HEAD：能读，只是没名字
        return "HEAD", name or "HEAD"

    for candidate in (f"refs/remotes/origin/{branch}", f"refs/heads/{branch}"):
        try:
            _run_git(["--git-dir", str(repo), "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"])
            return candidate, branch
        except GitError:
            continue
    raise GitError(f"QA 仓里没有分支 {branch}（分支留空就跟仓库默认分支走）")


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

    scenarios, domain_names = parse_catalog(catalog_text)

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

    return _assemble(scenarios, domain_names, cases, {
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
        "caseFiles": len(files),
    })


def _assemble(scenarios: list[dict], domain_names: dict[str, str], cases: list[dict], repo_meta: dict) -> dict:
    by_id = {s["id"]: s for s in scenarios}
    for s in scenarios:
        s["scripts"] = []
        s["knownBugs"] = []
        s["domainName"] = domain_names.get(s["domain"], "")

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
        domains.append({
            "code": code,
            "name": domain_names.get(code, ""),
            "total": len(rows),
            "covered": len([s for s in rows if s["state"] == "covered"]),
            # 页面按缺口排序找「黑洞域」，P0 缺口决定先啃哪个
            "gap": len(gaps),
            "p0Gap": len([s for s in gaps if s["priority"] == "P0"]),
        })

    known_bug_scenarios = [s["id"] for s in scenarios if s["knownBugs"]]
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
            "coveredWithBugs": len(covered_with_bugs),
            "claimedButUncovered": len(lying),
            "orphanScripts": len(orphan_scripts),
            "riskMismatch": len(risk_mismatch),
            "byPriority": by_priority,
        },
        "domains": domains,
        "scenarios": scenarios,
        "orphanScriptList": orphan_scripts,
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
