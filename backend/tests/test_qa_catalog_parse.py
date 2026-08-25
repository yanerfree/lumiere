"""QA 场景清单解析 + 只读约束封样。

数据取自真实的 uag-qa 仓（357 行清单 / 84 个用例文件）里的代表性片段：
多 ID 声明、`//` 与 `#` 两种注释、状态列挂 `@known-bug`、已废弃行。
"""
import re
from pathlib import Path

import pytest

from app.services.qa_catalog import (
    _assemble,
    _glob_to_re,
    _match_globs,
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
    assert doms["MCP"]["name"] == "MCP 能力"


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
