"""QA 场景清单的**域级 AI 评审**（只读，产出不回写 QA 仓）。

## 它回答的是 check-coverage.sh 回答不了的那个问题

QA 仓自己的门禁能拦三件事：用例声明了清单外的 ID、标 ✅ 却没有脚本声明、
目录跟主域码对不上。三条都是**对账**，靠 grep 就能做。

它拦不住的是同一件事的里子：**脚本声明了 `@scenario AGT-11`，但它到底有没有验
AGT-11 说的那件事。** 一个只 `curl` 一下拿到 200 就 `exit 0` 的脚本，在 QA 那边
是"已覆盖"，在这里应该是"这条不算覆盖"。这就是这次评审的主菜，也是它值得烧一次
模型调用的唯一理由 —— 剩下的都能用代码数出来。

## 为什么按「域」评，而不是按条评

用例审核那边是**逐条**评的，因为一条用例的结论要写回 `cases.review_status` 管门禁。
这里没有门禁，也没有可写的行；而且域是 QA 仓自己的组织单位（脚本按域码放目录），
「AGT 这个域的脚本能不能撑起 AGT 那 11 条场景」本身就是他们排活的粒度。
一次一个域，读得完、说得具体。

## 环境是结论的一部分

选环境不是走过场。`env_gaps()` 用**纯代码**算出「这个域的脚本引用了、你选的环境
（含项目全局变量）里没有」的变量名 —— 缺 `ADMIN_TOKEN` 的环境上，这个域一条都跑不起来，
这跟脚本写得好不好是两件独立的事。这一块不问模型：变量名是能精确算的，
让模型猜只会又慢又飘。

**只传变量名，绝不传值。** 环境变量里放的是真凭证。

## 只读

全程只有 `git show`。产出落在本库 `qa_catalog_reviews`，QA 仓那边一个字节都不会变。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone

from app.models.qa_catalog_review import QaCatalogReview
from app.services.ai import llm_client

logger = logging.getLogger(__name__)

# ── 额度 ──────────────────────────────────────────────────────
# 这几个数原来是拍脑袋定的，注释里还写着"中位数 2.4KB、6K 能装下九成"。
# 2026-08-27 真去量了一遍 uag-qa（109 份脚本）：**中位数 5.3KB、p90 11.6KB、最大 17.7KB**。
# 也就是说 6K 的单份上限把**一半的脚本截了**，而截断的脚本是不下"没验到"结论的 ——
# 于是「没发现问题」里有一大块其实是「没读到」。这两件事在页面上长得一模一样。
# 现在的定法：单份装得下最大的那份；一次调用的总量按批切；批数封顶防跑飞。
MAX_SCENARIOS = 200         # 场景清单一行一条，200 行也就 20KB。最大的域 80 条（2026-08-28 复量）
MAX_SCRIPTS = 60            # 一个域最多读这么多份（最大的域 MCP 49 份，2026-08-28 复量）
MAX_SCRIPT_BYTES = 18_000   # 单份上限：实测最大 17.7KB，这个数下全仓 0 份被截
TOTAL_SCRIPT_BYTES = 480_000  # 一个域所有脚本正文合计上限（MCP 域实测 409KB）
BATCH_SCRIPT_BYTES = 90_000  # **一次模型调用**带多少脚本正文。超了就分批，不是丢弃
MAX_BATCHES = 8             # **只是不变量断言的上界，不再截断**（那个 break 已删）。
                            # 原注释写「真有域超了，宁可少读也别把额度烧穿」——
                            # 两句实测都不成立：38 个域一个都没超，而且**结构上就够不着**
                            # （8 批要 504_000 字节，预算封在 480_000）；额度也早被
                            # TOTAL_SCRIPT_BYTES 钉死了，跟批数封不封顶无关。
                            # 所以那个 break 一分钱没省，只留了个静默丢脚本的口子。
                            # 「够不着」这件事本身是常量之间的隐形耦合 ——
                            # 谁把 TOTAL_SCRIPT_BYTES 调过 504_000 而没动这里，静默丢当场开始。
                            # 现在有 test_批数封顶够不着所以不会静默丢 会替他红一次。
BATCH_CONCURRENCY = 3       # 同时在飞几批。5 批一起打网关实测 5 个 429 全降级到 CLI 通道
                            # —— 那条通道慢，而且网关是全平台共用的，别一个域把它占满
MEASURED_MAX_OUTPUT_TOKENS = 6386  # **实测**一批最多想写多少 output token（2026-08-28，
                            # MCP 域两轮 × 6 批，正文 6717–10217 字符）。
                            # 这个数是**观测值**，不是拍的；MAX_OUTPUT_TOKENS 由它推出来。
                            # 重新量了就改这里，别只改下面那个 —— 有测试盯着两者的比例。
MAX_OUTPUT_TOKENS = 10_000  # 一批准写多长 = 实测最大 × 1.5 向上取千位。
                            # 上一版是 2400，只装得下实测的 38% —— 那是**常态性截断**
                            # 不是偶发，而截断在页面上长得跟"这批没抓到问题"一模一样。
                            # ⚠ 提上限之前先看墙钟：单批实测 237–404s。
                            # 那个数是**走 claude-proxy 量的**（兜底那一跳的下限 _PROXY_TIMEOUT=600，
                            # 本模块带了更长的会取 max）；而网关主路的超时默认取
                            # **服务配置里的 timeout_seconds（现值 120s）**。
                            # 也就是说：提 max_tokens ⇒ 输出变长 ⇒ 单批更慢 ⇒
                            # 主路上会整整齐齐**全在 120.1s 超时**（Epic 0 已经这么撞过一次）。
                            # 而各批耗时相近 ⇒ 不是丢一批，是 6 批一起挂 ⇒
                            # `if not good: raise` ⇒ **整个域的评审直接没了**。
                            # 所以这个数和下面的 MIN_TIMEOUT_SECONDS 必须一起改，
                            # 只提 token 比不改更差。
                            # 注：这里跟 arq 的 job_timeout=600 **无关** —— 域评审走的是
                            # `spawn()` 里的 asyncio.create_task，跑在 API 进程内；
                            # arq 的 functions 只注册了 git_sync / execution 两个。
                            # （这条是 2026-08-28 写错过一次又查回来的，别再照 arq 那个数推。）
MIN_TIMEOUT_SECONDS = 1020  # 这一批等多久 = 实测最慢单批 404s × 2.5。
                            # **`MAX_OUTPUT_TOKENS` 的连体双胞胎**：准写 10000 token
                            # 就得给够写完的时间，两个数必须一起改。
                            # 只提 token 不提超时的结果不是慢一点：各批耗时相近
                            # ⇒ 6 批一起卡在默认的 120s ⇒ `if not good: raise`
                            # ⇒ **整个域的评审直接没了**，比不改还差。
                            #
                            # 这个数**由本模块自己传给 `llm_client.complete(timeout=)`**，
                            # 不去动服务配置里那个 120 —— 那个是全平台共用的，
                            # 拧大它等于让每一个卡死的 AI 请求都多等十五分钟才报错。
                            # （上一版这里写的是"去页面上把能力位超时改大"：那既是一条
                            #   靠人记得做的上线步骤，而且**页面上根本没有那个输入框** ——
                            #   超时在「AI 服务配置」的服务上，能力位只管选模型。）

MAX_SOURCED_LIBS = 20       # 顺带读几份被 source 的公共库（只用来认变量，不进 prompt）
                            # 要跟着 source 链往下走，6 份打不住：uag-qa 的 MCP 域一趟 11 份


# ── 环境缺口：纯代码 ────────────────────────────────────────────

# `$VAR` / `${VAR}` / `${VAR:-默认}`。带默认值的那种**不算缺**——脚本自己兜住了。
_VAR_REF_RE = re.compile(r"\$\{(?P<braced>[A-Za-z_]\w*)(?P<mod>[^}]*)\}|\$(?P<bare>[A-Za-z_]\w*)")
# 赋值：`X=1` / `export X=` / `local X=` / `declare -r X=` / `for X in` / `read X`
#
# **按语句找，不是按行找。** 一行上可以有好几个赋值，真实写法都在用：
#   `export MCPB_TEAM="$tid" MCPB_AGENT="$aid" MCPB_OWNER="$owner"`
#   `TA_ID=""; MB_ID=""`
# 只认每行第一个，后面那些就全变成"环境缺的" —— 2026-08-26 在 uag-qa 的 MCP 域上
# 实测，14 份脚本报出 5 个假缺口（MB_ID/MB_USER/MB_PASS/MCPB_AGENT/MCPB_OWNER）。
_STMT_SPLIT_RE = re.compile(r"[;&|]{1,2}|\n")
_ASSIGN_RE = re.compile(
    r"^\s*(?:export\s+|local\s+|declare\s+-\w+\s+|readonly\s+|typeset\s+)?"
    r"(?P<name>[A-Z_a-z]\w*)\s*=")
# `export A=1 B=2` / `local a b c`：关键字后面跟着的每一个名字都算定义过了
_DECL_LINE_RE = re.compile(
    r"^\s*(?:export|local|readonly|typeset|declare(?:\s+-\w+)?)\s+(?P<rest>.+)$")
_NAME_RE = re.compile(r"[A-Za-z_]\w*")
_FOR_RE = re.compile(r"^\s*for\s+(?P<name>\w+)\s+in\b", re.M)
# `read -r A B C` —— 一次能读进好几个
_READ_RE = re.compile(r"\bread\s+(?P<rest>(?:-\w+\s+)*[A-Z_]\w*(?:\s+[A-Za-z_]\w*)*)", re.M)
# 名字是运行时拼出来的：`printf -v "${var}_ID"` / `export "${var}_TOKEN"`。
# 具体前缀（MB / TA / …）由调用方传，静态看不出来；但**后缀**看得出来，
# 所以把 `*_ID`/`*_TOKEN` 这一族整体放过。宁可漏报不可误报。
#
# 花括号是**必须的**：bash 里 `$var_ID` 会被当成变量 `var_ID`，所以拼名字只能写
# `"${var}_ID"`。写成可选的话，`${QA_ROOT}` 会被拆成 `${QA}` + 后缀 `_ROOT`，
# 于是 `_URL`/`_DSN`/`_APIKEY` 这些全成了"动态后缀"，把 UAG_APIKEY、PSQL_DSN
# 这类**真缺口**一起吃掉 —— 那正是这一列唯一有价值的东西。
_DYNAMIC_SUFFIX_RE = re.compile(r"\$\{\w+\}(?P<suffix>_[A-Z][A-Z0-9_]*)")
_SOURCE_RE = re.compile(r"^\s*(?:source|\.)\s+(?P<rest>\S.*)$", re.M)
# 从 source 那一行里认文件名。**不能只取第一个 token**：真实写法是
# `source "$(dirname "$0")/../lib/common.sh"`，第一个 token 是 `$(dirname` ——
# 按它去找永远找不到，于是公共库里定义的变量全被算成"环境缺的"。
_FILENAME_RE = re.compile(r"[\w.\-]+\.(?:sh|bash|env|inc)\b")

# shell/CI 自带的，不该算到"环境没配"头上
_AMBIENT = {
    "PATH", "HOME", "PWD", "OLDPWD", "IFS", "SHELL", "USER", "LOGNAME", "TMPDIR", "TERM",
    "LANG", "LC_ALL", "HOSTNAME", "RANDOM", "SECONDS", "LINENO", "BASH", "BASH_SOURCE",
    "BASHPID", "FUNCNAME", "PIPESTATUS", "REPLY", "OSTYPE", "EDITOR", "PS1", "PS4",
    "CI", "GITLAB_CI", "CI_PROJECT_DIR", "CI_COMMIT_SHA", "CI_JOB_ID", "CI_PIPELINE_ID",
}


# `export X="${X:-}"` 不是定义，是**声明这个变量得从外面传进来**。
# uag-qa 的 config/env.sh 就拿它当"外部输入清单"用：
#     export UAG_APIKEY="${UAG_APIKEY:-}"
#     export PSQL_DSN="${PSQL_DSN:-}"
# 纯代码要是把它算成定义，这两个**真缺口**一个都报不出来 —— 2026-08-26 实测，
# 环境里确实没有它俩，脚本跑到 `[ -n "${UAG_APIKEY:-}" ] || skip_case ...` 就
# 整条静默跳过：报告上是绿的，实际一条数据面用例都没执行。
# 只认**空兜底**。写成 `${X:-http://localhost:3000}` 的自带兜底值，没配也照样跑，不算缺。
#
# 尾注释必须放过 —— config/env.sh 里最该报的那一条恰好带着注释：
#     export PASSWORD="${PASSWORD:-}"          # 刻意不给默认值,强制外部注入
# 匹配失败不是"不知道"，是直接倒向另一边被当成"定义过了"，于是这个缺口一声不吭。
_PASSTHRU_RE = re.compile(r"""^\s*["']?\$\{(?P<name>\w+):?[-=]["']*\}["']?\s*(?:\#.*)?$""")


def _is_passthrough(name: str, rhs: str) -> bool:
    m = _PASSTHRU_RE.match(rhs)
    return bool(m) and m.group("name") == name


def _defined_names(text: str) -> set[str]:
    names: set[str] = set()
    for stmt in _STMT_SPLIT_RE.split(text):
        m = _ASSIGN_RE.match(stmt)
        if m and not _is_passthrough(m.group("name"), stmt[m.end():]):
            names.add(m.group("name"))
        d = _DECL_LINE_RE.match(stmt)
        if d:
            # `export A=1 B=2` / `local a b`：等号左边（或裸名）都算
            for part in d.group("rest").split():
                n = _NAME_RE.match(part.strip('"\''))
                if not n:
                    continue
                _, _, rhs = part.partition("=")
                if not (rhs and _is_passthrough(n.group(0), rhs)):
                    names.add(n.group(0))
    names |= {m.group("name") for m in _FOR_RE.finditer(text)}
    for m in _READ_RE.finditer(text):
        for part in m.group("rest").split():
            if not part.startswith("-"):
                names.add(part)
    return names


def dynamic_suffixes(text: str) -> set[str]:
    """运行时拼名字的那一族后缀，比如 `printf -v "${var}_ID"` 里的 `_ID`。

    `make_identity MB none` 会造出 MB_ID / MB_TOKEN / MB_USER / MB_PASS 四个变量，
    但仓库里搜不到任何一处 `MB_USER=`。不认这一层，调用方每用一次夹具就多几个假缺口。
    """
    out = set()
    for line in text.splitlines():
        if "printf -v" not in line and "export " not in line and "declare" not in line:
            continue
        for m in _DYNAMIC_SUFFIX_RE.finditer(line):
            out.add(m.group("suffix"))
    return out


def passthrough_names(text: str) -> set[str]:
    """`export X="${X:-}"` 这类"我要从外面拿 X"的声明里的名字。

    这是纯代码唯一能看见 UAG_APIKEY / PSQL_DSN 的地方 —— 它们在脚本里的用法是
    `[ -n "${UAG_APIKEY:-}" ] || skip_case ...`，带默认值，按"引用"根本扫不出来。
    """
    out = set()
    for stmt in _STMT_SPLIT_RE.split(text):
        m = _ASSIGN_RE.match(stmt)
        if m and _is_passthrough(m.group("name"), stmt[m.end():]):
            out.add(m.group("name"))
    return out


def _referenced_names(text: str) -> set[str]:
    out = set()
    for m in _VAR_REF_RE.finditer(text):
        name = m.group("braced") or m.group("bare")
        mod = m.group("mod") or ""
        # `${X:-兜底}` / `${X:=兜底}` / `${X-兜底}`：脚本自己给了默认值，环境没有也能跑
        if name and not re.match(r"^:?[-=]", mod):
            out.add(name)
    return out


def sourced_files(text: str, repo_paths: list[str]) -> list[str]:
    """脚本 `source` 进来的公共库在仓库里的路径（按文件名匹配）。

    不认这一层的话，公共库里 `export API_BASE=...` 定义的变量会被当成"环境缺的"
    —— QA 仓每个脚本都 source 同一份 lib，那样这一列会全是假的。
    路径里带 `$(dirname "$0")` 这类拼接，所以只取文件名去仓库里找，不做路径解析。
    """
    by_name: dict[str, list[str]] = {}
    for p in repo_paths:
        by_name.setdefault(p.rsplit("/", 1)[-1], []).append(p)
    hits: list[str] = []
    for m in _SOURCE_RE.finditer(text):
        names = _FILENAME_RE.findall(m.group("rest"))
        if not names:
            continue
        for p in by_name.get(names[-1], []):     # 最后一个才是被 source 的那份
            if p not in hits:
                hits.append(p)
    return hits[:MAX_SOURCED_LIBS]


# 家族匹配的尾段最短长度。**这个数是护栏，不是调参。**
# `DSN`(3) / `URL`(3) / `ID`(2) 这种短尾段谁都带一个，放进家族匹配就会把
# `PSQL_DSN`、`UAG_APIKEY` 这类**真缺口**整族降级 —— 而降级之后页面变干净，
# 看着像修好了。修误报最容易的翻车方式就是把真阳一起修掉。
_FAMILY_MIN = 5


def _tails(name: str) -> set[str]:
    """按 `_` 切段，收集后段拼接出来的尾巴：`A_B_C` → {A_B_C, B_C, C}。

    **按段切，不用 `endswith` 裸子串。** 裸子串会让 `VICE_TOKEN` 命中
    `SERVICE_TOKEN`（`"SERVICE_TOKEN".endswith("VICE_TOKEN")` 是真的）——
    本文件的 `_DYNAMIC_SUFFIX_RE` 注释里已经因为同一个原因被咬过一次。
    """
    parts = name.split("_")
    return {"_".join(parts[i:]) for i in range(len(parts))}


def _family(name: str, env_keys: set[str]) -> list[str]:
    """环境里有没有「同一家族、只是前缀不同」的键：`PASSWORD` ⇐ `ADMIN_PASSWORD`。

    实测 `uag-138:3000` 有 7 组角色账号（`ADMIN_PASSWORD`/`PLATADMIN_PASSWORD`/…），
    而脚本引用的是 `PASSWORD` —— 旧代码拿名字硬比，报「缺 PASSWORD」。
    危害不在这一条假阳本身：**它跟 `UAG_APIKEY`/`PSQL_DSN` 两个真缺口用同样的
    置信度并排显示**，一条响亮的假阳会让人把整列当噪音，两个真阳一起被无视。

    **单向匹配**：只认「候选名 == 某个环境键的尾巴」，不认反过来。
    反向（环境有 `APIKEY`、脚本要 `UAG_APIKEY`）算不算覆盖判不了，
    而判错的方向是把真缺口洗白 —— 这种时候宁可留着那条 `absent`。
    """
    if len(name) < _FAMILY_MIN:
        return []
    return sorted(k for k in env_keys if k != name and name in _tails(k))[:8]


def env_gaps(scripts: list[dict], env_keys: set[str], lib_texts: list[str] | None = None,
             lib_paths: list[str] | None = None) -> list[dict]:
    """只要**缺口**那两档（`absent` / `ambiguous`）。

    `satisfied` 不进这个列表：它的返回值在好几处被 `len()` 当成「缺 N 个」渲染
    （markdown 那句「连变量名都缺 N 个」、页面的 `nEnvVar`、MCP 的 `envMissing`），
    掺进环境里**有**的那些，那个数当场就变成一个不报错的错数。
    要三档齐全的调 `scan_env_vars`。
    """
    return [v for v in scan_env_vars(scripts, env_keys, lib_texts, lib_paths)
            if v["state"] != "satisfied"]


def scan_env_vars(scripts: list[dict], env_keys: set[str], lib_texts: list[str] | None = None,
                  lib_paths: list[str] | None = None) -> list[dict]:
    """这个域要从外面拿的变量，**逐个分三档**。纯代码，不问模型。

    `absent` 真没有 / `ambiguous` 名字对不上但环境里有同族的 / `satisfied` 环境里就有。

    ⚠ **只取键名，一个值都不取。** 值里是真凭证 —— 提示词、日志、页面、
    MCP 返回里都不许出现。`ambiguous` 那一档列的是那几个**真键名**，也仅此而已。


    宁可漏报不可误报：脚本自己赋过值的、给了默认值的、shell 自带的、公共库里定义的，
    全部不算。剩下的还有一类躲不掉的假阳（远程 CI 注入的变量），所以页面上那一列
    写的是"脚本引用、这个环境没有"，不是"这个环境是坏的"。

    有一类反过来：`export X="${X:-}"` 是仓库在**明说** X 得从外面传，
    它比"哪个脚本引用了 X"更硬 —— 所以这类名字直接进候选，不要求有人引用。
    """
    defined: set[str] = set()
    suffixes: set[str] = set()
    wanted: dict[str, list[str]] = {}          # 声明要从外面拿的 → 在哪份库里声明的
    for i, t in enumerate(lib_texts or []):
        defined |= _defined_names(t)
        suffixes |= dynamic_suffixes(t)
        src = (lib_paths or [])[i] if i < len(lib_paths or []) else ""
        for n in passthrough_names(t):
            wanted.setdefault(n, [])
            if src and src not in wanted[n]:
                wanted[n].append(src)
    for s in scripts:
        defined |= _defined_names(s.get("content") or "")
        for n in passthrough_names(s.get("content") or ""):
            wanted.setdefault(n, [])
            if s["path"] not in wanted[n]:
                wanted[n].append(s["path"])

    hits: dict[str, list[str]] = {}
    for name, srcs in wanted.items():
        # 别处真赋过值就不算缺；`${X:-}` 声明完又被 `X=真值` 覆盖的属于这种
        if name in defined or name in _AMBIENT:
            continue
        # 夹具运行时拼出来的那一族（MB_ID / TA_TOKEN / …）。
        # 这一句原来只写在下面的引用分支里 —— 同一个名字改用 `export X="${X:-}"`
        # 声明一次就绕过豁免，从这边冒出来。豁免要豁在**两个分支**上。
        if any(name.endswith(x) for x in suffixes):
            continue
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", name):
            continue
        hits[name] = srcs[:6]
    for s in scripts:
        for name in _referenced_names(s.get("content") or ""):
            if name in defined or name in _AMBIENT:
                continue
            if any(name.endswith(x) for x in suffixes):
                continue
            # 只看 SCREAMING_CASE：小写的基本都是脚本内部的临时变量
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", name):
                continue
            hits.setdefault(name, [])
            if s["path"] not in hits[name] and len(hits[name]) < 6:
                hits[name].append(s["path"])

    out = []
    for name, srcs in sorted(hits.items()):
        row = {"name": name, "scripts": srcs}
        if name in env_keys:
            row["state"] = "satisfied"
        elif (fam := _family(name, env_keys)):
            row["state"] = "ambiguous"
            # 这几个是**键名**。列出来是为了让人一眼看出「不是真缺，是名字对不上」——
            # 不列的话降级本身就成了一句无从复核的断言。
            row["family"] = fam
        else:
            row["state"] = "absent"
        out.append(row)
    return out


# ── prompt ────────────────────────────────────────────────────

# 2026-08-29 这里少了一项：原来还有第 3 项「待补的先做哪条」（nextUp）。
# 去掉的理由是**分批读的时候它算错**：每批只看得到一部分脚本却要给全域排序，
# 各批各排一份再拼起来，拼出来的顺序不表示任何东西（实测同一个域六批 18 行、
# 去重后只有 3 件事）。而排序代码能确定性地做 —— 本模块自己的规矩就是
# 「数和排序不许问模型」。
# ⚠ 别把这段解释写回下面那个字符串里：它是**发给模型的正文**，
# 在里面讲"以前有第 3 项"等于花钱让模型读一段跟它无关的施工记录，
# 还提示了一个我们不想要的输出键。
_SYSTEM = """你在评审一个黑盒验收仓里**某一个域**的自动化覆盖质量。

给你三样东西：这个域的场景清单（每条有 ID / 优先级 P / 风险分 R / 执行层 / 覆盖状态）、
声明覆盖了这些场景的**脚本正文**、以及这次选定的运行环境。

你只做两件判断，按重要性排：

1. **声明覆盖了、其实没验到**（最重要）。脚本头写 `@scenario AGT-11`，就等于宣称它验了
   AGT-11 描述的那件事。逐条对照场景描述读脚本正文：只发个请求看不看返回、
   只断状态码不看内容、断言恒真（`|| true`、`grep -q ""`）、改了数据不读回来确认、
   压根在验另一件事 —— 这些都算"声明了没验到"。**这一项是这次评审存在的理由**：
   仓库自己的门禁只能检查"有没有声明"，检查不了"有没有验到"。
2. **清单本身漏了什么**。这个域已有场景之间明显缺的一环（例如只有创建和查询、
   没有删除后的越权访问）。

**每条结论必须自报「谁动手」（`blame`），这是给人看那页的分栏依据：**

- `script` —— **改脚本就能解决**。断言写得站不住、验错了东西、改完不读回来。
  这一类才是真正要发给仓库主人的。
- `env` —— **改脚本解决不了**，要在真正跑套件的地方铺东西（密钥、库连接、数据面能力）。
  脚本本身可能写得很对，只是在这个环境里自己 `skip` 了。
  ⚠ 「这个环境缺某个变量」是**我们这侧的环境记录**里没有这个名字，
  **不等于 QA 自己跑的时候也没有** —— 所以这一类一律 `blame=env`，
  措辞只能是「在这个环境里跑不起来 / 会跳过」，**不许**写成「脚本没验」「覆盖是假的」。
- `catalog` —— 脚本和环境都没错，是清单认领的口径不对（认领了做不到的、该拆没拆）。

硬要求：
- 每条结论必须能指到**具体的场景 ID 或脚本路径**，并说清"哪一行/哪个动作"让你这么判。
  说不出来的宁可不说。
- **`solid` 不许空着**：撑得住的那部分也要说。整页只有坏消息，读的人会当成"全域都不能用"，
  下次就不看了。
- **每条结论都要归到一个维度（`dim`），九选一，只填 key。**
  三个大维度是人认得的那三个，`dim` 填的是它们底下的子项：

  **覆盖面**（该测的测了没）
  - `coverage` 清单里就没有这条场景 —— 该有的路径/角色/失败态一条都没认领
  - `both` 只测了成功那一半 —— 该被拒的那一半没测（或反过来）
  - `skip` 在这个环境里整条跳过 —— 缺变量/缺样本就 `exit 0`，清单照记「已覆盖」

  **场景设置**（这条场景本身定得合不合理）
  - `grain` 一条说了好几件事 —— 认领粒度太粗，脚本只验得了其中一件
  - `shape` 说不清要证明什么 —— 描述给不出可判定的预期，或优先级跟风险明显不匹配

  **断言**（断得对不对、站不站得住）
  - `assert` 断言恒真，改坏了也不会红
  - `claim` 断的不是认领的那件事 —— 脚本头认领 A，正文在验 B
  - `depth` 只断到接口回了 200 —— 没读回来确认那件事真发生
  - `expect` 断的值跟清单写的不一致 —— 清单写「401 且 error.code=X」，脚本断的是别的，
    或只断个非空糊过去。**注意反过来那种也算**：断言写的是当前实现的行为
    （写死的 id、写死的文案），而清单/通用规范要求的是另一个 —— 那是把实现当成了预期。

  人看的那页**只按这三个大维度摆**（一个域抓到几十条，没人一条条读；他要知道的是
  覆盖面、场景设置、断言这三块里哪块塌了）。九个都套不上就挑最接近的，
  **不许自己造新维度**，也不许留空。
  `catalogGaps` 的 `dim` 一般落在 `coverage` / `grain` / `shape`。
- **每条 `scriptGaps` 都要给 `oneLine`：≤20 字，人话，一眼看完。**
  人看的那一页**只显示这一行**，`problem` 那段技术描述根本不出现在他眼前。
  这里有 24 个域，他不是来读你的分析的，是来决定"这个域要不要停下来处理"。
  不许出现路径、表名、字段名、函数名、状态码 —— 那些留给 `problem`。
- **不要写放到哪个项目都成立的话**（"建议补充异常场景""缺少安全测试""覆盖率偏低"）。
  那种话说了等于没说。
- 不许建议我们去改这个仓库的流程、加字段、加钩子 —— 仓库是别人的，我们只读。
- 没截断的脚本才下"没验到"的结论；正文标了「已截断」的，拿不准就别列。

## 你要写给两拨人看，分开写，别混

**`brief`（给人看）** —— 读它的是测试经理/项目经理，三十秒决定"要不要停下来处理"。
只说**结论和后果**：这个域的"已覆盖"能不能当真、最要命的是哪一件、下一步做什么。
- 不出现脚本路径、变量名、函数名、断言写法、HTTP 状态码 —— 一个都不要。
- **不许出现 `ok` / `risky` / `bad` 这三个原词**。结论词页面上另有中文（认领都算数 /
  部分认领不算数 / 多数认领不算数）摆在旁边，你再写一遍英文，读的人还得自己对一次。
- 每一条都必须是下面 `scriptGaps` / 环境缺口里**某一条的人话版**，
  不许出现细节里没有的新说法（那就是编的）。
- 要带数字（几条、什么优先级）。"覆盖率偏低""建议加强"这类话一律不许写。
- **数字得跟你自己列出来的条数对得上**：脚本那条 = `scriptGaps` 里 `blame` 是
  `script` 的条数；环境那条 = `blame` 是 `env` 的条数；清单那条 = `catalogGaps`
  的条数**加上** `blame` 是 `catalog` 的条数。页面会把这三堆分栏摆在你这句话底下，
  数字对不上，人第一眼看到的就是"这页的数打架" —— 那比不写数字更糟。
- **每条点出「这是谁的事」**：脚本要改的、环境要铺的、清单要商量的，读的人不该自己去分。

**其余各项（给 AI / 动手整改的人看）** —— 读它的是接手改脚本的工程师或 Claude Code。
要具体到能直接动手：哪个文件、哪一句断言、改成什么。
- `evidence` 从脚本正文里**原样抄**一小段（≤3 行）当判据锚点，让接手的人一搜就定位。
  抄不出来就留空，**不许编**一段仓库里没有的代码。

只输出 JSON，不要任何解释：
```json
{
  "verdict": "ok | risky | bad",
  "brief": {
    "headline": "一句话（≤40字）：这个域标着「已覆盖」的那些，能不能当真",
    "points": ["最多 3 条，每条 ≤50 字：哪件事没保住 + 后果是什么 + 是谁的事"],
    "nextStep": "一句话：下一步最该做的那一件事",
    "solid": ["1-3 条，每条 ≤40 字：这个域**撑得住**的是哪部分（人话，不带路径）"]
  },
  "summary": "两句话说清这个域的覆盖到底靠不靠得住（可以带术语，给动手的人看）",
  "scriptGaps": [
    {"id": "AGT-11", "path": "scenarios/agt/x.sh", "severity": "blocker|major|minor",
     "blame": "script|env|catalog",
     "dim": "coverage|both|skip|grain|shape|assert|claim|depth|expect",
     "oneLine": "挂起的 Agent 还调得动，脚本没查",
     "problem": "脚本只断了 HTTP 200，没有检查被挂起的 Agent 是否真被拒",
     "evidence": "assert_status 200 \"$resp\"",
     "fix": "断言响应体 code == 403 且数据面返回为空"}
  ],
  "catalogGaps": [{"scenario": "...", "why": "...", "dim": "coverage|grain|shape|…"}]
}
```"""

_STATE_CN = {"covered": "已覆盖", "gap": "待补", "deprecated": "已废弃"}


def build_payload(domain: dict, scenarios: list[dict], scripts: list[dict],
                  env_name: str, env_keys: list[str], env_missing: list[dict],
                  batch: tuple[int, int] | None = None) -> str:
    """拼给模型的 user 消息。**只放变量名，不放变量值。**

    `batch=(第几批, 共几批)`：脚本太多装不进一次调用时分批读，场景清单每批都给全的
    （不给全的话，模型不知道这批脚本认领的场景在整个域里是什么位置）。
    """
    head = f"域：{domain.get('code')} {domain.get('name') or ''}".strip()
    lines = [head]
    if batch:
        lines += ["", f"⚠ 这个域的脚本一次装不下，切成了 {batch[1]} 批，**这是第 {batch[0]} 批**。",
                  "场景清单是全的，脚本正文只有这一批。**只对下面出现的脚本下结论**，",
                  "别对没给你的脚本说「没验到」—— 那些在别的批里，有人读。"]
    lines += ["", "## 场景清单"]
    shown = scenarios[:MAX_SCENARIOS]
    for s in shown:
        bits = [s["id"], s.get("title") or "", s.get("priority") or "—",
                f"R={s.get('risk') or '—'}", s.get("tier") or "—",
                _STATE_CN.get(s.get("state"), s.get("state") or "")]
        paths = "，".join(c["path"] for c in (s.get("scripts") or []))
        line = "- " + " | ".join(x for x in bits if x)
        if paths:
            line += f" | 脚本：{paths}"
        if s.get("knownBugs"):
            line += f" | 已知缺陷：{'、'.join(s['knownBugs'])}"
        lines.append(line)
    if len(scenarios) > len(shown):
        lines.append(f"（这个域共 {len(scenarios)} 条，上面只列了前 {len(shown)} 条）")

    lines += ["", "## 运行环境", f"环境名：{env_name or '（未选）'}"]
    lines.append("已配置的变量名：" + ("、".join(env_keys) if env_keys else "（一个都没有）"))
    # **只喂 `absent`。** `ambiguous` 是"名字对不上、环境里有同族的"，
    # 它进提示词只会被当成缺口再推一遍覆盖结论 —— 而那正是这一档要拦的误报。
    absent = [x for x in env_missing if x.get("state", "absent") == "absent"]
    ambiguous = [x for x in env_missing if x.get("state") == "ambiguous"]
    if ambiguous:
        lines.append("下面这些名字**不算缺**（环境里有同族的键，只是前缀不同）："
                     + "、".join(x["name"] for x in ambiguous[:20]))
        lines.append("（**不许由这一行推出任何覆盖结论。** 它们名字对不上而已，不是真缺。）")
    if absent:
        lines.append("脚本引用了、这个环境里没有的变量名："
                     + "、".join(x["name"] for x in absent[:20]))
        # ⚠ 这行话改过一次。原来写的是"判「能不能跑起来」时可以直接用"，
        # 结果模型拿它当铁证，把「我们的环境记录里没这个名字」写成了「场景层一条没跑、
        # 已覆盖是假的」—— 而这份名单只反映**我们这侧**环境记录里有什么，
        # QA 自己的 runner 里有没有，我们根本看不到。把它当铁证就是拿自己的配置缺口
        # 去判别人的脚本，最冤的一种误报。
        lines.append("（这是代码算的，不用你再算。但它只说明**我们这侧**的环境记录里没有"
                     "这个名字，**不能**推出 QA 自己跑的时候也没有 —— 由它得出的结论"
                     "一律 `blame=env`，措辞只能到「在这个环境里会跳过」，"
                     "不许升级成「脚本没验到」或「覆盖是假的」。）")

    lines += ["", "## 脚本正文"]
    if not scripts:
        lines.append("（这个域一份脚本都没有）")
    for s in scripts:
        mark = "（已截断）" if s.get("truncated") else ""
        lines.append(f"\n### {s['path']}{mark}\n```bash\n{s.get('content') or ''}\n```")
    return "\n".join(lines)


def parse_result(text: str) -> dict:
    """把模型输出扒成结构。扒不出来就报错，不返回一份空壳。

    空壳最坏：页面会显示"没发现问题"，而真相是"根本没评上"—— 这比报错难查得多。
    """
    m = re.search(r"```json\s*(\{.*?\})\s*```", text or "", re.S)
    raw = m.group(1) if m else (text or "")
    if not m:
        # 没围栏时退一步找最外层花括号
        i, j = raw.find("{"), raw.rfind("}")
        raw = raw[i:j + 1] if i >= 0 and j > i else raw
    try:
        data = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"模型没按 JSON 回：{str(e)[:120]}") from e
    if not isinstance(data, dict):
        raise ValueError("模型回了个不是对象的东西")

    def _rows(key: str) -> list[dict]:
        # **不封条数。** 上一版这里是 `[:6]`，提示词里也写着「每一项最多 6 条」——
        # 两处一起把结论砍在 6 条。实测（去掉提示词上限量的那一趟）一个批次能出到
        # 104 条，`[:6]` 静默扔掉 31/104 = 30%，而页面上只显示剩下的，
        # **看起来就像"这个域只有这么多问题"**。
        # 封顶真正省的是渲染长度，不是额度；而额度早被 MAX_OUTPUT_TOKENS 管着了。
        out = []
        for x in (data.get(key) or []):
            if isinstance(x, dict):
                row = {}
                for k, v in x.items():
                    if v is None:
                        continue
                    if k == "evidence":
                        # S2.3：evidence 是**要拿去回原文比对的**（Epic 3 的回验）。
                        # `str(v)[:600]` 从行中间切断 ⇒ 那半行在原文里找不到
                        # ⇒ 回验必然判 partial。而第一反应会是"回验不准"然后去放松回验
                        # —— 修错地方。所以按行边界截，且**截了就说截了**。
                        row[k], cut = _clip_lines(str(v))
                        if cut:
                            row["evidenceTruncated"] = "1"
                    else:
                        row[k] = str(v)[:600]
                b = (row.get("blame") or "").strip().lower()
                # 认不出来就落 script：这一栏是「要发给仓库主人的」，
                # 宁可多审一条，也别把该发的漏进"不是你的事"那一堆里
                row["blame"] = b if b in ("script", "env", "catalog") else "script"
                out.append(row)
            elif x:
                out.append({"problem": str(x)[:600]})
        return out

    verdict = str(data.get("verdict") or "").strip().lower()
    return {
        "verdict": verdict if verdict in ("ok", "risky", "bad") else "risky",
        "brief": _brief(data.get("brief"), data.get("summary")),
        "summary": str(data.get("summary") or "")[:800],
        "scriptGaps": _rows("scriptGaps"),
        "catalogGaps": _rows("catalogGaps"),
        # 这里没有 "nextUp"：2026-08-29 停产。模型即使照旧回了这个键也直接丢掉 ——
        # **停产要停在解析这一层**，只删渲染的话它还在库里长，
        # 下一个人翻到 result JSON 会以为它还是活的。
    }


def _brief(raw, summary) -> dict:
    """人话那一段。**模型不给就退回 summary，不留空**。

    留空的后果是页面上「给人看」那一页整版空白，而人不会去点隔壁那页 ——
    他只会得出"这个域没问题"。老记录（没有 brief 字段的）走的也是这条退路。
    """
    d = raw if isinstance(raw, dict) else {}
    points = d.get("points")
    if isinstance(points, str):
        points = [points]
    solid = d.get("solid")
    if isinstance(solid, str):
        solid = [solid]
    return {
        "headline": str(d.get("headline") or "")[:120] or str(summary or "")[:120],
        # 3 条封顶（原来是 4）。24 个域挨个看，每多一条就是多一屏。
        "points": [str(x)[:160] for x in (points or [])[:3] if x],
        "nextStep": str(d.get("nextStep") or "")[:200],
        "solid": [str(x)[:120] for x in (solid or [])[:3] if x],
    }


def brief_of(result: dict | None) -> dict:
    """**读的时候**再兜一次底。存的时候兜过了还不够——

    `brief` 是后加的字段，库里那些老记录的 `result` 里根本没有这一项，
    parse 那道兜底对它们从来没执行过。实测：老记录读出来 `brief` 是 `{}`，
    页面「给人看」那页整版空白 —— 正是这个字段要防的那件事，自己踩了一遍。

    所以凡是把结论往外送的地方（页面 / 导出 / MCP）都走这里，别直接 `result["brief"]`。
    """
    res = result or {}
    return _brief(res.get("brief"), res.get("summary"))


# ── 编排 ──────────────────────────────────────────────────────

def collect(catalog: dict, domain_code: str) -> tuple[dict, list[dict], list[str]]:
    """从已解析好的清单里取出这个域：(域信息, 场景列表, 要读正文的脚本路径)。"""
    domain = next((d for d in catalog.get("domains") or [] if d["code"] == domain_code), None)
    scenarios = [s for s in catalog.get("scenarios") or [] if s.get("domain") == domain_code]
    paths: list[str] = []
    # 先 P0/P1、再风险高的：脚本正文有总量上限，截断要截在不重要的那头
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    for s in sorted(scenarios, key=lambda x: (order.get(x.get("priority") or "", 9),
                                              -(x.get("risk") or 0), x["id"])):
        for c in s.get("scripts") or []:
            if c["path"] not in paths:
                paths.append(c["path"])
    return domain or {"code": domain_code, "name": ""}, scenarios, paths[:MAX_SCRIPTS]


def take_scripts(loader, paths: list[str]) -> list[dict]:
    """按总量上限读脚本正文。loader(path) -> str | None。"""
    out: list[dict] = []
    budget = TOTAL_SCRIPT_BYTES
    for p in paths:
        text = loader(p)
        if text is None:
            continue
        raw = text.encode("utf-8")
        cap = min(MAX_SCRIPT_BYTES, budget)
        if cap <= 0:
            break
        truncated = len(raw) > cap
        out.append({"path": p,
                    "content": raw[:cap].decode("utf-8", "ignore") if truncated else text,
                    "truncated": truncated})
        budget -= min(len(raw), cap)
    return out


def split_batches(scripts: list[dict]) -> list[list[dict]]:
    """按字节把脚本切成几批，每批塞进一次模型调用。

    **切的是调用，不是内容** —— 每一份脚本都会被读到，只是不在同一次对话里。
    这跟原来那种"超了就丢掉"的做法差一个性质：丢掉的那些，页面上看不出来。
    """
    if not scripts:
        return [[]]
    out: list[list[dict]] = [[]]
    used = 0
    for sc in scripts:
        n = len((sc.get("content") or "").encode("utf-8"))
        if out[-1] and used + n > BATCH_SCRIPT_BYTES:
            # 这里原来有一句 `if len(out) >= MAX_BATCHES: break` ——
            # 它违反的正是上面三行那句 docstring（「每一份脚本都会被读到」），
            # 而且**一分钱额度也没省**（总量早被 TOTAL_SCRIPT_BYTES 钉住）。
            # 它唯一的作用是：超了就无声地把剩下的脚本丢掉，而 scriptsRead
            # 数的是从 git 读到的份数，页面照样写「N 份全读了」。已删。
            out.append([])
            used = 0
        out[-1].append(sc)
        used += n
    return out


# 服务端说的「没写完」。openai 协议叫 length，anthropic 协议叫 max_tokens。
_TRUNC_REASONS = frozenset({"length", "max_tokens"})


def batch_completeness(resp, *, max_tokens: int = MAX_OUTPUT_TOKENS) -> str:
    """这一批模型到底写完了没有。**三态，不是布尔。**

    - `truncated`：有服务端事实证明没写完（结束原因是 length/max_tokens，
      或者写出来的 token 数已经顶到上限）。
    - `unknown`：**两个凭据一个都拿不到** —— 通道没报，或者报的是常量假值。
    - `complete`：拿到了凭据，而且都没命中。

    `unknown` 不是边角情况，是**主路**：2026-08-28 实测那一趟网关额度耗尽
    （`no upstream tokens available`），12 次调用全走 CLI 降级通道，
    而那条通道 `usage` 恒 0、`finish_reason` 恒 `"stop"`、连 `max_tokens` 都不理会。
    所以千万别把三态压成布尔 —— 压了之后**主路会一律显示成"写完了"**，
    而它其实是"没人知道"。这正是这个模块存在的意义要抓的那类错。

    （附带一条：CLI 通道既然不理会 `max_tokens`，它上面**根本不会发生**按上限截断。
      截断只发生在网关那条路上。所以三态里 `truncated` 和 `unknown` 是**互斥且分通道**的，
      不是"同一件事的两种确信度"。）
    """
    rep_ = getattr(resp, "reported", None) or frozenset()
    has_reason = "finish_reason" in rep_
    has_tokens = "completion_tokens" in rep_
    if not has_reason and not has_tokens:
        return "unknown"
    if has_reason and (getattr(resp, "finish_reason", "") or "") in _TRUNC_REASONS:
        return "truncated"
    if has_tokens and max_tokens and (getattr(resp, "completion_tokens", 0) or 0) >= max_tokens:
        return "truncated"
    return "complete"


_SEV_RANK = {"blocker": 0, "major": 1, "minor": 2}
_VERDICT_RANK = {"bad": 0, "risky": 1, "ok": 2}


def _clip_lines(text: str, limit: int = 600) -> tuple[str, bool]:
    """截长文本，但**只在行边界上截**。返回 (截完的文本, 是不是截过)。

    给 `evidence` 用。它是要被 Epic 3 拿去在脚本原文里回查的 ——
    从行中间切断会留下一个原文里根本不存在的半行，回验只能判"对不上"。
    唯一截不出行边界的情况是**第一行自己就超长**；那时只能硬切，
    但仍然标 `evidenceTruncated`，让回验知道这条本来就不完整，别赖到脚本头上。
    """
    if len(text) <= limit:
        return text, False
    head = text[:limit]
    cut = head.rfind("\n")
    return (head[:cut] if cut > 0 else head), True


def _gap_key(g: dict, kind: str = "") -> str:
    """跨批去重的键。**两种 gap 分开定 —— 它们的"同一条"根本不是一回事。**

    `scriptGaps` 是**脚本级**的：每份脚本只落在一个批里，跨批本来就撞不上，
    去重只为兜住模型在**同一批**里把同一条写两遍 —— 那种只有全等才该合，
    所以用**全文不截断**（截 60 字是在制造静默合并，而它防不住什么）。
    键里原先还有一段 `scenario`：Epic 0 副-D 实测 **72/72 条全是空的**，
    模型从来不填。留着会让这个键看起来考虑了三个维度、实际只有两个 —— 删掉。

    `catalogGaps` 是**域级**的：每批都拿到全量场景清单，所以同一条会被各批
    各说一遍，措辞还都不一样。副-A 实测这种自由文本键**一条都没去掉**
    （同一个域 18 行 nextUp 其实只有 3 件事，catalogGaps 是同一个病）。
    它的身份是「哪个场景的口径有问题」= `scenario`，不是后面那段解释文字，
    所以键就用 `scenario`。模型没填 `scenario` 时**退回原来的截断键** ——
    退化成"去不掉"，而不是拿一段自由文本去误合两条不同的发现。
    """
    if kind == "catalogGaps":
        sc = str(g.get("scenario") or "").strip()
        return "scenario:" + sc.lower() if sc else "why:" + str(g.get("why") or "")[:60]
    return "|".join(str(g.get(f) or "") for f in ("id", "path", "problem", "why"))


def merge_results(parts: list[dict]) -> dict:
    """把分批的结果并成一份。

    verdict 取**最坏**的那一批：47 份脚本分 5 批读，只要有一批判「多数认领不算数」，
    整个域就不能拿去当「认领都算数」用 —— 平均一下会把最要命的那批稀释掉。
    """
    out: dict = {"verdict": "ok", "summary": "", "brief": {},
                 "scriptGaps": [], "catalogGaps": []}
    seen: dict[str, int] = {}          # 键 → 它在 out[key] 里的下标
    for part in parts:
        v = part.get("verdict")
        if _VERDICT_RANK.get(v, 9) < _VERDICT_RANK.get(out["verdict"], 9):
            out["verdict"] = v
        for key in ("scriptGaps", "catalogGaps"):
            for g in part.get(key) or []:
                k = key + "|" + _gap_key(g, key)
                if k in seen:
                    # 合掉了就得留个数。域级那些每批都会各说一遍，
                    # 修好键之后页面上会**少掉一大截行** —— 不说清楚"这条 N 批都提到"，
                    # 读的人会以为是这一趟少发现了东西。
                    row = out[key][seen[k]]
                    row["mergedFrom"] = int(row.get("mergedFrom") or 1) + 1
                    continue
                seen[k] = len(out[key])
                out[key].append(g)
    out["scriptGaps"].sort(key=lambda g: _SEV_RANK.get(g.get("severity"), 9))
    out["summary"] = " ".join((p.get("summary") or "").strip() for p in parts).strip()[:800]
    out["brief"] = _brief(None, out["summary"])
    return out


_MERGE_SYSTEM = """同一个域的脚本太多，分了几批读，下面是**已经合并好**的全部结论。
你只做一件事：把它们收成**一份给人看的 brief** 和一句 summary。不重新评审，不加新发现。

- 只许用下面清单里已经有的说法，**一个新说法都不许加**（加了就是编的）。
- brief 里不许出现脚本路径、变量名、函数名、断言写法、HTTP 状态码。
- brief 里不许出现 `ok` / `risky` / `bad` 这三个原词（页面另有中文结论词）。
- `points` 最多 3 条，每条 ≤50 字，每条要点出「这是谁的事」：脚本要改 / 环境要铺 / 清单要商量。
- `solid` 不许空：撑得住的那部分也要说，1-3 条。
- 要带数字（几条、什么优先级）。"覆盖率偏低""建议加强"这类哪都成立的话一律不许写。
- **数字只许抄我在「按谁动手分」那三行里给你的**，不许自己数、不许估。页面会把这三堆
  分栏摆在你这句话底下，说「清单 17 处」底下却只列 1 条，人第一眼看到的就是
  "这页的数打架" —— 那比不写数字更糟。

只输出 JSON：
```json
{"brief": {"headline": "≤40字", "points": ["…"], "nextStep": "…", "solid": ["…"]},
 "summary": "两句话，给动手的人看，可以带术语"}
```"""


def _merge_payload(domain: dict, merged: dict, batches: int, scripts: int) -> str:
    gaps = merged.get("scriptGaps") or []
    n = {b: sum(1 for g in gaps if (g.get("blame") or "script") == b)
         for b in ("script", "env", "catalog")}
    lines = [f"域：{domain.get('code')} {domain.get('name') or ''}".strip(),
             f"（{scripts} 份脚本分 {batches} 批读完，下面是合并后的全部结论）", "",
             f"## 合并后的结论：{merged.get('verdict')}", "",
             # 数交给代码算，模型只负责抄 —— 让它自己数就会数出跟页面对不上的数
             "## 按谁动手分（写 points 时数字照抄这三行，别自己数）",
             f"- 脚本要改：{n['script']} 条",
             f"- 环境要铺：{n['env']} 条",
             f"- 清单要商量：{n['catalog'] + len(merged.get('catalogGaps') or [])} 条",
             "", "## 抓到的问题"]
    for g in merged.get("scriptGaps") or []:
        lines.append(f"- [{g.get('blame')}][{g.get('severity')}] {g.get('id') or ''} "
                     f"{g.get('oneLine') or ''}｜{g.get('problem') or ''}")
    if merged.get("catalogGaps"):
        lines += ["", "## 清单口径"]
        lines += [f"- {g.get('scenario') or ''}：{g.get('why') or ''}"
                  for g in merged["catalogGaps"]]
    lines += ["", "## 各批自己写的 summary", merged.get("summary") or "（空）"]
    return "\n".join(lines)


async def run_review(*, domain: dict, scenarios: list[dict],
                     scripts: list[dict], env_name: str, env_keys: list[str],
                     lib_texts: list[str], lib_paths: list[str] | None = None,
                     ai_config=None) -> dict:
    """真正评一次。返回落库用的 result；抛异常交给调用方标 failed。

    脚本装不进一次调用就**分批调、再合并**（`split_batches` / `merge_results`），
    不是截掉多的那些。原来那版是截：MCP 域 47 份脚本只进去 11 份，
    页面照样写「场景 75 条」—— 「没发现问题」和「没读到」在页面上长得一样。
    """
    scanned = scan_env_vars(scripts, set(env_keys), lib_texts, lib_paths)
    # `envMissing` 只装缺口那两档 —— 它在四个地方被 `len()` 当成「缺 N 个」渲染。
    missing = [v for v in scanned if v["state"] != "satisfied"]
    satisfied = [v["name"] for v in scanned if v["state"] == "satisfied"]
    batches = split_batches(scripts)

    async def _one(part: list[dict], mark: tuple[int, int] | None) -> tuple[dict, str]:
        user = build_payload(domain, scenarios, part, env_name, env_keys, missing, mark)
        resp = await llm_client.complete(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
            config=ai_config, max_tokens=MAX_OUTPUT_TOKENS, temperature=0,
            timeout=MIN_TIMEOUT_SECONDS)
        # 完整性跟结论一起带出来。**丢掉它就没法区分「这批没抓到问题」和
        # 「这批话没说完」** —— 两者在页面上长得一模一样，都是"这一格 0 条"。
        return parse_result(resp.content or ""), batch_completeness(resp)

    completeness: dict[str, str] = {}
    if len(batches) == 1:
        out, completeness["1"] = await _one(batches[0], None)
    else:
        n = len(batches)
        # 并发发出去，但**限流**。分批是为了每批读得完整，不是为了排队 ——
        # 串行会把一个域的耗时乘上批数，人在页面上等的就是这个数。
        # 但也不能全放出去：5 批一起打，网关实测直接 5 个 429，全降级到 CLI 通道
        gate = asyncio.Semaphore(BATCH_CONCURRENCY)

        async def _guarded(part: list[dict], mark: tuple[int, int]) -> dict:
            async with gate:
                return await _one(part, mark)

        # `return_exceptions=True` 不是"容错"顺手加的：**一批挂掉不该把另外四批读到的
        # 东西一起扔了**。实测撞过一次 —— 5 批里 3 批同时吃到网关 429（那次降级通道
        # 因为工作目录不对没加载到，见下面那条 raise 的注释），gather 抛出来，
        # 已经读完的 2 批结果跟着没了，页面上只剩一句"评审失败"，得整个域重跑 10 分钟。
        raw = await asyncio.gather(*[_guarded(b, (i + 1, n)) for i, b in enumerate(batches)],
                                   return_exceptions=True)
        good: list[dict] = []
        lost: list[tuple[int, BaseException]] = []
        for i, r in enumerate(raw, 1):
            if isinstance(r, BaseException):
                lost.append((i, r))
            else:
                parsed, comp = r
                good.append(parsed)
                completeness[str(i)] = comp
        for idx, err in lost:
            logger.warning("QA 域评审：第 %d/%d 批没读成 domain=%s：%r",
                           idx, n, domain.get("code"), err)
        # 全挂才算失败。**部分挂了必须让页面看得见**：少读一批就是少读十几份脚本，
        # 而"少读了"和"没问题"在页面上长得一模一样 —— 这是这套评审最要防的一件事。
        if not good:
            raise lost[0][1]
        out = merge_results(good)
        out["batchesFailed"] = [i for i, _ in lost]
        # 合并那一趟**只看结论、看不到脚本正文** —— 它没法编出一段仓库里没有的代码
        try:
            resp = await llm_client.complete(
                [{"role": "system", "content": _MERGE_SYSTEM},
                 {"role": "user", "content": _merge_payload(domain, out, n, len(scripts))}],
                config=ai_config, max_tokens=900, temperature=0)
            m = parse_result(resp.content or "")
            out["brief"] = m["brief"]
            out["summary"] = m["summary"] or out["summary"]
        except Exception:  # noqa: BLE001
            # 收口这一步挂了不算评审失败：分批的结论都在，人话那段退回拼接的 summary
            logger.exception("QA 域评审：分批收口失败，退回拼接版 domain=%s", domain.get("code"))
    out["envMissing"] = missing
    # 第三档。只存**键名**，给「缺 2 个」配个分母 —— 没分母的话
    # 「缺 2 个」既可能是 2/3 也可能是 2/40，读的人没法判这一列有多严重。
    out["envSatisfied"] = satisfied
    out["reviewedScripts"] = [{"path": s["path"], "truncated": s["truncated"]} for s in scripts]
    # 页面要说清"这次读了多少"：只读了 14 份里的 5 份却说"这个域没问题"是骗人的
    out["scenarioCount"] = len(scenarios)
    # 这一趟按哪一版维度口径评的。渲染时拿它区分「查了没抓到」和「压根没查」——
    # 不盖这个戳，存量结论会把"模型压根没被问过"渲染成一个漂亮的 0。
    out["dimSpec"] = DIM_SPEC
    # **上限截了多少，必须报出来。** MCP 域涨到 75 条场景那次实测：60 条进了 prompt、
    # 15 条模型根本没看见，14 份脚本只读进 11 份、其中 7 份正文还被截断 ——
    # 而页面上写的是「场景 75 条」，读的人只会以为这 75 条都评过了。
    # 截断本身不是 bug（额度有限），**把截断说成全量才是**。
    out["coverage"] = {
        "scenariosTotal": len(scenarios),
        "scenariosShown": min(len(scenarios), MAX_SCENARIOS),
        "scriptsTotal": len({c["path"] for s in scenarios for c in (s.get("scripts") or [])}),
        "scriptsRead": len(scripts),
        # **真进了模型的份数。** scriptsRead 数的是从 git 读到的份数 ——
        # 这两个数不相等的时候，差额就是"页面说读了、模型没看过"的那些。
        # 今天恒等（split_batches 不再丢），但**页面不许拿"应该恒等"当依据去断言"全读了"**：
        # 这个模块治的就是「结论看起来有据、依据其实没验过」，自己身上更不能有。
        "scriptsBatched": sum(len(b) for b in batches),
        "scriptsTruncated": sum(1 for s in scripts if s.get("truncated")),
        "batches": len(batches),
        # 没读成的批次号。非空 = 这一趟的"没发现问题"里有一块是"没读到"
        "batchesFailed": out.pop("batchesFailed", []),
        # 批次号 → complete / truncated / unknown。**这个键不存在 = 旧口径评的**，
        # 那时候不记这件事 —— 渲染时必须说"没记"，不许当成 complete。
        "completeness": completeness,
    }
    return out


def spawn(coro) -> None:
    """后台跑。评一个域实测 20–60 秒，同步 POST 会把人钉在页面上（review-spec §5）。"""
    asyncio.create_task(coro)  # noqa: RUF006 — 生命周期由 _run 自己的 try/finally 兜住


def finish(review: QaCatalogReview, result: dict | None, error: str | None) -> None:
    review.status = "done" if error is None else "failed"
    review.result = result
    review.error = error
    review.finished_at = datetime.now(timezone.utc)


# 词换过三轮，前两轮同一个毛病（**用一个词概括，读的人就得猜**），第三轮是另一个：
#   第一版「靠得住 / 有水分 / 撑不住」—— "水"在哪？"撑"的是什么？
#   第二版「能信 / 信一半 / 不能信」—— 信什么？信一半是哪一半？
#   第三版「都验到了 / 部分没验到 / 多数没验到」—— 意思是清楚了，但**没主语**。
# 这一栏答的是一个很具体的问题：*清单说这条场景「已覆盖」，那句认领算不算数？*
VERDICT_CN = {"ok": "认领都算数", "risky": "部分认领不算数", "bad": "多数认领不算数"}
VERDICT_WHY = {
    "ok": "清单里认领了的场景，脚本读下来都真在验那件事",
    "risky": "一部分认领对不上：脚本认领了，但断言太松、或在这个环境里压根没跑",
    "bad": "认领的主要场景多数没真验 —— 清单上那个「已覆盖」当不了数",
}
# 结论词的**主语**是这次评审的对象（QA 的清单与脚本），不是评审这个动作本身。
# 第三版「都验到了 / 部分没验到 / 多数没验到」栽在这儿：主语省掉了，而同一个抽屉里
# 还写着「一份都没真跑」「第 3 批没读成」—— 读的人完全有理由把「部分没验到」
# 读成"你自己只看了一部分"。**判断一个结论词行不行，除了"还要不要再问一句这是什么
# 意思"，还得问一句"这句话在说谁"。** 所以第四版把主语写进词里：认领 = 清单里
# 那句「这条场景有脚本覆盖」，算不算数说的是它。
VERDICT_SUBJECT = "这条结论说的是 QA 的清单和脚本，不是说我读了多少 —— 我这趟读了多少、有没有漏，在「怎么看的」里单独写。"

# 评审的**维度**。一个域抓到 46 条，人不会一条条读；他要知道的是
# 「哪一块塌了」——顶层固定就三块（覆盖面 / 场景设置 / 断言），24 个域横着比
# 也是这三块，一眼看出「这个域塌在断言上，那个域塌在覆盖面」。
#
# ⚠ 某一格 0 条 ≠ 那一块没问题，只等于**这一趟没抓到**。漏判是看不见的，
#   所以页面和报告里都必须把这句话写在表旁边，不许让 0 自己去暗示"这块过了"。
# 评审维度。**先定人认得的三个大维度，我那套查法挂在它们底下当子项** ——
# 上一版摆的是六条「断言能不能失败 / 一条认领拆没拆开 / 验到哪一层」，那是**我的查法**，
# 不是测试经理脑子里的维度。实测反馈就一句：「你写的都是什么维度，我咋看不懂」。
# 大维度只用现成的行话：**覆盖面 / 场景设置 / 断言**。子项才是判据。
AXES = (
    ("cover", "覆盖面", "该测的测了没", (
        ("coverage", "清单里就没有这条场景", "这个域该有的路径/角色/失败态，清单一条都没认领"),
        ("both", "只测了成功那一半", "该被拒的那一半没测（或反过来）—— 放行对了不等于拦得住"),
        ("skip", "在这个环境里整条跳过", "缺变量、缺样本就 exit 0，清单那边照记「已覆盖」"),
    )),
    ("design", "场景设置", "这条场景本身定得合不合理", (
        ("grain", "一条说了好几件事", "清单一条说三件事，脚本只验得了其中一件 —— 认领粒度太粗"),
        ("shape", "说不清要证明什么", "场景描述给不出可判定的预期，或优先级跟风险明显不匹配"),
    )),
    ("assertion", "断言", "断得对不对、站不站得住", (
        ("assert", "断言恒真，改坏了也不会红", "把动作删掉这条断言还成立 —— 跑绿证明不了任何事"),
        ("claim", "断的不是认领的那件事", "脚本头认领了 A，正文在验 B"),
        ("depth", "只断到接口回了 200", "没读回来确认那件事真的发生了"),
        ("expect", "断的值跟清单写的不一致", "清单写「401 且 error.code=X」，脚本断的是别的，或只断个非空糊过去"),
    )),
)
DIM_META = {k: (name, why, ax) for ax, _, _, dims in AXES for k, name, why in dims}
# 维度口径的版本。**加了新子项就得 +1**，否则存量结论会把「这一趟压根没查」渲染成
# 一个漂亮的 0 —— 那正是这套表最该堵掉的假安心。
DIM_SPEC = 2
# 每个子项从哪一版起才有。第 1 版只有六条（都是脚本那一侧的查法），
# 覆盖面/场景设置各自那条、以及「断的值对不对」是第 2 版才加的。
DIM_SINCE = {"coverage": 2, "shape": 2, "expect": 2}
DIM_KEYS = tuple(DIM_META)
DIM_OTHER = ("other", "其它（这次没归类）", "模型没给它归维度，或是旧结论 —— 归维度是后加的")


def dim_rollup(result: dict | None) -> list[dict]:
    """按维度把结论卷一遍：三个大维度，每个带自己的子项。
    **条数由代码数，模型只负责给每条打 `dim`。**

    让模型自己数就会数出跟页面对不上的数（实测撞过「清单 17 处」底下只列 1 条）。

    ⚠ 加维度**之前**评的那些结论，模型压根没被问过新加的那几条。那种情况下
    在页面上摆一个 0 是在骗人（"这一维没问题"），所以标 `unavailable` ——
    渲染成「这一趟没查」，不是「—」。
    """
    res = result or {}
    spec = res.get("dimSpec") or 1
    rows = list(res.get("scriptGaps") or []) + list(res.get("catalogGaps") or [])
    n: dict[str, int] = {k: 0 for k in DIM_KEYS}
    n[DIM_OTHER[0]] = 0
    for g in rows:
        d = g.get("dim")
        n[d if d in n else DIM_OTHER[0]] += 1
    out = []
    for ax, ax_name, ax_why, dims in AXES:
        items = [{"key": k, "name": name, "why": why, "level": 1, "count": n[k], "axis": ax,
                  "unavailable": DIM_SINCE.get(k, 1) > spec} for k, name, why in dims]
        out.append({"axis": ax, "name": ax_name, "why": ax_why, "level": 0,
                    "count": sum(i["count"] for i in items),
                    "unavailable": sum(1 for i in items if i["unavailable"]),
                    "items": items})
    if n[DIM_OTHER[0]]:
        k, name, why = DIM_OTHER
        out.append({"axis": k, "name": name, "why": why, "level": 0,
                    "count": n[k], "unavailable": 0, "items": []})
    return out


def dim_flat(result: dict | None) -> list[dict]:
    """卷完拍平成一列，给只能顺着往下渲染的地方（Markdown 表格）用。"""
    out = []
    for ax in dim_rollup(result):
        out.append(ax)
        out.extend(ax["items"])
    return out


# 「谁动手」。人最先想知道的不是严重度，是"这条要不要我处理" ——
# 上一版三类混在一张表里，读的人自己在心里分栏，分完还得怀疑分对了没有。
BLAME_CN = {
    "script": ("QA 的脚本要改", "断言写得站不住：跑绿了也证明不了它认领的那件事"),
    "env": ("不是脚本的问题：环境没铺东西",
            "脚本可能写得很对，只是在这个环境里自己跳过了。"
            "⚠ 我们只看得到**自己这侧**的环境记录，QA 跑的时候有没有，这儿判不了"),
    "catalog": ("清单口径要商量", "脚本和环境都没错，是认领的口径对不上"),
}
BLAME_ORDER = ("script", "env", "catalog")


def by_blame(rows: list[dict]) -> dict[str, list[dict]]:
    """按「谁动手」分栏。认不出来的落 script（宁可多审一条，别漏发给仓库主人）。"""
    out: dict[str, list[dict]] = {k: [] for k in BLAME_ORDER}
    for g in rows or []:
        out[g.get("blame") if g.get("blame") in out else "script"].append(g)
    return out


def to_markdown(r: QaCatalogReview) -> str:
    """把一次评审渲染成一份**能直接拿走的** Markdown 报告。

    这是 QA 那边取结论的唯一形态：**我们只生成文本，谁要谁自己拉**（`GET .../export`
    或 MCP 的 `lum_get_qa_review`）。平台不会替他往 QA 仓里放文件 —— 那条线
    `docs/qa-repo-readonly-catalog.md` §1 已经封死了，这里也不能从侧面开口子。

    所以报告里必须自带三样，缺一样就没法复核：评的是哪个 commit、在哪个环境上评的、
    哪几份脚本进了模型（还有哪几份被截断）。少了它们，两周后没人说得清这份结论
    是对着哪一版脚本下的。
    """
    res = r.result or {}
    b = brief_of(res)
    L: list[str] = []
    L.append(f"# QA 域评审 · {r.domain} {r.domain_name or ''}".rstrip())
    L.append("")
    L.append(f"- 结论：**{VERDICT_CN.get(res.get('verdict'), res.get('verdict') or '—')}**"
             f"（{res.get('verdict') or '—'}）"
             f" —— {VERDICT_WHY.get(res.get('verdict'), '')}".rstrip(" —"))
    L.append(f"  （{VERDICT_SUBJECT}）")
    L.append(f"- QA 仓：`{r.branch or '—'}` @ `{(r.commit_sha or '')[:10] or '—'}`")
    L.append(f"- 评审环境：{r.environment_name or '—'}（只拿变量名对账，不跑任何脚本）")
    L.append(f"- 场景 {res.get('scenarioCount') or r.scenario_count} 条 · "
             f"进模型的脚本 {len(res.get('reviewedScripts') or [])} 份")
    L.append(f"- {r.actor or '—'} 发起于 "
             f"{r.created_at.strftime('%Y-%m-%d %H:%M') if r.created_at else '—'}")
    L.append("")

    L.append("## 一句话（给人看）")
    L.append("")
    L.append(b.get("headline") or res.get("summary") or "—")
    for x in b.get("points") or []:
        L.append(f"- {x}")
    if b.get("nextStep"):
        L.append("")
        L.append(f"**下一步**：{b['nextStep']}")
    L.append("")

    # 别人第一次看到这份结论，第一个念头是"你凭什么这么说" —— 先答了再往下看。
    L.append("## 我是怎么看的")
    L.append("")
    L.append("**流程**（每一步都只读，QA 仓一个字没动）：")
    L.append("")
    L.append("1. 只读地取这个域的场景清单 —— 它**说要验**什么；")
    L.append("2. 只读地取认领了这些场景的脚本正文 —— 它**实际在验**什么；")
    L.append("3. 环境变量对账 —— **纯代码算的，不过模型**；只有变量**名**进提示词，"
             "值一个字节都不进；")
    L.append("4. 一条条对，只问一个问题：**这条断言能不能失败？**"
             "（改坏了会红，才算真在验；恒真的断言跑绿等于没跑。）"
             "脚本一次装不下就分批读，每批都拿到完整场景清单；")
    L.append("5. 合并：结论取**最坏**的那一批（平均一下会把最要命的那批稀释掉），"
             "三堆的条数由代码数好、模型只许照抄；")
    L.append("6. 落库、渲染成这份报告。**QA 那边自己来拉**"
             "（HTTP export / MCP `lum_get_qa_review`），平台不往他仓里放任何东西。")
    L.append("")
    cov0 = res.get("coverage") or {}
    if cov0.get("scriptsRead"):
        bat = cov0.get("batches") or 1
        read0 = cov0["scriptsRead"]
        fed0 = cov0.get("scriptsBatched")
        # **「读到」和「进了模型」是两件事**，别用前者的数去说后者。
        # 页面上这句是全篇第一个数字，人拿它当"评了多少"用。
        if fed0 is not None and fed0 < read0:
            L.append(f"⚠ 这一趟从 git 读出 {read0} 份脚本，但**只有 {fed0} 份真进了模型**"
                     f"（差 {read0 - fed0} 份）、**{cov0.get('scenariosShown', 0)} 条场景**。"
                     f"下面的结论**不包括**没进去那 {read0 - fed0} 份。")
        else:
            L.append(f"这一趟读了 **{fed0 if fed0 is not None else read0} 份脚本的正文**"
                     + (f"（一次装不下，分 {bat} 批读完再合并）" if bat > 1 else "")
                     + "、" + f"**{cov0.get('scenariosShown', 0)} 条场景**。")
        L.append("")
    L.append("**为什么一份都没跑** —— 跑不了，而且不该靠跑：")
    L.append("")
    L.append("- **跑不了**：脚本要 QA 自己那套运行环境（我们这侧连变量名都缺"
             f"{len(res.get('envMissing') or [])} 个），而且真跑会往被测系统写数据"
             "（造数、审批、删除）—— 那是别人的环境，只读红线也不允许。")
    L.append("- **不该靠跑**：这次要判的恰恰是「跑绿了但没验到」。"
             "恒真断言跑一万遍也是绿的，跑本身对这个问题**零信息量**。"
             "要判它只有两条路：读正文（这一趟做的），或者把动作删掉再跑看它变不变红 ——"
             "后者要改人家的脚本，只读做不到。")
    L.append("- **代价说清楚**：因此**脚本在真环境里跑不跑得起来，这份结论判不了**。"
             "那一半只有 QA 自己跑得出来。")
    L.append("")
    L.append("**这份结论靠得住吗** —— 按下面这几条自己掂量：")
    L.append("")
    L.append("- ✅ **每条都能十秒内被否掉**：`evidence` 是从脚本正文原样抄的，"
             "grep 一下就知道我说得对不对。**这才是它能被信的理由，不是「AI 说的」。**")
    L.append("- ⚠ **单趟单模型，没有第二意见**：同一份脚本再评一次，措辞会变、条数会差几条。"
             "拿它当「要不要停下来处理」的依据可以，别拿它当分数。")
    L.append("- ⚠ **漏判是看不见的**：抓到多少不等于只有多少；某一格 0 条只等于"
             "**这一趟没抓到**，不等于那一块没问题。")
    L.append("- ⚠ **环境那一列判的是我们这侧**：QA 自己跑的时候有没有那些变量，平台看不到。")
    if (res.get("coverage") or {}).get("batchesFailed"):
        L.append("- ⚠ **这一趟有批次没读成**（见下方「这次读了什么」），那几批等于没看。")
    # 「模型有没有把话说完」。三态，`unknown` 的说法必须和 `complete` 明确不同 ——
    # 把"没人知道"渲染成"写完了"，跟这个模块要抓的「跑绿了但没验到」是同一个病。
    comp = (res.get("coverage") or {}).get("completeness")
    if comp is None:
        L.append("- ⚠ **这一趟没记「模型有没有把话说完」**：旧口径评的，当时不区分"
                 "「写完了」和「写到一半撞上输出上限」——**别把它当成写完了**。")
    else:
        def _pick(state: str) -> list[int]:
            return sorted(int(k) for k, v in comp.items() if v == state and str(k).isdigit())
        tr, uk = _pick("truncated"), _pick("unknown")
        if tr:
            L.append(f"- ⚠ **有 {len(tr)} 批撞上输出上限被截断了**"
                     f"（第 {'、'.join(str(i) for i in tr)} 批）：截断处之后本该写的结论"
                     "根本没写出来 —— 那几批的「没抓到」是**没写完**，不是没问题。")
        if uk:
            L.append(f"- ⚠ **有 {len(uk)} 批说不清有没有写完**"
                     f"（第 {'、'.join(str(i) for i in uk)} 批）：走的是 CLI 降级通道，"
                     "它不报 token 数、结束原因恒为 `stop`（值是编的），"
                     "**没有服务端事实可查**。说不清 ≠ 写完了。")
        if not tr and not uk and comp:
            L.append("- ✅ **每一批都写完了**：通道报的结束原因和 token 数都拿到了，"
                     "都没撞上限 —— 这一条是有服务端事实支撑的，不是默认值。")
    L.append("")
    if b.get("solid"):
        L.append("## 撑得住的部分")
        L.append("")
        for x in b["solid"]:
            L.append(f"- {x}")
        L.append("")

    if res.get("summary"):
        L.append("## 结论详述")
        L.append("")
        L.append(res["summary"])
        L.append("")

    L.append("## 按维度看（人看这一张就够）")
    L.append("")
    L.append("> 三个维度 —— **覆盖面 / 场景设置 / 断言** —— 是固定的，24 个域横着比也是这三块。"
             "**某一格 0 条只说明这一趟没抓到，不等于那一块没问题。**")
    L.append("")
    L.append("| 维度 | 抓到 | 判据 |")
    L.append("|---|---|---|")
    stale = 0
    for d in dim_flat(res):
        if d["level"] == 0:
            L.append(f"| **{d['name']}** | **{d['count'] or '—'}** | {d['why']} |")
        elif d["unavailable"]:
            stale += 1
            L.append(f"| &nbsp;&nbsp;└ {d['name']} | *这一趟没查* | {d['why']} |")
        else:
            L.append(f"| &nbsp;&nbsp;└ {d['name']} | {d['count'] or '—'} | {d['why']} |")
    L.append("")
    if stale:
        # 「没查」和「查了没抓到」在表里长得一样，不点出来就是拿旧结论冒充全维度覆盖
        L.append(f"⚠ 上表里 {stale} 项标着「这一趟没查」—— 这个域是加这几条判据**之前**评的，"
                 f"模型没被问过它们。**重评一次这个域就补上了**，在那之前别把它们读成没问题。")
        L.append("")

    L.append("## 抓到的问题（按谁动手分）")
    L.append("")
    L.append("> 脚本头写了 `@scenario`，但正文没验到那件事 —— `check-coverage.sh` 查不了这一层。")
    L.append("")
    rows = res.get("scriptGaps") or []
    if not rows:
        L.append("（这一轮没抓到）")
    groups = by_blame(rows)
    last_blame = None
    for g in [x for k in BLAME_ORDER for x in groups[k]]:
        blame = g.get("blame") if g.get("blame") in BLAME_CN else "script"
        if blame != last_blame:
            title, why = BLAME_CN[blame]
            L.append(f"### {title}（{len(groups[blame])} 条）")
            L.append("")
            L.append(f"> {why}")
            L.append("")
            last_blame = blame
        L.append(f"#### {g.get('id') or '—'} · {g.get('severity') or '—'}"
                 + (f" · {g['oneLine']}" if g.get("oneLine") else ""))
        L.append("")
        if g.get("path"):
            L.append(f"- 脚本：`{g['path']}`")
        L.append(f"- 问题：{g.get('problem') or '—'}")
        if g.get("evidence"):
            L.append("- 判据（脚本原文）：")
            L.append("")
            L.append("  ```bash")
            ev_lines = str(g["evidence"]).splitlines()
            for line in ev_lines[:6]:
                L.append(f"  {line}")
            L.append("  ```")
            # 显示只留 6 行、入库时也可能截过。**两处都要说出来。**
            # 不说的话，读的人拿这段去 grep 发现"就这么点"，
            # 会把"我只给你看了一部分"读成"它就只有这么多" —— 本模块整套在防的正是这个。
            if len(ev_lines) > 6:
                L.append(f"  （原文还有 {len(ev_lines) - 6} 行，这里只显示前 6 行）")
            if g.get("evidenceTruncated"):
                L.append("  （这段判据入库时被截过，**不是脚本原文的全部**）")
        if g.get("fix"):
            L.append(f"- 建议改成：{g['fix']}")
        L.append("")

    L.append("## 这些名字我们这侧的环境记录里没有（**不是脚本的问题**）")
    L.append("")
    L.append(f"> 脚本引用的、或 `config` 里声明「要从外面传」的，而我们这条 "
             f"**{r.environment_name or '所选环境'}** 记录里没有。代码算的，不是模型猜的。")
    L.append(">")
    L.append("> **这一列不构成对 QA 的意见。** 它只说明我们这侧没记着这些名字；"
             "QA 自己的 runner 里有没有，平台看不到、也不该替他判。")
    L.append("")
    miss = res.get("envMissing") or []
    if not miss:
        L.append("（脚本要的变量这个环境都有）")
    ok_n = len(res.get("envSatisfied") or [])
    if miss and ok_n:
        L.append(f"（这个域要从外面拿 {len(miss) + ok_n} 个变量，其中 {ok_n} 个这个环境里有）")
        L.append("")
    for v in miss:
        tail = "、".join(v.get("scripts") or []) or "—"
        if v.get("state") == "ambiguous":
            # 降级要连**凭什么降**一起写出来，否则它就是一句无从复核的断言
            L.append(f"- `{v.get('name')}` — 名字对不上，**不是真缺**："
                     f"环境里有 {'、'.join(f'`{k}`' for k in v.get('family') or [])}"
                     f"　｜　{tail}")
        else:
            L.append(f"- `{v.get('name')}` — {tail}")
    if miss:
        L.append("")
        L.append("⚠ 两件事别搞混：**在平台这边补上变量不会让 QA 的脚本真跑起来**"
                 "（值要在真正跑套件的地方注入）；而平台这边没记着，也不等于那边缺。"
                 "所以由这一列推出的结论一律只到「在这个环境里会跳过」，"
                 "上面那节里它们都归在「不是脚本的问题」下。")
    L.append("")

    L.append("## 清单本身漏了什么")
    L.append("")
    L.append("> 建议而已 —— 清单是 QA 自己维护的，这边只读。")
    L.append("")
    cg = res.get("catalogGaps") or []
    if not cg:
        L.append("（没看出明显缺的一环）")
    for g in cg:
        line = f"- **{g.get('scenario') or g.get('problem') or '—'}** — {g.get('why') or ''}".rstrip(" —")
        n = int(g.get("mergedFrom") or 1)
        if n > 1:
            # 不是"重复了 N 次"，是"分批读的时候 N 批各自都提到了它" ——
            # 域级结论本来就每批看一遍，这个数说明它有多显眼，不是噪声。
            line += f"（{n} 批都提到）"
        L.append(line)
    L.append("")

    L.append("## 这次读了什么")
    L.append("")
    cov = res.get("coverage") or {}
    if cov.get("scenariosTotal", 0) > cov.get("scenariosShown", 0):
        L.append(f"> ⚠ 这个域共 {cov['scenariosTotal']} 条场景，进模型的只有前 "
                 f"{cov['scenariosShown']} 条（P0/P1 和高风险优先），"
                 f"**其余 {cov['scenariosTotal'] - cov['scenariosShown']} 条这次没评**。")
        L.append("")
    if cov.get("scriptsTotal", 0) > cov.get("scriptsRead", 0):
        L.append(f"> ⚠ 这个域挂了 {cov['scriptsTotal']} 份脚本，这次只读进 "
                 f"{cov['scriptsRead']} 份 —— 没读到的那几份不在下面这张表里。")
        L.append("")
    if (cov.get("batches") or 1) > 1:
        read = cov.get("scriptsRead") or 0
        fed = cov.get("scriptsBatched")
        if fed is None:
            # 旧口径没记「真进了模型几份」。**那就不许说"全读了"** ——
            # 这句原来是无条件断言，谁看都以为是核过的事实。
            L.append(f"> 这个域的脚本一次装不进一轮对话，分了 {cov['batches']} 批读，"
                     "每批都拿到完整场景清单，最后合并。"
                     "（这一版口径没记「真进模型几份」，所以这里不敢说"
                     f"{read} 份都进去了。）")
        elif fed < read:
            L.append(f"> ⚠ 这个域的脚本分了 {cov['batches']} 批读，"
                     f"但**从 git 读出 {read} 份、只有 {fed} 份进了模型，差 {read - fed} 份**。"
                     "下面这张表列的是**读出来的**，不是评过的 —— 差额那几份没人看过。")
        else:
            L.append(f"> 这个域的脚本一次装不进一轮对话，分了 {cov['batches']} 批读，"
                     f"每批都拿到完整场景清单，最后合并 —— **{fed} 份全进了模型**，"
                     "不是抽了几份。")
        L.append("")
    if cov.get("batchesFailed"):
        bad = "、".join(f"第 {i} 批" for i in cov["batchesFailed"])
        L.append(f"> ⚠ **{bad}没读成**（网关限流或超时），那几批里的脚本这一趟等于没看 —— "
                 "下面「没抓到问题」的部分不包括它们。重跑一次这个域就补上了。")
        L.append("")
    for sc in res.get("reviewedScripts") or []:
        L.append(f"- `{sc.get('path')}`{'（正文已截断，截断的不下结论）' if sc.get('truncated') else ''}")
    L.append("")
    L.append("---")
    L.append("")
    L.append("由 Lumiere 域级 AI 评审生成，只读了 QA 仓的清单与脚本正文，"
             "**没有对该仓库做任何写操作**。结论是建议，不是门禁。")
    return "\n".join(L)


def to_dict(r: QaCatalogReview, *, with_dims: bool = False) -> dict:
    """一条评审记录发给前端的样子。

    `with_dims` **默认关**，只有详情接口开。列表接口一次出几十行，
    每行挂一份维度口径就是把同一段常量发几十遍 —— 忘了传是"少个字段"，
    而不是"列表接口悄悄胖了十倍"，这个方向选错了代价不对称。
    """
    out = {
        "id": str(r.id),
        "domain": r.domain,
        "domainName": r.domain_name or "",
        "environmentId": str(r.environment_id) if r.environment_id else None,
        "environmentName": r.environment_name or "",
        "commitSha": (r.commit_sha or "")[:10],
        "branch": r.branch or "",
        "actor": r.actor or "",
        "status": r.status,
        "scenarioCount": r.scenario_count,
        "scriptCount": r.script_count,
        # 老记录没有 brief（后加的字段），读的时候补上——不补就是页面上一页空白
        "result": ({**r.result, "brief": brief_of(r.result)} if r.result else r.result),
        "error": r.error,
        "createdAt": r.created_at.isoformat() if r.created_at else None,
        "finishedAt": r.finished_at.isoformat() if r.finished_at else None,
    }
    if with_dims:
        # **维度口径由后端发，前端不再存副本。**
        # 之前前端抄了三份常量（`AXES` / `DIM_KEYS` / `DIM_SINCE`），注释里写着
        # 「跟后端必须一字不差」—— 而"必须一字不差"是一句没有任何东西在执行的话。
        # 漂了之后错得极安静：后端加一条子项、前端那份 `DIM_SINCE` 没跟上，
        # 新子项在**存量结论**上不会标「这一趟没查」，而是渲染成一个漂亮的 0。
        # 一个假的 0 正是这整套表最该堵掉的东西，它自己身上不能有。
        out["dims"] = dim_rollup(r.result)
        # 当前口径版本。结论自己是按哪一版评的在 `result.dimSpec` 里，
        # 两个数一比才知道「这条结论落后了几版」—— 只发一个数说明不了这件事。
        out["dimSpec"] = DIM_SPEC
    return out


async def execute(project_id: uuid.UUID, review_id: uuid.UUID, cfg: dict, domain_code: str) -> None:
    """后台那一趟：读脚本 → 评 → 落库。**自己开 session**，请求那条已经关了。

    任何异常都要落到 `error` 字段上。悄悄挂掉的后果是页面上一条 `running` 永远转下去，
    而人只会以为"AI 很慢"。
    """
    import anyio

    from app.deps.db import async_session_factory
    from app.services import qa_catalog
    from app.services.ai_config_resolver import resolve_ai_config

    async with async_session_factory() as session:
        review = await session.get(QaCatalogReview, review_id)
        if review is None:
            return
        try:
            review.status = "running"
            await session.commit()

            catalog = await anyio.to_thread.run_sync(
                lambda: qa_catalog.cached_read(str(project_id), cfg, False))
            domain, scenarios, paths = collect(catalog, domain_code)

            def _load() -> tuple[list[dict], list[str], list[str]]:
                repo_dir = qa_catalog._repo_dir(str(project_id))
                ref, _ = qa_catalog._resolve_ref(repo_dir, cfg.get("branch") or "")
                scripts = take_scripts(lambda p: qa_catalog._show(repo_dir, ref, p), paths)
                # 公共库只用来认变量名，不进 prompt —— 它跟"这个域验没验到"无关，
                # 塞进去只会把脚本正文的额度吃掉
                #
                # **要跟着 source 往下走。** 脚本 source 的是 `config/env.sh`，
                # 真正定义 TOKEN / API_BASE 的 `lib/auth.sh` 是它再 source 进来的。
                # 只走一层的话，这些变量全会被算成"环境缺的"。
                libs, lib_paths, seen = [], [], set()
                all_paths = qa_catalog._ls_tree(repo_dir, ref)
                queue = [p for s in scripts
                         for p in sourced_files(s["content"] or "", all_paths)]
                while queue and len(libs) < MAX_SOURCED_LIBS:
                    lib = queue.pop(0)
                    if lib in seen:
                        continue
                    seen.add(lib)
                    text = qa_catalog._show(repo_dir, ref, lib) or ""
                    libs.append(text)
                    lib_paths.append(lib)
                    queue.extend(p for p in sourced_files(text, all_paths) if p not in seen)
                return scripts, libs, lib_paths

            scripts, lib_texts, lib_paths = await anyio.to_thread.run_sync(_load)
            review.script_count = len(scripts)

            env_keys: list[str] = []
            if review.environment_id:
                from app.services.variable_service import build_run_env
                # **只取键名**：值里是真凭证，一个字节都不能进 prompt
                env_keys = sorted((await build_run_env(session, review.environment_id)).keys())

            cfg_ai = await resolve_ai_config(project_id, session, capability="lum-quality-review")
            if not cfg_ai:
                raise RuntimeError("AI 服务未配置 —— 去「AI 服务配置 → AI 能力」绑一个模型再来")

            result = await run_review(
                domain=domain, scenarios=scenarios, scripts=scripts,
                env_name=review.environment_name, env_keys=env_keys,
                lib_texts=lib_texts, lib_paths=lib_paths, ai_config=cfg_ai)
            finish(review, result, None)
        except Exception as e:  # noqa: BLE001
            logger.exception("QA 域评审失败 project=%s domain=%s", project_id, domain_code)
            finish(review, None, f"{type(e).__name__}: {e}"[:600])
        await session.commit()


def new_review(project_id: uuid.UUID, *, domain: dict, repo: dict, env, actor: str,
               scenario_count: int, script_count: int) -> QaCatalogReview:
    return QaCatalogReview(
        project_id=project_id,
        domain=domain.get("code") or "",
        domain_name=domain.get("name") or "",
        environment_id=(env.id if env is not None else None),
        environment_name=(env.name if env is not None else ""),
        commit_sha=(repo or {}).get("commitSha") or "",
        branch=(repo or {}).get("branch") or "",
        actor=actor,
        status="queued",
        scenario_count=scenario_count,
        script_count=script_count,
    )
