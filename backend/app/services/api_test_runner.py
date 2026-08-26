"""接口测试执行引擎 — 单步/场景/批量执行 + TokenCache + 变量传递"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import string
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_test import ApiTestScenario, ApiTestStep

logger = logging.getLogger(__name__)

_run_semaphore = asyncio.Semaphore(5)


@dataclass
class StepResult:
    step_id: str
    step_name: str
    method: str
    url: str
    status: str  # pass | fail | skip
    status_code: int | None = None
    duration: int = 0
    assertions: list[dict] = field(default_factory=list)
    response_body: dict | None = None
    error: str | None = None
    request_data: dict | None = None  # {method, url, headers, body} — 报告下钻展示


@dataclass
class ScenarioResult:
    scenario_id: str
    scenario_title: str
    scenario_status: str = "draft"  # draft | published | deprecated — 草稿调试不进报告
    folder_id: str | None = None
    # 场景绑的源用例。带着它，这次执行才能同时记进 script_runs ——
    # 否则接口执行只进 test_reports，用例的「执行历史」永远只看得见 UI，
    # 报告页永远只看得见接口，同一条用例在两个页面各显示一半事实。
    source_case_id: str | None = None
    steps: list[StepResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        # **一步都没真跑过不算通过。** 原来只判 `all(pass or skip)`，于是"全是 skip"
        # （步骤全禁用、或运行时一个都没勾）返回 True，页面报「全通过 0/0 步」，
        # 再经 apply_case_status 把用例的接口维度推成"跑绿了" —— 一条彻底的假绿，
        # 而且是最难发现的那种：没有任何红色，计数是 0 但结论是通过。
        if not any(s.status in ("pass", "fail") for s in self.steps):
            return False
        return all(s.status == "pass" or s.status == "skip" for s in self.steps)

    @property
    def pass_count(self) -> int:
        return sum(1 for s in self.steps if s.status == "pass")

    @property
    def fail_count(self) -> int:
        return sum(1 for s in self.steps if s.status == "fail")


@dataclass
class RunEvent:
    type: str  # scenario_start | step_result | scenario_done | run_done | error
    data: dict


class TokenCache:
    """环境级 Token 缓存 — 多角色支持 + 401 被动刷新（ADR-3）。

    凭据来源：env 中的 `{ROLE}_USER` / `{ROLE}_PASS`（默认角色 ADMIN）。
    登录端点：env 中的 `LOGIN_URL`，缺省 `{BASE_URL}/api/auth/login`。
    运行结束随对象销毁。
    """

    def __init__(self, env: dict):
        self._env = env
        self._tokens: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def _login_url(self) -> str | None:
        """自动登录用哪个地址。**LOGIN_URL 是路径就拼上 BASE_URL。**

        环境里 LOGIN_URL 存的是 `/api/auth/login`（步骤里写 `${BASE_URL}${LOGIN_URL}`
        本来就是拼起来用的）。原来这里直接拿它当完整 URL 发请求，httpx 报
        「Request URL is missing an 'http://' or 'https://' protocol」，异常被吞掉
        只留一行 warning —— 后果是**自动登录从来没成功过**：
          · 共享资源（automation_resources）的探测一律 401 → state=unknown →
            `${资源名}` 永远注入不进来，于是每条链只好自己写一步「按名字查上游」
            并硬断言它存在，一个底座缺失就放大成一批链全红
          · 401 被动刷新也失效（拿不到新 token）
        实测这个项目 4 个共享资源全年 unknown，没人发现 —— 因为链子自己带登录步骤，
        照样能跑，只有共享资源这条路悄悄死了。
        """
        lu = str(self._env.get("LOGIN_URL") or "").strip()
        base = str(self._env.get("BASE_URL") or "").rstrip("/")
        if lu.lower().startswith("http"):
            return lu
        if lu:
            return f"{base}/{lu.lstrip('/')}" if base else None
        return f"{base}/api/auth/login" if base else None

    async def get_token(self, client: httpx.AsyncClient, role: str = "ADMIN") -> str | None:
        async with self._lock:
            if role in self._tokens:
                return self._tokens[role]
            return await self._login(client, role)

    async def refresh_token(self, client: httpx.AsyncClient, role: str = "ADMIN") -> str | None:
        """401 被动刷新 — 丢弃缓存重新登录。"""
        async with self._lock:
            self._tokens.pop(role, None)
            return await self._login(client, role)

    async def _login(self, client: httpx.AsyncClient, role: str) -> str | None:
        url = self._login_url()
        # 环境里两种命名都见过：ADMIN_USER/ADMIN_PASS 与 ADMIN_USERNAME/ADMIN_PASSWORD。
        # 只认前者会导致自动登录静默失效（探测前置资源、401 重试都拿不到 token）。
        user = self._env.get(f"{role}_USER") or self._env.get(f"{role}_USERNAME")
        password = self._env.get(f"{role}_PASS") or self._env.get(f"{role}_PASSWORD")
        if not url or not user or not password:
            return None
        try:
            resp = await client.post(url, json={"username": user, "password": password}, timeout=15)
            if resp.status_code != 200:
                logger.warning("TokenCache 登录失败 role=%s status=%s", role, resp.status_code)
                return None
            body = resp.json()
            token = None
            for path in (("data", "token"), ("token",), ("access_token",), ("data", "access_token")):
                val = body
                for key in path:
                    val = val.get(key) if isinstance(val, dict) else None
                if isinstance(val, str) and val:
                    token = val
                    break
            if token:
                self._tokens[role] = token
            return token
        except Exception as e:
            logger.warning("TokenCache 登录异常 role=%s: %s", role, e)
            return None


def _inject_runtime_variables(env: dict, origins: dict | None = None) -> None:
    """场景级运行时变量 — 同一场景内引用同一个值（便于创建+清理配套使用）。"""
    for k, v in (
        ("RANDOM_8", "".join(random.choices(string.ascii_lowercase + string.digits, k=8))),
        ("TIMESTAMP", str(int(time.time()))),
    ):
        if k not in env:
            env[k] = v
            _note_origin(origins, k, "runtime", "平台运行时注入（同一场景内固定）")


# ── 变量溯源 ────────────────────────────────────────────────────────────────
# env 是个扁平字典，各来源合并进去以后"谁塞的"就没了。跑挂的时候最想知道的恰恰是
# 「这个 id 哪来的、这个 token 哪来的」，所以合并的每一处都同步登记一条来源。
def _note_origin(origins: dict | None, name: str, source: str, detail: str) -> None:
    if origins is None:
        return
    origins[name] = {"source": source, "detail": detail}


_ORIGIN_LABEL = {
    "env": "环境变量",
    "scenario_env": "场景自带变量",
    "runtime": "运行时注入",
    "scenario_var": "用例场景变量",
    "resource": "项目级前置资源",
    "extract": "上游步骤提取",
    "auto_token": "平台自动登录",
}


def _used_variables(origins: dict, env: dict, *objs) -> list[dict]:
    """这一步实际引用到的变量：名字 + 真实取值 + 从哪来。

    值一律给全值、不遮不截断。这是测试执行详情，看的就是"到底发出去了什么"——
    遮掉密码等于让人没法核对登录步骤，复制出来的 cURL 也跑不了。
    """
    used = []
    for name in sorted(set(_collect_ref_names(*objs))):
        o = origins.get(name) or {}
        val = env.get(name)
        used.append({
            "name": name,
            "value": val,
            "source": o.get("source", "unknown"),
            "sourceLabel": _ORIGIN_LABEL.get(o.get("source"), "来源不明"),
            "detail": o.get("detail", "未登记来源——可能是环境里直接带的键"),
        })
    return used


def _collect_ref_names(*objs) -> list[str]:
    names: list[str] = []

    def walk(node):
        if isinstance(node, str):
            names.extend(re.findall(r"\$\{(\w+)\}", node))
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(k)
                walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)

    for o in objs:
        walk(o)
    return names


# 文案占位：`${T:服务名已存在}` —— 按 TEST_LANGUAGE 换成当前语种的那句话。
#
# **文案不是 UI 专属的。** 接口的错误提示语同样会跟着语种变（Accept-Language 一改，
# message 字段就从中文变英文），断言里写死中文，跑英文环境照样全红。
# UI 脚本那边是 `t("更多")`（沙箱里的 tea_i18n），接口场景是平台执行的 JSON 步骤、
# 没有脚本可以 import，所以给一个占位语法走 ${} 这条既有的解析路。
_TEXT_REF_RE = re.compile(r"\$\{T:([^}]+)\}")


def _resolve_variables(text, env: dict) -> str:
    if not isinstance(text, str):
        return text
    # 先解文案占位。放在前面：译文里可能带 ${var}（如「服务 ${name} 已存在」），
    # 换完之后还要再过一轮普通变量解析。
    text = _TEXT_REF_RE.sub(lambda m: _t(m.group(1), env), text)
    return re.sub(r'\$\{(\w+)\}', lambda m: str(env.get(m.group(1), m.group(0))), text)


def _t(ref: str, env: dict) -> str:
    """`${T:ref}` → 当前语种的那句话。`${T:键|中文原文}` 把中文也带上。

    ref 是**语言中立的 key**（`services.form.nameRequired`）—— 中文和英文都是它的值。
    也认中文原文（采集器从脚本里抽的那批就是拿中文当 key 的），是为了兼容。

    **带上 `|中文原文` 有两个作用**：读断言的人一眼知道在验什么（光看键名看不出来），
    以及词典查不到时退回中文，而不是把键名当文案去比（那必然假红）。
        ${T:services.form.nameDuplicated|服务名已存在}

    查不到就退回中文 / 原样返回，绝不抛 —— 词典一定是不全的。
    """
    hint = None
    if "|" in ref:
        ref, hint = ref.split("|", 1)
        ref = ref.strip()
    lang = (env.get("TEST_LANGUAGE") or "").strip().lower()
    locale = str(env.get("PLAYWRIGHT_LOCALE")
                 or {"en": "en-US", "zh": "zh-CN"}.get(lang, "zh-CN"))
    row = (env.get("__I18N__") or {}).get(ref) or {}
    if row.get(locale):
        return row[locale]
    pre = locale.split("-")[0]
    for k, v in row.items():
        if k.split("-")[0] == pre and v:
            return v
    return hint if hint is not None else ref


def _resolve_obj(obj, env: dict):
    if isinstance(obj, str):
        return _resolve_variables(obj, env)
    if isinstance(obj, dict):
        return {k: _resolve_obj(v, env) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_obj(v, env) for v in obj]
    return obj


def _unresolved_refs(*objs) -> list[str]:
    """收集解析后仍残留的 ${NAME}——即没有任何来源可解析的变量名。"""
    names: list[str] = []

    def walk(node):
        if isinstance(node, str):
            for n in re.findall(r'\$\{(\w+)\}', node):
                if n not in names:
                    names.append(n)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for o in objs:
        walk(o)
    return names


def _split_path(path: str) -> list[str]:
    """按 `.` 切段，方括号里的 `.` 不算分隔符 —— 过滤值本身可能带点：`[name=svc-a.b]`。"""
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in path:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        if ch == "." and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return out


_SELECTOR_RE = re.compile(r'\[([^\[\]]+)\]')


def _extract_value(body, path: str):
    """按 JSONPath-lite 从响应体取值。三种选择器：

    - 下标：`data.items[0].id`，**负数从后往前** `data.items[-1].id`
    - 按字段值过滤：`data.items[name=${svcName}].id` —— 取第一条 name 等于该值的
    - 混用：`data.items[status=pending][0].id`

    **过滤器是为了消灭 `data[0]`。** 下标是另一种写死（见 lum_get_sync_spec 的变量纪律）：
    列表顺序、排序口径、分页一变，`data[0]` 就静默指向别的业务对象，断言照过。
    实测撞到：`/todos` 按 created_at 升序且满页，本次新建的那条压根不在第一页。
    有了过滤器就按业务标识定位，顺序怎么变都对得上。

    **`$.` 前缀一并接受。** MCP 工具的参数说明里写的是 "jsonpath"，
    外部 CC 照着写 `$.data.token` 是完全合理的，而这里只认 `data.token` ——
    结果是提取静默返回 None，下一步报「变量未解析」，把人指向环境变量，
    而根因在上一步。宽进严出：能认的写法就认下来。
    """
    val = body
    path = (path or "").strip()
    if path.startswith("$."):
        path = path[2:]
    elif path == "$":
        return body
    elif path.startswith("$["):
        path = path[1:]
    for seg in _split_path(path):
        if val is None:
            return None
        m = re.match(r'^([^\[\]]*)((?:\[[^\[\]]+\])*)$', seg)
        if not m:
            # 段里含无法解析的字符，退化为整段当作字典 key
            if isinstance(val, dict):
                val = val.get(seg)
                continue
            return None
        key, sel_part = m.group(1), m.group(2)
        if key:
            if isinstance(val, dict):
                val = val.get(key)
            else:
                return None
        for sel in _SELECTOR_RE.findall(sel_part):
            sel = sel.strip()
            if not isinstance(val, list):
                return None
            if re.fullmatch(r'-?\d+', sel):
                i = int(sel)
                if i < 0:
                    i += len(val)
                if not (0 <= i < len(val)):
                    return None
                val = val[i]
            elif "=" in sel:
                star = sel.startswith("*")
                f, _, want = (sel[1:] if star else sel).partition("=")
                hits = [e for e in val
                        if _scalar_eq(_extract_value(e, f.strip()), want.strip())]
                # `[*k=v]` 取**全部命中**，配 length 才能断"有且只有一条"。
                # 少了它，唯一性根本没法验：`[k=v]` 只取第一条，被测系统真接受了
                # 第二条同名，断言照样绿 —— 活体跑回推链路时就是这么被
                # lum_check_assertion_bite 抓出来的（still_green）。
                # 而 length 对整个列表用又不行：`?search=` 在被测系统里不是严格过滤。
                val = hits if star else (hits[0] if hits else None)
            else:
                return None
    return val


def expected_of(a: dict):
    """一条断言的期望值 —— `expected` 和 `value` **两种键名都认**。

    为什么会有两种：库里的口径是 status 写 `value`、body_field 写 `expected`
    （前端编辑器就是这么存的，规范里的例子也是），而写的人自然会两处都用 `expected`。
    同一个概念两个键名，于是**判定和显示各挑了一个**：

      · 判定（status 分支）只读 `value` → `{"type":"status","expected":200}` 拿到 None
      · 报错那一行读的是 `expected` → 打印出「期望 200，实际 200，判失败」

    实测代价：CC 新建的 23 步场景**全红**，逐字重跑两次结果相同，而 8/16 建的老场景
    15/15 全绿 —— 差别只有这一个键名。连锁反应还有：清理步骤实际 204 成功却被判 fail，
    lum_check_env_hygiene 于是报了 3 条"残留"，那 3 个 id 去查全是 404。

    两个键都给的时候 `expected` 优先 —— 那是历史形态 `{value: 字段路径,
    expected: 期望值}`（见 field_of）。**取期望值只许走这一个函数。**
    """
    v = a.get("expected")
    return a.get("value") if v is None else v


def field_of(a: dict, operator: str | None = None):
    """断言的字段路径 —— `field`，或历史形态里的 `value`（那时期望值在 `expected`）。"""
    if a.get("field"):
        return a["field"]
    if a.get("expected") is not None or operator in ("not_empty", "is_empty", "not_exists"):
        return a.get("value")
    return None


def _as_int(v):
    """状态码统一成整数比较 —— 变量插值出来的是字符串（"200"），不转必然假红。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return v


def _expects_status(assertions: list[dict], code: int) -> bool:
    """断言是否预期该状态码（401 重试判断用）。

    `in [401, 403]` 也算预期 —— 原来只判 `int(value) == code`，列表进来
    int() 直接抛，于是"这一步本来就该 401"被当成 token 过期，白重试一轮。
    """
    for a in assertions or []:
        if a.get("type") != "status":
            continue
        exp = expected_of(a)
        if isinstance(exp, (list, tuple)):
            if any(_as_int(x) == code for x in exp):
                return True
        elif _as_int(exp) == code:
            return True
    return False


# 每种断言类型认哪些 operator。不认识的必须当场说出来 ——
# 以前一律落到 passed=False，于是出现"状态码 200、期望 200、却显示失败"
# 这种查不出原因的假失败（写成 eq 而不是 == 就会中招）。
_VALID_OPS = {
    "status": ("==", "!=", "in"),
    "body_contains": ("contains", "not_contains"),
    # is_empty / length / 大小比较是后补的：
    # · is_empty —— 「列表应该是空的」以前根本表达不了，只能拿 body_contains not_contains
    #   某个字段名去绕，而那是在整个响应体里搜字符串，别的地方出现同名字段就假绿。
    # · length —— 按字段过滤之后断「恰好一条」。没有它，过滤器只能证明"有"，
    #   证明不了"只有这一条"。
    # · > < >= <= —— 编辑器的下拉里本来就有「大于/小于」，而执行器不认，
    #   人在页面上选了就得到「不认识的操作符」，永远失败。
    # not_exists 是活体跑回推链路时逼出来的：`data[name=${svcName}].id` 在东西被删掉之后
    # 取不到值（过滤没命中），而 is_empty 故意不认 None（那是"字段改名"，混在一起会把
    # 改名假绿掉）。少了它，「删完按名字查不到」只能退回 body_contains not_contains
    # 在整个响应体里搜字符串 —— 正是要消灭的那种绕法。
    "body_field": ("==", "!=", "not_empty", "is_empty", "not_exists", "contains",
                   "not_contains", "length", ">", "<", ">=", "<="),
}


# 没写 operator 时按类型兜底：body_contains 天然是「包含」，兜成 == 等于必然失败。
_DEFAULT_OP = {"status": "==", "body_field": "==", "body_contains": "contains"}


def _scalar_eq(actual, expected) -> bool:
    """标量相等 —— 一边是字符串一边是数字时按**数值**比。

    **不是在放松断言，是在修一个必然的假红。** 变量插值出来的值永远是字符串
    （`_resolve_variables` 用 str(...)），而 JSON 响应里的数字是 int/float。
    于是「拿上一步提取的版本号比 data.version」这种再常见不过的写法**必然挂**，
    而页面上显示的是「期望 2｜实际 2」—— 人完全看不出为什么失败。
    实测撞到：`data.rolled_back_to_version == ${baseVersion}`，期望 "2" 实际 2。

    只在**两边都能解析成数字**时按数值比；其余一律原样严格比较，
    所以 "true" 不会等于 True，"01" 不会等于 "1" 之外的东西。
    """
    # bool 要在严格比较**之前**挡掉：Python 里 1 == True 本来就成立，
    # 放过去的话「期望 true、实际 1」会被判相等，那是另一种假绿。
    if isinstance(actual, bool) != isinstance(expected, bool):
        return False
    if actual == expected:
        return True
    if isinstance(actual, bool):
        return False
    a_num = _as_number(actual)
    e_num = _as_number(expected)
    return a_num is not None and e_num is not None and a_num == e_num


def _as_number(v):
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        t = v.strip()
        if t:
            try:
                return float(t) if ("." in t or "e" in t.lower()) else int(t)
            except ValueError:
                return None
    return None


def _is_blank(v) -> bool:
    """空容器/空串算空；数字 0 和布尔 false **不算空**（那是有意义的值）。"""
    if isinstance(v, str):
        return v.strip() == ""
    if isinstance(v, (list, dict, tuple, set)):
        return len(v) == 0
    return False


def _check_assertions(assertions: list[dict], status_code: int, resp_body) -> list[dict]:
    results = []
    for a in assertions:
        passed = False
        # 实际值：下面几个分支本来就算出来了，以前在最后 append 时被丢掉，
        # 于是断言明细里永远是 `actual: null` —— 报告和 MCP 都只能说
        # 「期望 success」，说不出「实际是 pushing」。而"实际是什么"恰恰是
        # 判断"这是抢跑还是真错"的唯一依据。
        actual = None
        a_type = a.get("type")
        operator = a.get("operator") or _DEFAULT_OP.get(a_type, "==")

        valid = _VALID_OPS.get(a_type)
        if valid is not None and operator not in valid:
            results.append({**a, "passed": False,
                            "error": f"不认识的操作符「{operator}」；{a_type} 支持：{'、'.join(valid)}"})
            continue
        if valid is None:
            results.append({**a, "passed": False,
                            "error": f"不认识的断言类型「{a_type}」；支持：{'、'.join(_VALID_OPS)}"})
            continue

        expected = expected_of(a)
        field_path = field_of(a, operator)

        if a_type == "status":
            # **别在这儿重新取一遍期望值。** 原来这行是 `expected = a.get("value")`，
            # 把上面的口径覆盖掉，于是 `{"type":"status","expected":200}` 变成 None、
            # 200 == None 判失败，而报错那行读 expected、打印「期望 200」。见 expected_of。
            expected = ([_as_int(x) for x in expected]
                        if isinstance(expected, (list, tuple)) else _as_int(expected))
            actual = status_code
            if operator == "==":
                passed = actual == expected
            elif operator == "!=":
                passed = actual != expected
            elif operator == "in":
                passed = actual in (expected if isinstance(expected, list) else [expected])
        elif a_type == "body_contains":
            _cv = expected_of(a)
            contain_val = "" if _cv is None else _cv
            op = operator if operator in ("contains", "not_contains") else "contains"
            hit = str(contain_val) in str(resp_body)
            passed = hit if op == "contains" else not hit
            # 响应体可能很大，只给前 120 字当"实际" —— 够判断是不是完全不沾边
            actual = str(resp_body)[:120] if not passed else None
        elif a_type == "body_field":
            actual = _extract_value(resp_body, field_path) if field_path else resp_body
            if operator == "==":
                passed = _scalar_eq(actual, expected)
            elif operator == "!=":
                passed = not _scalar_eq(actual, expected)
            elif operator == "not_empty":
                # 空数组/空对象**必须算空**。原来只判 `actual != ""`，于是
                # `{"type":"body_field","field":"data.items","operator":"not_empty"}`
                # 在 `"items": []` 上是绿的 —— 「查出来应该有数据」这条断言
                # 恰好在没数据时通过，是纯假绿。
                passed = actual is not None and not _is_blank(actual)
            elif operator == "not_exists":
                # 路径压根取不到值：过滤没命中、字段不在、数组为空取不到下标。
                # ⚠ 字段名写错也一样取不到 → 这条会**恒真**。所以回推门禁要求
                # 同一条路径在前面有一步断过 not_empty/==（证明它取得到过），
                # 见 sync._missing_path_baseline。
                passed = actual is None
            elif operator == "is_empty":
                # 只有"取到了且是空容器"才算通过。字段不存在（None）**不算空**：
                # 那是接口改了字段名，跟"列表为空"是两件事，混在一起会把改名假绿掉。
                passed = actual is not None and _is_blank(actual)
            elif operator == "length":
                # **对象不算**。`data.items[name=x]` 过滤出来的是一个对象，
                # 它的 len 是键数 —— 拿它断「恰好一条」会因为"这条对象刚好有 1 个字段"
                # 而通过，是纯假绿。断条数只能对列表用。
                if isinstance(actual, (list, tuple, str)) and _as_number(expected) is not None:
                    passed = len(actual) == _as_number(expected)
                    actual = len(actual)
                else:
                    results.append({**a, "passed": False, "actual": actual,
                                    "error": "length 只能用在数组/字符串上（对象的长度是键数，"
                                             "拿它断条数会假绿），期望值要是数字"})
                    continue
            elif operator in (">", "<", ">=", "<="):
                a_num, e_num = _as_number(actual), _as_number(expected)
                if a_num is None or e_num is None:
                    results.append({**a, "passed": False, "actual": actual,
                                    "error": f"{operator} 要两边都是数字；"
                                             f"实际取到 {actual!r}，期望值 {expected!r}"})
                    continue
                passed = {">": a_num > e_num, "<": a_num < e_num,
                          ">=": a_num >= e_num, "<=": a_num <= e_num}[operator]
            elif operator == "contains":
                passed = expected is not None and str(expected) in str(actual)
            elif operator == "not_contains":
                passed = expected is not None and str(expected) not in str(actual)

        results.append({**a, "passed": passed, "actual": actual})
    return results


def _inject_accept_language(headers: dict, env: dict) -> None:
    """按 `TEST_LANGUAGE` 给请求带上 `Accept-Language`。

    **这是 `${T:}` 的另一半，此前是缺的。** 断言那边按 TEST_LANGUAGE 取译文，
    请求这边却从来不告诉被测系统"我要哪个语种" —— 于是切到 en 只有期望值变了，
    响应还是原样，`${T:}` 断言必红。而这是**假红**：排查的人会去查产品，
    查半天发现是测试自己没把语种发过去。

    步骤自己写了 Accept-Language 就不动它（大小写都算），那是有意为之。
    """
    lang = (env.get("TEST_LANGUAGE") or "").strip().lower()
    if not lang or not isinstance(headers, dict):
        return
    if any(str(k).lower() == "accept-language" for k in headers):
        return
    headers["Accept-Language"] = _LANG_TO_TAG.get(lang, lang)


_LANG_TO_TAG = {"zh": "zh-CN", "en": "en-US"}


async def run_single_step(
    step: ApiTestStep,
    env: dict,
    client: httpx.AsyncClient,
    token_cache: TokenCache | None = None,
    origins: dict | None = None,
    step_index: int = 0,
) -> StepResult:
    if not step.enabled:
        return StepResult(
            step_id=str(step.id), step_name=step.name,
            method=step.method, url=step.url, status="skip",
        )

    url = _resolve_variables(step.url, env)
    headers = _resolve_obj(step.headers or {}, env)
    body = _resolve_obj(step.body, env)
    _inject_accept_language(headers, env)

    # 变量没解析出来时 _resolve_variables 会原样留下 ${NAME}，直接发出去只会得到
    # 一个莫名其妙的 404/422，看不出是"环境/场景变量没配"。这里发之前就拦下来说清楚。
    unresolved = _unresolved_refs(url, headers, body)
    if unresolved:
        names = "、".join(f"${{{n}}}" for n in unresolved)
        return StepResult(
            step_id=str(step.id), step_name=step.name,
            method=step.method, url=step.url, status="fail", duration=0,
            error=(
                f"变量未解析：{names}。请求未发出。\n"
                "**先往上看**：如果这些变量本该由前面的步骤 variables_extract 提取，"
                "那问题在上一步 —— 它可能断言过了但没取到值（看那一步的错误）。\n"
                "其次检查：①所选执行环境是否配了这些键（环境管理）"
                "②提取步骤的顺序对不对 ③是否该在用例「场景变量」里定义。"
            ),
            request_data={
                "method": step.method, "url": url,
                "urlTemplate": step.url,
                "headers": headers, "body": body,
                "variablesUsed": _used_variables(origins or {}, env, step.url, step.headers, step.body),
                "preScript": step.pre_script, "postScript": step.post_script,
            },
        )

    auth_origin = None
    if "Authorization" in headers:
        auth_origin = "步骤自己设的 Authorization 头（值由上面的变量解析而来）"
    else:
        if "AUTH_TOKEN" in env:
            headers["Authorization"] = f"Bearer {env['AUTH_TOKEN']}"
            auth_origin = "平台自动补：环境/上游提取里存在 AUTH_TOKEN"
        elif token_cache:
            token = await token_cache.get_token(client)
            if token:
                headers["Authorization"] = f"Bearer {token}"
                auth_origin = "平台自动登录取得（用所选环境的 ADMIN_USERNAME/ADMIN_PASSWORD 调 LOGIN_URL）"
    if "Content-Type" not in headers and body:
        headers["Content-Type"] = "application/json"

    # headers 不再截断 Authorization —— 以前存的是 eyJ0eXAiO...UilM，
    # 想核对 token 对不对根本没法看。请求体里的 password 仍然遮，那是长期凭据。
    request_data = {
        "method": step.method, "url": url,
        "urlTemplate": step.url,
        "headers": headers,
        "body": body,
        "params": _resolve_obj(getattr(step, "params", None), env),
        "authOrigin": auth_origin,
        "variablesUsed": _used_variables(origins or {}, env, step.url, step.headers, step.body),
        "preScript": step.pre_script,
        "postScript": step.post_script,
    }

    start = time.time()
    try:
        resp = await client.request(method=step.method, url=url, headers=headers, json=body if body else None)

        # 401 被动重试：断言不预期 401 且有 TokenCache → 刷新 token 重试一次
        if resp.status_code == 401 and token_cache and not _expects_status(step.assertions, 401):
            token = await token_cache.refresh_token(client)
            if token:
                headers["Authorization"] = f"Bearer {token}"
                resp = await client.request(method=step.method, url=url, headers=headers, json=body if body else None)

        duration = int((time.time() - start) * 1000)

        try:
            resp_body = resp.json()
        except Exception:
            resp_body = {"_text": resp.text[:2000]}

        # 断言里的 ${var} 也要解析（如 data.name == ${svcName}），与 url/headers/body 口径一致
        resolved_assertions = _resolve_obj(step.assertions or [], env)
        assertion_results = _check_assertions(resolved_assertions, resp.status_code, resp_body)
        all_pass = all(a["passed"] for a in assertion_results) if assertion_results else True

        extracted: list[dict] = []
        if step.variables_extract:
            for var_name, path in (step.variables_extract or {}).items():
                # 提取路径里的 ${var} 也要解析 —— 过滤器最有用的地方正是这里：
                # `data[description=${svcName}].id` 才是"拿到本次那条的 id"的正解。
                # 不解析的话只能退回 `data[0].id`，而那是规范自己禁止的写法。
                path = _resolve_variables(str(path), env)
                val = _extract_value(resp_body, path) if resp_body else None
                if val is not None:
                    env[var_name] = str(val)
                    _note_origin(origins, var_name, "extract",
                                 f"第 {step_index + 1} 步「{step.name}」从响应 {path} 提取")
                extracted.append({
                    "name": var_name, "path": path,
                    "value": None if val is None else str(val),
                    "ok": val is not None,
                    # 取不到时把响应顶层键列出来 —— 十有八九是路径写错了层级，
                    # 光说"取不到"人还得自己去翻响应体。
                    "availableTopKeys": (
                        None if val is not None or not isinstance(resp_body, dict)
                        else sorted(resp_body.keys())[:12]
                    ),
                })
        request_data["extracted"] = extracted

        # 提取失败必须在**当步**说出来。原先只是静默记 ok:false，
        # 报错落到下一步的「变量未解析」上，把人指向环境变量 —— 而根因在这里。
        failed_ex = [e for e in extracted if not e["ok"]]
        if failed_ex and all_pass:
            detail = "；".join(
                f"{e['name']} ← {e['path']}"
                + (f"（响应顶层只有 {'/'.join(e['availableTopKeys'])}）"
                   if e.get("availableTopKeys") else "")
                for e in failed_ex
            )
            all_pass = False
            extract_error = (
                f"这一步的响应里没取到：{detail}。\n"
                "断言是过了的，但后面用到这些变量的步骤会全部失败。\n"
                "路径写法：点号 + 数组下标，如 data.token / data.items[0].id"
                "（`$.` 前缀也接受）。"
            )
        else:
            extract_error = None

        return StepResult(
            step_id=str(step.id), step_name=step.name,
            method=step.method, url=url,
            status="pass" if all_pass else "fail",
            status_code=resp.status_code, duration=duration,
            assertions=assertion_results,
            response_body=resp_body if isinstance(resp_body, (dict, list)) else {"_text": str(resp_body)},
            error=extract_error,
            request_data=request_data,
        )
    except Exception as e:
        duration = int((time.time() - start) * 1000)
        return StepResult(
            step_id=str(step.id), step_name=step.name,
            method=step.method, url=url,
            status="fail", duration=duration, error=str(e)[:500],
            request_data=request_data,
        )



async def run_step(
    step: ApiTestStep,
    env: dict,
    client: httpx.AsyncClient,
    token_cache: "TokenCache | None" = None,
    origins: dict | None = None,
    step_index: int = 0,
) -> StepResult:
    """跑一步，带**等待**和**重试**。

    被测系统的配置下发常是异步的（实测网关从「发布成功」到真能转发要 0.06~0.5s
    且抖动），而步骤之间只隔几毫秒 —— 「发布完立刻打网关」必然抢跑，跑出来是红的，
    但那不是缺陷，是这条用例自己没等。**假红比漏测更毒**：它让整份报告不可信，
    人看两次就不看了。

    - `wait_ms`：发之前先等。下策 —— 要么白等要么不够，换台机器就崩。
    - `retry_timeout_ms`：断言没过就整步重发，直到过了或超时。等的是"它真的好了"。

    ⚠ 重试会**重发请求**。写操作（POST/PUT/DELETE）上开重试会造出多份数据 ——
    所以只该用在"读回来确认"的那种步骤上。回推工具里对写操作开重试会软警告。
    """
    if getattr(step, "wait_ms", 0):
        await asyncio.sleep(step.wait_ms / 1000)

    timeout_ms = getattr(step, "retry_timeout_ms", 0) or 0
    result = await run_single_step(step, env, client, token_cache, origins=origins, step_index=step_index)
    if timeout_ms <= 0 or result.status != "fail":
        return result

    interval = (getattr(step, "retry_interval_ms", 0) or 300) / 1000
    deadline = time.monotonic() + timeout_ms / 1000
    attempts = 1
    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        attempts += 1
        result = await run_single_step(step, env, client, token_cache, origins=origins, step_index=step_index)
        if result.status != "fail":
            # 说清"重试了几次才过" —— 一次就过和试了 8 次才过不是一回事，
            # 后者说明这个等待窗口快不够了，早晚会变成偶发红。
            result.error = (result.error or "") or None
            result.step_name = f"{step.name}（重试 {attempts} 次后通过）"
            return result
    result.error = (f"重试 {attempts} 次、等了 {timeout_ms}ms 仍然没过。"
                    f"要么被测系统真有问题，要么这个窗口还不够长。\n"
                    + (result.error or ""))
    return result


async def _auto_create_resource(client, base: str, headers: dict, res, env: dict) -> dict:
    """探到「确实没有」时，照 `create_def` 把共享底座建出来。

    **为什么改成平台自建。** 原来的规矩是"探不到就报变量未解析，你自己去造" ——
    纪律上说得通，实践上把一个资源缺失放大成一整批脚本红：二十条链都引用同一个
    共享上游，它没了就二十条一起挂，而每条链自己都没有能力兜（接口场景是声明式
    JSON，写不出 if/else）。资源怎么造**已经登记在 create_def 里**了，那就该由
    平台在跑之前补上，而不是让人挨个去救。

    三道闸，一道都不能少：
      · **只在 state=missing 时建** —— 那是"请求成功且明确没匹配上"。
        401/5xx/超时是 unknown，一次 token 过期就照着建会造出一堆重复底座。
      · **只试一次，不重试** —— 失败就如实报，让链子红在"变量未解析"上并带上原因。
      · **4xx 冲突当成"别人刚建好"** —— 并发跑时两条链可能同时探到 missing，
        撞唯一约束的那条不该失败，复探一次就能拿到那个 id。
    """
    cd = res.create_def or {}
    url = str(cd.get("url") or "")
    if not url:
        return {"ok": False, "reason": "create_def 里没有 url"}
    url = _resolve_variables(url, env)
    if not url.lower().startswith("http"):
        url = f"{base}/{url.lstrip('/')}"
    method = (cd.get("method") or "POST").upper()
    body = _resolve_obj(cd.get("body"), env)
    h = {**headers, "Content-Type": "application/json"}
    try:
        resp = await client.request(method, url, headers=h, json=body)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"补建请求失败: {e}"}
    if resp.status_code < 300:
        return {"ok": True, "reason": f"已按 create_def 补建（{method} {url} → {resp.status_code}）"}
    if resp.status_code in (400, 409, 422):
        return {"ok": True, "conflict": True,
                "reason": f"补建撞了冲突（{resp.status_code}）—— 多半是并发的另一条链刚建好，复探一次"}
    return {"ok": False, "reason": f"补建失败 HTTP {resp.status_code}: {resp.text[:160]}"}


async def _resolve_automation_resources(session, scenario, env: dict, token_cache=None) -> dict:
    """把项目级前置资源（automation_resources）的 extract 值解析成变量。

    这些资源是"环境里本该长期存在的基础数据"（上游/负载、隔离上下文……）。
    以前只有存在性预检、拿不到 id，编排链只能把 UUID 写死；现在跑前探一次、
    按 exists_check.extract 抽出 id 注入 env，步骤里就能 ${资源名} 引用。
    """
    from app.services import precheck_service

    # 三个提前返回必须跟正常出口同形（dict, list）——调用方是按两个值解包的。
    # 之前这里 return {} 会让每次运行都抛 ValueError 被 try/except 吞掉，
    # 等于这条路从来没真跑起来过，日志里只留一句"解析项目级前置资源失败"。
    project_id = getattr(scenario, "project_id", None)
    if not project_id:
        return {}, []
    base = (env.get("BASE_URL") or "").rstrip("/")
    if not base:
        return {}, [{"name": "-", "ok": False, "reason": "当前环境没有 BASE_URL，无法探测前置资源"}]

    from app.models.automation_resource import AutomationResource

    resources = (await session.execute(
        select(AutomationResource).where(AutomationResource.project_id == project_id)
    )).scalars().all()
    if not resources:
        return {}, []

    out: dict = {}
    report: list[dict] = []
    async with httpx.AsyncClient(timeout=15, verify=False) as client:
        for res in resources:
            # **每条资源可以指定用哪个角色的 token**（exists_check.role，默认 ADMIN）。
            # 实测撞到：读上游 ADMIN 能读，但 `upstream:create` 是租户管理员的能力，
            # 拿 ADMIN 去补建回 403 INSUFFICIENT_CAPABILITY。角色写死成 ADMIN 就等于
            # 「能探不能补」，而补建恰恰是这条路的价值所在。
            role = str((res.exists_check or {}).get("role") or "ADMIN").upper()
            headers = {}
            token = env.get("AUTH_TOKEN")
            if not token and token_cache is not None:
                token = await token_cache.get_token(client, role)
            if token:
                headers["Authorization"] = f"Bearer {token}"
            try:
                item = await precheck_service._check_one(client, base, headers, res)
            except Exception as ex:
                report.append({"name": res.name, "ok": False, "reason": f"探测异常: {ex}"})
                continue
            # 确实没有 + 登记过怎么造 → 平台补建，然后复探一次。
            # 只认 missing（探测请求成功且明确没匹配上），unknown 一律不动。
            created_note = None
            if item.get("state") == "missing" and res.create_def:
                r = await _auto_create_resource(client, base, headers, res, env)
                created_note = r["reason"]
                if r.get("ok"):
                    try:
                        item = await precheck_service._check_one(client, base, headers, res)
                    except Exception as ex:  # noqa: BLE001
                        report.append({"name": res.name, "ok": False,
                                       "reason": f"补建后复探异常: {ex}", "autoCreate": created_note})
                        continue

            vals = item.get("values") or {}
            for k, v in vals.items():
                out[k] = v
            if res.name not in out and item.get("exists") and vals:
                first = next(iter(vals.values()), None)
                if first is not None:
                    out.setdefault(res.name, first)
            if not item.get("exists"):
                report.append({"name": res.name, "ok": False,
                               "reason": item.get("reason") or "未在当前环境找到",
                               **({"autoCreate": created_note} if created_note else {}),
                               **({"hint": "登记 create_def 之后平台会在跑前自动补建 —— "
                                           "这条没登记，所以只能你自己造"}
                                  if not res.create_def and item.get("state") == "missing" else {})})
            elif not vals:
                report.append({"name": res.name, "ok": False,
                               "reason": "资源存在，但 exists_check.extract 没抽到值（检查 JSONPath）"})
            else:
                row = {"name": res.name, "ok": True, "vars": sorted(vals.keys())}
                if created_note:
                    # 补建这件事必须说出来：环境被平台改过，人要知道
                    row["autoCreated"] = created_note
                report.append(row)
    return out, report


async def run_scenario(
    scenario: ApiTestScenario,
    steps: list[ApiTestStep],
    session: AsyncSession,
    base_env: dict | None = None,
    token_cache: TokenCache | None = None,
    env_name: str | None = None,
    persist: bool = True,
) -> AsyncIterator[RunEvent]:
    """persist=False：照常跑、照常发事件，但**不写 last_status / last_response、不 commit**。

    给变异验证用（故意跳掉动作步、看验证步会不会红）。那种运行天然是全红的，
    写进去会把用例的接口维度、页面上的步骤状态、执行历史全带成"这条挂了" ——
    而它其实是一次**诊断**，不是一次回归。
    """
    async with _run_semaphore:
        # 变量优先级：步骤提取 > 场景变量(SV_*) > 运行时 > 用户选择的环境(base_env) > 场景 env_variables
        # origins 与 env 平行维护，记录每个键"从哪来"，供运行详情做溯源展示。
        origins: dict[str, dict] = {}
        env = dict(scenario.env_variables or {})
        for k in env:
            _note_origin(origins, k, "scenario_env", "场景自带的 env_variables")
        for k, v in (base_env or {}).items():
            env[k] = v
            _note_origin(origins, k, "env", f"所选执行环境「{env_name or '未命名'}」的变量 {k}")
        _inject_runtime_variables(env, origins)
        # 源用例的场景变量：与 UI 脚本共用同一份定义（random 每次执行唯一）
        # 接口步骤里既可写 ${名字}（与抽屉提示一致），也可写 ${SV_名字}（与 UI 脚本 process.env.SV_x 同名）
        if getattr(scenario, "source_case_id", None):
            try:
                from app.services.scenario_variable_service import resolve_scenario_variables
                sv = await resolve_scenario_variables(session, scenario.source_case_id, global_lookup=env)
                for k, val in sv.items():
                    env[k] = val  # SV_<name> / SV_RUN_ID
                    _note_origin(origins, k, "scenario_var", f"用例「场景变量」定义的 {k}")
                    if k.startswith("SV_") and k != "SV_RUN_ID":
                        if k[3:] not in env:  # 裸名 ${名字}，不覆盖已有环境变量
                            env[k[3:]] = val
                            _note_origin(origins, k[3:], "scenario_var",
                                         f"用例「场景变量」定义的 {k[3:]}（同名 {k}）")
            except Exception as e:
                logger.warning("解析源用例场景变量失败 case_id=%s: %s", scenario.source_case_id, e)
        if token_cache is None:
            token_cache = TokenCache(env)

        # 项目级前置资源（自动化数据）：跑前按 exists_check 探一遍，把 extract 声明的值
        # 注入成变量，让步骤能写 ${资源名} 而不是写死 UUID。
        # 探测发生在所有步骤之前，此时还没登录过，必须用 TokenCache 自己换一个 token，
        # 否则被测系统直接 401、探不到任何东西（等于这条路白给）。
        precheck_report: list[dict] = []
        try:
            vals, precheck_report = await _resolve_automation_resources(session, scenario, env, token_cache)
            for k, v in vals.items():
                env[k] = v
                _note_origin(origins, k, "resource", f"项目级前置资源 exists_check 探测得到的 {k}")
        except Exception as e:
            logger.warning("解析项目级前置资源失败 scenario=%s: %s", scenario.code, e)

        yield RunEvent(type="scenario_start", data={
            "scenarioId": str(scenario.id),
            "title": scenario.title,
            "stepCount": len(steps),
        })

        # 跑前预检结论：缺哪个前置资源要当场说，别等用到它的那一步才报"变量未解析"
        if precheck_report:
            missing = [r for r in precheck_report if not r.get("ok")]
            created = [r for r in precheck_report if r.get("autoCreated")]
            yield RunEvent(type="precheck_result", data={
                "scenarioId": str(scenario.id),
                "total": len(precheck_report),
                "readyCount": len(precheck_report) - len(missing),
                "missing": missing,
                # 平台动了被测环境，必须说出来 —— 悄悄补建比不补建更糟
                "autoCreated": created,
                "resources": precheck_report,
            })

        results = []
        async with httpx.AsyncClient(timeout=30, verify=False) as client:
            for i, step in enumerate(steps):
                result = await run_step(step, env, client, token_cache, origins=origins, step_index=i)
                results.append(result)

                if persist:
                    step.last_status = result.status
                    step.last_response = {
                        "statusCode": result.status_code,
                        "duration": result.duration,
                        "body": result.response_body,
                        "assertions": result.assertions,
                        "request": result.request_data,
                    } if not result.error else {"error": result.error,
                                                "request": result.request_data}

                # 详情随事件一起下发。以前只发状态码，前端要靠内存里那份 scenario 的
                # lastResponse 取详情 —— 那是跑之前加载的，跑完不刷新就是空，展开只会看到
                # 「暂无详情数据」。步骤在发请求前就挂掉（变量未解析）时更糟：statusCode
                # 是 null、耗时 0ms、面板上一行红字什么都不说，用户只能看到"全失败"。
                yield RunEvent(type="step_result", data={
                    "scenarioId": str(scenario.id),
                    "stepId": result.step_id,
                    "stepName": result.step_name,
                    "method": result.method,
                    "status": result.status,
                    "statusCode": result.status_code,
                    "duration": result.duration,
                    "error": result.error,
                    "request": result.request_data,
                    "responseBody": result.response_body,
                    "assertions": result.assertions,
                })

        if persist:
            await session.commit()
        # persist=False 时上面压根没往 step 上写东西，所以不 commit 就等于没留痕；
        # 不要在这里 rollback/expunge —— 那会连带清掉调用方 session 里别的东西。

        scenario_result = ScenarioResult(
            scenario_id=str(scenario.id),
            scenario_title=scenario.title,
            scenario_status=scenario.status,
            folder_id=str(scenario.folder_id) if scenario.folder_id else None,
            steps=results,
        )
        yield RunEvent(type="scenario_done", data={
            "scenarioId": str(scenario.id),
            "title": scenario.title,
            "passed": scenario_result.passed,
            "passCount": scenario_result.pass_count,
            "failCount": scenario_result.fail_count,
        })


async def run_batch(
    scenario_ids: list[uuid.UUID],
    session: AsyncSession,
    user_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    report_name: str | None = None,
    base_env: dict | None = None,
    branch_id: uuid.UUID | None = None,
    env_name: str | None = None,
    env_id: uuid.UUID | None = None,
    step_ids: set[str] | None = None,
) -> AsyncIterator[RunEvent]:
    """step_ids：**运行时**只跑这几步（页面上勾选的那些）。None = 全跑。

    刻意不复用步骤上的 `enabled` 字段：那是持久禁用，改它会写库、会影响别人
    和后续每一次回归；勾选是"这一次先只跑这几步"，跑完不留痕。

    没被勾选的步骤**整个不进 steps 列表** —— 不是标成 skip。区别在于
    skip 会把 `last_status` 覆盖成 'skip'，等于把上一次的真实结果擦掉；
    而"这次没跑它"本来就不该动它上次的结论。
    """
    all_results: list[ScenarioResult] = []
    # 批量执行共享 TokenCache：同一角色只登录一次（ADR-3）
    shared_env = dict(base_env or {})
    # 文案词典：断言里的 ${T:中文} 靠它换语种。挂在这里 = 一次批量只查一次库。
    # 取不到就留空 —— _t() 查不到会原样返回中文，不会因此挂掉。
    if "__I18N__" not in shared_env:
        try:
            from app.services.i18n_harvest_service import load_locale_table
            pid = project_id or await _project_of_scenarios(session, scenario_ids)
            shared_env["__I18N__"] = await load_locale_table(session, pid) if pid else {}
        except Exception:  # noqa: BLE001
            shared_env["__I18N__"] = {}
    token_cache = TokenCache(shared_env)

    for sid in scenario_ids:
        scenario = await session.get(ApiTestScenario, sid)
        if not scenario:
            continue

        steps_result = await session.execute(
            select(ApiTestStep)
            .where(ApiTestStep.scenario_id == sid)
            .order_by(ApiTestStep.sort_order)
        )
        steps = steps_result.scalars().all()

        if step_ids is not None:
            steps = [s for s in steps if str(s.id) in step_ids]
            if not steps:
                # 一个都没勾还点了运行 —— 必须当场说清楚。放它跑下去的话
                # 场景里 0 步、passed 恒真，页面会显示「全通过 0/0 步」。
                yield RunEvent(type="error", data={
                    "scenarioId": str(scenario.id),
                    "message": f"「{scenario.title}」没有勾选任何步骤，本次未执行。",
                })
                continue

        scenario_result = None
        async for event in run_scenario(scenario, steps, session, base_env=base_env, token_cache=token_cache, env_name=env_name):
            yield event
            if event.type == "scenario_done":
                scenario_result = ScenarioResult(
                    scenario_id=str(scenario.id),
                    scenario_title=scenario.title,
                    scenario_status=scenario.status,
                    folder_id=str(scenario.folder_id) if scenario.folder_id else None,
                    source_case_id=(
                        str(scenario.source_case_id) if scenario.source_case_id else None
                    ),
                )
                # url 取**实际发出去**的那个，不是步骤定义里的模板。
                # 以前直接用 s.url，报告里就成了 ${BASE_URL}/api/auth/login，而同一屏的
                # 请求头又是真 token —— 一半变量一半真值，拿它根本没法定位问题。
                scenario_result.steps = [
                    StepResult(
                        step_id=str(s.id), step_name=s.name,
                        method=s.method,
                        url=((s.last_response or {}).get("request") or {}).get("url") or s.url,
                        status=s.last_status or "skip",
                        status_code=s.last_response.get("statusCode") if s.last_response else None,
                        duration=s.last_response.get("duration", 0) if s.last_response else 0,
                        assertions=s.last_response.get("assertions", []) if s.last_response else [],
                        response_body=s.last_response.get("body") if s.last_response else None,
                        error=s.last_response.get("error") if s.last_response else None,
                        request_data=s.last_response.get("request") if s.last_response else None,
                    ) for s in steps
                ]
        if scenario_result:
            all_results.append(scenario_result)

    # 批量执行统一生成报告（不区分草稿/已发布，单步调试走 run-step 不经过这里）
    if all_results and user_id:
        folder_name = await _resolve_common_folder_name(session, all_results)
        report_id = await _create_report(session, all_results, user_id, project_id, report_name,
                                         folder_name, branch_id, env_id=env_id)
        yield RunEvent(type="report_created", data={"reportId": str(report_id)})

    yield RunEvent(type="run_done", data={"totalScenarios": len(scenario_ids)})


async def _resolve_common_folder_name(session: AsyncSession, results: list[ScenarioResult]) -> str | None:
    """所有场景同属一个文件夹时返回文件夹名（报告命名用）。"""
    folder_ids = {r.folder_id for r in results}
    if len(folder_ids) != 1:
        return None
    folder_id = folder_ids.pop()
    if not folder_id:
        return None
    from app.models.api_test_folder import ApiTestFolder
    folder = await session.get(ApiTestFolder, uuid.UUID(folder_id))
    return folder.name if folder else None


_ASSERT_LABELS = {
    "status": "状态码",
    "body_field": "响应字段",
    "body_contains": "响应包含",
    "not_contains": "响应不含",
    "header": "响应头",
    "json_path": "JSONPath",
    "duration": "耗时",
}


def describe_assertion(a: dict) -> str:
    """一条断言写成人话：`响应字段 data.total == 3`。

    断言原文是 `{type, operator, value/expected, field, actual, passed}`。
    只印 type（"✓ status"、"断言未通过: body_field"）等于没说 —— 人要看的是
    "断言了什么、期望多少、实际多少"。实测 CC 拿到 `断言未通过: body_field`
    没法自己修，只能绕过，而这些字段本来就在对象里带着。

    **这是唯一的渲染口径**：报告轨迹、MCP 返回都用它，前端 RunResultPanel 里
    那份是它的镜像（改了这儿记得同步，两处说法不一样比不说更糟）。
    """
    field = field_of(a, a.get("operator")) or ""
    exp = expected_of(a)
    if exp is not None and str(exp) == str(field):
        exp = ""          # 历史形态 `{value: 字段路径}`：别把路径当期望值再印一遍
    return a.get("message") or " ".join(
        str(x) for x in (
            _ASSERT_LABELS.get(a.get("type"), a.get("type") or "断言"),
            field,
            a.get("operator") or "==",
            "" if exp is None else exp,
        ) if str(x) != ""
    )


_TYPE_CN = {bool: "布尔", int: "数字", float: "数字", str: "字符串",
            list: "数组", dict: "对象", type(None): "null"}


def _type_hint(a: dict) -> str:
    """两边**看起来一样、类型不一样**时把类型点出来，并说清怎么改。

    不点出来的话报错长这样：「期望 data.enabled == true，实际 True」——
    `true` 和 `True` 差一个大小写，谁都以为平台在说胡话，然后去怀疑判定逻辑。
    实测就是这么卡住的：AT-0011 断言里写的是字符串 "true"，响应里是布尔 true。

    数字那一类已经由 `_scalar_eq` 兜住（变量插值出来必然是字符串，不兜必然假红）。
    布尔**故意不兜** —— 放过去的话「期望 true、实际 1」也会算相等，那是假绿。
    所以这里只负责把话说明白，判定不放松。
    """
    expected = expected_of(a)
    actual = a.get("actual")
    if expected is None or actual is None:
        return ""
    if type(expected) is type(actual):
        return ""
    if str(expected).strip().lower() != str(actual).strip().lower():
        return ""      # 值本身就不同，类型不是重点
    et = _TYPE_CN.get(type(expected), type(expected).__name__)
    at = _TYPE_CN.get(type(actual), type(actual).__name__)
    fix = f"，断言里应写成 {json.dumps(actual, ensure_ascii=False)}（不加引号）" \
        if isinstance(actual, bool) else ""
    return f"（值一样但类型不同：期望是{et}、实际是{at}{fix}）"


def failure_detail(assertions, error) -> dict:
    """把一步失败的原因整理成给调用方看的东西。

    给 MCP 用：CC 那边只拿到 `{step, status, statusCode}` 时，看到"200 却 fail"
    是完全无解的 —— 实测就卡在这儿。
    """
    bad = [a for a in (assertions or []) if isinstance(a, dict) and not a.get("passed")]
    why = "；".join(
        describe_assertion(a)
        + (f"，实际 {a.get('actual')!r}" if a.get("actual") is not None else "")
        + _type_hint(a)
        for a in bad
    )
    if error:
        why = f"{why}｜{error}" if why else str(error)
    return {
        "why": why or "没有断言失败也没有错误信息 —— 这种情况本身就该报 bug",
        "failedAssertions": [{
            "type": a.get("type"), "field": a.get("field"),
            "operator": a.get("operator") or "==",
            "expected": expected_of(a),
            "actual": a.get("actual"),
        } for a in bad],
        # **通过的那几条也要列出来。** 只列失败的，等于看不出"其余断言到底求值了没有" ——
        # 实测 CC 卡在这儿：状态码那条恒失败（键名 bug），body_field 的新过滤语法
        # `data[*key=val]` 有没有被求值、取到了什么，返回里一个字都没有，
        # 于是"改对了没有"这件事无从判断。一条一行，够看清就行。
        "checked": [{
            "desc": describe_assertion(a),
            "passed": bool(a.get("passed")),
            "actual": a.get("actual"),
        } for a in (assertions or []) if isinstance(a, dict)][:12],
    }


def _first_error(result: ScenarioResult) -> str | None:
    """第一个挂掉的步骤的错误。摘要只留一条——列表里那一列本来就只显示一行。"""
    for s in result.steps:
        if s.status == "fail":
            return f"步骤「{s.step_name}」：{s.error or '断言不通过'}"
    return None


def _readable_trace(result: ScenarioResult) -> str:
    """给**人**看的执行轨迹，不是给机器解析的。

    接口执行以前只往报告里塞，用例那边什么都没有；现在要落 script_runs，
    顺手把 stdout 写成人话——UI 那边塞的是 pytest 原文，看不懂是实测反馈过的问题，
    接口这边没有必要重蹈一遍。断言逐条列出来，失败的标出期望和实际。
    """
    lines = [f"场景：{result.scenario_title}", ""]
    for i, s in enumerate(result.steps, 1):
        mark = {"pass": "✅", "fail": "❌", "skip": "⏭"}.get(s.status, "•")
        code = f" → {s.status_code}" if s.status_code is not None else ""
        lines.append(f"{mark} {i}. {s.step_name}  [{s.method} {s.url}{code}]  {s.duration}ms")
        for a in (s.assertions or []):
            if not isinstance(a, dict):
                continue
            desc = describe_assertion(a)
            if a.get("passed"):
                lines.append(f"      ✓ {desc}")
            else:
                lines.append(f"      ✗ {desc}｜实际 {a.get('actual')}")
        if s.error:
            lines.append(f"      错误：{s.error}")
    return "\n".join(lines)


async def _create_report(
    session: AsyncSession,
    results: list[ScenarioResult],
    user_id: uuid.UUID,
    project_id: uuid.UUID | None,
    report_name: str | None,
    folder_name: str | None = None,
    branch_id: uuid.UUID | None = None,
    run_mode: str | None = None,
    env_id: uuid.UUID | None = None,
) -> uuid.UUID:
    from datetime import datetime, timezone
    from app.models.report import TestReport, TestReportScenario, TestReportStep

    # `script_run_service` 在下面还有一处**函数内**导入，Python 因此把它当局部变量 ——
    # 在那之前引用就是 UnboundLocalError。第一版把 `mode = ...` 放在函数顶部，
    # 于是**整条页面「运行全部」路径直接抛异常**：报告没了、记账没了、状态也不推了，
    # 而 SSE 只回一句 error。所以这里提前统一导入一次，下面那处也删掉。
    from app.services import script_run_service

    # **默认按调试记，不按回归。** 走到这里的只有"人在页面上点了运行"这一种情况
    # （计划回归走 engine/tasks/adhoc_execution，那条不传 user_id、压根不进这里）。
    # 原来写死 REGRESSION，代价有三：① 执行历史里 UI 跑标「调试」、接口跑标「回归」，
    # 同一个详情页上的两个按钮说法不一致 ② 手动调试进回归通过率口径
    # ③ 更糟的是 REGRESSION 失败会把 api_status 打回 debugging，而断点续跑正是靠
    # 状态判待办 —— 人手动试一次没成功，CC 下一轮就把这条已完成的用例捡回来重做。
    mode = run_mode or script_run_service.DEBUG

    total_pass = sum(r.pass_count for r in results)
    total_fail = sum(r.fail_count for r in results)
    total_steps = sum(len(r.steps) for r in results)
    total_duration = sum(s.duration for r in results for s in r.steps)
    pass_rate = round(total_pass / total_steps * 100, 2) if total_steps > 0 else 0

    now = datetime.now(timezone.utc)
    # 名字里的时间给人看 → 本地时区；executed_at 照旧存 UTC。
    # 两边不一致时，同一行会显示相差 8 小时的两个时间。
    local = now.astimezone()
    if not report_name:
        if len(results) == 1:
            report_name = results[0].scenario_title
        elif folder_name:
            report_name = f"{folder_name} {local.strftime('%Y-%m-%d %H:%M')}"
        else:
            report_name = f"接口测试回归 {local.strftime('%Y-%m-%d %H:%M')}"

    report = TestReport(
        report_type="api_test",
        report_name=report_name,
        project_id=project_id,
        branch_id=branch_id,
        # 环境**必须落库**：库里 24/24 条接口报告的 environment_id 都是 NULL，
        # 报告列表那一列于是全是「-」。而"这次是打 staging 还是 prod"是复盘一份
        # 报告时第一个要问的问题 —— 值一直在调用方手上（页面上选的那个环境），
        # 只是从来没往下传。
        environment_id=env_id,
        executed_by=user_id,
        executed_at=now,
        completed_at=now,
        total_scenarios=len(results),
        passed=sum(1 for r in results if r.passed),
        failed=sum(1 for r in results if not r.passed),
        pass_rate=pass_rate,
        total_duration_ms=total_duration,
    )
    session.add(report)
    await session.flush()

    for i, result in enumerate(results):
        scenario_duration = sum(s.duration for s in result.steps)
        # 场景绑了用例就把用例一起记上：报告里能点回用例，用例那边也能反查到这次执行
        case = None
        if result.source_case_id:
            from app.models.case import Case
            case = await session.get(Case, uuid.UUID(result.source_case_id))
        report_scenario = TestReportScenario(
            report_id=report.id,
            case_id=case.id if case else None,
            case_code=case.case_code if case else None,
            scenario_name=result.scenario_title,
            status="passed" if result.passed else "failed",
            execution_type="automated",
            duration_ms=scenario_duration,
            sort_order=i,
        )
        session.add(report_scenario)
        await session.flush()

        # 同一次执行也落进 script_runs —— 用例的「执行历史」读的是这张表。
        # 不记的话，接口场景跑了多少次，用例页面上都是零。
        if case is not None:
            await script_run_service.record_run(
                session,
                case_id=case.id,
                script_type="api",
                result={
                    "status": "passed" if result.passed else "failed",
                    "duration_ms": scenario_duration,
                    "error_summary": _first_error(result),
                    "stdout": _readable_trace(result),
                },
                executed_by=user_id,
                run_mode=mode,
                report_scenario_id=report_scenario.id,
            )
            script_run_service.apply_case_status(
                case, "api",
                "passed" if result.passed else "failed",
                mode,
            )

        for j, step in enumerate(result.steps):
            session.add(TestReportStep(
                scenario_id=report_scenario.id,
                step_name=step.step_name,
                http_method=step.method,
                url=step.url,
                status=step.status,
                status_code=step.status_code,
                duration_ms=step.duration,
                sort_order=j,
                request_data=step.request_data,
                response_data=step.response_body,
                assertions=step.assertions,
                error_summary=step.error,
            ))

    await session.commit()
    logger.info("Created API test report %s: %s", report.id, report_name)
    return report.id


async def _project_of_scenarios(session, scenario_ids) -> object | None:
    """这批场景属于哪个项目 —— 取第一条的 branch → project。

    批量只会在同一个分支里跑（页面和 MCP 都是按分支选的），所以取第一条就够。
    """
    from sqlalchemy import select as _select

    from app.models.api_test import ApiTestScenario
    from app.models.project import Branch

    ids = list(scenario_ids or [])
    if not ids:
        return None
    bid = (await session.execute(
        _select(ApiTestScenario.branch_id).where(ApiTestScenario.id == ids[0])
    )).scalar_one_or_none()
    if not bid:
        return None
    return (await session.execute(
        _select(Branch.project_id).where(Branch.id == bid)
    )).scalar_one_or_none()
