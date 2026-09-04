"""页面枚举爬取的**只读五层**——判定逻辑全在这里，纯函数，零 IO，零模型。

这五层不是「尽量别写」，是「写不出去」。**理由是无向枚举的力学，不是环境归谁：**
爬虫不认识页面上的控件，点下去可能是「删除」后面跟着确认框的二段写（Q2-F L2 的原话）；
它也不知道自己造了什么、更没法清理。这件事在**我们自己的**测试环境上一样成立。

⚠ **别把这五层读成「被测环境只读」。** 被测环境就是给测试用的，**可以写** ——
有向的 UI 脚本按写好的步骤写、自带清理，走用例管理那套纪律，不受这五层管
（我们自己的 UI 脚本零写守卫；QA 自己的 bash 套件在同一套环境上有 408 处写调用）。
真正不许碰的是 **QA 的 git 仓库**（`app/services/qa_catalog.py` 只放行 6 个只读子命令）——
往那里写一笔，对方自己的 `check-coverage.sh` 门禁会红在一个查不到原因的地方。
分界线一句话：**测试环境可以操作，代码仓库不许操作。**


| 层 | 判定 | 谁调 |
|---|---|---|
| L1 网络 | `is_write_request()` | 沙箱 conftest 的 `readonly_guard`，判定为写就 `route.abort()` |
| L2 控件 | `classify_control()` | 爬虫的动作词典，只点 `SAFE_TO_CLICK` 那一档 |
| L3 账号 | `pick_main_crawl_role()` | 编排层选角色，主爬必须是只读账号 |
| L4 凭证 | `drop_credentials()` | 落库前，HAR 里的凭证整个键扔掉 |
| L5 自检 | `totals_changed()` / `resolve_terminal_status()` | 编排层，爬前爬后对不上就 `dirty` |

**为什么判定必须离开 fixture**（架构 AD-7）：L1 真正执行的地方是生成到沙箱里的
`conftest.py`——一段**模板字符串**，要起浏览器才跑得到。写在那里的逻辑不是"难测"，
是**实际上就不会被测**。所以那边只留一句 `if is_write_request(...): route.abort()`。

**这个模块不认识任何被测系统的业务。** 它只认 HTTP 方法、控件文案、HAR 形状。
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# ── L1 ────────────────────────────────────────────────────────────────────

# 读方法。**其余一律当写**——包括没见过的方法（PROPFIND、自定义动词）。
# 反过来写（"这几个是写、其余放行"）在遇到新动词时会静默放行，
# 而放行一次的代价是别人的数据。
READ_METHODS = ("GET", "HEAD", "OPTIONS")

# 登录是**唯一**默认放行的写操作：不登录就什么都爬不到。
# 它是账本项（`loginCount`）不是免检项——放行几次要报给 QA。
DEFAULT_WRITE_ALLOWLIST = ("/api/auth/login", "/api/login")


def _path_of(url: str) -> str:
    """只取路径。查询串里出现 `/api/auth/login` 不算命中白名单。"""
    try:
        return urlsplit(url or "").path or ""
    except ValueError:
        return ""


def _path_matches(path: str, entry: str) -> bool:
    """路径相等，或者在 `/` 边界上以它开头。

    **不能用 `entry in url`**：那样 `DELETE /api/services/x?next=/api/auth/login`
    会被放行——白名单变成了「URL 里任何位置出现这串字符」。
    也不能用裸 `startswith`：`/api/auth/login-as-anyone` 会蹭进来。
    """
    path = path.rstrip("/") or "/"
    entry = (entry or "").rstrip("/") or "/"
    return path == entry or path.startswith(entry + "/")


def is_write_request(method: str, url: str, allowlist=None) -> bool:
    """这条请求会不会改别人的数据。**判不准就算会。**

    白名单按**路径**匹配（见 `_path_matches`），只用来放行登录这类
    「不做就爬不动」的写操作，不是给"这个接口应该没事"开的口子。
    """
    m = (method or "").strip().upper()
    if not m:
        return True                      # 方法都拿不到，别放行
    if m in READ_METHODS:
        return False
    path = _path_of(url)
    for entry in (DEFAULT_WRITE_ALLOWLIST if allowlist is None else allowlist):
        if _path_matches(path, entry):
            return False
    return True


# ── L2 ────────────────────────────────────────────────────────────────────

# 只点这一档。`unknown` **不点**——认不出来的控件按会写算，
# 漏爬一个按钮只是少一条账本，点错一个是动了别人的数据。
SAFE_TO_CLICK = ("read",)

_WRITE_WORDS = (
    "删除", "移除", "清空", "重置", "新建", "创建", "添加", "新增", "保存", "提交",
    "确认", "确定", "启用", "禁用", "停用", "发布", "下线", "上线", "审批", "通过",
    "驳回", "拒绝", "导入", "同步", "执行", "运行", "启动", "停止", "重启", "生成",
    "编辑", "修改", "更新", "复制", "克隆", "申请", "续期", "撤销", "作废", "归档",
    "delete", "remove", "clear", "create", "new", "add", "save", "submit", "confirm",
    "enable", "disable", "publish", "approve", "reject", "import", "sync", "run",
    "start", "stop", "restart", "generate", "edit", "update", "duplicate", "revoke",
)
_READ_WORDS = (
    "查看", "详情", "搜索", "查询", "筛选", "过滤", "导出", "下载", "刷新", "展开",
    "收起", "全部", "更多", "返回", "取消", "关闭", "上一页", "下一页", "排序",
    "view", "detail", "search", "filter", "export", "download", "refresh", "expand",
    "collapse", "more", "back", "cancel", "close", "next", "prev",
    "previous", "sort",
)

# 角色本身就说明了会不会改状态。`switch` / `checkbox` / `radio` 点一下就是改，
# 文案再像"查看"也不行——所以角色**先于**文案判。
_WRITE_ROLES = ("switch", "checkbox", "radio", "slider", "spinbutton")
# 导航类：点了只是换个页面。爬虫要靠它走完站点。
_READ_ROLES = ("link", "tab", "menuitem", "treeitem")


# ── 词表怎么匹配：ASCII 认词边界，中文认子串 ───────────────────────────────
#
# 2026-09-04 实测：表头 `Created At` 被 `create` 子串命中判成「开层按钮」，
# 于是整页的预算被表头和左侧导航吃光，`dialogsOpened` **恒为 0** ——
# 而账本上「点了 255 下」看着非常健康。子串匹配还顺带把 `Address` 判成写
# （含 `add`）、`Preset` 判成禁点（含 `reset`）。
#
# 词边界只对 ASCII 有意义：Python 的 `\w` 把汉字也算词字符，
# `\b新建\b` 在「新建服务」里两边都不是边界，一律不命中 —— 所以中文走子串。
#
# 尾巴只放 `s/es/ing`（`Details`、`Adding`），**不放 `d/ed`**：
# 放了 `Created`/`Updated` 就又回到原样。代价是 `Running`、`Stopped`
# 这类分词判成 unknown —— unknown 是**不点**的那一档，宁可少点。
_ASCII_RE_CACHE: dict[str, "re.Pattern"] = {}


def _word_hit(text: str, word: str) -> bool:
    """`word` 在 `text` 里算不算命中。text/word 都应已 `lower()`。"""
    if not word.isascii():
        return word in text
    rx = _ASCII_RE_CACHE.get(word)
    if rx is None:
        rx = re.compile(r"\b" + re.escape(word) + r"(?:s|es|ing)?\b")
        _ASCII_RE_CACHE[word] = rx
    return bool(rx.search(text))


def classify_control(label: str, role: str = "") -> str:
    """`read` / `write` / `unknown`——这个控件点下去会不会改数据。

    判定顺序是**角色优先**：`switch` 的文案常常是「已启用」，
    按文案判会把它当成状态展示点下去，而那一下就是禁用一个服务。
    """
    r = (role or "").strip().lower()
    if r in _WRITE_ROLES:
        return "write"
    text = (label or "").strip().lower()
    if not text:
        return "read" if r in _READ_ROLES else "unknown"
    for w in _WRITE_WORDS:
        if _word_hit(text, w):
            return "write"
    for w in _READ_WORDS:
        if _word_hit(text, w):
            return "read"
    return "read" if r in _READ_ROLES else "unknown"


# ── L2b：开层 ≠ 提交 ─────────────────────────────────────────────────────

# `classify_control` 答的是「点下去**会不会写**」，`新建`/`编辑` 判成 write 是对的。
# 这一段答的是另一个问题：「我们**点不点**」。合成一句的话只有两个坏选择 ——
# 要么按 write 一辈子不点（那个系统的表单就永远枚举不到），
# 要么改判成 read（那是把层里的「保存/提交」也一起放行了）。
#
# 为什么值得多这一段：2026-09-04 那一趟量出来，1266 个可操作项里**一个输入框
# 都没有**。不是被测系统没有表单，是表单全在没被打开的层里 —— 于是
# 「表单覆盖了没」这个问题的**分母是 0，任何覆盖率都成立**。
#
# 开层的那一下**本身不写**：真正的写在层里的「保存/提交/确定」上，而那些
# 我们一个都不点。就算判错了，L1 那层网也会把写请求 abort 掉。
_OPENER_WORDS = (
    "新建", "创建", "添加", "新增", "编辑", "修改", "配置", "设置",
    "new", "create", "add", "edit", "config", "setting",
)

# **一个都不许点**，哪怕 L1 会把请求拦下来。三条理由各自独立成立：
#  ① 退出/登出：点完这一下，后面每一页都渲染成登录页，**而每一格都是绿的**
#     —— 2026-09-04 刚修完一次一模一样的假绿，别自己再造一次；
#  ② 删除/清空/重置：多数系统弹二次确认（我们不点确认），但**有的用原生
#     `window.confirm`** —— 那一下会把整个分片吊死在一个 Playwright 默认不处理的
#     弹框上，报出来是超时，看不出是自己点出来的；
#  ③ 就算请求被 L1 拦了，页面上也已经弹了一条失败提示 —— 我们是来看的，
#     不该在别人的环境里留脚印。
_NEVER_CLICK_WORDS = (
    "删除", "移除", "清空", "重置", "停用", "禁用", "注销", "退出", "登出",
    "下线", "作废", "撤销", "重启", "停止", "驳回", "拒绝", "审批", "通过",
    "delete", "remove", "clear", "reset", "disable", "logout", "sign out",
    "revoke", "restart", "stop", "approve", "reject",
)


def click_intent(label: str, role: str = "") -> str:
    """`safe` / `opener` / `never` —— 无向枚举里**这个控件点不点**。

    顺序是**禁点优先**：`重置密码` 里既有"重置"也有"密码"，先判禁点才不会
    因为别的词表命中而放行。`safe` 就是 L2 那档（`SAFE_TO_CLICK`），
    `opener` 是"点开一个层"，其余一律 `never`。
    """
    text = (label or "").strip().lower()
    for w in _NEVER_CLICK_WORDS:
        if _word_hit(text, w):
            return "never"
    # 角色照旧**先于**文案：一个文案叫「新增」的开关，点下去就是打开一个开关，
    # 不是弹一个层。少了这一句，`_OPENER_WORDS` 会把整档 switch/checkbox
    # 重新放行 —— 而那正是 `classify_control` 里角色优先要挡的东西。
    if (role or "").strip().lower() in _WRITE_ROLES:
        return "never"
    if classify_control(label, role) in SAFE_TO_CLICK:
        return "safe"
    for w in _OPENER_WORDS:
        if _word_hit(text, w):
            return "opener"
    return "never"


# ── L3 ────────────────────────────────────────────────────────────────────

# 主爬账号。**只读账号**——L1/L2 拦不住的东西，靠它在服务端被拒。
# 三层是叠着的，不是三选一：前两层是我们自己的判断，这一层是对方系统的判断。
#
# 名字是 `auditor` 而不是 `qa-auditor`：**角色名必须跟环境变量的前缀同源**，
# 因为取凭证只有这一条路（`<PREFIX>_USERNAME` / `_PASSWORD`）。实测那个环境里
# 只读账号配在 `AUDITOR_USERNAME=qa-auditor` 上 —— `qa-auditor` 是**账号名**，
# 前缀才是角色。用账号名当角色名的话，这里认得出"配了只读账号"，
# 而爬取那边去找 `QA_AUDITOR_USERNAME` 找不到，于是它被静默跳过 ——
# 报出来是「主爬账号没登上」，而不是「你这个常量取错了名字」。
MAIN_CRAWL_ROLE = "auditor"


def _role_names(roles) -> list[str]:
    """清出真的角色名。

    `str(None)` 是字符串 `"None"` —— 非空、`strip()` 也活得好好的，
    于是一个空角色会变成一个**名叫 None 的账号**被拿去登录/浅扫。
    所以先判 `is None` 再转字符串，别指望 `if str(r).strip()` 能挡住它。
    """
    out = []
    for r in roles or []:
        if r is None:
            continue
        name = str(r).strip()
        if name:
            out.append(name)
    return out


def pick_main_crawl_role(roles) -> str:
    """主爬用哪个角色。没有只读账号就**不许开爬**，不许"先用 admin 顶一下"。

    顶一下的后果不是「风险高一点」：L1 白名单里有登录、L2 认不出的控件不点，
    这两层都是**我们自己**判的，判错就没有第二道网了。
    """
    names = _role_names(roles)
    if MAIN_CRAWL_ROLE not in names:
        raise ValueError(
            f"没有只读账号 {MAIN_CRAWL_ROLE}，不开爬。"
            f"环境里现在配的是：{names or '（一个都没有）'}。"
            f"用有写权限的账号主爬，等于把只读五层减成两层，而那两层都是我们自己判的。")
    return MAIN_CRAWL_ROLE


def shallow_scan_roles(roles) -> list[str]:
    """浅扫的其余角色（角色维度的缺口要靠它们）。主爬那个不重复排。"""
    seen, out = set(), []
    for name in _role_names(roles):
        if name != MAIN_CRAWL_ROLE and name not in seen:
            seen.add(name)
            out.append(name)
    return out


# ── L4 ────────────────────────────────────────────────────────────────────

# 这三个头**整个扔掉，不是脱敏**。HAR 里的 `Authorization` 是完整可用凭证，
# 存成 `"***"` 留下的是一个"我们存了，但很安全"的印象——而键还在，
# 说明这条路径上真的流过凭证，下一个人加一行日志就又出去了。
DROP_HEADERS = ("authorization", "cookie", "set-cookie", "proxy-authorization",
                "x-api-key", "x-auth-token")

_SECRET_KEY_RE = re.compile(
    r"(PASSWORD|PASSWD|PWD|TOKEN|SECRET|APIKEY|API_KEY|AUTH|CREDENTIAL|COOKIE|SESSION)", re.I)

# 正文一概不落库（S6.2 原话）。HAR 里正文挂在这些键上。
_BODY_KEYS = ("postData", "content", "text", "params")


def _clean_header_list(items):
    """HAR 的头是 `[{"name": "Authorization", "value": "Bearer …"}]`。

    **键是 `name`/`value`，头名在值里** —— 所以按键名脱敏的 `_mask_deep`
    对它结构性失明。实测过：把一份带三个凭证头的 HAR 喂给 `_mask_deep`，
    `Bearer …` / `session=…` / `Set-Cookie` **原样三个全在**。
    架构 AD-7 写的「复用 `_mask_deep`」在 HAR 这个形状上是不够的，
    所以这里先按 HAR 形状扔，再拿它兜底扫别的键。
    """
    out = []
    for it in items:
        if isinstance(it, dict) and str(it.get("name", "")).strip().lower() in DROP_HEADERS:
            continue
        out.append(drop_credentials(it))
    return out


def _clean_url(url: str) -> str:
    """查询串里的 token 也是凭证（`?access_token=…` 到处都有）。值换掉，键留着。

    键留着是故意的：**「这个接口在 URL 上收 token」本身是要能看见的事实**，
    连键一起删就把它抹平成了「这个接口没参数」。
    """
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return url
    if not parts.query:
        return url
    q = [(k, "***" if _SECRET_KEY_RE.search(k or "") else v)
         for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def drop_credentials(obj, depth: int = 0):
    """落库前把凭证从 HAR（或任何形状）里**扔掉**。返回新对象，不改原来那份。

    三件事：① 三个凭证头按 HAR 形状整条剔除；② 正文键一概不留；
    ③ 剩下的按键名兜底脱敏（这一层才是 `_mask_deep` 那套）。

    深度上限 12 —— 比 `_mask_deep` 的 6 深一倍。这里要说清一件容易讲错的事：
    **浅封顶不会漏凭证**（到底了返回的是 `"…"`，不是原对象），它吃掉的是**证据**。
    HAR 的头躺在 `log.entries[i].request.headers[j].value`，光走到那里就 8 层，
    6 层封顶会让整个 `request` 塌成一个省略号 —— url、方法、`Accept` 全没了，
    而这份 HAR 是失败分类唯一的网络证据来源。不报错，只是从此什么都看不到。
    """
    if depth > 12:
        return "…"                       # 到底了就截断，**不原样返回**
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = str(k)
            if key.strip().lower() in DROP_HEADERS or key in _BODY_KEYS:
                continue
            if key == "url" and isinstance(v, str):
                out[key] = _clean_url(v)
            elif _SECRET_KEY_RE.search(key):
                out[key] = "***"
            elif key in ("headers", "cookies") and isinstance(v, list):
                out[key] = _clean_header_list(v)
            else:
                out[key] = drop_credentials(v, depth + 1)
        return out
    if isinstance(obj, list):
        return [drop_credentials(v, depth + 1) for v in obj]
    if isinstance(obj, str) and len(obj) > 300:
        return obj[:300] + "…"
    return obj


# ── L5 ────────────────────────────────────────────────────────────────────

def totals_changed(before: dict | None, after: dict | None) -> list[str]:
    """爬前爬后哪些计数对不上。**只出现在一边也算变了。**

    只比交集是最顺手的写法，也正好漏掉最该抓的那种：爬完多出来一类对象
    （我们建了什么），在 `before` 里根本没有这个键。
    """
    b, a = before or {}, after or {}
    return sorted(k for k in set(b) | set(a) if b.get(k) != a.get(k))


def resolve_terminal_status(*, shards_total: int, shards_ok: int,
                            totals_before: dict | None = None,
                            totals_after: dict | None = None) -> str:
    """这一趟最后落哪个终态。

    **`dirty` 压过一切**，包括 `failed`：一趟全片失败、但环境里的数变了，
    要看的是"我们动了什么"，不是"我们没爬成"。把它排在后面，
    这条信息就会被一句"这趟失败了"盖过去——而那正是最需要人来看的一趟。
    """
    if totals_changed(totals_before, totals_after):
        return "dirty"
    if shards_ok <= 0:
        return "failed"
    if shards_ok < shards_total:
        return "partial"
    return "done"
