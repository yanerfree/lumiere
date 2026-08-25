"""QA 仓场景清单 —— **只读**。

QA 仓是别人的仓库（黑盒验收仓：只有用例脚本，没有产品代码）。平台对它的全部动作
只有三个：`clone --bare`、`fetch`、`git show`。**不写、不 push、不建 worktree、
不 checkout 工作区**，也不要求对方仓库为我们改任何东西（不加字段、不加文件、不加钩子）。
新增能力时请守住这条：这个模块里出现任何写远端的 git 子命令都是 bug。

读什么：
  1. 清单文件（默认 `docs/test-scenario-catalog.md`）—— 场景的**分母**：
     哪些场景应该存在、优先级/风险/层级各是什么、是已覆盖(✅)/待补(⬜)/已废弃(❌)。
  2. 用例脚本头部的声明 —— 场景的**分子**：`@scenario`(可多个) / `@tier` / `@known-bug`。

为什么要读两边而不是只读清单：清单的"状"列是人手维护的，脚本头是机器可校验的。
两边对不上正是最该看见的信息（清单说 ✅ 但没有脚本声明它 = 清单说谎），
所以这里如实呈现两边，不做"以清单为准"的抹平。
"""
import logging
import re
from functools import lru_cache
from pathlib import Path

from app.config import settings
from app.services.git_service import GitError, _run_git, ensure_bare_repo, fetch_origin

logger = logging.getLogger(__name__)

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


def parse_case_header(text: str) -> dict:
    """从用例文件前 25 行取 @scenario / @tier / @known-bug。"""
    ids: list[str] = []
    tier = ""
    bugs: list[str] = []
    for line in text.splitlines()[:_HEADER_LINES]:
        m = _SCENARIO_RE.match(line)
        if m:
            ids.extend(m.group("val").split())
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


def _resolve_ref(repo: Path, branch: str) -> str:
    """分支名 → 可用的 ref。远端分支名可能和默认的 main 不一致，回退到 HEAD。"""
    for candidate in (f"refs/remotes/origin/{branch}", f"refs/heads/{branch}", "HEAD"):
        try:
            _run_git(["--git-dir", str(repo), "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"])
            return candidate
        except GitError:
            continue
    raise GitError(f"QA 仓里找不到分支 {branch}，也读不到 HEAD")


def sync_and_read(project_id: str, cfg: dict, do_fetch: bool = True) -> dict:
    """把 QA 仓抓到本地只读缓存并解析。阻塞调用，请在线程里跑。"""
    repo = _repo_dir(project_id)
    url = cfg["url"]

    first_time = ensure_bare_repo(url, repo)
    if do_fetch and not first_time:
        fetch_origin(repo, repo / "qa-catalog.lock")

    ref = _resolve_ref(repo, cfg.get("branch") or "main")
    commit_sha = _run_git(["--git-dir", str(repo), "rev-parse", ref])
    commit_date = _run_git(["--git-dir", str(repo), "log", "-1", "--format=%cI", ref])
    commit_subject = _run_git(["--git-dir", str(repo), "log", "-1", "--format=%s", ref])

    catalog_path = cfg.get("catalogPath") or ""
    catalog_text = _show(repo, ref, catalog_path) if catalog_path else None
    if catalog_text is None:
        raise GitError(f"QA 仓的 {cfg.get('branch') or 'HEAD'} 上没有 {catalog_path or '（未配置清单路径）'}")

    scenarios, domain_names = parse_catalog(catalog_text)

    globs = cfg.get("caseGlobs") or []
    files = [p for p in _ls_tree(repo, ref) if _match_globs(p, globs)] if globs else []

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
        "branch": cfg.get("branch") or "main",
        "catalogPath": catalog_path,
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
        domains.append({
            "code": code,
            "name": domain_names.get(code, ""),
            "total": len(rows),
            "covered": len([s for s in rows if s["state"] == "covered"]),
        })

    known_bug_scenarios = [s["id"] for s in scenarios if s["knownBugs"]]
    lying = [s["id"] for s in scenarios if s["claimedButUncovered"]]

    return {
        "repo": repo_meta,
        "summary": {
            "total": total,
            "covered": covered,
            "gap": gap,
            "deprecated": deprecated,
            "scripts": len(cases),
            "knownBugScenarios": len(known_bug_scenarios),
            "claimedButUncovered": len(lying),
            "orphanScripts": len(orphan_scripts),
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
    key = f"{project_id}:{cfg.get('url')}:{cfg.get('branch')}:{cfg.get('catalogPath')}"
    repo = _repo_dir(project_id)
    if not refresh and (repo / "HEAD").exists() and key in _CACHE:
        try:
            ref = _resolve_ref(repo, cfg.get("branch") or "main")
            sha = _run_git(["--git-dir", str(repo), "rev-parse", ref])
            cached_sha, data = _CACHE[key]
            if cached_sha == sha:
                return data
        except GitError:
            pass

    data = sync_and_read(project_id, cfg, do_fetch=refresh)
    _CACHE[key] = (data["repo"]["commitSha"], data)
    return data
