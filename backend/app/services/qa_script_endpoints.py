"""Q 边补全：QA 脚本里那些**封装在 helper 里**的调用。

`qa_coverage_reconcile.extract_endpoints` 只认写在行里的 url（`${API}/x`、`curl …`）。
而 UAG 那个仓几乎全走自家 helper：

    api_get "/agents/${ID}"
    api_json_code POST "/teams" "$body"
    api_json_code_as "$tok" DELETE "/teams/${TID}" '{}'

这些行**既不是命中也不是漏读，是整个看不见** —— 不进 hits，也不进
`endpointsUnextracted`（`_CALL_HINT` 只认 `curl|http|wget`）。后果不是少几条：
实测全仓 369 个脚本（`refs/remotes/origin/main`），旧解析器命中 136，
认出 helper 之后 2943。于是 P 边上几乎
每个端点都"没人测过"，G1/G3 变成一片假缺口，而账本上一个字都不欠。
**报告第一版就会全是噪声，然后没人再看第二版。**

## 参数位置为什么从他们的源码现场解析，不写死一张表

写死 `api_json_code = (方法在$1, 路径在$2)` 能跑，但它会**漂移**：对方改一次
helper 签名，我们这边照旧按老位置取参 —— 取到的是 token 或 body，
`normalize_path` 照样能把它变成一条像路径的东西，然后**它会去 `covers()` 里
碰运气**。抽错路径不报错，只是让一个真缺口凭空消失（那正是
`qa_coverage_reconcile` 头部「宁可漏报不可误报」算过的那个反号代价）。

所以 `parse_helper_lib()` 读 `lib/*.sh`，自己推出「路径在第几个位置参数、
方法在第几个」。**推不出来的 helper 不猜位置**，进 `unparsed`，
它的调用点一律记成漏读。

## 四个桶，不是两个

- `hits` —— 位置读出来了、路径是字面量、方法是具体方法。
- `misses` —— 读不出来（路径是变量、方法是变量、helper 没解析出来）。**账本项。**
- `otherBase` —— 打的不是 BFF（`${GW}` Kong、`${AI}`、`${GW_ADMIN}`）。
- `infra` —— **路径写死在 helper 里**的那些（`require_login` 探
  `/admin-users?page=1&page_size=1`、`login` 打 `/api/auth/login`、
  `facade_endpoints` 打 `/api/docs/routes`）。

后两个桶是这一版新加的两条闸门，不是分类癖好：

**`otherBase`** —— R 边是 BFF 的 `/api/docs/routes`，而 `covers()` 的后缀匹配
能吃掉 2 段前缀，于是 `gw_call "/v1/chat/completions"` 会**盖住** BFF 的
`/api/v1/chat/completions`（`a=[v1,chat,completions]` vs
`b=[api,v1,chat,completions]`，差 1 段、尾部逐段相等）。一个 Kong 调用把一个
BFF 端点标成"测过了"，缺口消失、没有任何一条测试会红。
（他们 `AI=${GW}/ai/v1` 也在这一类。）

**`infra`** —— `require_login` 在 369 个脚本里被调了 359 次，它内部固定探
`/admin-users?page=1&page_size=1` 来看 token 活没活。按"它确实发了这个请求"
算的话，`/admin-users` 就成了「359 个脚本都在测」——**这是最典型的误报**：
那个端点真挂了，359 条里可能一条都不会红（探活只看 token 不看它的业务语义）。
判据很干净：**Q 边只认脚本自己指定路径的调用**；路径写死在 helper 内部的，
是登录/探活/自检管道，记账不计覆盖。

⚠ **方法读不出来一律进 misses，不留空方法。**
`compute_gaps._covered()` 里空方法**匹配任何方法**
（`if qm and method and qm != method.upper(): continue`）——
一条 `("", "/agents/{}")` 会把这个路径上的 DELETE 也算成测过了。
「不知道打的什么方法」和「什么方法都打过」差着一个真缺口。
"""
from __future__ import annotations

import re
import shlex

from app.services.branch_diff_service import WILDCARD, normalize_path

# 这些 base 展开后落在 BFF 上，路径可以直接跟 R 边（`/api/docs/routes`）比。
# 实读 UAG `config/env.sh`：`API=${BFF}/api/v1`、`AUTH=${BFF}/api/auth`。
# `covers()` 吃 2 段前缀，够覆盖这两种。
BFF_BASES = ("API", "BFF", "AUTH")

# 这些打的是别的东西。**不是漏读，是口径外** —— 单独记账，绝不进 hits。
# 实读：`GW` = Kong proxy、`AI=${GW}/ai/v1`、`GW_ADMIN` = Kong Admin、`WEB_URL` = 前端。
OTHER_BASES = ("GW", "GW_ADMIN", "AI", "DP", "WEB_URL", "WEB")

_FUNC_DEF = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{", re.M)
_LOCAL_DECL = re.compile(r"\b(?:local|declare)\s+(.+)")
_ASSIGN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=\"?\$\{?(\d+)\}?\"?")
# `"${API}${path}"` / `"${BFF}$1"` / `"${AUTH}/login"`
_URL_BUILD = re.compile(r"\$\{(?P<base>[A-Z_][A-Z0-9_]*)\}(?P<rest>[^\s\"']*)")
_X_FLAG = re.compile(r"-X\s+\"?(?:\$\{?(?P<var>[A-Za-z_][A-Za-z0-9_]*)\}?|(?P<lit>[A-Za-z]{3,7}))\"?")
_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
_LITERAL_METHOD = re.compile(r"^(?:%s)$" % "|".join(_METHODS), re.I)
_VAR_TOKEN = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")
_OPS = {"|", "||", "&&", ";", ";;", ">", ">>", "<", "&", ")", "(", "\n"}


def _body_of(text: str, brace: int) -> str:
    """函数体。`brace` = `{` 后面那个下标。

    **必须认单行写法** —— 他们 `lib/common.sh` 里就有：

        bff_get()  { curl -s ... "${BFF}$1" "${@:2}"; }

    只找 `\\n}` 的话，这个函数的"正文"会一路吞到后面某个多行函数的收尾大括号
    （实测吞了 40 行，把一整段错误信封注释和另外三个函数都算进来）——
    于是 base、方法、参数位置全是从别人身上读的。

    多行的那半故意**不做花括号配平**：shell 里 `}` 出现在行首几乎只有收尾，
    而配平器会被 `${x}`、`awk '{print}'`、`jq '{a:1}'` 带偏。
    **读少一点比读歪好**：读少了这个 helper 进 `unparsed`，
    读歪了它会给出一个错的参数位置，而那个不报错。
    """
    nl = text.find("\n", brace)
    first = text[brace:nl if nl > 0 else len(text)]
    if "}" in first:
        return first[:first.rindex("}")]
    end = text.find("\n}", brace)
    return text[brace:end if end > 0 else brace + 2000]


def _positions(body: str) -> dict[str, int]:
    """变量名 → 它绑的是第几个位置参数。

    位置从 1 数起，跟 shell 的 `$1` 对齐 —— 别改成 0 起，
    这个偏移错了会稳定取到前一个参数（`_as` 那批就会拿 token 当路径），
    而且看着很像能跑。
    """
    pos: dict[str, int] = {}
    for decl in _LOCAL_DECL.findall(body):
        for var, n in _ASSIGN.findall(decl):
            pos.setdefault(var, int(n))
    return pos


_STMT_SEP = re.compile(r"(?<!\\)[\n;|&`()]")


def _stmt_of(body: str, at: int) -> str:
    """`at` 落在的那**一条命令**（前后各切到最近的 `;`/`|`/`&`/换行/`$(`）。

    要它是因为 `-X` 得跟 url 在**同一条命令**里才说明是这个 url 的方法。
    整个函数体里搜 `-X` 的话，`cleanup() { curl "${API}${path}"; curl -X DELETE
    "${API}/other/$id"; }` 会把第二条的 DELETE 记到第一条那个调用方路径上 ——
    **一条只被 GET 打过的路径被记成 DELETE 测过了**，这是误报那一侧。
    """
    ms = [m.end() for m in _STMT_SEP.finditer(body[:at])]
    start = ms[-1] if ms else 0
    m = _STMT_SEP.search(body, at)
    return body[start:(m.start() if m else len(body))]


def _resolve_method(body: str, pos: dict[str, int], url_at: int) -> tuple[str, int] | None:
    """→ `(字面方法, 0)` / `("", 第几个位置参数)` / `None`（推不出来）。

    四条，按优先级：

    ① `-X "$m"` / `-X POST` —— curl 的标准写法。
    ② **紧挨 url 前面那个词** —— 他们大量用 `http_once "$m" "${API}${path}"`
       和 `http_once POST "${API}${path}"`，方法是**位置参数**不是 `-X`。
       只看 `-X` 的话 `mcpb_post_as` 会被判成 GET（名字里明写着 post，76 个调用点
       全变成假的 GET 覆盖，而它真正打的 POST 端点则显示没人测）。
    ③ 正文里有 `curl` 且没上面两种 —— curl 默认 GET。**这是 curl 的语义，
       不是我们的猜测**（`api_get`/`bff_get` 就是这一类）。
    ④ 其它 —— 不猜，`None`。转手给别的 helper 的（`crud_cycle` 那种复合流程）
       落这里，进 `unparsed`。
    """
    stmt = _stmt_of(body, url_at)
    # 「整个函数体只拼了一处 BFF url」时，跨句找 `-X`/`curl` 不会串台
    # （`local url="${API}${path}"` 换行再 `curl -X "$m" "$url"` 就是这个形状）。
    # 多处拼 url 的时候必须锁在本句 —— 实测对方 lib 里这类只有 `login` 一个，
    # 且它同句就带 `-X`，所以这条收紧在真实数据上零代价。
    lone = len([u for u in _URL_BUILD.finditer(body)
                if u.group("base") in BFF_BASES]) == 1
    xm = _X_FLAG.search(stmt) or (_X_FLAG.search(body) if lone else None)
    if xm:
        if xm.group("lit"):
            return xm.group("lit").upper(), 0
        v = xm.group("var")
        return ("", pos[v]) if v in pos else None

    # ⚠ 先把 url 参数**自己的开引号**剥掉再切词。`http_once "$m" "${API}${path}"`
    # 里 `url_at` 落在 `${API}` 上，前缀是 `... http_once "$m" "`，
    # `split()[-1]` 拿到的是那个孤立的 `"` —— 于是方法读不出来、整条 helper 掉进
    # `unparsed`。实测这一个字符埋掉了 `mcpb_post_as`(89 处)、
    # `api_json_once_as`(44 处) 两批调用点，而表现是"这些端点没人测"。
    head = body[:url_at].rstrip("\"' \t\\\n")
    # ⚠ 只认「这一句的**第一个参数**」，不是"前面那个词"。
    # `api_get_as` 的正文是 `curl … -H "Authorization: Bearer $tok" "${API}${path}"`,
    # url 前面那个词是 `$tok` —— 而 `tok` 恰好也在位置参数表里（$1），
    # 于是**鉴权 token 被当成方法**：`api_get_as` 变成"方法在位置1"，
    # 它所有调用点的方法都读成 `$tok`、一律记漏读，一整批 GET 覆盖凭空消失。
    # 判据换成结构性的：切到本句（`;`/`|`/`&`/`$(`/换行 为界）后必须正好是
    # `命令 方法` 两个词 —— `http_once "$m" "${API}…"` 满足，
    # 挂在 `-H` 后面的那个 token 不满足。
    seg = re.split(r"[\n;|&()]", head)[-1].split()
    prev = seg[1].strip("\"'") if len(seg) == 2 else ""
    if _LITERAL_METHOD.match(prev):
        return prev.upper(), 0
    vm = _VAR_TOKEN.match(prev)
    if vm and vm.group(1) in pos:
        return "", pos[vm.group(1)]

    if "curl" in stmt or (lone and "curl" in body):
        return "GET", 0
    return None


def _pos_of_token(tok: str, pos: dict[str, int]) -> int:
    """`"${path}${sep}page=1"` / `"$2/x"` → 它开头那个变量是第几个位置参数。

    只看**开头**：路径必须从这个变量起头才算"路径由调用方给"，
    `"/teams/${tid}/agents"` 那种（变量在中间）是 helper 自己定的路径。
    """
    m = re.match(r"\$\{?([A-Za-z_0-9]+)\}?", tok or "")
    if not m:
        return 0
    v = m.group(1)
    if v.isdigit():
        return int(v)
    return pos.get(v, 0)


def _inherit_method(spec: dict, args: list[str], pos: dict[str, int]) -> tuple[str, int] | None:
    """转手调用时，方法从被调 helper 那边继承过来。"""
    if spec.get("methodPos"):
        tok = (_pick(args, spec["methodPos"]) or "").strip()
        if _LITERAL_METHOD.match(tok):
            return tok.upper(), 0
        p = _pos_of_token(tok, pos)
        return ("", p) if p else None
    return ((spec.get("method") or ""), 0) if spec.get("method") else None


def _classify_direct(name: str, body: str, pos: dict[str, int],
                     file: str) -> tuple[str, object]:
    """自己拼 URL 的那一类 → `("helper"|"infra"|"other"|"unparsed"|"", spec/理由)`。

    `""` = 这个函数正文里没有任何 `${BASE}` 拼接，**留给转手那一趟**。
    """
    base, rest, url_at = "", "", -1
    for um in _URL_BUILD.finditer(body):
        b = um.group("base")
        if b in OTHER_BASES:
            return "other", b
        if b in BFF_BASES:
            base, rest, url_at = b, um.group("rest"), um.start()
            break
    if not base:
        return "", ""

    # 路径那一段：是位置参数（`${path}` / `$1`）还是写死的（`/login`）
    path_pos, path_literal = 0, ""
    if rest.startswith("/"):
        path_literal = rest
    else:
        path_pos = _pos_of_token(rest, pos)
    if not path_pos and not path_literal:
        return "unparsed", "路径参数认不出来"

    meth = _resolve_method(body, pos, url_at)
    if meth is None:
        return "unparsed", "方法认不出来"
    method, method_pos = meth

    if path_literal:
        return "infra", {"base": base, "method": method, "methodPos": method_pos,
                         "pathLiteral": path_literal, "file": file}
    return "helper", {"base": base, "pathPos": path_pos, "method": method,
                      "methodPos": method_pos, "file": file}


def _classify_delegated(name: str, body: str, pos: dict[str, int], file: str,
                        known: dict) -> tuple[str, object]:
    """自己**不拼 URL**、转手调别的 helper 的那一类。

    为什么必须单独认这一趟：`list_all()` 在脚本里被调 110 次，它自己不拼 URL，
    只是 `api_get "${path}${sep}page=..."` 翻页而已 —— 于是它既不进 `helpers`
    也不进 `infra`，**一个桶都不进，是整个看不见的**（模块头部说的正是这种状态最坏：
    账本上不欠，报告里凭空多出 100 多条假缺口）。

    两种去向，判据仍然是「路径谁给的」：
      · 转手时把**自己的位置参数**当路径传下去（`list_all` → `api_get "$path…"`）
        ⇒ 真覆盖，进 `helpers`，`pathPos` 换成自己的那个位置。
      · 路径是**它自己写死**的（`make_echo_provider` → `api_json POST "/providers"`）
        ⇒ 进 `infra`。这类是造数/夹具，跟 `require_login` 同一档：记账不计覆盖。
        ⚠ 这里是**明知会漏报**的取舍 —— 夹具确实真打了那个端点，它坏了脚本也会红。
        但「这个 helper 是在测这个端点，还是只是路过」没有机械判据，
        而误报的代价是一个真缺口凭空消失（`qa_coverage_reconcile` 头部算过的反号代价）。

    **只走一跳。** 两跳要先给函数排拓扑序，而排错序的表现是静默取到错位置参数。
    一跳能吃掉实测里全部的转手型 helper（他们的 lib 没有 A→B→C）。
    """
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for h in _iter_helper_names(line, known):
            spec = known[h]
            args = _args_of(line, h)
            if not args:
                continue
            arg = _pick(args, spec.get("pathPos") or 0)
            if arg is None:
                continue
            meth = _inherit_method(spec, args, pos)
            if meth is None:
                return "unparsed", f"转手调 {h}，方法认不出来"
            method, method_pos = meth
            p = _pos_of_token(arg, pos)
            if p:
                return "helper", {"base": spec["base"], "pathPos": p, "method": method,
                                  "methodPos": method_pos, "file": file, "via": h}
            if arg.startswith("/"):
                return "infra", {"base": spec["base"], "method": method,
                                 "methodPos": method_pos, "pathLiteral": arg,
                                 "file": file, "via": h}
            return "unparsed", f"转手调 {h}，路径认不出来"
    return "", ""


def parse_helper_lib(files: dict[str, str]) -> dict:
    """`{路径: 正文}` → `{"helpers", "infra", "otherBase", "unparsed"}`。

    `helpers[name] = {base, pathPos, method|methodPos, file}` —— 路径由调用方给，
    这才算覆盖。`infra[name] = {base, method, pathLiteral, file}` —— 路径写死在
    helper 里的（登录/探活/自检/造数夹具），记账不计覆盖，理由见模块头部。

    **两趟**：先认自己拼 URL 的，再拿第一趟的结果去认转手调用的
    （`_classify_delegated` 的文档说了为什么这一趟不能省）。
    第一趟已经判成 `unparsed` 的**不给第二趟机会** —— 它确实拼了 URL，
    只是我们读不懂；再从"它还调了别的 helper"去反推，是在一个已经读失败的
    函数上叠第二层猜测。

    ⚠ 一个 helper 只记**第一处** URL 拼接，方法也只从**那一条命令**里取
    （`_stmt_of`）。`crud_cycle` 那种一趟打
    POST/GET/PUT/DELETE 四个方法的复合流程，因此只会被记成其中一个 ——
    方向是**漏报**（另外三个方法在这条路径上显示没人测）。不改成"记全部方法"
    是因为那需要判断哪几段路径属于哪个方法，读错就变误报。
    """
    helpers: dict[str, dict] = {}
    infra: dict[str, dict] = {}
    other: dict[str, str] = {}
    unparsed: list[dict] = []
    deferred: list[tuple[str, str, dict, str]] = []

    for path, text in (files or {}).items():
        for m in _FUNC_DEF.finditer(text or ""):
            name = m.group(1)
            body = _body_of(text, m.end())
            pos = _positions(body)
            kind, spec = _classify_direct(name, body, pos, path)
            if kind == "other":
                other[name] = str(spec)
            elif kind == "helper":
                helpers[name] = spec        # type: ignore[assignment]
            elif kind == "infra":
                infra[name] = spec          # type: ignore[assignment]
            elif kind == "unparsed":
                unparsed.append({"helper": name, "file": path, "why": str(spec)})
            else:
                deferred.append((name, body, pos, path))

    known = dict(helpers)
    for name, body, pos, path in deferred:
        if name in known or name in infra or name in other:
            continue
        kind, spec = _classify_delegated(name, body, pos, path, known)
        if kind == "helper":
            helpers[name] = spec            # type: ignore[assignment]
        elif kind == "infra":
            infra[name] = spec              # type: ignore[assignment]
        elif kind == "unparsed":
            unparsed.append({"helper": name, "file": path, "why": str(spec)})

    return {"helpers": helpers, "infra": infra, "otherBase": other, "unparsed": unparsed}


def normalize_script_path(raw: str) -> str:
    """`/agents/${ID}/tools` → `/agents/{}/tools`；不像路径的一律返回 ""。

    **直接用 `branch_diff_service.normalize_path`，不自己写一套。**
    R 边（`/api/docs/routes`）和 P 边在 `qa_coverage_reconcile` 里走的就是它，
    Q 边这边另写一条正则的话，同一个 `${ID}` 在一边压成 `{}`、
    在另一边留着原文，两边永远差一位 —— 而差出来的表现是「这个端点没人测」，
    跟真缺口长得一模一样。

    ⚠ 归一化**之前**先看它像不像路径。`normalize_path('{"name":"x"}')` 会把那段
    body 当成 `{id}` 形状的段、压成 `/{}` —— 一个 JSON body 就这么变成一条
    "端点"。所以先卡 `startswith('/')`，再卡全通配。
    """
    r = (raw or "").strip()
    if not r.startswith("/"):
        return ""
    # `$1`/`$2` 那种位置参数，共享的 `normalize_path` **认不出来**（它的变量正则是
    # `\$[A-Za-z_]…`，开头是数字就不匹配），于是 `/admin-users/$1` 原样留着，
    # 跟 R 边的 `/admin-users/{}` 永远对不上 —— 表现是这个端点"没人测"。
    # 实测 20 条命中踩这个。在这儿补一手（shell 里 `/x/$1` 的 `$1` 只可能是位置参数），
    # 不去改那个函数：它同时给分支对账的 R/P 两边用，语义不该被 Q 边的方便改动。
    p = normalize_path(re.sub(r"\$(\d+)", r"${\1}", r))
    segs = [x for x in p.split("/") if x]
    if not segs or all(x == WILDCARD for x in segs):
        return ""
    return p


def _args_of(line: str, helper: str) -> list[str] | None:
    """`helper a b c | jq '.x'` → `['a','b','c']`。

    **不能先用正则在 `|`/`;`/`)` 上切一刀再分词** —— 那些字符大量出现在引号里
    （`jq '.data[] | select(.id)'`、`grep -E '^(200|201)$'`），先切就把引号切断，
    然后 `shlex` 抛 ValueError。实测这么写有 1744 行"分词失败"，
    而它们绝大多数是好行 —— 一个解析器的账本上 1700 条噪声等于没有账本。

    所以反过来：用 `shlex` 先按 shell 词法分词（它认引号），
    **再**在词一级上遇到操作符就停。
    """
    m = re.search(r"(?<![A-Za-z0-9_])" + re.escape(helper) + r"(?![A-Za-z0-9_])", line)
    if not m:
        return None
    lex = shlex.shlex(line[m.end():], posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    out: list[str] = []
    try:
        for tok in lex:
            if tok in _OPS or set(tok) <= set("|&;<>()"):
                break
            out.append(tok)
    except ValueError:
        # 引号真没闭合（跨行拼的 body）—— 有多少用多少，不猜后面
        pass
    return out


def _pick(args: list[str], n: int) -> str | None:
    return args[n - 1] if 0 < n <= len(args) else None


def _iter_helper_names(line: str, known: dict):
    """这一行调了哪些已知 helper。

    **按名字长的先匹配**：`api_get_as` 里含 `api_get`，短的先命中就会按
    `api_get` 的位置去 `$1` 取参 —— 而 `$1` 是 token，于是抽出一条以 token
    为路径的假端点。这个坑在统计脚本里已经踩过一次。
    """
    for name in sorted(known or (), key=len, reverse=True):
        if re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", line):
            yield name
            return          # 一行按一个调用算：嵌套/管道另一半会作为它自己的行被扫到


def extract_helper_calls(text: str | None, parsed: dict) -> dict:
    """脚本正文 + `parse_helper_lib()` 的结果 → 四个桶。

    `hits` 每条 `{method, path, line, helper}`；method 一定是具体方法
    （读不出来的进 `misses`，理由见模块头部那条 ⚠）。
    """
    helpers = (parsed or {}).get("helpers") or {}
    infra = (parsed or {}).get("infra") or {}
    other = (parsed or {}).get("otherBase") or {}
    hits: list[dict] = []
    misses: list[dict] = []
    other_hits: list[dict] = []
    infra_hits: list[dict] = []

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        for name in _iter_helper_names(line, other):
            other_hits.append({"line": line[:200], "helper": name, "base": other[name]})
        for name in _iter_helper_names(line, infra):
            infra_hits.append({"line": line[:200], "helper": name,
                               "path": normalize_script_path(infra[name]["pathLiteral"]),
                               "method": infra[name].get("method") or ""})

        for name in _iter_helper_names(line, helpers):
            spec = helpers[name]
            args = _args_of(line, name)
            if args is None:
                misses.append({"line": line[:200], "helper": name, "why": "分词失败"})
                continue

            path = normalize_script_path(_pick(args, spec["pathPos"]) or "")
            if not path:
                misses.append({"line": line[:200], "helper": name, "why": "路径不是字面量"})
                continue

            # 调用点自己带 `-X POST` 的优先（`bff_code "/x" -X POST` 这种把
            # 额外参数透传给 curl 的 helper，方法是调用方给的，不在签名里）
            method = ""
            cm = _X_FLAG.search(line)
            if cm and cm.group("lit") and _LITERAL_METHOD.match(cm.group("lit")):
                method = cm.group("lit").upper()
            elif spec.get("methodPos"):
                tok = (_pick(args, spec["methodPos"]) or "").strip()
                if _LITERAL_METHOD.match(tok):
                    method = tok.upper()
                else:
                    # 方法是变量 ⇒ **不认**。空方法在 `_covered()` 里通吃所有方法。
                    misses.append({"line": line[:200], "helper": name,
                                   "why": "方法不是字面量"})
                    continue
            else:
                method = spec.get("method") or ""
            if not method:
                misses.append({"line": line[:200], "helper": name, "why": "方法读不出来"})
                continue

            hits.append({"method": method, "path": path, "line": line[:200],
                         "helper": name})

    return {"hits": hits, "misses": misses, "otherBase": other_hits, "infra": infra_hits}
