"""QA 场景清单解析 + 只读约束封样。

数据取自真实的 uag-qa 仓（357 行清单 / 84 个用例文件）里的代表性片段：
多 ID 声明、`//` 与 `#` 两种注释、状态列挂 `@known-bug`、已废弃行。
"""
import re
import subprocess
from pathlib import Path

import pytest

from app.services.git_service import GitError
from app.services.qa_catalog import (
    _assemble,
    _glob_to_re,
    _match_globs,
    _resolve_ref,
    detect_catalog_path,
    discover_case_files,
    parse_case_header,
    parse_catalog,
)

CATALOG = """\
## 2. 域码表

| 域码 | 名称 | 覆盖的 API 组 |
|---|---|---|
| `SMK` | 冒烟 | Health, Docs, System |
| `MCP` | MCP 能力 | MCP-Tools, Skills |

## 3. 场景清单

### 3.1 SMK — 冒烟

| ID | 场景 | P | R | 层 | 状 |
|---|---|---|---|---|---|
| SMK-01 | `GET /healthz` 返回 200 | P0 | 6 | smoke | ✅ |
| SMK-08 | `GET /api/docs/openapi.yaml` 返回合法 YAML | P2 | 2 | api | ⬜ |
| SMK-09 | 早年的探针端点 | P3 | 1 | api | ❌ 已废弃 |

### 3.12 MCP — MCP 能力

| ID | 场景 | P | R | 层 | 状 |
|---|---|---|---|---|---|
| MCP-38 | 审批挂起要落审计 | P0 | 9 | scenario | ✅ `@known-bug GL#531` |
| MCP-99 | 清单标了已覆盖但没人写 | P1 | 4 | api | ✅ |

## 4. 统计

| 层 | 用例文件 | 覆盖的 ID |
|---|---|---|
| smoke | `api/smk/health-and-version.sh` | SMK-01 |
"""


def test_parse_catalog_rows():
    scen, domains = parse_catalog(CATALOG)
    ids = [s["id"] for s in scen]
    # §4 统计段那张表首列是层级不是 ID，不能被当成场景行读进来
    assert ids == ["SMK-01", "SMK-08", "SMK-09", "MCP-38", "MCP-99"]
    assert domains == {"SMK": "冒烟", "MCP": "MCP 能力"}

    first = scen[0]
    assert first["domain"] == "SMK"
    assert first["priority"] == "P0"
    assert first["risk"] == 6
    assert first["tier"] == "smoke"
    assert first["state"] == "covered"


def test_parse_catalog_states_and_note():
    scen, _ = parse_catalog(CATALOG)
    by_id = {s["id"]: s for s in scen}
    assert by_id["SMK-08"]["state"] == "gap"
    assert by_id["SMK-09"]["state"] == "deprecated"
    assert by_id["SMK-09"]["stateNote"] == "已废弃"
    # 状态列里挂的说明要留着 —— 它常常是"为什么还带着缺陷也算覆盖"的唯一线索
    assert by_id["MCP-38"]["state"] == "covered"
    assert "GL#531" in by_id["MCP-38"]["stateNote"]


def test_parse_catalog_dedup_keeps_first():
    dup = CATALOG + "\n| SMK-01 | 重复行 | P3 | 1 | api | ⬜ |\n"
    scen, _ = parse_catalog(dup)
    assert [s["id"] for s in scen].count("SMK-01") == 1
    assert next(s for s in scen if s["id"] == "SMK-01")["state"] == "covered"


SH_CASE = """\
#!/usr/bin/env bash
# @scenario AUT-01 AUT-02 AUT-05
# @tier smoke
# @known-bug GL#531 审计记录各缺一半
set -euo pipefail
"""

TS_CASE = """\
// @scenario AUT-20
// @tier ui
import { test, expect } from '@playwright/test'
"""


def test_parse_case_header_bash():
    h = parse_case_header(SH_CASE)
    assert h["ids"] == ["AUT-01", "AUT-02", "AUT-05"]
    assert h["tier"] == "smoke"
    assert h["knownBugs"] == ["GL#531 审计记录各缺一半"]


def test_parse_case_header_playwright():
    h = parse_case_header(TS_CASE)
    assert h["ids"] == ["AUT-20"]
    assert h["tier"] == "ui"
    assert h["knownBugs"] == []


def test_parse_case_header_stops_at_trailing_note():
    # 行尾常跟着说明：整行 split() 会把「←」「覆盖哪些场景」也当成场景 ID
    h = parse_case_header("# @scenario AUT-01 AUT-02  ← 覆盖哪些场景，第一个是主场景\n")
    assert h["ids"] == ["AUT-01", "AUT-02"]


def test_parse_case_header_only_scans_head():
    body = "\n".join(["x"] * 40) + "\n# @scenario LATE-01\n"
    assert parse_case_header(body)["ids"] == []


@pytest.mark.parametrize(
    "pattern,path,expected",
    [
        # `**` 要能匹配"零层目录"，否则 api 根下的用例读不到
        ("api/**/*.sh", "api/health.sh", True),
        ("api/**/*.sh", "api/smk/health.sh", True),
        ("api/**/*.sh", "api/a/b/c/health.sh", True),
        ("api/**/*.sh", "api/smk/health.ts", False),
        # `*` 不许跨目录 —— 否则 lib/ 里的支持脚本会被当成用例
        ("api/*.sh", "api/smk/health.sh", False),
        ("ui/tests/**/*.spec.ts", "ui/tests/aut/login.spec.ts", True),
        ("ui/tests/**/*.spec.ts", "ui/support/auth.ts", False),
        ("scenarios/**/*.sh", "scenariosx/mcp/a.sh", False),
    ],
)
def test_glob_matching(pattern, path, expected):
    assert bool(_glob_to_re(pattern).match(path)) is expected


def test_match_globs_any():
    globs = ["api/**/*.sh", "ui/tests/**/*.spec.ts"]
    assert _match_globs("ui/tests/aut/login.spec.ts", globs)
    assert not _match_globs("lib/runner.sh", globs)


def _assembled():
    scen, domains = parse_catalog(CATALOG)
    cases = [
        {"path": "api/smk/health.sh", "ids": ["SMK-01"], "tier": "smoke", "knownBugs": []},
        {
            "path": "scenarios/mcp/audit.sh",
            # 第一个 ID 是主 ID（决定文件放哪个域目录），后面的是搭车覆盖
            "ids": ["MCP-38", "SMK-08"],
            "tier": "scenario",
            "knownBugs": ["GL#531 审计记录各缺一半"],
        },
        {"path": "api/xxx/ghost.sh", "ids": ["GHOST-01"], "tier": "api", "knownBugs": []},
    ]
    return _assemble(scen, domains, cases, {"url": "git@x:qa.git", "branch": "main"})


def test_assemble_links_scripts_and_primary_flag():
    data = _assembled()
    by_id = {s["id"]: s for s in data["scenarios"]}
    assert [x["path"] for x in by_id["MCP-38"]["scripts"]] == ["scenarios/mcp/audit.sh"]
    assert by_id["MCP-38"]["scripts"][0]["primary"] is True
    # 同一个脚本给第二个 ID 记账时不是主 ID
    assert by_id["SMK-08"]["scripts"][0]["primary"] is False
    assert by_id["MCP-38"]["knownBugs"] == ["GL#531 审计记录各缺一半"]
    assert by_id["SMK-01"]["domainName"] == "冒烟"


def test_assemble_flags_lying_catalog_and_orphans():
    data = _assembled()
    by_id = {s["id"]: s for s in data["scenarios"]}
    # 标了 ✅ 却没有任何脚本声明它 —— QA 仓的 check-coverage.sh 管这叫"抓清单说谎"
    assert by_id["MCP-99"]["claimedButUncovered"] is True
    assert by_id["SMK-01"]["claimedButUncovered"] is False
    assert data["orphanScriptList"] == [{"path": "api/xxx/ghost.sh", "ids": ["GHOST-01"]}]


def test_assemble_summary_excludes_deprecated_from_total():
    data = _assembled()
    s = data["summary"]
    # 5 行清单里 1 行已废弃：总数按 4 算，废弃单独计
    assert (s["total"], s["covered"], s["gap"], s["deprecated"]) == (4, 3, 1, 1)
    assert s["scripts"] == 3
    assert s["claimedButUncovered"] == 1
    assert s["orphanScripts"] == 1
    assert s["byPriority"]["P0"] == {"total": 2, "covered": 2, "gap": 0}
    # 已废弃那条是 P3，不该出现在分优先级统计里
    assert "P3" not in s["byPriority"]


def test_domain_rollup():
    data = _assembled()
    doms = {d["code"]: d for d in data["domains"]}
    assert doms["SMK"]["total"] == 2  # SMK-09 已废弃，不计
    assert doms["SMK"]["covered"] == 1
    assert doms["SMK"]["gap"] == 1
    assert doms["MCP"]["name"] == "MCP 能力"
    assert doms["MCP"]["gap"] == 0


def test_summary_separates_covered_with_bugs_from_all_bug_scenarios():
    """「已覆盖 N 条」里有一批是明知跑出来红的，页面要能把这批单独点出来。

    夹具里那个脚本同时声明了 MCP-38(✅) 和 SMK-08(⬜)，@known-bug 记在两条上：
    带缺陷的场景有 2 条，但"覆盖率虚高"只该算已覆盖的那 1 条。
    """
    s = _assembled()["summary"]
    assert s["knownBugScenarios"] == 2
    assert s["coveredWithBugs"] == 1


# 清单 §1.1：P 和 R 是独立的两条轴，R 高 P 低是「回去重新审优先级」的信号
_RISK_CATALOG = """
| 域码 | 名称 |
|---|---|
| `SEC` | 安全边界 |

| ID | 场景 | P | R | 层 | 状 |
|---|---|---|---|---|---|
| SEC-01 | 越权访问被拒 | P0 | 9 | api | ⬜ |
| SEC-02 | 审计链完整 | P2 | 9 | api | ⬜ |
| SEC-03 | 冷门开关 | P2 | 2 | api | ✅ |
| SEC-04 | 早年的探针 | P3 | 9 | api | ❌ 已废弃 |
"""


def test_summary_flags_high_risk_low_priority():
    scen, domains = parse_catalog(_RISK_CATALOG)
    cases = [{"path": "api/sec/toggle.sh", "ids": ["SEC-03"], "tier": "api", "knownBugs": []}]
    data = _assemble(scen, domains, cases, {"url": "git@x:qa.git", "branch": "main"})

    # SEC-02 一条：R9 却排在 P2。SEC-01 是 P0（本来就该先做，不算背离），
    # SEC-04 已废弃（不该再提醒人回去审它的优先级）
    assert data["summary"]["riskMismatch"] == 1

    sec = {d["code"]: d for d in data["domains"]}["SEC"]
    assert (sec["total"], sec["covered"], sec["gap"], sec["p0Gap"]) == (3, 1, 2, 1)


# ---- 只读约束 ----

# QA 仓是别人的仓库。用户的要求是一个字都不许改：不 push、不 commit、不建 worktree、
# 不 checkout 工作区。这条封样盯的就是这个 —— 以后谁往这个模块里加写操作会直接红。
_WRITE_SUBCOMMANDS = [
    "push", "commit", "worktree", "checkout", "add", "rm ", "reset",
    "merge", "rebase", "tag", "am ", "apply", "clean", "gc",
]


def test_qa_catalog_module_has_no_write_git_commands():
    src = Path(__file__).resolve().parents[1] / "app" / "services" / "qa_catalog.py"
    text = src.read_text()
    # 只看真正传给 git 的参数列表（_run_git([...]) / clone 走 git_service）
    calls = re.findall(r"_run_git\(\s*\[(.*?)\]", text, re.S)
    assert calls, "没找到 _run_git 调用，封样失效了"
    for call in calls:
        lowered = call.lower()
        for bad in _WRITE_SUBCOMMANDS:
            assert f'"{bad.strip()}"' not in lowered, f"QA 仓只读：不许出现 git {bad.strip()}（{call.strip()}）"


def test_qa_catalog_api_only_reads():
    src = Path(__file__).resolve().parents[1] / "app" / "api" / "qa_catalog.py"
    text = src.read_text()
    # refresh 端点只允许触发 fetch，不允许出现任何回写 QA 仓的动作
    assert "push" not in text
    assert "worktree" not in text


# ---- 自动识别：清单路径 / 用例文件 / 分支。用真仓库跑，锁住 git grep 的参数写法
#      （POSIX 字符类 [[:space:]] 那套和 Python 正则不通用，写错了只会静默捞不到东西）

def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args],
        cwd=str(repo), capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


@pytest.fixture
def qa_repo(tmp_path: Path) -> Path:
    """一个长得像 QA 仓的临时仓库：清单 + 两种注释风格的用例 + 干扰文件。"""
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "qa-main")

    rows = "\n".join(f"| SMK-{i:02d} | 场景 {i} | P0 | 5 | api | ⬜ |" for i in range(1, 11))
    (work / "docs").mkdir()
    (work / "docs" / "catalog.md").write_text("| ID | 场景 | P | R | 层 | 状 |\n" + rows + "\n")
    # 干扰项 1：README 里举了两行例子，行数不够，不能被选成清单
    (work / "README.md").write_text("举个例子：\n| SMK-01 | 场景 1 | P0 | 5 | api | ⬜ |\n| SMK-02 | 场景 2 | P1 | 3 | api | ⬜ |\n")

    (work / "api").mkdir()
    (work / "api" / "smoke.sh").write_text("#!/usr/bin/env bash\n# @scenario SMK-01 SMK-02\n# @tier smoke\n")
    (work / "ui").mkdir()
    (work / "ui" / "login.spec.ts").write_text("// @scenario SMK-03\nimport {test} from '@playwright/test'\n")
    # 干扰项 2：没声明场景的支持库；干扰项 3：文档里原样引用了 @scenario
    (work / "api" / "_lib.sh").write_text("#!/usr/bin/env bash\nhelper() { :; }\n")
    (work / "docs" / "howto.md").write_text("文件头写 `# @scenario SMK-01` 就算覆盖。\n")
    # 干扰项 4：新建用例的模板，占位 ID + 行尾中文说明（uag-qa 里真有这么一份）
    tmpl = work / ".claude" / "skills" / "qa-module" / "templates"
    tmpl.mkdir(parents=True)
    (tmpl / "case.sh.tmpl").write_text("# @scenario XXX-01 XXX-02  ← 覆盖哪些场景，第一个是主场景\n")

    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "init")
    return work / ".git"


def test_detect_catalog_path_picks_the_richest_markdown(qa_repo: Path):
    # 不认文件名（别的仓不叫 test-scenario-catalog.md），只认哪份 .md 的场景行最多
    assert detect_catalog_path(qa_repo, "HEAD") == "docs/catalog.md"


def test_detect_catalog_path_errors_when_no_catalog(tmp_path: Path):
    work = tmp_path / "plain"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    (work / "README.md").write_text("没有清单表格\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "init")
    with pytest.raises(GitError) as e:
        detect_catalog_path(work / ".git", "HEAD")
    assert "场景清单" in e.value.message


def test_discover_case_files_finds_both_comment_styles(qa_repo: Path):
    files = discover_case_files(qa_repo, "HEAD", "docs/catalog.md")
    # .md、没声明 @scenario 的支持库、以及模板文件都不算用例
    assert files == ["api/smoke.sh", "ui/login.spec.ts"]


def test_discover_case_files_skips_templates(qa_repo: Path):
    # 模板里的占位 ID（XXX-01）会变成假的"孤儿脚本"，页面上看着像 QA 写错了
    assert not any(f.endswith(".tmpl") for f in discover_case_files(qa_repo, "HEAD", "docs/catalog.md"))


def test_resolve_ref_empty_branch_follows_repo_default(qa_repo: Path):
    # 分支留空不该猜 main —— 这个仓的默认分支叫 qa-main
    ref, name = _resolve_ref(qa_repo, "")
    assert ref == "HEAD"
    assert name == "qa-main"


def test_resolve_ref_named_branch_must_exist(qa_repo: Path):
    ref, name = _resolve_ref(qa_repo, "qa-main")
    assert ref == "refs/heads/qa-main" and name == "qa-main"
    # 填错了要报错，不能悄悄回退 HEAD 拿别的分支的数据顶包
    with pytest.raises(GitError) as e:
        _resolve_ref(qa_repo, "nope")
    assert "nope" in e.value.message
