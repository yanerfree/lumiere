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

MAX_SCENARIOS = 60          # 一个域最多这么多条场景进 prompt。实测最大的域 26 条，够用
MAX_SCRIPTS = 14            # 脚本正文最多带这么多份
MAX_SCRIPT_BYTES = 6_000    # 单份脚本截断长度：QA 脚本中位数 2.4KB，6K 能装下九成
TOTAL_SCRIPT_BYTES = 56_000  # 所有脚本正文合计上限
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


def env_gaps(scripts: list[dict], env_keys: set[str], lib_texts: list[str] | None = None,
             lib_paths: list[str] | None = None) -> list[dict]:
    """「这个域的脚本要、你选的环境没有」的变量名。纯代码，不问模型。

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
        if name in defined or name in _AMBIENT or name in env_keys:
            continue
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", name):
            continue
        hits[name] = srcs[:6]
    for s in scripts:
        for name in _referenced_names(s.get("content") or ""):
            if name in defined or name in _AMBIENT or name in env_keys:
                continue
            # 夹具运行时拼出来的那一族（MB_ID / TA_TOKEN / …）
            if any(name.endswith(x) for x in suffixes):
                continue
            # 只看 SCREAMING_CASE：小写的基本都是脚本内部的临时变量
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", name):
                continue
            hits.setdefault(name, [])
            if s["path"] not in hits[name] and len(hits[name]) < 6:
                hits[name].append(s["path"])
    return [{"name": k, "scripts": v} for k, v in sorted(hits.items())]


# ── prompt ────────────────────────────────────────────────────

_SYSTEM = """你在评审一个黑盒验收仓里**某一个域**的自动化覆盖质量。

给你三样东西：这个域的场景清单（每条有 ID / 优先级 P / 风险分 R / 执行层 / 覆盖状态）、
声明覆盖了这些场景的**脚本正文**、以及这次选定的运行环境。

你只做三件判断，按重要性排：

1. **声明覆盖了、其实没验到**（最重要）。脚本头写 `@scenario AGT-11`，就等于宣称它验了
   AGT-11 描述的那件事。逐条对照场景描述读脚本正文：只发个请求看不看返回、
   只断状态码不看内容、断言恒真（`|| true`、`grep -q ""`）、改了数据不读回来确认、
   压根在验另一件事 —— 这些都算"声明了没验到"。**这一项是这次评审存在的理由**：
   仓库自己的门禁只能检查"有没有声明"，检查不了"有没有验到"。
2. **清单本身漏了什么**。这个域已有场景之间明显缺的一环（例如只有创建和查询、
   没有删除后的越权访问）。
3. **待补的那些先做哪条**。只在标记为「待补」的场景里挑，结合 P/R 和上面两条给顺序。

硬要求：
- 每条结论必须能指到**具体的场景 ID 或脚本路径**，并说清"哪一行/哪个动作"让你这么判。
  说不出来的宁可不说。
- **不要写放到哪个项目都成立的话**（"建议补充异常场景""缺少安全测试""覆盖率偏低"）。
  那种话说了等于没说。
- 不许建议我们去改这个仓库的流程、加字段、加钩子 —— 仓库是别人的，我们只读。
- 没截断的脚本才下"没验到"的结论；正文标了「已截断」的，拿不准就别列。
- 每一项最多 6 条。

只输出 JSON，不要任何解释：
```json
{
  "verdict": "ok | risky | bad",
  "summary": "两句话说清这个域的覆盖到底靠不靠得住",
  "scriptGaps": [
    {"id": "AGT-11", "path": "scenarios/agt/x.sh", "severity": "blocker|major|minor",
     "problem": "脚本只断了 HTTP 200，没有检查被挂起的 Agent 是否真被拒",
     "fix": "断言响应体 code == 403 且数据面返回为空"}
  ],
  "catalogGaps": [{"scenario": "...", "why": "..."}],
  "nextUp": [{"id": "AGT-19", "why": "..."}]
}
```"""

_STATE_CN = {"covered": "已覆盖", "gap": "待补", "deprecated": "已废弃"}


def build_payload(domain: dict, scenarios: list[dict], scripts: list[dict],
                  env_name: str, env_keys: list[str], env_missing: list[dict]) -> str:
    """拼给模型的 user 消息。**只放变量名，不放变量值。**"""
    head = f"域：{domain.get('code')} {domain.get('name') or ''}".strip()
    lines = [head, "", "## 场景清单"]
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
    if env_missing:
        lines.append("脚本引用了、这个环境里没有的变量名："
                     + "、".join(x["name"] for x in env_missing[:20]))
        lines.append("（这一条是代码算出来的，不用你再算；判「能不能跑起来」时可以直接用。）")

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
        out = []
        for x in (data.get(key) or [])[:6]:
            if isinstance(x, dict):
                out.append({k: str(v)[:600] for k, v in x.items() if v is not None})
            elif x:
                out.append({"problem": str(x)[:600]})
        return out

    verdict = str(data.get("verdict") or "").strip().lower()
    return {
        "verdict": verdict if verdict in ("ok", "risky", "bad") else "risky",
        "summary": str(data.get("summary") or "")[:800],
        "scriptGaps": _rows("scriptGaps"),
        "catalogGaps": _rows("catalogGaps"),
        "nextUp": _rows("nextUp"),
    }


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


async def run_review(*, domain: dict, scenarios: list[dict],
                     scripts: list[dict], env_name: str, env_keys: list[str],
                     lib_texts: list[str], lib_paths: list[str] | None = None,
                     ai_config=None) -> dict:
    """真正评一次。返回落库用的 result；抛异常交给调用方标 failed。"""
    missing = env_gaps(scripts, set(env_keys), lib_texts, lib_paths)
    user = build_payload(domain, scenarios, scripts, env_name, env_keys, missing)
    resp = await llm_client.complete(
        [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
        config=ai_config, max_tokens=2400, temperature=0)
    out = parse_result(resp.content or "")
    out["envMissing"] = missing
    out["reviewedScripts"] = [{"path": s["path"], "truncated": s["truncated"]} for s in scripts]
    # 页面要说清"这次读了多少"：只读了 14 份里的 5 份却说"这个域没问题"是骗人的
    out["scenarioCount"] = len(scenarios)
    return out


def spawn(coro) -> None:
    """后台跑。评一个域实测 20–60 秒，同步 POST 会把人钉在页面上（review-spec §5）。"""
    asyncio.create_task(coro)  # noqa: RUF006 — 生命周期由 _run 自己的 try/finally 兜住


def finish(review: QaCatalogReview, result: dict | None, error: str | None) -> None:
    review.status = "done" if error is None else "failed"
    review.result = result
    review.error = error
    review.finished_at = datetime.now(timezone.utc)


def to_dict(r: QaCatalogReview) -> dict:
    return {
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
        "result": r.result,
        "error": r.error,
        "createdAt": r.created_at.isoformat() if r.created_at else None,
        "finishedAt": r.finished_at.isoformat() if r.finished_at else None,
    }


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

            cfg_ai = await resolve_ai_config(project_id, session, capability="tb-quality-review")
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
