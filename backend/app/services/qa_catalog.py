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


# ── 列怎么认：看**列里的值长什么样**，不看列位 ────────────────────────
# 清单的列顺序是每个项目自己定的，手上两份就已经对不上：
#   uag-qa | ID | 场景 | P    | R    | 层   | 状   |
#   网关   | ID | 场景 | 类型 | 优先 | 状态 |          ← 少一列、顺序也不一样
# 写死列位的代价不是报错，是**读串了还一路绿**：网关那份把「类型」当成优先级、
# 把「状态」当成层，真正的状态列压根没被读到 → 268 行全判 gap，而 unparsedRows=0、
# duplicateIds=0、error=null，页面上每一盏健康灯都是绿的。（2026-08-30 实测）
# 现有的防线只防「行掉了」，不防「行读串了」—— 后者更常见也更难发现。
# 所以改成按值的形状认列：形状是清单自己带的，换个项目不用再兼容一次。
# **认不出来的列一律进 catalogIssues，绝不猜着填。**
_PLACEHOLDER_CELL = {"—", "–", "——", "-", "－", "/", "N/A", "n/a", "无", "TBD", "tbd", "?"}
# `P?` 也算优先级形状：那是**我们自己**生成清单行时写的"还没定"标记
# （`qa_coverage_reconcile._UNSET_PRIORITY`，故意不猜一个 P2 上去）。
# 不认它的话，提案行粘回清单后这一列认不出角色，「层」还会被它顶掉 ——
# 自己产出的东西自己读不回来，是最不该有的那种不兼容。
_PRIORITY_RE = re.compile(r"[Pp][0-3?]")
_RISK_RE = re.compile(r"\d{1,2}")

# 状态词表。匹配用**子串**不用全等 —— 状态格里常挂着一句话
# （`✅ @known-bug GL#531`、`不适用（见下）`），要求全等的话这些一个都认不出来。
# 命中多个词时取**位置最靠前**的那个，不是列表顺序：状态标记写在格子开头，
# 后面那截是备注。实测 uag-qa 的 MCP-79 状态格是
# 「✅ **@known-bug GL#580** … 该单**未被作废**…」—— 按列表顺序判就会被备注里的
# 「作废」拐去 deprecated，一条已覆盖的场景凭空变成已废弃（覆盖率还会跟着降）。
# 位置相同才按列表顺序，所以「已废弃」仍然是 deprecated 而不是被「已」系词捞走。
_STATE_TOKENS: tuple[tuple[str, str], ...] = (
    ("deprecated", "❌"), ("deprecated", "作废"), ("deprecated", "废弃"),
    ("deprecated", "不适用"), ("deprecated", "deprecated"),
    ("covered", "✅"), ("covered", "☑"), ("covered", "已建"), ("covered", "已覆盖"),
    ("covered", "已实现"), ("covered", "已写"), ("covered", "covered"), ("covered", "done"),
    ("gap", "⬜"), ("gap", "🔲"), ("gap", "☐"), ("gap", "未建"), ("gap", "待建"),
    ("gap", "待迁"), ("gap", "待补"), ("gap", "缺口"), ("gap", "todo"), ("gap", "gap"),
)
_STATE_SYMBOLS = ("✅", "⬜", "❌", "🔲", "☐", "☑")

# 表头名 → 角色。**只当补充判据**：值的形状认得出来就不看表头，因为表头本身也在变
# （光 uag-qa 一份里「场景/优先级/判据强度」就有三种写法，它没出事纯粹是列序恰好一致）。
_HEADER_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("state", ("状", "state", "status", "覆盖", "进度")),
    ("priority", ("优先", "priority", "prio", "级别", "p")),
    ("risk", ("判据", "风险", "risk", "强度", "r")),
    ("tier", ("层", "tier", "类型", "type", "位置", "kind")),
    ("title", ("场景", "描述", "说明", "名称", "标题", "title", "scenario", "name")),
)
# 表头行的首列长什么样（`| ID | 场景 | ... |` 里的那个 ID）
_HEADER_FIRST_CELL = {"id", "编号", "场景id", "用例id", "case", "caseid", "case id"}


def _bare_cell(value: str) -> str:
    """剥掉 markdown 装饰后的裸值；占位破折号一律归成空。

    占位符不归成空的话，「—」会被算进值域里 —— 筛选下拉多一个「—」选项事小，
    统计口径把「没填」当成一个真值事大。
    """
    v = value.strip().strip("`").strip()
    if v.startswith("**") and v.endswith("**") and len(v) > 4:
        v = v[2:-2].strip()
    v = v.strip("`").strip()
    return "" if v in _PLACEHOLDER_CELL else v


def _state_token(value: str) -> tuple[str, str] | None:
    """值里认出状态词 → (状态, 命中的词)。认不出来返回 None（**不是** gap）。"""
    low = value.lower()
    best = None
    for order, (state, token) in enumerate(_STATE_TOKENS):
        pos = value.find(token)
        if pos < 0:
            pos = low.find(token)
        if pos < 0:
            continue
        if best is None or (pos, order) < best[0]:
            best = ((pos, order), state, token)
    return (best[1], best[2]) if best else None


def _header_role(header: str) -> str:
    """表头名 → 角色。单字母表头（P/R）要求全等，否则「P」会在任何含 p 的表头上命中。"""
    h = header.strip().strip("`*").strip().lower()
    if not h:
        return ""
    for role, tokens in _HEADER_HINTS:
        for t in tokens:
            if h == t or (len(t) >= 2 and t in h):
                return role
    return ""


def _is_header_line(line: str) -> bool:
    return (line.lstrip().startswith("|")
            and _first_cell(line).lower().replace(" ", "") in _HEADER_FIRST_CELL)


def _row_cells(line_rest: str) -> list[str]:
    return [c.strip() for c in line_rest.split("|")]


def _cell_at(cells: list[str], idx, width: int) -> str:
    """取第 idx 格。**最后一格吸收溢出**。

    单元格里出现没转义的 `|` 时这一行会多切出几格（实测 uag-qa 有 2 行，多出来的
    都在状态列那句长备注里）。多出来的格子如果不管，要么被当成新列（造出两个只有
    2 行数据的幽灵列），要么整行错位。归给最右那一格是唯一不丢字的处理。
    """
    if idx is None or idx >= len(cells):
        return ""
    if idx == width - 1 and len(cells) > width:
        return " | ".join(cells[idx:])
    return cells[idx]


def _classify_columns(rows: list[dict], headers: list[str], width: int):
    """认列。返回 (角色 -> 列号, 每列是怎么认出来的, 没认出来的列)。"""
    stats: list[dict] = []
    for i in range(width):
        vals = [_bare_cell(_cell_at(r["cells"], i, width)) for r in rows]
        ne = [v for v in vals if v]
        n = len(ne)
        uniq = sorted(set(ne), key=lambda x: (-ne.count(x), x))
        stats.append({
            "index": i,
            "header": headers[i] if i < len(headers) else "",
            "n": n,
            "samples": uniq[:6],
            "fill": n / len(rows) if rows else 0.0,
            "distinct": len(uniq) / n if n else 0.0,
            "avgLen": sum(len(v) for v in ne) / n if n else 0.0,
            "p": sum(1 for v in ne if _PRIORITY_RE.fullmatch(v)) / n if n else 0.0,
            "d": sum(1 for v in ne if _RISK_RE.fullmatch(v)) / n if n else 0.0,
            "s": sum(1 for v in ne if _state_token(v)) / n if n else 0.0,
        })

    roles: dict[str, int] = {}
    notes: list[dict] = []
    taken: set[int] = set()

    def claim(role: str, st: dict, basis: str) -> None:
        # 一个角色只认第一次。重复 claim 会**静默改掉**已认定的列 —— 页面上
        # columnRoles 里会并排躺着两条同 role 的记录，不盯着看根本发现不了。
        if role in roles:
            return
        roles[role] = st["index"]
        taken.add(st["index"])
        notes.append({"index": st["index"], "header": st["header"],
                      "role": role, "basis": basis})

    def best_by(key: str, thr: float):
        pool = [s for s in stats if s["index"] not in taken and s["n"] and s[key] >= thr]
        return max(pool, key=lambda s: s[key], default=None)

    # 判别力从强到弱。三个都是"要么整列中要么整列不中"的形状 —— 实测两份真清单上
    # 命中列 1.00、其余列 ≤0.05，0.6 这条线中间是空的，不是拍脑袋定的。
    for role, key, thr, desc in (("state", "s", 0.6, "%d%% 的值是状态词"),
                                 ("priority", "p", 0.6, "%d%% 的值形如 P0-P3"),
                                 ("risk", "d", 0.6, "%d%% 的值是一两位数字")):
        st = best_by(key, thr)
        if st is not None:
            claim(role, st, desc % round(st[key] * 100))

    # **有名字、但名字不是我们任何一个角色**的列（`负责人`、`备注`）：这不是
    # "没信息"，是一条正面证据 —— 清单自己说了它是什么，而那不是我们要的东西。
    # 所以它不参加下面任何一轮"猜"，直接进 unresolvedColumns 由页面报出来。
    named_other = {s["index"] for s in stats
                   if s["header"].strip() and not _header_role(s["header"])}

    # 标题和层用**表头名**先分一次。这两个角色的"形状"是分不开的（都是自由文本），
    # 而 P/R/状态那三列的形状是决定性的，所以只有这里需要借表头。实测：`| ID | 场景 |
    # 优先级 | 判据强度 | 层 | 状态 |` 这种小表上，`层` 列（api/ui）唯一值率同样是
    # 1.00、还比一两个字的场景名更长 —— 光比形状会把「层」当成场景描述。
    for role in ("title", "tier"):
        if role in roles:
            continue
        st = next((s for s in stats
                   if s["index"] not in taken and s["n"] and _header_role(s["header"]) == role),
                  None)
        if st is not None:
            claim(role, st, "表头写的是「%s」" % st["header"])

    # 场景描述：唯一值最多、**而且每行都填了**的那列（实测命中列 1.00，其余 ≤0.22）。
    # 「每行都填了」这条不能省：只剩三五行的小表上，一个只填了一格的枚举列
    # （比如整表只有一行写了 `smoke`、其余是占位破折号）唯一值率同样是 1.00，
    # 光比唯一值率就会把「层」当成场景描述 —— 然后标题列整列消失。
    pool = [] if "title" in roles else [
        s for s in stats
        if s["index"] not in taken and s["index"] not in named_other
        and s["distinct"] >= 0.5 and s["fill"] >= 0.5]
    st = max(pool, key=lambda s: (s["distinct"] * s["fill"], s["avgLen"], -s["index"]),
             default=None)
    if st is not None:
        claim("title", st, "%d%% 是唯一值、%d%% 的行都填了、平均 %d 字"
              % (round(st["distinct"] * 100), round(st["fill"] * 100), round(st["avgLen"])))

    # 形状认不出来的，再看表头名
    for s in stats:
        if s["index"] in taken or not s["n"]:
            continue
        role = _header_role(s["header"])
        if role and role not in roles:
            claim(role, s, "表头写的是「%s」" % s["header"])

    # 「层」是个枚举（api/ui/smoke）：**值域小、值还短**，两条一起才是它的特征。
    # 值域用**绝对个数**不用比率：比率在小表上必然失效 —— 只有一两行时每列的唯一值率
    # 都是 1.00，`≤0.3` 那条线一行都框不住（实测 S7.6 把生成的行喂回来验收时就是
    # 单行无表头，层列整个读不到）。「值还短」是防它去捞备注那种自由文本列。
    if "tier" not in roles:
        pool = [s for s in stats
                if s["index"] not in taken and s["index"] not in named_other and s["n"]
                and len(set(s["samples"])) <= max(3, round(s["n"] * 0.3))
                and s["avgLen"] <= 12]
        st = min(pool, key=lambda s: (len(set(s["samples"])), s["avgLen"]), default=None)
        if st is not None:
            claim("tier", st, "值域只有 %d 种、平均 %d 字，按枚举列当「层」"
                  % (len(set(st["samples"])), round(st["avgLen"])))

    # 标题一个都没认出来时的兜底：剩下最长的那列。**宁可认错也不能整份没标题** ——
    # 标题空掉的页面看着像"清单是空的"，而那是最容易被当成"QA 仓没东西"的假象。
    if "title" not in roles:
        st = max([s for s in stats if s["index"] not in taken and s["n"]],
                 key=lambda s: s["avgLen"], default=None)
        if st is not None:
            claim("title", st, "兜底：剩下的列里最长的那列")

    unresolved = [{"index": s["index"], "header": s["header"],
                   "count": s["n"], "samples": s["samples"]}
                  for s in stats if s["index"] not in taken and s["n"]]
    return roles, notes, unresolved


def _state_note(raw: str, token: str | None) -> str:
    """状态格里除了状态本身还常挂一句话（`@known-bug GL#531`、`（见下）`），留着。"""
    note = raw
    for sym in _STATE_SYMBOLS:
        note = note.replace(sym, "")
    note = note.strip().strip("`").strip()
    if token and len(token) > 1 and note.lower().startswith(token.lower()):
        note = note[len(token):].strip()
    return note.strip("`").strip()


def parse_catalog(text: str, claimed_ids: set[str] | None = None) -> tuple[list[dict], dict[str, dict], dict]:
    """解析清单 markdown。返回 (场景行, 域码->{name,groups,groupsRaw}, 读不进来的东西)。

    只认「场景清单」正文里的行；统计段里那张"已实现清单"表首列是层级不是 ID，
    天然不会命中 _ROW_RE，所以不需要额外切段。

    `claimed_ids` 是**脚本头声明过的场景 ID**，只用来给状态词表兜底（见下面的
    「反推」一节）。不传也能跑，只是遇到没见过的状态词时只能判缺口。

    ⚠ 第三个返回值是**这次没读懂什么**，必须一路带到页面上。少一行的后果是
    「那条场景不存在」—— 覆盖率不会掉、缺口不会涨、门禁不会红，谁都发现不了。
    读串一列更狠：数字全在、全是错的。四类：首列像 ID 但整行没解析成（漏了尾部的
    `|`、破折号打成 `–`、大小写写错）、同一个 ID 出现两次（保留第一条）、
    有列认不出角色、有状态词不在词表里。
    """
    scenarios: list[dict] = []
    domains: dict[str, dict] = {}
    groups_unreadable: list[dict] = []
    seen: set[str] = set()
    unparsed: list[dict] = []
    duplicates: list[str] = []

    # 第一趟：只把行原样收下来，不碰列的含义 —— 认列要看整列的分布，
    # 边读边判就只能看单行，那正是"写死列位"的老路。
    raw_rows: list[dict] = []
    headers: list[str] = []
    header_votes: dict[int, dict[str, int]] = {}

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

        if _is_header_line(line):
            cells = _row_cells(line.strip().strip("|"))
            for i, cell in enumerate(cells[1:]):  # 跳过首列 ID
                header_votes.setdefault(i, {}).setdefault(cell.strip(), 0)
                header_votes[i][cell.strip()] += 1
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
        raw_rows.append({"id": sid, "cells": _row_cells(m.group("rest"))})

    for i in sorted(header_votes):
        best = max(header_votes[i].items(), key=lambda kv: kv[1])
        headers.append(best[0])

    # 列宽以**表头声明的列数**为准；没有表头行才退回数据行的众数。
    # 两处都不能取最大值：单元格里混了个没转义的 `|` 的那几行会把宽度顶大，
    # 造出只有两三行数据的幽灵列（实测 uag-qa 有 2 行这样的备注）。
    # 众数在真清单上够用（526 : 2），但小表上会平票 —— 平票时取**小**的那个，
    # 因为溢出只会让格子变多、不会变少。
    if headers:
        width = len(headers)
    else:
        width_votes: dict[int, int] = {}
        for r in raw_rows:
            width_votes[len(r["cells"])] = width_votes.get(len(r["cells"]), 0) + 1
        width = max(width_votes.items(), key=lambda kv: (kv[1], -kv[0]))[0] if width_votes else 0

    roles, column_notes, unresolved_columns = _classify_columns(raw_rows, headers, width)

    # 第二趟：按认出来的角色取值
    pending: list[tuple[dict, str]] = []
    for r in raw_rows:
        cells = r["cells"]
        title = _cell_at(cells, roles.get("title"), width).strip()
        priority = _bare_cell(_cell_at(cells, roles.get("priority"), width))
        risk_raw = _bare_cell(_cell_at(cells, roles.get("risk"), width))
        tier = _bare_cell(_cell_at(cells, roles.get("tier"), width))
        state_raw = _cell_at(cells, roles.get("state"), width).strip()

        hit = _state_token(state_raw) if state_raw else None
        item = {
            "id": r["id"],
            "domain": r["id"].rsplit("-", 1)[0],
            "title": title,
            "priority": priority.upper() if _PRIORITY_RE.fullmatch(priority) else priority,
            "risk": int(risk_raw) if risk_raw.isdigit() else None,
            "tier": tier,
            "state": hit[0] if hit else "",
            "stateNote": _state_note(state_raw, hit[1] if hit else None),
        }
        scenarios.append(item)
        if not hit:
            pending.append((item, _bare_cell(state_raw)))

    # ── 状态没认出来时怎么办：**别默认判 gap** ──────────────────────────
    # 默认 gap 正是网关那 268 行整份变成缺口的原因 —— 一个"没读懂"被写成了一个
    # 确定的结论，之后页面上再也看不出这里发生过什么。改成拿「有没有脚本声明过
    # 这条场景」反推。反推是**按词整体投票**，不是逐行照抄脚本：某个没见过的词
    # 多数行都有脚本 → 这个词的意思是"已覆盖"；个别行的"清单说有、脚本没有"
    # 照样会被 claimedButUncovered 抓出来，说谎检测这条线不会被反推抹平。
    claimed = claimed_ids or set()
    unknown_tokens: list[dict] = []
    if "state" not in roles:
        # 整份清单认不出状态列。逐行看脚本认领 —— 并且把这件事亮到页面上。
        for item in scenarios:
            item["state"] = "covered" if item["id"] in claimed else "gap"
        if scenarios:
            unknown_tokens.append({
                "token": "(没有状态列)", "count": len(scenarios),
                "resolvedAs": "按脚本认领逐行反推",
                "basis": "这份清单里认不出状态列，覆盖与否只能看脚本有没有声明它",
            })
    elif pending:
        buckets: dict[str, list[dict]] = {}
        for item, token in pending:
            buckets.setdefault(token[:32] or "(空)", []).append(item)
        for token, group in sorted(buckets.items()):
            hits = len([s for s in group if s["id"] in claimed])
            if claimed and hits * 10 >= len(group) * 6:
                resolved = "covered"
            else:
                resolved = "gap"
            for item in group:
                item["state"] = resolved
            unknown_tokens.append({
                "token": token, "count": len(group), "resolvedAs": resolved,
                "basis": ("%d/%d 行有脚本认领" % (hits, len(group))) if claimed
                         else "没有脚本可反推，只能当缺口",
            })

    return scenarios, domains, {
        "unparsedRows": unparsed,
        "duplicateIds": duplicates,
        "domainGroupsUnreadable": groups_unreadable,
        # 这两条不是"错误"，是**这份清单是怎么被读的**。读串了的时候，健康灯全绿
        # 是最要命的 —— 所以把认列结果本身也摆出来，让人一眼能对：
        # 「状态 = 第 4 列（表头『状态』）」对不上就是对不上。
        "columnRoles": column_notes,
        "unresolvedColumns": unresolved_columns,
        "unknownStateTokens": unknown_tokens,
    }


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



# ---- 「这个域最近在动吗」：谁最后改的、什么时候 ----

# 一次 log 走完整个历史，取每个路径**第一次出现**的那个提交 —— 提交按时间倒序出来，
# 所以第一次出现 = 最后一次改它。**别退化成「一个路径一次 `git log -1`」**：
# 152 个脚本就是 152 次进程启动，比整趟走一遍慢两个数量级，结果一模一样。
# 上限是防「仓库十万个提交时把内存和超时一起撑爆」，不是判据；真撞上了如实说
# （`activityTruncated`），别让「走到一半停了」长得像「这个域从来没人动过」——
# 那正好是本页最要紧的那句话说反了。
_ACTIVITY_MAX_COMMITS = 5000

# blame --line-porcelain 的行头：`<40 位 sha> <原行号> <新行号> [同组行数]`
_BLAME_HEAD_RE = re.compile(r"^([0-9a-f]{40}) \d+ \d+")


def _repo_activity(repo: Path, ref: str, catalog_path: str) -> dict:
    """读出两份时间线，**分开放，不合并**：

      · `paths`  路径 → 最后改它的提交（脚本侧：真有人在写用例）
      · `rows`   场景 ID → 最后改这一行的提交（清单侧：有人在改计划）

    为什么必须分两份：uag-qa 实测 2026-08-27 20:42 有一次**整体导入**，一个提交
    把 24 个域的清单行全刷了一遍。两份合成一个 max 之后，24 个域显示同一个时间、
    「最近更新」标记全亮 —— **一个恒真的标记比没有标记更坏，它看着像信息**。
    分开之后「脚本侧 8-29 / 清单侧 8-27」一眼分得出「真在写」和「只是被那次导入扫到」。
    """
    commits: dict[str, dict] = {}
    paths: dict[str, str] = {}
    try:
        out = _run_git([
            "--git-dir", str(repo), "log", f"--max-count={_ACTIVITY_MAX_COMMITS}",
            "--name-only", "--format=%x01%H%x02%cI%x02%s", ref,
        ])
    except GitError as e:
        # 时间读不到不该把整页拖垮（清单和覆盖率跟它无关）：这次不给时间，页面上说明白
        logger.warning("qa activity: log 走不通(%s)，这次不给时间", e)
        return {"paths": {}, "rows": {}, "commits": {}, "truncated": False, "unavailable": True}

    sha = ""
    for line in out.splitlines():
        if line.startswith("\x01"):
            sha, _, rest = line[1:].partition("\x02")
            date, _, subject = rest.partition("\x02")
            commits[sha] = {"sha": sha[:9], "date": date, "subject": subject}
            continue
        # 合并提交默认不列文件：文件真正落地的那个提交照样在历史里，日期取它自己的
        if line and sha and line not in paths:
            paths[line] = sha

    rows = _blame_catalog_rows(repo, ref, catalog_path, commits) if catalog_path else {}
    return {
        "paths": paths,
        "rows": rows,
        "commits": commits,
        "truncated": len(commits) >= _ACTIVITY_MAX_COMMITS,
        "unavailable": False,
    }


def _blame_catalog_rows(repo: Path, ref: str, catalog_path: str,
                        commits: dict[str, dict]) -> dict[str, str]:
    """清单里每条场景行**最后一次被改**是哪个提交（`git blame`，只读）。

    没有脚本的域（实测 uag-qa 有 6 个是 0 脚本）在脚本侧永远是空的，只有这一份能
    回答它们「是刚立项还是躺了半年」。一次逐行 blame 拿全，不按域分别 blame ——
    那是 24 次进程启动换同一份数据。
    """
    try:
        out = _run_git(["--git-dir", str(repo), "blame", "--line-porcelain", ref,
                        "--", catalog_path])
    except GitError as e:
        logger.warning("qa activity: blame 走不通(%s)，清单侧这次没有时间", e)
        return {}

    rows: dict[str, str] = {}
    sha = ""
    ct: int | None = None
    for line in out.splitlines():
        m = _BLAME_HEAD_RE.match(line)
        if m:
            sha, ct = m.group(1), None
            continue
        if line.startswith("committer-time "):
            try:
                ct = int(line.split()[1])
            except (IndexError, ValueError):
                ct = None
            continue
        if not line.startswith("\t"):
            continue
        # 正文行：首列是场景 ID 才算数（域码表、统计表、散文都不是）
        sid = _first_cell(line[1:])
        if not _ID_RE.fullmatch(sid):
            continue
        if sha in commits:
            rows[sid] = sha
        elif ct is not None:
            # 提交落在 max-count 之外：log 那趟没收进来，退回 blame 自己报的时间。
            # 标题拿不到（blame 不给），留空 —— 空标题比编一个准确
            commits.setdefault(sha, {
                "sha": sha[:9],
                "date": datetime.fromtimestamp(ct, tz=timezone.utc).astimezone().isoformat(timespec="seconds"),
                "subject": "",
            })
            rows[sid] = sha
    return rows

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

    # 先捞脚本、再解析清单。顺序是**故意**的：清单里出现没见过的状态词时，
    # parse_catalog 要拿「有没有脚本声明过这条场景」来反推它是什么意思。
    # 反过来放（先解析清单）就只能把没读懂的词一律判成缺口 —— 那正是要修的 bug。
    # 脚本发现只依赖 repo/ref/catalog_path，不依赖解析结果，所以提前没有代价。
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

    claimed_ids = {sid for c in cases for sid in c["ids"]}
    scenarios, domain_names, catalog_issues = parse_catalog(catalog_text, claimed_ids)

    activity = _repo_activity(repo, ref, catalog_path)

    return _assemble(scenarios, domain_names, cases, catalog_issues, activity=activity, repo_meta={
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


def _parse_iso(value: str):
    """git 的 `%cI` 在 UTC 提交上给的是 `...Z`，别的时区给 `+08:00`。

    **不能拿字符串直接比大小**：`2026-08-29T09:00:00Z`（= 北京 17:00）比
    `2026-08-29T10:00:00+08:00` 字典序大，但它其实更早。混着两种写法的仓库
    （实测两家 QA 仓一家 Z 一家 +08:00）会因此把"谁最近"排反，而排反了不报错。
    """
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _latest(commits: dict[str, dict], shas) -> dict | None:
    """一堆提交里最新的那个（按真实时刻比，见 `_parse_iso`）。"""
    best = None
    best_at = None
    for sha in shas:
        c = commits.get(sha)
        at = _parse_iso((c or {}).get("date", ""))
        if at is None:
            continue
        if best_at is None or at > best_at:
            best, best_at = c, at
    return best


def _domain_activity(domain_scen: list[dict], activity: dict | None) -> dict:
    """一个域的「最近有人动」——**脚本侧和清单侧分开给，不取合并的 max**。

    合并的坏处见 `_repo_activity` 的注释（整体导入会把所有域刷成同一天）。
    显示用哪个由前端决定，这里只负责如实给两个数：
      · 脚本侧 = 这个域的场景被哪些脚本覆盖，那些文件最后一次被改
      · 清单侧 = 这个域在清单里的那些行最后一次被改
    `updatedAt` 是给「一列显示不下两个」的地方用的合成值：**优先脚本侧** ——
    有人在写用例才叫"在做这个域"，清单被一次批量重排扫到不算。
    """
    act = activity or {}
    commits = act.get("commits") or {}
    paths = act.get("paths") or {}
    rows = act.get("rows") or {}

    script_shas = {
        paths[c["path"]]
        for s in domain_scen for c in (s.get("scripts") or [])
        if c["path"] in paths
    }
    row_shas = {rows[s["id"]] for s in domain_scen if s["id"] in rows}

    by_script = _latest(commits, script_shas)
    by_catalog = _latest(commits, row_shas)
    winner = by_script or by_catalog
    return {
        "scriptUpdatedAt": (by_script or {}).get("date"),
        "scriptCommit": by_script,
        "catalogUpdatedAt": (by_catalog or {}).get("date"),
        "catalogCommit": by_catalog,
        "updatedAt": (winner or {}).get("date"),
        "updatedFrom": "script" if by_script else ("catalog" if by_catalog else ""),
    }


def _assemble(scenarios: list[dict], domain_meta: dict[str, dict], cases: list[dict],
              catalog_issues: dict, repo_meta: dict, activity: dict | None = None) -> dict:
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

    # 逐条场景的更新时间：清单行和覆盖脚本取更晚的那次改动，两个分量也一起带出去 ——
    # 「脚本三个月没动、清单昨天刚改」和「两边一起改的」是两回事，合成一个数就看不出来了。
    # 取数走 `_repo_activity` 那一份（域级那一列也用它）：两个视角、**一份数据**。
    # 这里曾经有过自己的一套 `_file_mtimes` / `_catalog_row_mtimes`，跟它扫的是同一段
    # 历史、只是输出格式不同 —— 留着就是每次同步多打两趟 git，且两份数一旦对不上，
    # 「这一行显示 8-27、它所在的域显示 8-29」没人查得清是哪份错了。
    commits = (activity or {}).get("commits") or {}
    paths = (activity or {}).get("paths") or {}
    rows = (activity or {}).get("rows") or {}
    for s in scenarios:
        row_shas = [rows[s["id"]]] if s["id"] in rows else []
        script_shas = [paths[c["path"]] for c in s["scripts"] if c["path"] in paths]
        s["rowUpdatedAt"] = (_latest(commits, row_shas) or {}).get("date")
        s["scriptUpdatedAt"] = (_latest(commits, script_shas) or {}).get("date")
        s["updatedAt"] = (_latest(commits, row_shas + script_shas) or {}).get("date")

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
        # 「这个域最近有人动吗」—— 已废弃的场景也算：它被废掉本身就是这个域的动静
        domain_scen = [s for s in scenarios if s["domain"] == code]
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
            **_domain_activity(domain_scen, activity),
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
            # 「这次有没有读串」。上面两条防的是"行掉了"，这两条防的是"行读串了" ——
            # 后者更常见也更毒：数字全在、全是错的。0 同样要出现在页面上。
            "unresolvedColumns": len(catalog_issues.get("unresolvedColumns") or []),
            "unknownStateTokens": len(catalog_issues.get("unknownStateTokens") or []),
            # 「更新时间」那一列这次算没算成 / 有没有走到底。0 也要出现，理由同上两条
            "activityUnavailable": bool((activity or {}).get("unavailable")),
            "activityTruncated": bool((activity or {}).get("truncated")),
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
