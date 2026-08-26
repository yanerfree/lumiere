"""改名封样：仓库里不该再冒出 testBench 旧名和 tb_/tb- 旧前缀。

2026-08-26 全站改名 testBench → Lumiere（工具前缀 tb_ → lum_、平台 skill
tb-* → lum-*）。改名是一次性动作，但**新写的代码会照着旧文件抄** —— 这条封样
就是拦这个：粘一段带 `tb_update_case` 的示例、在文案里写「传到 testBench」，
都会在这里红掉，而不是等到界面上被人看见。

两道独立的墙：

1. `testbench`（大小写不敏感）—— 品牌名。
2. `tb_` / `tb-`（只管小写）—— 旧前缀。大写 `TB_USERNAME` 是被测系统 UAG 的
   环境变量名，不在管辖范围内。

白名单分三种，都写明原因；**加白名单必须带理由，不然这堵墙就是纸的**：

- `ALLOWED_SUBSTRINGS`：行里出现这个字面量就放过。给的是「真名字」——
  目录路径、被测系统的数据、还没改的库名。
- `ALLOWED_PATHS`：整个文件豁免。给的是「专门讲改名这件事的文件」和
  「等别的步骤一起改的部署脚本」。
- `ALLOWED_LINE_PATTERNS`：某个文件里符合某特征的行放过。给的是带日期的
  历史陈述 —— 那些句子记的是当时的事实，改了是篡改历史。
"""
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# 只扫 git 跟踪的文件。走 `git ls-files` 而不是 rglob，是因为工作区里有一堆
# 运行时产物带着旧名字（`.mock_state/` 里 mock 录下来的工具清单、egg-info、
# playwright 的页面快照），它们会自己重新生成，拦它们只会让这堵墙天天假红；
# 而且 rglob 会读到 `.env`。
SKIP_FILES = {
    ".claude/settings.local.json",   # 本机个人设置，不是仓库内容
    "tests/report.txt",              # 归档的历史测试报告
}
MAX_BYTES = 512 * 1024  # 再大的基本是产物/数据，不是人写的源码

ALLOWED_SUBSTRINGS = {
    "/home/dreamer/testBench": "工作目录真名，等仓库改名（第 7 步）一起改",
    "testbench_test": "两套测试库的真名，等库改名（第 5 步）一起改",
    "yanerfree/testBench": "GitHub 仓库真名，等仓库改名（第 7 步）一起改",
    "tb-self-shared-project": "自测链那个长期项目的标识，标识故意不动",
    "tb-fwgl": "被测系统 UAG 的模块域码，不是我们的名字",
    "tb-zcgl": "被测系统 UAG 的模块域码，不是我们的名字",
    "tb-dup-": "被测系统返回的租户名，抄在注释里当反例",
    "tb-shared-": "mock 上游主机名，被测侧的配置",
    "faketoken": "测试假 Key 字面量，跟工具前缀无关",
    "-home-dreamer-testBench": "Claude 会话目录名（工作目录压平来的），同上",
}

# 全仓通用的行级放过：给的不是「某个文件的例外」，而是「这种写法本身指的不是品牌名」。
ALLOWED_LINE_REGEXES = {
    # 数据库真名还是 testbench，改它要停后端（第 5 步）。连接串/psql -d 里的那一个词
    # 是库名不是品牌名，所以按上下文放过，而不是把 config.py 整个文件豁免掉 ——
    # 整个豁免的话，以后谁在 config.py 里写一句带旧品牌名的注释就拦不住了。
    re.compile(r"""(?:/|-d["',\s]+|POSTGRES_DB:\s*|DATABASE\s+)testbench\b"""):
        "连接串/psql 里的库名，等库改名（第 5 步）一起改",
    re.compile(r"(?:^|cd )testBench/?\s*$"):
        "仓库目录真名，等仓库改名（第 7 步）一起改",
}

ALLOWED_PATHS = {
    "docs/rename-to-lumiere.md": "改名作业本身的记录，通篇都是新旧对照",
    "backend/alembic/versions/zzv0lumren_rename_tb_to_lum.py": "改名迁移，两边名字都得写",
    "backend/tests/test_name_seal.py": "就是这堵墙自己",
    "DEPLOY.md": "部署命名，第 6 步一起改",
    "docker-compose.yml": "服务/库名，第 5、6 步一起改",
    "backend/pyproject.toml": "包名 testbench-backend，改它要重装 venv（第 6 步）",
    # User-Agent 是发给被测方的报文头，改之前要等 UAG 那边确认没有依赖
    "frontend/src/components/ApiStepList.jsx": "User-Agent: testBench/1.0，等 UAG 回话",
    "frontend/src/pages/apis/ApiManagement.jsx": "User-Agent: testBench/1.0，等 UAG 回话",
}
ALLOWED_PATH_PREFIXES = {
    "deploy/": "部署脚本/systemd 单元命名，第 6 步一起改",
    # BMAD 规划归档：带日期的历史文档，记的是当时叫这个名字。改了等于改档案。
    "_bmad-output/": "BMAD 规划/实现归档，历史文档",
}

ALLOWED_LINE_PATTERNS = {
    # 带日期的历史陈述：记的是「2026-07 当时对 testBench 自己接口做的 dogfood」
    "docs/cc-platform-loop-spec.md": re.compile(r"dogfood"),
    "backend/alembic/versions/zz9orph1_drop_orphan_scenarios.py": re.compile(r"dogfood"),
}

BRAND = re.compile(r"testbench", re.IGNORECASE)
PREFIX = re.compile(r"\btb[_-]")


def _tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, check=True,
                         capture_output=True, text=True).stdout
    return sorted(f for f in out.split("\0") if f)


def _iter_text_files():
    for rel in _tracked_files():
        path = REPO / rel
        if not path.is_file() or path.is_symlink():
            continue
        if rel in SKIP_FILES:
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # 二进制/读不了的跳过
        yield rel, text


def _exempt_file(rel: str) -> bool:
    return rel in ALLOWED_PATHS or any(rel.startswith(p) for p in ALLOWED_PATH_PREFIXES)


def _hits(pattern: re.Pattern) -> list[str]:
    out = []
    for rel, text in _iter_text_files():
        if _exempt_file(rel):
            continue
        line_ok = ALLOWED_LINE_PATTERNS.get(rel)
        for n, line in enumerate(text.splitlines(), 1):
            if not pattern.search(line):
                continue
            if any(s in line for s in ALLOWED_SUBSTRINGS):
                continue
            if any(r.search(line) for r in ALLOWED_LINE_REGEXES):
                continue
            if line_ok and line_ok.search(line):
                continue
            out.append(f"{rel}:{n}: {line.strip()[:120]}")
    return out


def test_no_old_brand_name():
    """全仓不该再出现 testBench / TestBench / testbench。"""
    hits = _hits(BRAND)
    assert not hits, (
        "发现旧品牌名 testBench（平台已改名 Lumiere）：\n" + "\n".join(hits)
        + "\n\n改成 Lumiere；确实该留（真路径/被测数据/待办步骤）就往 "
          "test_name_seal.py 的白名单里加一条并写清理由。"
    )


def test_no_old_tool_prefix():
    """全仓不该再出现 tb_ / tb- 旧前缀（MCP 工具名、平台 skill 目录）。"""
    hits = _hits(PREFIX)
    assert not hits, (
        "发现旧前缀 tb_ / tb-（现在是 lum_ / lum-）：\n" + "\n".join(hits)
        + "\n\nMCP 工具是 lum_xxx、平台预置 skill 是 lum-xxx。"
    )


@pytest.mark.parametrize("rel", sorted(set(ALLOWED_PATHS) | set(ALLOWED_LINE_PATTERNS)))
def test_whitelist_entries_still_exist(rel):
    """白名单不许烂掉 —— 文件删了/改名了，那一条就该跟着删。

    没有这条，白名单会越滚越长，最后墙上全是洞而没人知道哪个洞还有用。
    """
    assert (REPO / rel).exists(), f"白名单指向的文件不在了：{rel}（请从白名单里删掉）"
