"""MCP 回推同步工具 —— 把 Claude Code 活体验证过的成果显式写回 testBench。

范围（已与用户确认）：步骤用例 由 tb_create_case 负责；本模块负责另外三类回推——
  1. 用例编排的接口场景     tb_sync_orchestrated_scenario（核心新通道）
  2. 场景变量               tb_upsert_scenario_variables / tb_list_scenario_variables
  3. (只读) 项目级可引用数据 tb_list_global_data

核心纪律（用户强调）：脚本里**不允许写死数据变量**。所有取值只能来自
  ① 场景变量（${名字} / ${SV_名字}）
  ② 项目级全局引用（BASE_URL / token / 账号，来自环境变量、全局变量、自动化数据）
  ③ 步骤间提取物（上一步 variables_extract 出来的名字）
本模块在入库前做「悬空引用硬拦截 + 疑似写死软警告」把这条纪律落地。

口径（2026-08-15 起只剩一种，历史包袱写在这里免得再有人翻出来）：
  接口场景 = 本模块 tb_sync_orchestrated_scenario 回推的那种：与某功能用例绑定
  （source_case_id）、你亲手验证过的多步 E2E 链、共享该用例的场景变量。

  此前还有「单接口·凭文档 AI 造」（tb_generate_api_test，无 source_case_id），
  连同「接口测试」页面一起下线了 —— 不绑用例就拿不到场景变量，结构上跑不了。
  存量 47 条已清，source_case_id 收成 NOT NULL + 外键 CASCADE（迁移 zz9orph1），
  所以 source_case_id **必填**，不传直接被这里挡回去。
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_test import ApiTestScenario, ApiTestStep
from app.models.api_test_folder import ApiTestFolder
from app.models.case import Case
from app.services import script_service
from app.models.environment import EnvironmentVariable, GlobalVariable
from app.models.scenario_variable import ScenarioVariable
from app.models.user import User

# 运行时内置 / 特殊环境键（api_test_runner 会自动提供或消费），引用这些不算悬空
BUILTIN_VARS = {
    "RANDOM_8", "TIMESTAMP", "SV_RUN_ID",
    "BASE_URL", "LOGIN_URL", "AUTH_TOKEN", "TEST_TOKEN", "TOKEN",
}
_KINDS = ("literal", "random", "global_ref", "template")
# 场景变量一项只收这几个键 —— 多出来的一律报错，不静默丢。
_SV_KEYS = {"name", "kind", "value_template", "var_type", "description"}

_REF_RE = re.compile(r"\$\{(\w+)\}")             # 步骤插值语法 ${name}
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
# 这些键由环境变量直接注入，镜像成场景变量纯属多余
_ENV_MIRROR_KEYS = {
    "BASE_URL", "LOGIN_URL", "AUTH_TOKEN", "TOKEN",
    "ADMIN_USERNAME", "ADMIN_PASSWORD", "TENANT_USERNAME", "TENANT_PASSWORD",
    "ADMIN_USER", "ADMIN_PASS",
}
# AUTH/AUTHORIZATION/CREDENTIAL/COOKIE 也得算 —— create_def 里最常见的凭证
# 恰恰是 headers.Authorization，漏了它等于白脱敏。
# 含 PASS：ADMIN_PASS 这种写法此前漏网，明文进了 CC 的上下文。
# 脱敏这件事误报是安全方向（多盖一个值无所谓），漏报不是。
_SECRET_RE = re.compile(r"(PASSWORD|PASSWD|PASS|PWD|TOKEN|SECRET|KEY|AUTH|CREDENTIAL|COOKIE|SESSION)", re.I)
# 明显是结构值/枚举/路径，不该被当成「写死的业务数据」误报
_STRUCT_ENUM = {
    "true", "false", "null", "none", "yes", "no", "on", "off",
    "get", "post", "put", "delete", "patch", "head", "options",
    "asc", "desc", "string", "number", "boolean", "object", "array",
    "application/json", "text/plain", "multipart/form-data",
}



def _is_synthetic_uuid(val: str) -> bool:
    """这个 UUID 是不是"摆明编出来的"。

    负向测试要一个肯定不存在的 id：全 0、全 f、nil UUID、或者十六进制部分
    只用了一两种字符。这类值不指向任何真实资源，换环境也不会挂 ——
    不该按"环境里已存在的资源 id"来报警。
    """
    hexpart = val.strip().replace("-", "").lower()
    if len(hexpart) != 32:
        return False
    # 只由 0 / f / 少数几个字符拼成 —— 真实 UUID 不长这样
    return len(set(hexpart)) <= 3

def _loads(v: Any) -> Any:
    """MCP 客户端有时把 JSON 字段序列化成字符串，这里尽量还原成对象。"""
    if isinstance(v, str):
        s = v.strip()
        if s and s[0] in "[{":
            try:
                return json.loads(s)
            except (ValueError, TypeError):
                return v
    return v


def _iter_strings(obj: Any):
    """深度遍历 JSON 结构，产出 (json路径, 字符串叶子)。"""
    def walk(node, path):
        if isinstance(node, dict):
            for k, val in node.items():
                yield from walk(val, f"{path}.{k}" if path else str(k))
        elif isinstance(node, list):
            for i, val in enumerate(node):
                yield from walk(val, f"{path}[{i}]")
        elif isinstance(node, str):
            yield path, node
    yield from walk(obj, "")


def _collect_refs(*objs: Any) -> set[str]:
    """收集若干对象里所有 ${name} 引用名。"""
    names: set[str] = set()
    for obj in objs:
        for _, s in _iter_strings(obj):
            names.update(_REF_RE.findall(s))
    return names


# 字段名一看就是"枚举槽位"的。这些位置上的短小写标识符是**被测系统的契约**，
# 写死才是对的 —— 把 service_type="api"、protocol="http" 标成"疑似写死"，
# 等于在教人把常量也做成变量，那比写死更糟。实测被误报过这两个。
_ENUM_FIELD_RE = re.compile(
    r"(^|[._])(type|protocol|status|state|mode|method|scheme|kind|level|role|"
    r"category|format|action|strategy|policy|direction|unit)$", re.I)
# 枚举值长什么样：短的小写标识符。`api` / `http` / `round_robin` / `not_started`
_ENUM_VALUE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,20}$")


_BOOL_STRINGS = {"true", "false", "True", "False"}


# 断言的期望值/字段路径**只有一个取法**，跟执行器共用 —— 显示和判定各挑一个键名，
# 就是「期望 200，实际 200，判失败」那个 bug 的形状（见 expected_of 的说明）。
from app.services.api_test_runner import (  # noqa: E402
    _DEFAULT_OP as _DEF_OP, expected_of as _exp_of, field_of as _field_of,
)

# 这些 operator 必须有期望值，没有就**永远判不过** —— 拦在入库
_NEEDS_EXPECTED = {
    "status": ("==", "!=", "in"),
    "body_contains": ("contains", "not_contains"),
    "body_field": ("==", "!=", "length", ">", "<", ">=", "<=", "contains", "not_contains"),
}


def _step_def_sig(st) -> str:
    """一个步骤的**定义**指纹（不含运行结果）。取字典或 ORM 对象都行。"""
    get = st.get if isinstance(st, dict) else (lambda k, d=None: getattr(st, k, d))
    return json.dumps([
        (get("method") or "GET").upper(), str(get("url") or "").strip(),
        get("headers"), get("body"), get("assertions"), get("variables_extract"),
        bool(get("enabled", True)),
        int(get("wait_ms") or 0), int(get("retry_timeout_ms") or 0),
        int(get("retry_interval_ms") or 300),
    ], sort_keys=True, ensure_ascii=False, default=str)


def _carried_evidence(carry: dict, st: dict) -> tuple:
    """这个步骤能不能沿用上一次运行的结果 → (last_status, last_response)。

    **定义变了就丢。** 改了 url/断言/提取的步骤，旧的 last_response 已经不代表它了，
    留着会让 tb_check_env_hygiene 拿过期的 id 去报残留 —— 那比看不见更糟。
    """
    sig, status, resp = carry.get(str(st.get("name") or ""), (None, None, None))
    return (status, resp) if sig is not None and sig == _step_def_sig(st) else (None, None)


def _canon_assertion(a: dict) -> dict:
    """键名归一到**前端编辑器的口径**：status / body_contains 用 `value`，
    body_field 用 `field` + `expected`。

    执行器现在两种键名都认（`expected_of`），这里只管别让库里长出两种形状：
    存两种的话，页面上打开再保存会规回一种，于是 diff 里多出一堆无意义变更，
    而"库里到底是哪一种"又变回靠猜。
    """
    if not isinstance(a, dict):
        return a
    out = dict(a)
    a_type = out.get("type")
    op = out.get("operator") or _DEF_OP.get(a_type, "==")
    exp = _exp_of(out)
    if a_type == "body_field":
        fld = _field_of(out, op)
        out.pop("value", None)
        if fld is not None:
            out["field"] = fld
        if op in ("not_empty", "is_empty", "not_exists") or exp is None or exp == fld:
            out.pop("expected", None)     # 这几个 operator 不看期望值
        else:
            out["expected"] = exp
    else:
        out.pop("expected", None)
        if exp is not None:
            out["value"] = exp
    return out


def _unevaluatable_assertions(seq: int, st: dict) -> list[dict]:
    """**永远判不过**的断言 —— 期望值缺了，或 body_field 没给字段路径。

    这类不能放行：它不是"可能红"，是**必然红，而且报错像平台在说胡话**。
    实测（CC 的 23 步场景）：状态码断言写成 `{"type":"status","expected":200}`，
    而执行器那时只读 `value` → 拿到 None → 200 == None 判失败，报错却打印「期望 200」。
    键名那半已经在执行器里收口了（两种都认）；这里拦的是**真的什么都没给**那半 ——
    否则同一个"全红且看不懂"的现象会换个入口再来一次。
    """
    out = []
    for a in (st.get("assertions") or []):
        if not isinstance(a, dict):
            continue
        a_type = a.get("type")
        op = a.get("operator") or _DEF_OP.get(a_type, "==")
        name = st.get("name") or f"step{seq}"
        if a_type == "body_field" and _field_of(a, op) is None:
            out.append({"step": seq, "name": name, "assertion": a,
                        "why": "body_field 没给字段路径（field）—— 执行器会拿整个响应体去比，必然不过。"})
            continue
        if op in _NEEDS_EXPECTED.get(a_type, ()) and _exp_of(a) is None:
            out.append({"step": seq, "name": name, "assertion": a,
                        "why": f"{a_type} {op} 必须有期望值（expected 或 value），"
                               f"这条一个都没给 —— 跑起来永远判不过。"})
    return out


def _typo_assertions(seq: int, st: dict) -> list[dict]:
    """断言的期望值被写成字符串 `"true"`，而响应里多半是布尔 —— 大概率假红。

    **这条从硬拦降成了警告**（判据规范 ① + 附则）：反例是"有些老接口真的返回
    `"enabled": "true"`"，那时写字符串才是对的，硬拦等于逼人写错。
    平台在回推那一刻并不知道这个接口返回什么类型 —— 没跑过就没有证据。
    有证据的时候（历史执行记录里那个字段确实是布尔）才配硬拦，那个判定放在评审侧做。

    为什么值得硬拦而不是软警告：这类错误只有跑起来才暴露，报错还长得像平台
    在说胡话（「期望 data.enabled == true，实际 True」差一个大小写），于是人先去
    怀疑判定逻辑，绕一圈才回到那对引号上。实测两轮各撞一次：
    TC-FWGL-00006 的 `rolled_back_to_version` 期望写成 "2"（实际数字 2）、
    TC-FWGL-00001 的 `data.enabled` 期望写成 "true"（实际布尔 true）。

    数字那一类平台已经兜住（`_scalar_eq`：变量插值出来永远是字符串，不兜必然假红），
    所以只拦布尔。布尔平台**故意不兜** —— 兜了「期望 true、实际 1」也会算相等，
    那是假绿，比假红难发现得多。`${var}` 不拦，那本来就该是字符串。
    """
    out = []
    for a in (st.get("assertions") or []):
        if not isinstance(a, dict):
            continue
        exp = _exp_of(a)
        if not isinstance(exp, str) or "${" in exp:
            continue
        if exp in _BOOL_STRINGS:
            out.append({
                "step": seq, "name": st.get("name") or f"step{seq}",
                "field": a.get("field") or a.get("type"),
                "wrote": f'"{exp}"', "shouldBe": exp.lower(),
                "why": "布尔写成了字符串。平台故意不做布尔兜底（兜了「期望 true、实际 1」"
                       "就会算相等，那是假绿），所以这里必挂。",
            })
    return out


# 异步下发之后立刻断言「已生效」的两种形状：读推送/同步状态，或直接打数据面。
_ASYNC_FIELD_RE = re.compile(r"(push|sync)[-_]?status|data\.(status|phase|synced_count)", re.I)
_DATA_PLANE_RE = re.compile(r"\$\{(gatewayBase|gateway_base|dataPlane|GATEWAY_URL)\}", re.I)


def _needs_retry(seq: int, st: dict) -> dict | None:
    """断的是「异步下发之后的结果」却没开重试 —— 抢跑假红，而且**时好时坏**。

    实测这批：6 条编排场景 23 个数据面/收敛断言里只有 4 个开了重试，其余 19 个裸奔 ——
    3 个当场挂、4 个**侥幸跑赢时间窗**。侥幸过的那几个最危险：看着是绿的，换台机器
    或换个时刻就红，然后没人分得清是环境抖动还是真缺陷。

    只警告不拦（有些接口确实是同步的，判不出来），但必须把建议值一起给出去 ——
    否则 CC 会退回插「等待 N 毫秒」的占位步骤凑时间窗，那正是这个字段要消灭的东西。
    """
    if int(st.get("retry_timeout_ms") or 0) > 0:
        return None
    # **写操作一律不建议加重试。** 重试的语义是整步重发，POST 重发就是多造一份数据 ——
    # 而本文件下面那条门禁正在为这件事发警告。同一个步骤同时收到「快加重试」和
    # 「加了会造脏数据」两条相反建议，是平台自己在打架，实测被 CC 指出来：
    # 6 条场景报了 19 处，全是 申请/驳回/审批/撤销 这类 POST —— 它们的 data.status
    # 是**同步响应直接回传的**，压根没有异步可等。真异步的是数据面下发，
    # 那些步骤本来就开着重试。
    # 平台文档里早就写着「只该用在『读回来确认』那种步骤上」，判据补上就是了。
    if (st.get("method") or "GET").upper() not in ("GET", "HEAD", "OPTIONS"):
        return None
    url = str(st.get("url") or "")
    assertions = json.dumps(st.get("assertions") or [], ensure_ascii=False)
    hits = []
    if _DATA_PLANE_RE.search(url):
        hits.append("这一步直接打数据面（网关），而配置下发是异步的")
    if _ASYNC_FIELD_RE.search(url) or _ASYNC_FIELD_RE.search(assertions):
        hits.append("这一步断的是推送/同步状态")
    if not hits:
        return None
    return {
        "step": seq, "field": "retry_timeout_ms",
        "value": f"{'；'.join(hits)}，但没开重试 —— 断言会抢在收敛之前跑，时好时坏。"
                 f"建议 retry_timeout_ms=10000（断言没过就整步重发，直到过或超时）。"
                 f"**不要**改成插一个「等待」步骤占时间窗。",
    }


# 步骤名里声明了「这是保持型断言」的词。声明过就不再唠叨。
# 「一致」「还原」也算：`最后回读：两个开关必须与执行前一致` 就是一条还原校验。
_STEADY_RE = re.compile(r"保持|不变|仍(应|然)|依旧|不中断|不应|全程|始终|一致|还原")


def _assert_sig(a: dict) -> tuple[str, str]:
    """(断的是哪个东西, 断成什么样)。

    **operator 归到"什么样"那一半**：`body_contains contains X` 和
    `not_contains X` 断的是同一个东西的两种结果，算进"值变了"才能认出
    「申请前没有 → 申请后有 → 驳回后又没有」这种状态确实动过的链。
    """
    key = json.dumps({k: a.get(k) for k in ("type", "field")}, sort_keys=True, ensure_ascii=False)
    val = json.dumps([a.get("operator"), _exp_of(a)],
                     sort_keys=True, ensure_ascii=False, default=str)
    return key, val


def _nondiscriminating(norm: list[dict]) -> list[dict]:
    """同一个请求上，这一步的断言和前面某一步**逐字相同** → 它不区分中间那些动作。

    这是「全绿但抓不到问题」里唯一能机械判定的一半。实测那条：
    TC-DYGL-00002 有一步「驳回后打网关仍 401」，而它从申请到驳回**全程都是 401**——
    驳回逻辑坏掉这条断言照样绿。平台判不了断言强弱，但判得出
    「同一 method+url 上，动作前后断的是同一件事」，那就等于没验动作。

    保持型断言本身是合法的（「弃用后存量调用不中断」正该这么写），
    所以**在步骤名里声明**（保持/不变/仍/依旧/始终…）就不再提示 ——
    平台要的不是它消失，是它别装成"验过了"。

    **在真实 23 条场景 / 460 步上标定过。** 直接按 method+url 比会报 42 条，
    绝大多数是假的，三条 narrowing 把它压到只剩真的那种：

    ① **只看读操作。** 写操作的状态码断言验的是**这一次调用**成不成功，
       天然不该跟上一次比（一级审批、二级审批都 POST /approve 都断 200，各自都对）。
    ② **headers 和 body 算进"同一个请求"。** 多角色场景里同一个 URL 是不同的人在读
       （`Authorization: Bearer ${tokenA}` vs `${tokenB}`），登录也是同一个 POST
       不同的账号 —— 那压根不是同一个请求。
    ③ **中间证明过状态离开又回来的，不算。** `基准 200 → 禁用后 404 → 启用后 200`
       里最后那个 200 是有效断言：启用没生效它就红在 404 上。判据是两次相同断言之间，
       同一请求上有没有**同一件事、不同期望值**的断言。
    """
    out: list[dict] = []
    # 每个请求签名下按顺序记 (第几步, {断的哪个东西: 断成什么样})
    seen: dict[tuple, list[tuple[int, dict[str, str]]]] = {}
    for i, st in enumerate(norm):
        method = (st.get("method") or "GET").upper()
        if method not in ("GET", "HEAD", "OPTIONS"):
            continue
        sig = (method, str(st.get("url") or "").strip(),
               json.dumps(st.get("headers"), sort_keys=True, ensure_ascii=False, default=str),
               json.dumps(st.get("body"), sort_keys=True, ensure_ascii=False, default=str))
        hist = seen.setdefault(sig, [])
        cur = dict(_assert_sig(a) for a in (st.get("assertions") or []) if isinstance(a, dict))
        if not cur:
            continue
        # **整步比，不逐条比。** 只要有一条断言不一样，这一步至少在那一维上是区分动作的；
        # 逐条比会把「恢复 200 + body 含 httpbin」里的 body 那条单独揪出来滥报。
        twin = next((idx for idx, prev in reversed(hist) if prev == cur), None)
        hist.append((i + 1, cur))
        if twin is None or _STEADY_RE.search(str(st.get("name") or "")):
            continue
        # twin 之后，同一个东西被断成过别的样子吗？断过就说明状态真的离开又回来了，
        # 这一步是在验"回来了"，不是恒真（基准 200 → 禁用后 404 → 启用后 200）。
        moved = any(idx > twin and any(k in cur and v != cur[k] for k, v in prev.items())
                    for idx, prev in hist)
        if moved:
            continue
        out.append({
            "step": i + 1, "field": "assertions",
            "value": f"这一步的断言和第 {twin} 步一模一样（同一个请求，中间没有任何断言"
                     f"证明它变过）—— 那几步动作坏掉它也不会红，等于没验动作。"
                     f"要么加一条能区分动作的断言（状态/字段值变成什么），"
                     f"要么在步骤名里写明这是保持型（保持/不变/仍/依旧）。",
        })
    return out


def _missing_path_baseline(norm: list[dict]) -> list[dict]:
    """断了「空 / 取不到」（not_exists、is_empty、length==0），但这条路径**从来没被证明过有东西**。

    活体跑回推链路时逼出来的一条：`data[name=${svcName}].id not_exists` 用来验
    「删完按名字查不到」是对的写法，但**字段名写错也一样取不到** —— 于是它恒真，
    而且是那种最舒服的恒真：一路全绿。

    判据是结构性的、不会误报：同一条 field 路径，在这一步之前有没有任何一步
    断过 not_empty / == / contains（都要求取到值）。有 = 基准建过了；没有 = 这条
    从头到尾没人证明过它取得到。
    """
    proven: set[str] = set()
    out: list[dict] = []
    for i, st in enumerate(norm):
        for a in (st.get("assertions") or []):
            if not isinstance(a, dict) or a.get("type") != "body_field":
                continue
            field = str(a.get("field") or "")
            op = a.get("operator") or "=="
            empty_ish = (op == "not_exists" or op == "is_empty"
                         or (op == "length" and str(a.get("expected")) == "0"))
            if empty_ish:
                if field and field not in proven:
                    out.append({
                        "step": i + 1, "field": "assertions",
                        "value": f"`{field}` 断的是「空/取不到」，但前面没有任何一步证明过"
                                 f"这条路径取得到值 —— **字段名写错也是取不到**，"
                                 f"这条会一路恒真。在删/清理之前加一步用同一条路径断 "
                                 f"not_empty（东西还在时它必须取得到），基准就有了。",
                    })
            elif op in ("not_empty", "==", "!=", "contains", "length", ">", "<", ">=", "<="):
                if field:
                    proven.add(field)
    return out


def _looks_hardcoded(value: str, field_path: str = "") -> bool:
    """疑似写死的业务数据（启发式，宁保守勿滥报——只软警告，不拦截）。

    `field_path` 是这个值在 body 里的位置（如 `config.protocol`）。**判据里最准的
    信号是字段名**：枚举槽位上的短标识符不算业务数据，那是接口契约的一部分。
    没有它就只能看值本身，于是 `api`、`http` 这种一律误报 —— 而误报的代价不只是
    噪音：警告多了人就不看了，真正写死的 id 反而被淹掉。
    """
    s = value.strip()
    if not s or "${" in s or "{{" in s:      # 有引用/模板 → 不算写死
        return False
    if len(s) < 3:                            # 太短（枚举/单字符）
        return False
    if s.startswith("/") or "://" in s:       # 路径 / URL 是结构，不算业务数据
        return False
    if s.lower() in _STRUCT_ENUM:
        return False
    if re.fullmatch(r"[\d.\-:+eE]+", s):      # 纯数字/时间戳/小数
        return False
    # 枚举字段上的枚举值 —— 该写死，不报
    leaf = (field_path or "").rsplit("[", 1)[0]
    if _ENUM_FIELD_RE.search(leaf) and _ENUM_VALUE_RE.fullmatch(s):
        return False
    return bool(re.search(r"[A-Za-z一-鿿]", s))  # 含字母或中文才像业务数据


async def _active_user_id(session: AsyncSession) -> uuid.UUID | None:
    """回推落库时的 created_by —— **优先用调用方自己的身份**。

    MCP 请求没有登录会话，但 API Key 上绑着 user_id，中间件已经按 key_hash 查出来了。
    此前这里直接取"第一个 active 用户"，于是多人一起用时所有人的回推都记成同一个
    admin：操作日志失去意义，「CC归因 vs 人确认」没法按人分桶，而且**这段历史事后
    补不回来**（行里永久写着 admin）。

    拿不到调用方身份（环境变量 key / 匿名放行）才退回兜底 —— executed_by 是
    NOT NULL FK，宁可记成兜底用户也不能让"脚本明明跑通了却存不下结果"。
    """
    try:
        from app.mcp.middleware import current_caller_user_id
        caller = await current_caller_user_id()
        if caller:
            uid = uuid.UUID(caller)
            exists = (await session.execute(
                select(User.id).where(User.id == uid, User.is_active.is_(True))
            )).scalar_one_or_none()
            if exists:
                return exists
    except Exception:  # noqa: BLE001
        pass
    return (
        await session.execute(
            select(User.id).where(User.is_active.is_(True))
            .order_by(User.role.asc(), User.created_at.asc()).limit(1)
        )
    ).scalar_one_or_none()


# ─────────────────────────────────────────────────────────────
# 1. 回推规范
# ─────────────────────────────────────────────────────────────

_SPEC_VARIABLES = """## 变量分层（回推纪律的基准，务必分清）

| 层 | 存哪 | 生命周期 | 影响范围 |
|---|---|---|---|
| 环境变量 | 环境管理（人工维护） | 长期 | 全项目 —— 描述"这个环境是什么" |
| 项目级共享资源 | 自动化数据（CC 造一次并登记） | 长期 | 全项目 —— 只读引用的底座 |
| 场景变量 | 用例·场景变量（tb_upsert_scenario_variables） | 定义长期、值可每次随机 | 单条用例（UI+接口共用） |
| 步骤提取物 | 步骤 variables_extract | **仅本次执行** | 本次运行的后续步骤 |

### 一个值该放哪一层？按顺序问，命中就停

默认放**最窄**的一层，只有明确需要共享才往上提。**放宽一层的代价是污染别人。**

**Q1 这个值只在本次执行有意义吗？**（登录 token、刚创建对象的 id、查出来的临时 id）
→ 是：**步骤提取物**。绝大多数值都停在这里，这是默认答案。
⚠ 提取物**永远不要**写回环境变量或共享资源 —— 那是跨次运行的污染源。

**Q2 本用例多处要用、且每次跑该换个新的吗？**（本次要创建的服务名、订阅备注）
→ 是：**场景变量**，要唯一就 `kind=random` / `template`（如 `svc-{{$string:6}}`）。

**Q3 这条链会修改 / 消耗 / 删除它吗？**（禁用、审批掉、删掉、改状态）
→ 是：**必须自己造**（路线 A：开头建、末尾删）。
哪怕多条用例都要"一个服务"，只要各自会改它的状态，就**各造各的**。
共享一个会被改状态的资源 = 用例之间互相打架，而且是偶发的、最难查。

**Q4 多条用例都要用、只读引用不改它、且反复重建代价大吗？**
（上游/负载、隔离上下文、长期存在的消费方应用）
→ 是：**项目级共享资源**（路线 B：查 → 没有就自己造且不清理 → 登记 exists_check）。

**Q5 它描述的是"这个环境是什么"而不是"这次测什么"吗？**（BASE_URL、登录路径、各角色账号密码）
→ 是：**环境变量**，人工在环境管理里按环境维护。你不要写进场景变量，也不要在测试里改它。

> 一句话判据：**会被改的别共享，只读的才配共享；能停在提取物就别往上提。**

**铁律：步骤里任何取值都必须来自上面各层之一，禁止写死。**
- ✅ `"name": "${svcName}"`（场景变量，kind=template：svc-{{$string:6}}）
- ✅ `"Authorization": "Bearer ${token}"`（token 来自上一步 variables_extract）
- ✅ `"url": "${BASE_URL}/api/v1/services/${serviceId}"`（BASE_URL 全局、serviceId 提取物）
- ❌ `"name": "test-service-001"`（写死业务名——换环境/重复跑必冲突）
- ❌ `"Authorization": "Bearer eyJhbGci..."`（写死 token）

内置可直接用、不用声明：RANDOM_8、TIMESTAMP、SV_RUN_ID，以及 Authorization 由平台按登录态自动注入（无需手写 token 步骤，除非要测鉴权本身）。

### 两个常见错误（很多人踩，务必避开）

**① 别把环境变量镜像成场景变量。** BASE_URL / LOGIN_URL / 账号密码这些执行时由平台
直接注入，步骤里写 `${BASE_URL}` 就能用，不要再建 kind=global_ref 的同名场景变量——
纯噪音。场景变量只放「这条用例自己的数据」，比如本次要创建的服务名。

**② 前置数据你自己造，不许写死 UUID。** 像 upstreamId / isolationId / 被订阅的 appId
这类"链子跑起来必须先有"的资源，存成 `kind=literal` + 一个真实 UUID 是错的：
换环境或资源被删就全挂。**造数据是你的活，别赌环境里刚好有。** 按性质二选一：

**路线 A · 场景自足**（优先）—— 这条用例自己的数据：本次要创建的服务、要发起的订阅等。
场景开头加步骤真的调接口创建 → `variables_extract` 提取 id → 末尾加步骤删掉。
自建自删，跑一百遍都干净。能自足就别依赖外部。

**路线 B · 共享基础数据** —— 多条用例都要用、反复重建代价大的底座（上游/负载、
隔离上下文、长期存在的消费方应用）。三步：

1. `tb_list_global_data` 先查项目里登记过没有；
2. 没有就你造一次（活体验证时顺手造），造完 **不要清理** —— 它要留给后续场景复用；
3. 用 `tb_upsert_automation_resource` 登记两样：`exists_check`（怎么按名字找到它 +
   抽哪个字段当 id）和 **`create_def`（当初是怎么造的）**。之后每次跑，平台在第一个
   步骤之前探一次并注入 `${资源名}`；**探到"确实没有"就照 create_def 自动补建**，
   补了会在运行结论里明说。所以 create_def 不是备查，是兜底 —— 别省。

**UI 脚本里怎么取这些资源。** 和接口场景同一份，写 `os.getenv("SV_<键>")` 或
`os.getenv("<键>")`（键就是 `exists_check.extract` 里声明的名字，比如 `projectId`），
平台在跑前探到之后两种拼法都注。**只在脚本真的引用了才去探**，所以不引用的脚本
不会白付探测开销。回出来的 `resourcesInjected` 会列出这次真注了哪几个键 ——
拿不到值时先看它是不是空的。

⚠ 所以 UI 脚本里的 `projectId` 这类"环境里长期存在的底座 id"，
**既不要 `kind=literal` + 真实 UUID**（换环境即挂），**也不用自己在脚本里按名字
反查一遍**（能跑，但那是你自己发明的路，没人保证）—— 登记成项目级共享资源，
`os.getenv("SV_projectId")` 取。

⚠ 只在探测**明确没匹配上**时补建；401/5xx/超时算"没查成"，一律不动
（一次 token 过期就照着建，会在被测环境里造一堆重复底座）。
⚠ **别把共享底座写成硬依赖**：`${资源名}` 引用就够了，不要再自己加一步
「按名字查上游」并断言它必须存在 —— 那等于把"底座缺失"变成二十条链一起红，
而链子自己没有 if/else 能兜。登记好 create_def，缺失这件事由平台在跑前解决。

⚠ `exists_check` 的 `match` 必须用**稳定标识**（name / code 这类），**不要用 id** ——
用 id 去 match 等于换个地方写死，换环境照样匹配不上。

⚠ `extract` 的路径**相对 match 命中的那一条**写，直接写 `"id"`，不要写
`"data.items[0].id"`。下标是另一种写死：match 找的是 name==X、extract 抽的却是第 0 条，
列表顺序一变就静默注入别的资源的 id，步骤照跑不报错，最难查。

```json
{"method":"GET","url":"${BASE_URL}/api/v1/upstreams?page_size=100",
 "match":{"field":"name","equals":"autotest-default-upstream"},
 "extract":{"upstreamId":"id"}}
```

自检：这条链换到一个**干净环境**还能不能跑通？跑不通就是 A 没造全，或 B 漏了第 2 步。

⚠ **路线 A 必须自己删干净。** 造了不删的链每跑一次留一份，堆多了会反过来毁掉断言 ——
列表里同类数据一多，`data[0]` 指向别人、满页把本次那条挤到第二页，断言开始时红时绿，
而人会当成被测系统的缺陷去查。`tb_check_env_hygiene(project_id)` 报两类：
造了东西却没有清理步骤、最后一次运行没跑到清理（残留 id 和删它的请求都给出来）。

自检标准：**这条链换到一个干净环境还能不能跑通？** 跑不通就说明前置数据没交代清楚。"""

_SPEC_API_SCENARIO = """## 用例编排的接口场景（tb_sync_orchestrated_scenario）

把你**亲手活体验证过**的接口链显式写回。每个 step 形状：
```json
{
  "name": "创建服务",
  "method": "POST",
  "url": "${BASE_URL}/api/v1/services",
  "headers": {"Content-Type": "application/json"},
  "body": {"name": "${svcName}", "type": "http"},
  "assertions": [
    {"type": "status", "operator": "==", "value": 200},
    {"type": "body_field", "field": "data.id", "operator": "not_empty"},
    {"type": "body_field", "field": "data.name", "operator": "==", "expected": "${svcName}"},
    {"type": "body_contains", "operator": "contains", "value": "success"}
  ],
  "variables_extract": {"serviceId": "data.id"},
  "group_name": "服务管理",
  "enabled": true
}
```
断言类型（对齐执行器 _check_assertions）：
- `status`：`value`=状态码，operator ∈ ==/!=/in
- `body_field`：`field`=路径，比较值放 `expected`，operator ∈
  ==/!=/not_empty/is_empty/not_exists/length/contains/not_contains/>/</>=/<=
  · `is_empty` 取到了、是空容器（`"items": []`）
  · `not_exists` 路径取不到值（过滤没命中、字段不在）——「删完按名字查不到」用它。
    ⚠ 字段名写错也取不到 → 恒真。所以删之前必须有一步用**同一条路径**断 not_empty
    当基准，否则回推时会收到警告
- `body_contains`：`value`=子串，operator ∈ contains/not_contains。
  它在**整个响应体**里搜字符串 —— 别拿它当"某字段等于某值"用，别处出现同一串就假绿。
  「列表应为空」用 `is_empty`，不要用 `not_contains` 搜字段名去绕。

`field` 路径 = 点号 + 三种选择器：
- `data.items[0].id` 下标 · `data.items[-1].id` 倒数第一
- `data.items[name=${svcName}].id` **按字段值过滤**，取第一条命中的
- `data.items[*name=${svcName}]` 取**全部命中**（不能再往后接字段），配 `length` 断
  「有且只有一条」—— **验唯一性只有这一种写法**：`[k=v]` 只取第一条，被测系统真收下了
  第二条同名，断言照样绿（实测就是这么被 tb_check_assertion_bite 抓出来的）
- 断条数用 `length`，且**只能对列表用**：URL 上带查询条件让服务端过滤，再
  `{"field":"data.items","operator":"length","expected":1}`。
  对 `data.items[k=v]` 用 length 是错的 —— 那取到的是一个对象，长度是它的键数

**别用下标定位业务对象。** `data[0]` 是另一种写死：排序口径、分页、别人造的数据一变，
它就静默指向另一个对象，断言照过。实测三次差点误报缺陷 —— 跨租户列表首列是消费方
租户名不是应用名；`/todos` 按 created_at 升序且满页，本次新建那条被截在后面。
**读列表做断言前先确认排序与分页口径**，然后按业务标识过滤。

variables_extract：`{变量名: 路径}`，供**后续步骤** ${变量名} 用（登录抽 token、创建抽 id、清理按 id 删）。

### 断言纪律

**新写的断言先让它红一次再让它绿。** 没红过的断言等于没验证过 ——
方向写反、路径写错、恒真，三种都是绿的。

跑绿之后用 `tb_check_assertion_bite(case_id, skip_steps='动作步名', env_id)` 收一次：
它把那个**改状态的动作步**跳掉再跑一遍，后面该红的必须红。
  · `bites` 红了 → 这条断言咬得住这个动作
  · `still_green` 照样绿 → 恒真，改成断动作真正改变的那个东西（状态字段变成什么）
  · `inconclusive` 没发出请求 → 你跳的是产出 id 的创建步，换成跳改状态那步
只读运行，不写步骤状态也不动用例维度，但**请求是真发的**（制备和清理照跑）。

**动作前后断同一件事＝没验动作。** 「驳回后打网关仍 401」，若申请时就是 401，
驳回逻辑坏掉它照样绿。回推时平台会比对整步断言并警告（同一请求、中间没有任何断言
证明它变过）。

保持型／否定断言合法（「弃用后存量调用不中断」正该这么写），两个条件：
  ① **在步骤名里写明**（保持/不变/仍/依旧/始终/一致），平台据此不再提示；
  ② **基准要在动作之前建**。链子里必须有一步先证明它当时不是这个值 ——
     「驳回后仍 401」后面再补一步「重新获批后 200」不算基准，那证明的是之后，
     不是之前。没有前置基准，这条断言从头到尾都是绿的，平台也判不出来。

**别缩小断言作用域。** 页面级→行级、整体→单字段：条数没少，强度降了。

### 改几个断言不用重发全链

`mode='patch'` + 只传要改的那几步，按 step name 匹配，其余原样保留。
name 必须和现有步骤完全一致，对不上整批拒绝（怕静默漏改）；加步骤或改名用 `mode='replace'`。

### 断言错误提示语：用 `${T:中文}`，别写死

**文案不是 UI 专属的。** 接口的错误提示语跟着语种变（Accept-Language 一改，
`message` 字段就从中文变英文），断言里写死中文，跑英文环境照样全红。

    ❌ {"type": "body_contains", "value": "服务名已存在"}
    ✅ {"type": "body_contains", "value": "${T:services.form.nameDuplicated|服务名已存在}"}
    竖线后面是中文原文：读断言的人一眼看懂在验什么，词典没收录时也退回它（不会拿键名去比）

`${T:…}` 由平台按环境变量 `TEST_LANGUAGE=zh|en` 换成当前语种（不配就是中文），
同时平台会给每个请求带上 `Accept-Language`（步骤自己写了就不覆盖）——
两边都换，被测系统才会真的回那个语种的文案，
译文取自项目国际化词典。**词典里查不到就原样返回中文**，所以没收录的话也不会挂。

判据：**这个断言值是被测系统给用户看的文字吗？** 是就套 `${T:}`。
状态码、枚举值（active/draft）、字段名不用 —— 那些不随语种变。
词条自己登记：`tb_upsert_i18n_terms`。

**但先找有没有稳定错误码 —— 有就断码，别断文案。** 实测这个 409 回的是
`{"error":{"code":"SERVICE_NAME_CONFLICT","message":"service name already exists in this
tenant: tb-dup-xxxx"}}`：message 是英文的（压根没走 i18n）**还拼了动态服务名**，
拿它做等值断言必挂，套 `${T:}` 也没用（词典里没有它，返回原文照样对不上）。
    ✅ {"type":"body_field","field":"error.code","operator":"==","expected":"SERVICE_NAME_CONFLICT"}
    ⚠ 只能断文案、且文案里带动态内容时，用 contains 断那段固定的，别断整句。
顺序就是：**错误码 > `${T:}` 文案 > contains 片段**。

"""

_SPEC_SCENARIO_SHAPE = """## 场景怎么切、怎么才算验到了（回推时会逐条提示，但不拦你）

外部 CC 上一批返工全集中在这四条上，都不是写不出来，是**没想到要验那一步**。

**① 一条场景只验一件事。** 跑红了要能一眼看出是哪件坏了。
   「配下去 → 真生效」是一件事的两个阶段，那是对的；
   「租户内订阅」和「跨租户订阅」是两件事，拆两条。

**② 对照组拆成两条用例，互为对照。** 别在一条里换个身份再跑一遍。
   挤在一条里有个真实的坑：前半段为了造场景改过的开关（比如把审批关了），
   会让后半段的结论直接失假 —— 而它长得跟"通过了"一模一样。

**③ 写完必须读回来。** `POST` 回 201 只是接口自己说的；`GET` 读回来才是数据面说的。
   实测踩过：接口回 200、字段压根没落库。制备类步骤不需要读回，
   那就在步骤名里写明「制备：…」，提示会自动跳过它。

**④「生效」的判据不是控制面的状态字段。**
   「转 approved」只是控制面写了个状态。真正的判据是二选一（能都验最好）：
   - 拿那个凭据**去调需要认证的服务**：审批前必须调不通，审批后才通；
   - **页面上**那个入口/按钮是不是还灰着、跳不进去。
   这一步几乎总是要**跨到另一个入口**（数据面/网关/前台），
   所以"所有请求都打在同一个 ${BASE_URL} 上"基本等于没验生效 —— 回推时会提示你。

   **数据面入口叫什么，平台不猜。** 每个项目不一样，你在测的过程中摸清了
   （网关基址、集群前缀、租户隔离前缀这些），就自己写进共享数据：
   `tb_upsert_automation_resource`。之后所有用例 `${资源名}` 直接取，
   下一轮你自己也不用再摸一遍。"""


_SPEC_TIMING = """## 异步下发怎么办：别插假步骤占时间窗，用 wait_ms / retry_timeout_ms

被测系统的配置下发常常是**异步**的（实测某网关从「发布成功」到真能转发要
0.06~0.5s 且抖动），而接口场景的步骤之间只隔几毫秒 —— 「发布完立刻打网关」
必然抢跑，跑出来是红的，但那**不是缺陷，是这条用例自己没等**。

**假红比漏测更毒**：它让整份报告不可信，人看两次就不看了。

步骤上有三个字段（`tb_sync_orchestrated_scenario` 的 steps 里直接传）：

| 字段 | 干什么 | 什么时候用 |
|---|---|---|
| `retry_timeout_ms` | 断言没过就**整步重发**，直到过了或超时 | **首选**。等的是"它真的好了" |
| `retry_interval_ms` | 两次重发间隔，默认 300 | 跟着上面用 |
| `wait_ms` | 发请求前先固定等 | 下策 —— 要么白等要么不够，换台机器就崩 |

```json
{"name": "发布后打网关（应调通）", "method": "GET", "url": "...",
 "assertions": [{"type": "status", "operator": "==", "value": 200}],
 "retry_timeout_ms": 6000, "retry_interval_ms": 300}
```

重试成功时平台会如实报「重试 N 次后通过」—— 一次就过和试了 8 次才过不是一回事，
后者说明这个窗口快不够了。

⚠ **重试会重发请求**。写操作（POST/PUT/DELETE）上开重试会造出多份数据，
所以只该用在「读回来确认」那种步骤上；在非幂等方法上开会收到软警告。

❌ **不要再靠插入真实断言步骤去占时间窗**（查版本历史、查操作日志……）。
那招能用但很脆：换台机器、网络慢一点就不够，而且把"等待"伪装成了"验证"，
读用例的人分不出哪几步是真要验的。
"""


_SPEC_ORDER = """## 动手顺序（**这一条错了，后面全歪**）

外部 CC 上一轮就是顺序反了：读需求 → curl 打接口 → 读前端源码 → 写接口场景 →
最后挑几条"我认为 UI 有独立价值"的补脚本。后果它自己抓到了：
**页面打开订阅管理调的是 `/subscriptions/provider-unified`，而 22 条接口场景全用
`/subscriptions/provider`** —— 后者存在、返回 200，所以用例一直绿，
但页面根本不用它：那个端点坏掉、少给字段、跨租户条目漏掉，这批用例一条都不会红。

**正确顺序**：

1. **先在页面上把这件事做一遍**（用户能做的事，就得从用户的路径进）。
2. **`tb_proxy_capture` 取这次页面真发的请求** —— 方法、URL、body 都在里面，
   不用自己开 devtools 抄（抄错了后面全是错的）。
3. **先写 UI 脚本**：你刚在页面上走通的那条路，趁手就写下来。
   顺序反过来（先接口后 UI）必然出现"推断页面怎么调"，而推断错了测试还是绿的。
4. **照真实流量写接口场景**：端点、方法、body 形状都以流量为准，不以文档为准。
5. **最后判哪些断言必须留在 UI 层**（见下）。

**手工步骤的按钮名、入口路径、提示文案，只能来自真页面** —— 步骤是给人照着做的，
路径靠想象的话，这条用例本身就是假的。

**UI 脚本写不写，判据是「这个结论只有页面能证明吗」**，不是「接口能不能测」：
- 必须写：按钮置灰、入口消失、Toast 文案、列表回显、权限导致的不可见
- 可以不写：断言对象是数据/协议（转发、状态流转、幂等、限额），或压根没有页面入口
- **不要用 UI 做造数和清理** —— 见下

**前置和清理走接口，别在页面上点。** 沙箱里有 `api` fixture —— **多角色**的接口客户端，
自动登录、自动带 token、401 自动重登重试（网关 token 15 分钟就过期）：
```python
def test_xxx(logged_in_page, api):
    # 前置：接口造。多角色的数据（网关那种）就换身份造，一个 admin 造不出来
    svc = api.json("POST", "/api/v1/services", json={"name": name})["data"]
    sub = api.role("tenant").json("POST", "/api/v1/subscriptions",
                                  json={"service_id": svc["id"]})["data"]
    api.role("provider").json("POST", f"/api/v1/subscriptions/{sub['id']}/approve")

    ...页面上只做被测那一个动作，验页面看得见的结果...

    api.delete(f"/api/v1/services/{svc['id']}")          # 清理：接口删
```
角色名来自环境变量前缀：环境里成对的 `<X>_USERNAME`/`<X>_PASSWORD` 自动成为一个角色
（`admin` / `tenant` / `app` / `provider` …）；没登记的账号用 `api.login(u, p)`。
调 `api.role("不存在的")` 会直接告诉你这个环境有哪些角色，不用猜。

一个 20 步的 UI 脚本里往往 15 步是造数 —— 那 15 步慢、脆、且跟被测点无关。

⚠ **`goto` 之后不要紧跟 `reload()`**：`logged_in_page` 登录后已经落在页面上，
再 reload 会打断首屏那几个请求，断言变成偶发红（活体验证时撞过一次）。
要等就 `wait_for_timeout` 或等某个元素可见。
**换角色也别用清 storage 那招**：造数走接口，页面上只保留被测那个身份。

**提单之前先答一句「用户从哪进」。** 在界面上根本没有入口的纯接口路径上报 bug，
优先级和"用户点了就炸"完全不同；没有入口就写明"这是内部接口，用户碰不到"。"""


_SPEC_NAMING = """## 命名规范（**这条最省事**：写规范了，平台就不用猜你的意图）

**标题 = 「对象+动作-预期」两段**，前段 20 字内一眼看完。
**分隔符用短横 `-`，两边不留空格** —— 标题在列表里就那么点宽度，别浪费在空格上：
  ✅ `租户管理员跨租户订阅-一级自动跳过、二级批完即生效`
  ❌ `消费方租户管理员自己申请跨租户订阅时一级节点自动跳过，提供方直接批二级后订阅生效`
后者得读完整句才知道在测什么，而**列表上只露标题**。细节放预期结果里。

**每个步骤名带角色前缀**（手工步骤、接口步骤、UI 脚本注释都一样）：

| 前缀 | 这步在干什么 |
|---|---|
| `前置:` | 造数据、登录、取 id —— 不是被测对象 |
| `操作:` | 触发被测行为的那一下 |
| `验证:` | 断言在验什么 |
| `清理:` | 收尾删数据 |

例：`前置: 建服务 A` / `操作: 发布服务 A` / `验证: 打网关应 200` / `清理: 删服务 A`

**为什么值得你多打四个字**：平台判「写完有没有验效果」「哪步是制备」「这条在验生效吗」
原来全靠从步骤名里搜关键词猜 —— 你写"取消订阅"会被猜成"取"（制备），
写"依然可调通"会被漏掉。写了前缀就是读一个字段，判得准，也不会误报烦你。"""


_SPEC_CASE = """## 步骤用例（tb_create_case，非本模块，但一并说明口径）

**case_type 看测试对象：**
- `api` 单接口 —— 测试对象是**某一个接口的参数、权限**
- `e2e` 场景 —— 测试对象是**某功能是否按需实现**
- 为这条用例造数据用了几个接口，不影响判断 —— 造数据不是测试对象

活体验证后回写步骤用例：多步编排的功能验证用 case_type=e2e，步骤是**页面操作**（点按钮/填表单），
按钮名/字段标签/Toast 文案用被测系统真实文案；预期结果必须 UI 可见；禁止模糊词
（操作成功/显示正常/无报错）；每条只验一个点；preconditions 分环境前置+数据前置；
steps 每项含 seq/action/expected；多角色加 [管理员]/[租户] 标记。

改步骤/预期会清掉「预期已确认」。只是**措辞润色**（实质没变）就传
`tb_update_case(reconfirm=true)`：依据沿用原落款、只重盖时间。实质变了就重新对一遍。

**写不了就说清等什么**：`tb_update_case(blocked_external='等环境变量 X 加上')`。
「我没写」和「外面缺东西我写不了」在看板上长得一模一样，不标注就每轮都要人来问你。
它不免检任何阻塞，只是归责；条件到位了传空串撤掉。

**跑出来红、但红的原因不在用例（产品 bug）→ 关联上去，别只在对话里说一句**：
`tb_update_case(bug_refs=[{"ref":"UAG-123 或一句话","url":"可选","status":"open"}])`。
它跟 blocked_external 分工不同：那个是"我还写不了"，这个是"我写完了、跑出来是红的"。

    关联 open ──▶ 批量回归跳过它（跑了只是刷红），也不计入通过率
        │
        │  git 上那条 issue 关闭 / 人告知修好了
        ▼
    你回来把它调通 ──▶ 把那条关联标成 status:"fixed"
        │
        └─ 没调通 → 留在 open，补一句 note 说清现在卡在哪

三件事别搞错：
1. **`fixed` 是"我回来调通了"，不是"据说修好了"。** issue 关了但你还没调通，
   它就该留在 open —— open 的含义是"还没验回来"。
2. **关联是永久痕迹，标完 fixed 就留着，不要清。** 清掉就看不出这条用例曾经抓到过
   bug —— 而"哪些用例真抓到过问题、抓到过几次"是评估用例价值的唯一依据。
   `bug_refs=[]` 只用于关联错了（挂到了不相干的 bug 上）。
3. 平台不会自己动 status：判 bug 死活、判验没验过，都是你和人的事。

**待办从哪来**：`tb_list_cases(bug_state="blocked")` 拿到所有"关联的 bug 还没验回来"
的用例（返回带每条的 ref），跟你从 git 拉到的**已关闭 issue** 取交集 —— 交集就是
这一轮该回来调的。`bug_state="fixed"` 是另一回事：抓到过 bug 已验回来的痕迹清单。

`tags=["冒烟","需要真数据"]` 只用来分拣（最多 20 个、每个 32 字内）。
别拿标签表达状态或审核结论 —— 那两样有确定语义、驱动门禁。

**模块名怎么起**（实测这两种都发生过，现在会被门禁硬拒）：
- 一级模块 = **被测系统的功能域**（订阅管理、服务管理、监控），不是"你这一轮在测什么"。
  想不出该放哪就先 `tb_get_folder_tree` 看现有的，别顺手新开一个。
- **看着是两级就别拼成一级**：`监控-请求日志` 要写成 `module="监控", submodule="请求日志"`。
  拼成一级之后「监控」下别的用例找不到家，导航栏也会被长名字撑满。
- **同一个模块只能有一个写法**：已经有「LLM PROVIDERS」就别再传 `LLM Providers` /
  `llm_providers` / `LLM-PROVIDERS` —— 门禁会告诉你现成的那个名字，用它。

**回推完自己过一遍评审**：`tb_review_case(case_id=...)`。六维打分 + 逐条指到位置，
判定在平台代码里（有 blocker 一律不过、加权低于 80 不过），不是 AI 说了算。
`mustFix` 里的 blocker 一条都不许留着交上去；改完再调一次复核。
断言咬不咬得住静态看不出来 —— 拿不准就 `run_first=true` 先真跑一遍再评。

**放错目录自己搬**：`tb_update_case(module="订阅管理", submodule="跨租户订阅")`，
目录不存在会自动建；只传 module 就搬到模块根下。建用例时漏传 submodule 是常见笔误
（实测一个模块 21 条里 3 条落在了根目录），发现了自己搬，不用等人去界面上拖。
**编号不跟着变**（TC-DYGL-00013 搬完还是这个号）—— 编号是回推、脚本、报告共用的锚点。"""


_SPEC_UI_SCRIPT = """## 用例的 UI 脚本（tb_sync_ui_script）

把你在本地**写好并真跑通过**的 Playwright 脚本回推到某条用例的「UI 测试」页签。
平台不再自己生成脚本——生成这件事由你（外部 Claude Code）做，平台负责存、跑、留痕。

### 变量怎么取（这是硬检查，写死会被拒绝入库）

外部取值一律从环境变量读，**在模块顶部声明一次**，后面全用变量拼：

```python
import os
from playwright.sync_api import Page, expect

BASE_URL = os.getenv("BASE_URL", "")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SVC_NAME = os.getenv("SV_svcName", "")        # 场景变量前缀 SV_

def test_创建服务后列表可见(page: Page):
    page.goto(f"{BASE_URL}/login")
    page.get_by_placeholder("用户名").fill(ADMIN_USERNAME)
    page.get_by_placeholder("密码").fill(ADMIN_PASSWORD)
    page.get_by_role("button", name="登 录").click()
    ...
    expect(page.get_by_text(SVC_NAME)).to_be_visible()
```

- ❌ `page.goto("http://192.168.51.108:5173/login")` —— 写死服务地址，换环境必挂，**拒绝入库**
- ❌ `fill("admin123")` —— 写死凭据，**拒绝入库**
- ✅ `page.goto(f"{BASE_URL}/login")`

平台执行时会把该环境的变量注入进程环境，并把 `NAME = os.getenv("NAME", "默认值")`
这一行的默认值替换成真值——所以**必须写成这个形状**，写 `os.environ["X"]` 拿不到替换。

可用的键：环境变量（BASE_URL / 各角色账号密码 / LOGIN_URL，见 tb_list_global_data）、
场景变量（`SV_` + 变量名，跟接口场景共用同一份，见 tb_list_scenario_variables）、
`TEST_TOKEN`（平台按用例前置条件自动登录拿到的 token，造数/清理用）。

### 形状要求

- Python：必须有 `def test_xxx` —— 平台用 pytest 跑它。默认文件名 test_ui.py
- TypeScript：必须有 `test(...)` —— 平台用 `npx playwright test` 跑它。默认文件名 ui.spec.ts
- 两种都支持，按内容/文件名自动判，也可以显式传 language

### 多角色：一人一个 browser context，不要清 storage 换人

审批类功能天然多角色（申请人／审批人／二级审批人）。**别用一个 page 反复清
storage 换人** —— 清 storage 擦不掉内存里的 store 和查询缓存，实测渲染进程会挂死
（连 `Page.screenshot` 都 30s 超时）；而且"一个会话扮多个人"本来就不是真实场景。

```python
def test_申请后审批通过(browser, browser_context_args):
    # 必须带上 browser_context_args：语种和视口是平台在这个 fixture 里注入的，
    # 自己裸开 new_context() 会退回默认 locale，上面那套文案纪律当场失效。
    rest = {k: v for k, v in browser_context_args.items() if k != "record_har_path"}
    applicant = browser.new_context(**browser_context_args)   # 第一个留着录 HAR
    approver = browser.new_context(**rest)                    # 其余去掉：同一个
    p1, p2 = applicant.new_page(), approver.new_page()         # HAR 路径会互相覆盖
    ...                                  # 各自登录、各自操作
    applicant.close(); approver.close()  # 不 close 就没有 HAR，失败时没有网络证据
```

### 扫描/遍历类的"找不到"断言：先等就绪锚点

「翻遍列表都找不到这条」这类**负例**在空列表上**恒真** —— 一张卡都没渲染出来时
"找不到"当然成立。而触发过重新拉取（审批完、提交完、切了 tab）之后正好有个空窗期，
断言就在那一瞬间通过，日志里看着是绿的，实际一条都没扫。

所以扫描前必须先锚定一个**就绪信号**，且这个信号要能区分"列表还没回来"和"列表回来了但没有它"：

```python
expect(page.locator(".todo-card")).to_have_count(3, timeout=15000)   # 先等数量落到预期
# 再去扫身份 —— 这时"扫遍都没有"才是真结论
for card in page.locator(".todo-card").all():
    assert svc_name not in card.inner_text()
```

判据：**如果这段断言在"页面上一条数据都没有"时也会通过，它就还缺一个锚点。**
（实测 CC 的 TC-DYGL-00016：第一版平台跑 44/44 全绿，加了"卡片数从 4 落到 3"这个锚点后
步骤数变成 58 —— 多出来的 14 步正是原先被空窗期跳过的扫描。）

### 导航：SPA 别等 load

`page.goto()` 默认等 `load`，在 SPA + 有轮询的页面上会卡满 30s。
用 `page.goto(url, wait_until="domcontentloaded")` + **显式元素断言**当就绪信号。
`networkidle` 同样别用 —— 轮询永远不会 idle。

### 文案纪律：优先 testid，退回文案时用 `t()`

**数据不许写死已经有硬拦截，文案是同一件事的另一半。**
`name="更多"` 换英文环境全挂 —— 实测 9 个脚本 57 处写死中文、只有 5 处用 testid。
（接口断言同样受语种影响，那边用 `${T:中文}`，见 tb_get_sync_spec(kind='api_scenario')。）

定位方式按这个顺序选：

  1. **`data-testid`**（`page.get_by_test_id("sync-status-bar")`）—— 文案改了、
     语种换了都不受影响。被测系统有就用它
  2. **结构 + 角色**（`get_by_role("button")` + 位置/父级），不带 name
  3. **文案** —— 前两条都不行才用，而且**必须写成占位变量 `${键|中文原文}`**，
     跟接口断言完全同形（那边是 `${T:键|中文}`），一眼就是"平台给的值"：

         page.get_by_role("button", name="${services.action.more|更多}").click()
         expect(page.get_by_test_id("sync-status-bar")).to_contain_text("${status.draft|草稿}")

     竖线后面是中文原文：读脚本的人一眼知道在验什么，词典缺这个语种时也退回中文。
     不带点号的 `${BASE_URL}` 不是文案键，平台不碰它（环境变量走 os.getenv）。

     要循环/拼接的场合用注入的表：`from tea_i18n import TEXT` → `TEXT.get("键", "中文")`
     （`t("键","中文")` 是 i18next 那套写法的别名，老脚本还在用，等价）。

`TEXT` 由平台注入沙箱（tea_i18n.py），内容是**按 `TEST_LANGUAGE=zh|en` 解析好的**{键: 文案}（不配就是中文）—— 脚本里当变量表用，不用自己挑语种。

⚠ **浏览器 locale 换不动被测系统。** 实测 stoa：context locale 设成 en-US，页面照旧全中文 ——
它的语种存在 `localStorage['stoa-lang']`。所以环境里还要配一行
`UI_LANG_STORAGE_KEY=<那个键名>`，平台会在页面脚本跑之前把当前语种种进去。
不配的话：期望值换成了英文、被测系统还在说中文，**全红，而且是假红**。
自己 `browser.new_context()` 开的上下文不吃这个注入（init script 挂在平台给的 context 上），
多角色脚本里要么复用 `context` fixture，要么自己种一遍 —— **`add_init_script` 收的是
语句正文，不是函数**：

```python
ctx.add_init_script("try{localStorage.setItem('stoa-lang','en-US')}catch(e){}")   # ✅ 会执行
ctx.add_init_script("() => { localStorage.setItem('stoa-lang','en-US') }")        # ❌ 只定义了个箭头函数，永不执行
```

写成箭头函数**不报错、也不生效**：语种没换过去，脚本按英文断言、系统还说中文，
**全红而且是假红**（实测 CC 第一次跑 TEST_LANGUAGE=en 就栽在这里）。
**ref 优先用语言中立键**（`services.form.name`）—— 多义词只能这么区分，
拿中文当键时「服务」在标题和按钮上永远是同一条。

⚠ **占位没换掉的话平台直接拒绝执行**，所以竖线后面的中文别省：
  · `${键|中文}` → 词典有就用译文；词典有键但缺这个语种 → 退回它自己的中文；
    词典压根没这条 → 退回你写的中文。三种都跑得起来
  · `${键}` 光写键、词典里又没这条 → **回推被硬拦，执行被拒**
    （返回里列出 textPlaceholdersUnresolved）。为什么不是"让它红在找不到元素上"：
    那只对正例成立。「不应出现」这类**负例会假绿** —— 未替换的占位匹配不到任何元素，
    "不该存在"当然成立。恒真断言不会自己喊疼，只能拦在执行前
  · 同理，`TEXT["键"]` 裸下标查不到会**抛 KeyError**（不再静默返回键名）；
    要兜底就写 `TEXT.get("键", "中文原文")`
  · 拿中文当键 → 不会挂，但中文一改键就失效（静默退回原文），
    而且多义词区分不开（「服务」在标题和按钮上是两回事）

键可以带 i18next 的命名空间：`${subscription:manage.rejectBtn|驳回}`。
**两种分隔符互认** —— 被测系统里是 `t('subscription:manage.rejectBtn')`，平台词典里
存的是全点号 `subscription.manage.rejectBtn`，查词时两种拼法指向同一条，随便写哪种。

**本地怎么跑**：调 `tb_render_ui_script(case_id, lang, env_id)` —— 它吐**一个能直接
pytest 跑的文件**（文案、环境变量默认值、被测系统的语种开关都烧进去了）。
凭据默认不烧，返回里 `exportEnv` 告诉你 export 哪几个；要完全自包含传
`include_credentials=true`。`textUnresolved` 非空就先登记词条或补上 `|中文原文`。

**英文要在本地先验**（不然"本地跑通再回推"这条纪律在文案上是空的）：
`tb_render_ui_script(case_id, lang="en")` 渲一份跑。别自己写 `def t(s): return s`
的 stub —— 那种 stub 永远只能跑中文，等于没验。
词典缺条目就 `tb_upsert_i18n_terms` 登记；回推时扫到硬编码中文只给**软警告**
（词典总有不全的时候，不硬拦）。

回推时会扫硬编码中文给**软警告**（不硬拦 —— 词典总有不全的时候）。

### 流程

1. 本地写脚本，**先自己跑通**（别回推没验证过的东西）
2. `tb_sync_ui_script(case_id, content)` 入库
3. `tb_run_ui_script(case_id, env_id)` 在目标环境上再跑一遍——平台跑通了才算通
4. 失败看 `tb_get_ui_script_result(case_id)`：状态、耗时、错误摘要、截图数
"""


async def get_sync_spec(kind: str = "all") -> dict:
    """获取回推规范。kind: case(步骤用例) / api_scenario(编排接口场景) / scenario_shape(场景怎么切、怎么才算验到了) / ui_script(UI 脚本) / variables(变量纪律) / all。

    回推前先调它对齐口径：怎么选变量层、步骤/断言/提取物 JSON 形状、禁止写死的正反例。"""
    parts = {
        "variables": _SPEC_VARIABLES,
        "api_scenario": _SPEC_API_SCENARIO,
        "ui_script": _SPEC_UI_SCRIPT,
        "order": _SPEC_ORDER,
        "naming": _SPEC_NAMING,
        "case": _SPEC_CASE,
        "scenario_shape": _SPEC_SCENARIO_SHAPE,
        "timing": _SPEC_TIMING,
    }
    if kind in parts:
        selected = {kind: parts[kind]}
    else:
        kind = "all"
        selected = parts
    playbook = (
        "# 回推同步 playbook\n\n"
        "1. 先 tb_list_global_data 看项目有哪些可引用的全局项（BASE_URL/token/账号/共享资源），"
        "决定哪些走 global_ref、哪些走场景变量、哪些是步骤提取物。\n"
        "2. tb_upsert_scenario_variables 建/更新该用例的场景变量（部分固定+部分随机用 kind=template）。\n"
        "3. tb_sync_orchestrated_scenario 回推接口链——步骤里**只用 ${var}，零写死**；"
        "悬空引用会被硬拦截，疑似写死会软警告。\n"
        "4. tb_run_api_test 执行，确认变量都被正确解析。\n"
        "5. 要顺带补 UI 脚本：本地写好跑通 → tb_sync_ui_script 入库 → tb_run_ui_script 在目标环境再跑一遍。\n\n"
        + "\n\n".join(selected.values())
    )
    return {"kind": kind, "playbook": playbook, "sections": selected}


# ─────────────────────────────────────────────────────────────
# 2. 回推：用例编排的接口场景
# ─────────────────────────────────────────────────────────────

_STEP_FIELDS = ("name", "method", "url", "headers", "body", "assertions",
                "variables_extract", "enabled", "group_name",
                "wait_ms", "retry_timeout_ms", "retry_interval_ms")

# tb_get_api_test 读回来是驼峰，写回去要下划线 —— 两边互认，见入库处的注释。
_STEP_ALIASES = {"variablesExtract": "variables_extract", "groupName": "group_name",
                 "waitMs": "wait_ms", "retryTimeoutMs": "retry_timeout_ms",
                 "retryIntervalMs": "retry_interval_ms"}
# 读回来带着、写回去用不上的只读字段：丢掉是对的，不用报给调用方。
# 名单要跟 api_tests._last_run_facts 的输出对齐 —— 漏一个，读改写就会
# 收到一条"忽略了 lastStatusCode"的假警报，真丢东西时反而被噪声盖住。
_STEP_READONLY = {"id", "sort_order", "sortOrder", "lastStatus", "last_status",
                  "lastResponse", "last_response", "statusCode", "error",
                  "extracted", "durationMs", "duration",
                  "lastError", "lastStatusCode", "failedAssertions", "failedExtracts"}


async def _merge_patch(session: AsyncSession, bid: uuid.UUID, scid: uuid.UUID,
                       incoming: list[dict], patched: list[str]) -> list[dict] | dict:
    """把 incoming 按 step name 合并进现有场景，返回完整的步骤列表（出错则返回 {"error": ...}）。

    **为什么要它**：整条覆盖是唯一入库方式时，改 3 个断言要重发 27 步。
    费 token 是小事，**重发时手误引入新问题**才是大事 —— 那 24 步没人再看一遍。
    """
    prev = (await session.execute(
        select(ApiTestScenario).where(ApiTestScenario.branch_id == bid,
                                      ApiTestScenario.source_case_id == scid)
        .order_by(ApiTestScenario.created_at)
    )).scalars().first()
    if prev is None:
        return {"error": "这条用例还没有接口场景，patch 无从下手：先用 mode='replace' 整条推一次。"}
    old = (await session.execute(
        select(ApiTestStep).where(ApiTestStep.scenario_id == prev.id)
        .order_by(ApiTestStep.sort_order)
    )).scalars().all()

    by_name: dict[str, int] = {}
    for s in old:
        by_name[s.name] = by_name.get(s.name, 0) + 1

    want: dict[str, dict] = {}
    for st in incoming:
        nm = str(st.get("name") or "").strip()
        if not nm:
            return {"error": "patch 模式下每个 step 必须有 name —— 它是唯一的匹配依据。"}
        if nm in want:
            return {"error": f"patch 里有两个同名 step「{nm}」，认不出改哪一条。"}
        want[nm] = st

    unknown = [n for n in want if n not in by_name]
    if unknown:
        return {"error": "这些 step name 在现有场景里找不到，已拒绝（怕静默漏改）：",
                "notFound": unknown, "existingNames": [s.name for s in old],
                "hint": "名字要和现有步骤完全一致；要加新步骤或改名就用 mode='replace' 整条推。"}
    ambiguous = [n for n in want if by_name[n] > 1]
    if ambiguous:
        return {"error": f"现有场景里有同名步骤（{'、'.join(ambiguous)}），patch 认不出改哪一条："
                         f"改用 mode='replace'。"}

    merged: list[dict] = []
    for s in old:
        base = {f: getattr(s, f) for f in _STEP_FIELDS}
        if s.name in want:
            base.update({k: v for k, v in want[s.name].items() if k in _STEP_FIELDS})
            patched.append(s.name)
        merged.append(base)
    return merged


# 提示分档：**平台能证明的**和**靠猜的**不该混在一堆里。
# 混着给的后果实测过：CC 看到"提示 5 条"不知道哪条必须处理，于是一条都不处理。
_MUST_LOOK_KINDS = {"tautology_assertion", "vague_expectation", "no_readback",
                    "missing_baseline", "bool_as_string"}


def _tiered(warnings: list) -> dict:
    must, fyi = [], []
    for w in warnings:
        kind = w.get("kind") if isinstance(w, dict) else None
        text = (w.get("value") if isinstance(w, dict) else str(w))
        (must if kind in _MUST_LOOK_KINDS else fyi).append(text)
    out = {}
    if must:
        out["mustLook"] = must
        out["mustLookHint"] = ("这几条平台能证明（不是猜）：要么改，要么回一句"
                               "「为什么这样写就够」——别默认忽略。")
    if fyi:
        out["fyi"] = fyi
    return out



async def _latest_ui_script(session: AsyncSession, scid) -> dict | None:
    """这条用例现在有没有 UI 脚本 —— 给反问的 facts 用，别让它恒为 false。"""
    from sqlalchemy import select as _sel

    from app.models.script import Script
    row = (await session.execute(
        _sel(Script).where(Script.case_id == scid, Script.script_type == "ui",
                           Script.status != "archived")
        .order_by(Script.version.desc()).limit(1))).scalar_one_or_none()
    if row is None:
        return None
    return {"version": row.version, "fileName": row.file_name,
            "language": row.language, "chars": len(row.content or "")}


async def _reflect_block(session: AsyncSession, scid, norm: list, answers: dict | None) -> dict:
    """收下反问答案 + 把还没答的问题带回去。**不拦入库**（见 reflect.py 的口径）。"""
    if not scid:
        return {}
    from app.models.case import Case
    from app.services.review import reflect

    case = await session.get(Case, scid)
    if case is None:
        return {}
    # 被打回之后又回推 = 一次**整改提交**。记下来，详情页那条时间线才连得起来
    if case.review_status == "rejected":
        prev = (case.review_reason or {}).get("findings") or []
        from app.services.review import rounds
        await rounds.record(session, case.id, "cc_resubmit", actor="cc",
                            changed={"stepCount": len(norm),
                                     "pendingFindings": len([f for f in prev
                                                             if f.get("severity") != "minor"]),
                                     "note": "打回后重新回推了接口场景"})
        await session.commit()

    saved = reflect.normalize(answers)
    if saved:
        # 补答：只覆盖这次给了的几项，别把上次答过的抹掉
        merged = {**(case.reflections or {}), **saved}
        case.reflections = merged
        await session.commit()
    if not reflect.pending(case):
        return {"reflectionAnswered": True}

    neighbors = []
    if case.folder_id:
        from sqlalchemy import select as _sel
        rows = (await session.execute(
            _sel(Case.case_code, Case.title).where(
                Case.folder_id == case.folder_id, Case.id != case.id,
                Case.deleted_at.is_(None)).limit(12))).all()
        neighbors = [{"caseCode": c, "title": t} for c, t in rows]
    return {
        "reflectionPending": True,
        # 第四个参数（UI 脚本）**必须查**：不传的话 facts 里的「UI 脚本」恒为 false，
        # 明明先回推过 UI 脚本，反问却当它不存在 —— 平台数出来的事实里掺了一条假的，
        # 而这四问值钱就值钱在"事实是平台数的、不是模型猜的"。
        "reflect": reflect.build(case, {"steps": norm}, neighbors,
                                 await _latest_ui_script(session, scid)),
        "reflectHint": "这四问规则判不了，只有你答得上（你手上有需求和代码）。"
                       "答案用 reflections={...} 传回来 —— 不答不拦入库，"
                       "但交付门禁不放行、评审会按「自证不全」扣分。",
    }


async def sync_orchestrated_scenario(
    session: AsyncSession,
    project_id: str,
    branch_id: str,
    title: str,
    steps: list,
    source_case_id: str | None = None,
    folder_name: str | None = None,
    priority: str = "P1",
    description: str | None = None,
    mode: str = "replace",
    reflections: dict | None = None,
) -> dict:
    """把活体验证过的接口链显式写入「用例·编排的接口场景」。

    **返回里的提示分三档，按"平台能证明到什么程度"分**：
      · `blockers`  —— 必然出错且没有合法写法（悬空变量、写死地址、脚本必挂）：**没入库**，改完再传
      · `mustLook`  —— 能证明的假绿（恒真断言、模糊预期、写完什么都没验）：入库了，但要么改、要么回一句为什么这样够
      · `fyi`       —— 靠猜的（只断状态码、对照组、命名）：自己判断，不用回

    **`reflect` 是四个场景级反问**（规则判不了、只有你答得上，因为你手上有需求和代码）：
    验证点合不合理 / 场景清不清晰 / 有没有相关场景没覆盖 / 预期从哪来。
    每问都带平台数出来的事实（承诺了几件事、几条断言、邻居有哪些、本模块还缺哪几类）。
    答案用 `reflections={"verificationPoints": "...", "clarity": "...",
    "coverage": "...", "expectationSource": "..."}` 传回来（可以下一次调用时补）。
    **不答不拦入库**，但交付门禁不放行、评审按"自证不全"扣分 ——
    答案的用处是给评审一个锚：你说"第 8 步验编号不变"，评审就去核对第 8 步的断言，
    **说的和断言对不上是最硬的证据**。

    这是接口场景**唯一**的入库路径：你亲手验证过的多步 E2E，绑定 source_case_id、
    共享该用例场景变量。入库前会做悬空引用硬拦截 + 疑似写死软警告。"""
    pid = uuid.UUID(project_id)
    bid = uuid.UUID(branch_id)
    steps = _loads(steps)
    if not isinstance(steps, list) or not steps:
        return {"error": "steps 必须是非空数组"}
    # source_case_id 从"强烈建议"变成**必填**（2026-08-15，迁移 zz9orph1）：
    # 库里 source_case_id 已是 NOT NULL，不传的话下面会撞非空约束、抛出一个
    # 看不懂的 IntegrityError。在这里挡住，顺便把"为什么"说清楚 ——
    # 不绑用例的场景拿不到场景变量（scenario_variables.case_id 也是 NOT NULL），
    # 跑起来必挂在「变量未解析」，建出来也是个死物。
    if not source_case_id:
        return {"error": "source_case_id 必填：接口场景必须绑定某条用例。"
                         "没有对应用例就先 tb_create_case 建一条 —— 不绑用例的场景"
                         "拿不到场景变量（凭据、随机数据都在用例上），跑起来必然"
                         "「变量未解析」。"}

    # 归一化每个 step 的 JSON 字段（防止客户端把对象序列化成字符串）
    norm: list[dict] = []
    aliased: set[str] = set()
    dropped: set[str] = set()
    for i, raw in enumerate(steps):
        st = _loads(raw)
        if not isinstance(st, dict):
            return {"error": f"第 {i + 1} 个 step 不是对象"}
        st = dict(st)
        # **读回来的键名要能原样写回去。** tb_get_api_test 吐驼峰
        # （variablesExtract/groupName/waitMs…），这里此前只认下划线 —— 于是
        # "读回来 → 改一个 URL → 存回去"这条最自然的路，会把所有提取和分组**静默丢掉**，
        # 然后报「存在悬空变量引用」：错误指向的是后果（后面 ${id} 没人提供），
        # 不是原因（提取被丢了）。别让调用方去记两套拼法。
        for camel, snake in _STEP_ALIASES.items():
            if camel in st and snake not in st:
                st[snake] = st.pop(camel)
                aliased.add(snake)
        # 读回来还带着 id/sortOrder/lastStatus 这些只读字段，写回时用不上（下面按
        # _STEP_FIELDS 取值，它们本来就会被丢）。**丢了要说一声** —— 静默丢弃
        # 和"我压根没打算存它"在调用方眼里一样，真丢了要紧的东西时看不出来。
        dropped.update(k for k in st if k not in _STEP_FIELDS and k not in _STEP_READONLY)
        for f in ("headers", "body", "assertions", "variables_extract"):
            if f in st:
                st[f] = _loads(st[f])
        # 断言键名归一（status→value、body_field→field+expected），见 _canon_assertion
        if isinstance(st.get("assertions"), list):
            st["assertions"] = [_canon_assertion(a) for a in st["assertions"]]
        norm.append(st)

    # ── mode=patch：只改点名的那几步，其余原样留着 ──
    if mode not in ("replace", "patch"):
        return {"error": "mode 只能是 replace（整条覆盖）或 patch（按 name 只改点名的步骤）"}
    patched: list[str] = []
    if mode == "patch":
        norm_or_err = await _merge_patch(session, bid, uuid.UUID(source_case_id), norm, patched)
        if isinstance(norm_or_err, dict):
            return norm_or_err
        norm = norm_or_err

    # ── 建立引用允许名单 ──
    allow: set[str] = set(BUILTIN_VARS)
    # 全局变量键
    for k in (await session.execute(select(GlobalVariable.key))).scalars().all():
        allow.add(k)
    # 环境变量键（跨环境，运行时按所选环境解析）
    for k in (await session.execute(select(EnvironmentVariable.key))).scalars().all():
        allow.add(k)
    # 项目级前置资源（自动化数据）：运行时 run_scenario 会按 exists_check 探测并把
    # extract 声明的键注入成变量，所以这些名字是合法引用——不加进来会把
    # ${资源名} 误判成悬空、逼着 CC 回去写死 UUID。
    from app.models.automation_resource import AutomationResource

    for res in (await session.execute(
        select(AutomationResource).where(AutomationResource.project_id == pid)
    )).scalars().all():
        allow.add(res.name)
        for var in ((res.exists_check or {}).get("extract") or {}):
            allow.add(str(var))

    # 源用例的场景变量（裸名 + SV_ 前缀，与运行时注入一致）
    scenario_var_names: list[str] = []
    scid = None
    if source_case_id:
        scid = uuid.UUID(source_case_id)

        rows = (await session.execute(
            select(ScenarioVariable.name).where(ScenarioVariable.case_id == scid)
        )).scalars().all()
        for n in rows:
            scenario_var_names.append(n)
            allow.add(n)
            allow.add(f"SV_{n}")

    # ── 逐步扫描：硬拦截悬空引用 + 软警告疑似写死 ──
    dangling: list[dict] = []
    warnings: list[dict] = []
    bad_types: list[dict] = []
    dead_asserts: list[dict] = []
    extracted: set[str] = set()
    for i, st in enumerate(norm):
        refs = _collect_refs(st.get("url"), st.get("headers"), st.get("body"), st.get("assertions"))
        for name in sorted(refs):
            if name not in allow and name not in extracted:
                dangling.append({"step": i + 1, "name": st.get("name") or f"step{i + 1}", "variable": name})
        # 写操作上开重试 → 软警告：重试会**重发请求**，POST 重发就是多造一份数据
        if int(st.get("retry_timeout_ms") or 0) > 0 and \
                (st.get("method") or "GET").upper() not in ("GET", "HEAD", "OPTIONS"):
            warnings.append({
                "step": i + 1, "field": "retry_timeout_ms",
                "value": f"{st.get('method')} 上开了重试 —— 重试会重发请求，"
                         f"写操作重发会造出多份数据。确认这个接口幂等再用，"
                         f"否则该把重试放在后面那个「读回来确认」的步骤上。",
            })
        # body 里疑似写死的业务数据 → 软警告
        for path, val in _iter_strings(st.get("body")):
            if _looks_hardcoded(val, path):
                warnings.append({"step": i + 1, "field": f"body.{path}" if path else "body", "value": val[:60]})
        # 断言里把布尔/数字写成字符串 → 硬拦。见 _bool_typed_as_string。
        bad_types.extend(_typo_assertions(i + 1, st))
        # 期望值/字段路径压根没给 → 硬拦（必然红，且报错看不懂）。见 _unevaluatable_assertions。
        dead_asserts.extend(_unevaluatable_assertions(i + 1, st))
        # 异步下发的断言没开重试 → 软警告。见 _needs_retry。
        r = _needs_retry(i + 1, st)
        if r:
            warnings.append(r)
        # 本步提取物在其后步骤可用
        extra = st.get("variables_extract")
        if isinstance(extra, dict):
            extracted.update(extra.keys())

    # 动作前后同一条断言 → 软警告。见 _nondiscriminating。
    warnings.extend(_nondiscriminating(norm))
    # not_exists 没有基准 → 软警告。见 _missing_path_baseline。
    warnings.extend(_missing_path_baseline(norm))
    # 场景形态（写完没读回 / 只打控制面 / 对照组塞一条 / 一条验两件事）→ **全部软警告**。
    # 这几条都要从自然语言猜意图，猜错就是滥报，所以一条都不拦；
    # 价值在时机 —— 回推那一刻 CC 还在上下文里，补一步很便宜。
    from app.services.scenario_shape import check_shape
    warnings.extend(check_shape(norm, title or ""))

    if dead_asserts:
        return {
            "error": "有断言永远判不过，已拒绝入库 —— 缺期望值/字段路径的断言不是"
                     "「可能红」，是必然红，而报错会长得像平台在说胡话。",
            "deadAssertions": dead_asserts,
            "hint": "状态码写 {\"type\":\"status\",\"operator\":\"==\",\"value\":200}"
                    "（in 的话 value 是数组）；响应字段写 {\"type\":\"body_field\","
                    "\"field\":\"data.status\",\"operator\":\"==\",\"expected\":\"pending\"}。"
                    "expected / value 两种键名执行器都认，但**总得给一个**。",
        }

    if bad_types:
        # **警告不拦**（判据规范附则）：老接口真返回字符串 "true" 的情况存在，
        # 而平台在回推这一刻没有任何证据说明该字段是什么类型。
        for b in bad_types:
            warnings.append({
                "step": b["step"], "kind": "bool_as_string",
                "value": f"第 {b['step']} 步「{b['name']}」的 {b['field']} 期望写成了 "
                         f"{b['wrote']}（字符串）。平台故意不做布尔兜底"
                         f"（兜了「期望 true、实际 1」就会算相等，那是假绿），"
                         f"所以响应里若是布尔，这条会必挂 —— 改成 {b['shouldBe']}。"
                         f"**如果这个接口确实返回字符串**（有些老接口如此），忽略这条。",
            })

    if dangling:
        return {
            "error": "存在悬空变量引用，已拒绝入库（纪律：不允许写死，且引用必须可解析）",
            "dangling": dangling,
            "hint": "每个 ${x} 必须来自：①该用例场景变量（先 tb_upsert_scenario_variables）"
                    "②更早步骤的 variables_extract ③全局/环境键 ④内置(RANDOM_8/TIMESTAMP/SV_RUN_ID)。"
                    "可引用项见 tb_list_global_data / tb_list_scenario_variables。",
            "allowedSample": sorted(allow)[:30],
        }

    # ── 幂等：一个用例 = 一条接口场景。重推永远覆盖那一条，不按标题区分 ──
    # 产品口径就是 1:1（用例详情里只呈现一条、只有一套编辑器）。若按标题去重，
    # CC 换个标题重推就会多出一条，用例里又变成"两份"。
    existing = None
    if scid:
        existing = (await session.execute(
            select(ApiTestScenario).where(
                ApiTestScenario.branch_id == bid,
                ApiTestScenario.source_case_id == scid,
            ).order_by(ApiTestScenario.created_at)
        )).scalars().first()

    folder_id = None
    if folder_name:
        folder = (await session.execute(
            select(ApiTestFolder).where(ApiTestFolder.branch_id == bid, ApiTestFolder.name == folder_name)
        )).scalars().first()
        if not folder:
            folder = ApiTestFolder(branch_id=bid, name=folder_name)
            session.add(folder)
            await session.flush()
        folder_id = folder.id

    creator = await _active_user_id(session)
    if not creator:
        return {"error": "找不到可用的用户来记录 created_by（需要至少一个 active 用户）"}

    replaced = False
    carry: dict[str, tuple] = {}      # 步骤名 → (定义指纹, last_status, last_response)
    if existing is not None:
        # 覆盖：保留原 code（外部可能已引用），换掉步骤与元信息
        scenario = existing
        scenario.title = title          # 一个用例一条，标题以最新一次回推为准
        scenario.priority = priority
        scenario.source = "mcp"
        if folder_id:
            scenario.folder_id = folder_id
        if description:
            scenario.description = description
        # **重推前把上一次运行的证据留住。** 步骤行是删了重建的，于是
        # last_status / last_response 一并没了 —— 而 tb_check_env_hygiene 判"上次跑到清理没有"
        # 靠的就是它。实测后果：CC 跑完再 patch 一次，那条链的运行痕迹归零，
        # 工具从此看不见残留（"报 0 条"于是变成一句空话）。
        # 只对**定义没变**的步骤沿用：定义改了，旧结果就是过期的，不该继续代表它。
        for _old in (await session.execute(
            select(ApiTestStep).where(ApiTestStep.scenario_id == scenario.id)
        )).scalars().all():
            carry[_old.name] = (_step_def_sig(_old), _old.last_status, _old.last_response)
        await session.execute(
            sa_delete(ApiTestStep).where(ApiTestStep.scenario_id == scenario.id)
        )
        code = scenario.code
        replaced = True
    else:
        # **编号就用用例自己的**，不从 AT-#### 序列里再领一个号。
        #
        # 一个用例 = 一条编排场景（按 source_case_id 幂等），所以它**没有独立身份** ——
        # 再发一个 AT-0011 就是给同一件东西起第二个名字，而那个号还是从
        # 「接口测试模块」的序列里拿的：人在用例详情里看到 AT-0011，去接口测试页面
        # 搜却搜不到（那个页面默认只列单接口场景）。实测就是这么被问的：
        # 「为什么要单独一个 ID，不是和用例同一个 id 吗」。
        #
        # 现在 code = 用例编号（TC-XXXX-00001）。AT-#### 序列从此只归单接口场景，
        # 原来这里还有一条「取不到用例就回退 AT-#### max+1」的兜底。**已删**：
        # 那条兜底会在用例 id 不存在时照样建，然后撞 source_case_id 的外键 ——
        # 报出来的是 IntegrityError，人得自己猜是用例没了。而且 AT-#### 序列
        # 本来就是「接口测试」模块的号，那个模块 2026-08-15 已经下线。
        # 现在编号只有一种：用例编号。取不到用例就直说。
        src_case = await session.get(Case, scid)
        if src_case is None:
            return {"error": f"用例不存在：{source_case_id}。"
                             "接口场景的编号就是用例编号，用例找不到就无从落库。"
                             "先用 tb_list_cases 确认 case_id，或 tb_create_case 建一条。"}
        code = src_case.case_code
        scenario = ApiTestScenario(
            project_id=pid,
            branch_id=bid,
            code=code,
            title=title,
            priority=priority,
            source="mcp",
            status="draft",
            folder_id=folder_id,
            description=description,
            source_case_id=scid,
            created_by=creator,
        )
        session.add(scenario)
    await session.flush()

    kept_evidence = 0
    for i, st in enumerate(norm):
        # 定义没变的步骤沿用上一次的运行结果（见上面 carry 那段）
        prev_status, prev_resp = _carried_evidence(carry, st)
        if prev_status:
            kept_evidence += 1
        session.add(ApiTestStep(
            scenario_id=scenario.id,
            sort_order=i,
            last_status=prev_status,
            last_response=prev_resp,
            group_name=st.get("group_name"),
            name=st.get("name") or f"step{i + 1}",
            method=(st.get("method") or "GET").upper(),
            url=st.get("url") or "",
            headers=st.get("headers"),
            body=st.get("body"),
            assertions=st.get("assertions"),
            variables_extract=st.get("variables_extract"),
            enabled=st.get("enabled", True),
            # 异步下发那种"发布完立刻打网关会抢跑"的场景靠这三个字段解决，
            # 不用再插一堆假步骤去占时间窗
            wait_ms=int(st.get("wait_ms") or 0),
            retry_timeout_ms=int(st.get("retry_timeout_ms") or 0),
            retry_interval_ms=int(st.get("retry_interval_ms") or 300),
        ))

    # 回写用例的「接口」维度状态：挂上了活体验证过的编排场景，还显示"未开始"会误导
    # （用例列表/详情都靠这个字段判断该维度做没做）。只从 not_started 往前推一格，
    # 不覆盖人工已设成 executable 等更具体的状态。
    if scid:
        case_obj = await session.get(Case, scid)
        if case_obj is not None and case_obj.api_status == "not_started":
            case_obj.api_status = "debugging"

    await session.commit()
    await session.refresh(scenario)

    return {
        "status": "ok",
        "scenarioId": str(scenario.id),
        "code": code,
        "title": title,
        "stepCount": len(norm),
        **({"keptLastRunEvidence": kept_evidence} if kept_evidence else {}),
        "sourceCaseId": str(scid) if scid else None,
        "scenarioVariablesLinked": scenario_var_names,
        "hardcodeWarnings": warnings,          # 兼容老调用方，等于 mustLook + fyi
        **_tiered(warnings),
        **(await _reflect_block(session, scid, norm, reflections)),
        "replacedExisting": replaced,
        "mode": mode,
        "patchedSteps": patched,
        # 键名做过什么手脚，明说。静默改写和静默丢弃是同一类坑的两半。
        **({"keysAliasedFromCamel": sorted(aliased)} if aliased else {}),
        **({"keysIgnored": sorted(dropped),
            "keysIgnoredNote": "这些字段不入库（写回时用不上）。要是里面有你指望存住的，"
                               "说明拼法不对 —— 步骤只认："
                               + "、".join(_STEP_FIELDS)} if dropped else {}),
        "message": (f"已按 name 改了 {len(patched)} 步（{'、'.join(patched)}），"
                    f"其余 {len(norm) - len(patched)} 步原样保留"
                    if mode == "patch" else
                    (f"已覆盖同名场景 {code}" if replaced else f"已新建场景 {code}")
                    + f"（{len(norm)} 步）")
                   + (f"，⚠ {len(warnings)} 处待看（疑似写死/断言不区分动作，仅提醒，已入库）"
                      if warnings else "，无告警"),
    }


# ─────────────────────────────────────────────────────────────
# 3. 场景变量回写 / 读取
# ─────────────────────────────────────────────────────────────

async def upsert_scenario_variables(
    session: AsyncSession,
    case_id: str,
    variables: list,
    project_id: str | None = None,
    branch_id: str | None = None,
) -> dict:
    """按 name 回写/更新用例的场景变量。variables 每项:
    {name, kind(literal/random/global_ref/template), value_template, var_type, description}。

    - literal:    整段固定
    - random:     value_template 作前缀，执行期加 -{runId}-{rand} 唯一化
    - global_ref: value_template = 全局键名，运行时从合并变量取（项目级引用）
    - template:   部分固定+部分随机，value_template 内嵌 {{$fn}} 生成器（见 tb_list ...·generators）
                  如 svc-{{$string:6}}-{{$city}}"""
    cid = uuid.UUID(case_id)
    variables = _loads(variables)
    if not isinstance(variables, list):
        return {"error": "variables 必须是数组"}
    antipatterns: list[dict] = []

    created, updated, errors, renamed = [], [], [], []
    for item in variables:
        item = _loads(item)
        if not isinstance(item, dict) or not item.get("name"):
            errors.append({"item": item, "reason": "缺少 name"})
            continue
        name = str(item["name"]).strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            errors.append({"name": name, "reason": "变量名须以字母/下划线开头，仅含字母数字下划线"})
            continue
        kind = item.get("kind") or "literal"
        if kind not in _KINDS:
            # **别静默改成 literal。** 写错 kind 的人以为存进去的是自己那个语义
            # （比如把 random 拼成 rand），拿到的却是"整段固定"，然后在执行期
            # 才以另一副面孔炸出来。键名对不上就报错，不要替人猜。
            errors.append({"name": name, "reason": f"kind「{kind}」不认识，只能是 {'/'.join(_KINDS)}"})
            continue

        # **收口「键名对不上就静默假绿」。** value_template 写成 value 是最常踩的一个：
        # 此前 item.get("value_template") 取不到 → 存成空串 → 回「新增 N、更新 M」、
        # errors 空，看上去全绿；直到执行期整条链挂在「变量未解析：${x}」上，
        # 而错误指向的是后果不是原因。收了别名，但**必须回显**说明改了什么。
        if "value_template" not in item and "value" in item:
            renamed.append(name)
            item = {k: v for k, v in item.items() if k != "value"} | \
                   {"value_template": item.get("value")}
        unknown = sorted(set(item) - _SV_KEYS)
        if unknown:
            errors.append({"name": name, "reason": f"不认识的字段 {unknown}，"
                                                   f"只收 {sorted(_SV_KEYS)}"})
            continue

        val = str(item.get("value_template") or "")
        # 空值直接拒（random 除外 —— 它的 value_template 只是前缀，空前缀仍能解析出值）。
        # literal/global_ref/template 存成空串等于**保证**执行期 ${x} 解析不出来；
        # 入库时不喊、执行时才炸，是这个项目里最贵的一类 bug。
        if kind != "random" and not val.strip():
            errors.append({"name": name,
                           "reason": f"kind={kind} 的 value_template 不能为空"})
            continue
        # 反模式①：literal + 真实 UUID —— 那是"环境里已存在的资源 id"，换环境/资源被删就全挂
        #
        # 但**摆明是编出来的 UUID 不算**：全零、全 f、nil UUID —— 那是负向测试的
        # 常规写法（"查一个肯定不存在的 id，应该 404 而不是 500"）。
        # 对这种写法报警，等于逼人把正当的负向用例改掉，或者干脆学会忽略告警 ——
        # 后者更糟：真的反模式也就一起被忽略了。
        if kind == "literal" and _UUID_RE.fullmatch(val.strip()) \
                and not _is_synthetic_uuid(val):
            antipatterns.append({
                "name": name, "issue": "literal_uuid",
                "hint": f"{name} 存的是一个真实资源 UUID。要么用 tb_upsert_automation_resource "
                        "登记为项目级前置数据（带 exists_check/create_def），要么在场景开头"
                        "自己创建该资源并 variables_extract 提取 id、末尾清理。",
            })
        # 反模式②：把环境变量镜像成场景变量 —— 步骤里直接 ${BASE_URL} 就能用
        if kind == "global_ref" and val.strip().upper() in _ENV_MIRROR_KEYS:
            antipatterns.append({
                "name": name, "issue": "env_mirror",
                "hint": f"{name} 只是环境键 {val} 的镜像。环境变量执行时由平台直接注入，"
                        "步骤里写 ${" + val.strip() + "} 即可，这个场景变量是多余的。",
            })

        existing = (await session.execute(
            select(ScenarioVariable).where(ScenarioVariable.case_id == cid, ScenarioVariable.name == name)
        )).scalar_one_or_none()
        if existing:
            existing.kind = kind
            existing.value_template = item.get("value_template", existing.value_template) or ""
            existing.var_type = item.get("var_type") or existing.var_type or "string"
            if item.get("description") is not None:
                existing.description = item.get("description")
            updated.append(name)
        else:
            session.add(ScenarioVariable(
                case_id=cid,
                name=name,
                kind=kind,
                value_template=item.get("value_template") or "",
                var_type=item.get("var_type") or "string",
                description=item.get("description"),
            ))
            created.append(name)

    await session.commit()
    return {
        "status": "ok" if not errors else "partial",
        "created": created,
        "updated": updated,
        "errors": errors,
        "renamedFromValue": renamed,
        "antipatterns": antipatterns,
        "message": f"新增 {len(created)}、更新 {len(updated)} 个场景变量"
                   + (f"，{len(errors)} 个失败" if errors else "")
                   + (f"。⚠ {len(renamed)} 个传的是 `value`，已按 `value_template` 存"
                      f"（{'、'.join(renamed[:5])}）—— 正确键名是 value_template"
                      if renamed else "")
                   + (f"。⚠ {len(antipatterns)} 处反模式（见 antipatterns，已入库但建议改）"
                      if antipatterns else ""),
    }


_HAS_ZH = re.compile(r"[一-鿿]")


async def upsert_i18n_terms(session: AsyncSession, project_id: str, items: list) -> dict:
    """把脚本里要用的文案登记进项目国际化词典（按 key upsert）。

    **这是 `t()` 的登记通道。** 以前只有页面能录，MCP 没有 —— 于是纪律要求
    「文案走 t()」，而 CC 想补一条词只能把键值整理成表交给人工，实测就这么卡住过。

    键怎么选：
      · 有语言中立键（`services.form.name`）就用它 —— 多义词只能这么区分，
        「服务」在标题和按钮上是两回事，拿中文当键就永远指向同一条。
      · 只有中文就用中文当键，此时 zh 不填也行（中文键的中文就是它自己）。

    ⚠ **用键的前提是先登记**：`t("services.form.name")` 查不到时原样返回那串键，
    选择器拿它去匹配必然找不到 → 红。中文当键则退回中文，不会挂。
    ⚠ 没有 en 译文的词条注入后 `t()` 在英文环境仍退回中文 —— 登记了不等于能测英文。
    """
    from app.models.i18n_message import ProjectI18nMessage

    pid = uuid.UUID(project_id)
    items = _loads(items)
    if not isinstance(items, list) or not items:
        return {"error": "items 必须是非空数组：[{key, zh, en, module, category}]"}

    created, updated, errors, no_en = [], [], [], []
    for i, raw in enumerate(items):
        it = _loads(raw)
        if not isinstance(it, dict):
            errors.append({"index": i, "why": "不是对象"})
            continue
        key = str(it.get("key") or it.get("key_text") or "").strip()
        if not key:
            errors.append({"index": i, "why": "key 必填"})
            continue
        zh = str(it.get("zh") or it.get("zh-CN") or "").strip()
        en = str(it.get("en") or "").strip()
        if not zh and _HAS_ZH.search(key):
            zh = key           # 中文当键：它的中文就是它自己，补上才能被反查到
        if not en:
            no_en.append(key)
        # 语种键写 BCP-47 全码，跟从被测系统 locale 导进来的 2400+ 条对齐 ——
        # 一行里同时躺着 "en" 和 "en-US" 迟早会分叉（解析两种都认，人却分不出哪个是新的）。
        trans = {k: v for k, v in (("zh-CN", zh), ("en-US", en)) if v}

        row = (await session.execute(
            select(ProjectI18nMessage).where(ProjectI18nMessage.project_id == pid,
                                             ProjectI18nMessage.key_text == key)
        )).scalars().first()
        if row:
            row.translations = {**(row.translations or {}), **trans}
            if it.get("module"):
                row.module = str(it["module"])[:64]
            if it.get("category"):
                row.category = str(it["category"])[:20]
            if it.get("description"):
                row.description = str(it["description"])
            updated.append(key)
        else:
            session.add(ProjectI18nMessage(
                project_id=pid, key_text=key[:500], translations=trans,
                module=(str(it["module"])[:64] if it.get("module") else None),
                category=(str(it["category"])[:20] if it.get("category") else None),
                description=it.get("description"), source="manual",
            ))
            created.append(key)

    await session.commit()
    return {
        "status": "ok" if not errors else "partial",
        "created": created, "updated": updated, "errors": errors,
        "missingEn": no_en,
        "message": f"新增 {len(created)}、更新 {len(updated)} 条词条"
                   + (f"，{len(errors)} 条失败" if errors else "")
                   + (f"。⚠ {len(no_en)} 条没有 en 译文：英文环境下 t() 仍退回中文，"
                      f"用它做断言测不出英文" if no_en else ""),
    }


async def list_scenario_variables(session: AsyncSession, case_id: str) -> dict:
    """读取用例的所有场景变量（含 kind / value_template）。"""
    cid = uuid.UUID(case_id)
    rows = (await session.execute(
        select(ScenarioVariable).where(ScenarioVariable.case_id == cid).order_by(ScenarioVariable.name)
    )).scalars().all()
    return {
        "caseId": case_id,
        "total": len(rows),
        "variables": [{
            "name": v.name,
            "kind": v.kind,
            "valueTemplate": v.value_template,
            "varType": v.var_type,
            "description": v.description,
            "referenceAs": [f"${{{v.name}}}", f"${{SV_{v.name}}}"],
        } for v in rows],
    }


# ─────────────────────────────────────────────────────────────
# 4. 只读：项目级可引用数据
# ─────────────────────────────────────────────────────────────

def _mask_deep(obj, depth: int = 0):
    """递归脱敏 create_def —— 它记着"当初怎么造的"，大概率带 Authorization 头和账号。"""
    if depth > 6:
        return obj
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _SECRET_RE.search(str(k)):
                out[k] = "***"
            else:
                out[k] = _mask_deep(v, depth + 1)
        return out
    if isinstance(obj, list):
        return [_mask_deep(v, depth + 1) for v in obj[:20]]
    if isinstance(obj, str) and len(obj) > 300:
        return obj[:300] + "…"
    return obj


async def list_global_data(
    session: AsyncSession,
    project_id: str,
    env_id: str | None = None,
    probe: bool = False,
) -> dict:
    """汇总项目级**可引用**的全局数据，帮你判断哪些该走 global_ref、哪些不该写死。

    含：全局变量、各环境变量键（凭证类脱敏）、项目自动化共享资源。返回的键名可用于
    场景变量 kind=global_ref（value_template 填该键名），或步骤里 ${键名} 直接引用。

    probe=True 时**在指定环境上真探测一遍**共享资源（需要 env_id），每条返回：
      state=exists   探到了，附 values（extract 抽出来的 id 等）
      state=missing  确实没有 —— 照它的 createDef 自己调接口造出来，造完不用改任何
                     配置，existsCheck 下次自然探得到
      state=unknown  **我没查成**（401/5xx/超时/没配 url）—— 别动它。一次 token 过期
                     就照 createDef 补建，会在被测环境造出一堆重复底座，而且 keep=true
                     没人清理
    平台**不执行** createDef，只告诉你缺了什么、当初怎么造的。"""
    from app.models.automation_resource import AutomationResource
    from app.models.environment import Environment

    pid = uuid.UUID(project_id)

    def _mask(key: str, value: str) -> str:
        if _SECRET_RE.search(key or ""):
            return "***"
        return (value or "")[:80]

    global_vars = [{
        "key": v.key,
        "value": _mask(v.key, v.value),
        "description": v.description,
    } for v in (await session.execute(
        select(GlobalVariable).where(GlobalVariable.project_id == pid)
        .order_by(GlobalVariable.sort_order, GlobalVariable.key)
    )).scalars().all()]

    # 环境变量 —— 列出键名，凭证值脱敏。
    # 环境和全局变量 2026-08-21 起都是项目级的（迁移 zzo0envproj / zzp0gvarproj）：
    # 这里必须按 pid 过滤，否则这份"可引用的全局数据"清单会把别的项目的
    # 被测地址和账号键名一起端给 CC。
    envs = (await session.execute(
        select(Environment).where(Environment.project_id == pid).order_by(Environment.name)
    )).scalars().all()
    env_data = []
    for e in envs:
        evs = (await session.execute(
            select(EnvironmentVariable).where(EnvironmentVariable.environment_id == e.id)
            .order_by(EnvironmentVariable.key)
        )).scalars().all()
        env_data.append({
            "envId": str(e.id),
            "envName": e.name,
            "variables": [{"key": ev.key, "value": _mask(ev.key, ev.value)} for ev in evs],
        })

    rows = (await session.execute(
        select(AutomationResource).where(AutomationResource.project_id == pid)
        .order_by(AutomationResource.name)
    )).scalars().all()
    resources = [{
        "name": r.name,
        "description": r.description,
        "keep": r.keep,
        "existsCheck": r.exists_check,
        # 缺失时照这个自己造（平台不执行它）。带凭证的字段已脱敏。
        "createDef": _mask_deep(r.create_def) if r.create_def else None,
    } for r in rows]

    probe_note = None
    if probe:
        if not env_id:
            probe_note = "probe=true 需要 env_id —— 探测要拿该环境的 BASE_URL 和 token。先调 tb_list_environments。"
        else:
            from app.services import precheck_service
            rep = await precheck_service.check_resources(session, pid, env_id)
            by_name = {i["name"]: i for i in
                       (rep.get("satisfied", []) + rep.get("missing", []) + rep.get("unknown", []))}
            for item in resources:
                hit = by_name.get(item["name"])
                if hit:
                    item["state"] = hit.get("state")
                    item["reason"] = hit.get("reason")
                    item["values"] = hit.get("values")
            probe_note = (f"已在环境 {env_id} 上探测：存在 {len(rep.get('satisfied', []))} / "
                          f"缺失 {len(rep.get('missing', []))} / 没查成 {len(rep.get('unknown', []))}")

    return {
        "projectId": project_id,
        "globalVariables": global_vars,
        "environments": env_data,
        "automationResources": resources,
        "probed": bool(probe and env_id),
        "probeNote": probe_note,
        "usage": "键名可用于：场景变量 kind=global_ref(value_template=键名)，或步骤 ${键名}。"
                 "凭证类值已脱敏(***)，运行时由平台按所选环境真实注入。"
                 " 想知道某个共享资源在某环境上现在到底有没有，传 probe=true + env_id。",
    }


# ─────────────────────────────────────────────────────────────
# 5. 回推：项目级前置资源（自动化数据）
# ─────────────────────────────────────────────────────────────

async def upsert_automation_resource(
    session: AsyncSession,
    project_id: str,
    name: str,
    exists_check: Any = None,
    create_def: Any = None,
    description: str | None = None,
    keep: bool = True,
) -> dict:
    """登记一条「共享基础数据」，让后续每次跑都能自动找到它并注入成变量。

    用法（路线 B，共享基础数据）——**造数据是你的活，这个工具只负责登记怎么找到它**：
      1. 先 tb_list_global_data 查项目里登记过没有；
      2. 没有 → **你自己调接口把它造出来**（活体验证时顺手造），造完**不要清理**，
         它要留给后续场景复用；
      3. 造好后（或本来就有）调本工具登记 exists_check —— 写明"怎么按名字/条件找到它 +
         从响应里抽哪个字段当 id"。之后场景开跑前（第一个步骤之前）平台会自动探一次，
         把 extract 的键注入成 ${资源名}，换环境也能找到那个环境里的对应资源。

    什么该走这条路：多条用例都要用、反复重建代价大的底座（上游/负载、隔离上下文、
    长期存在的消费方应用）。**只属于本条用例的数据别用这个** —— 那种应该在场景开头
    自建、末尾清理（路线 A），能自足就别依赖外部。

    ⚠ match 必须用**稳定标识**（name/code 这类），**不要用 id** —— 用 id 去 match
      等于换个地方写死，换环境照样匹配不上。
    ⚠ extract 路径**相对 match 命中的那一条**写，直接 "id"，别写 "data.items[0].id" ——
      下标是另一种写死，列表顺序一变就静默抽到别的资源，步骤照跑不报错。
      （绝对路径仍兼容：命中项上取不到时会退回整包解析。）
    ⚠ **create_def 别省，它是兜底不是备查**：探到「确实没有」（探测请求成功但没匹配上）
      时平台会照它在跑前补建，并在运行结论里明说补了什么。没登记就只能让引用它的步骤
      报「变量未解析」。401/5xx/超时算「没查成」，一律不动 —— 一次 token 过期就照着建，
      会在被测环境里造出一堆重复底座。

    exists_check 形如 {"method":"GET","url":"${BASE_URL}/api/v1/upstreams?page_size=100",
                      "match":{"field":"name","equals":"autotest-default-upstream"},
                      "extract":{"upstreamId":"id"}, "role":"TENANT"}
      · `role`（可选，默认 ADMIN）：探测和补建用哪个角色的 token，对应环境里的
        `{ROLE}_USERNAME/{ROLE}_PASSWORD`。实测有坑：读上游 ADMIN 能读，但建上游
        要租户管理员的能力（ADMIN 去建回 403），所以这类资源要写 role="TENANT"。
    create_def   形如 {"method":"POST","url":"${BASE_URL}/api/v1/upstreams","body":{...}}
                 （这资源当初是怎么造的；探到确实没有时平台照它补建）
    """
    from app.models.automation_resource import AutomationResource

    try:
        pid = uuid.UUID(project_id)
    except (ValueError, AttributeError):
        return {"error": f"project_id 不是合法 UUID: {project_id}"}
    if not name or not name.strip():
        return {"error": "name 必填（步骤里用 ${name} 引用它）"}

    exists_check = _loads(exists_check)
    create_def = _loads(create_def)
    if not exists_check:
        return {
            "error": "exists_check 必填——没有存在性检查就没法预检，等于又回到写死。",
            "hint": '形如 {"method":"GET","url":"/api/v1/upstreams",'
                    '"match":{"field":"name","equals":"default-upstream"},'
                    '"extract":{"upstreamId":"data.items[0].id"}}',
        }

    existing = (await session.execute(
        select(AutomationResource).where(
            AutomationResource.project_id == pid,
            AutomationResource.name == name.strip(),
        )
    )).scalars().first()

    if existing:
        existing.exists_check = exists_check
        if create_def is not None:
            existing.create_def = create_def
        if description:
            existing.description = description
        existing.keep = keep
        action = "updated"
        res = existing
    else:
        res = AutomationResource(
            project_id=pid, name=name.strip(),
            exists_check=exists_check, create_def=create_def,
            description=description, keep=keep,
        )
        session.add(res)
        action = "created"

    await session.commit()
    await session.refresh(res)
    return {
        "status": "ok",
        "action": action,
        "name": res.name,
        "keep": res.keep,
        "hasCreateDef": res.create_def is not None,
        "message": f"已{'更新' if action == 'updated' else '登记'}前置资源「{res.name}」。"
                   f"场景开跑前会自动探测并注入，步骤里用 ${{{res.name}}} 引用，别再写死 UUID。"
                   + (" 探到确实没有时会照 create_def 自动补建（补了会在运行结论里说），"
                      "401/5xx/超时算没查成、不会乱建。"
                      if res.create_def is not None else
                      " ⚠ **没登记 create_def**：探不到时平台补不了，引用它的步骤会报"
                      "「变量未解析」。把当初怎么造的补登上来，这个资源就再也不会拖垮链子。"),
    }


# ── UI 脚本回推 ──────────────────────────────────────────────────────────────

_UI_ENV_HINT = 'BASE_URL = os.getenv("BASE_URL", "")'

# 服务地址/凭据写死是硬伤：换环境就全挂，而且挂得很隐蔽（脚本还在跑，只是打了别的系统）。
_URL_LITERAL_RE = re.compile(r"""["'`](https?://[^"'`\s]+)["'`]""")
_CRED_LITERAL_RE = re.compile(
    r"""(password|passwd|pwd|token|secret|api_?key)\s*[:=]\s*["'`]([^"'`\s]{4,})["'`]""",
    re.I,
)
# **合法写法：故意用错的凭据。** 「用错密码登录应失败」这条用例里，
# 那个密码就该是字面量 —— 它不是配置，是本次要验的输入。
# 原来一律硬拦，等于逼人把"错密码"也搬进环境变量（那才是真的乱）。
_INVALID_CRED_RE = re.compile(
    r"wrong|invalid|bad[-_]?|expired|revoked|fake|dummy|nonexist|notexist|"
    r"xxx+|placeholder|错误|无效|过期",
    re.I,
)


def _detect_language(content: str, file_name: str | None) -> str:
    name = (file_name or "").lower()
    if name.endswith((".ts", ".tsx", ".js", ".mjs")):
        return "typescript"
    if name.endswith(".py"):
        return "python"
    if "import { test" in content or "@playwright/test" in content:
        return "typescript"
    return "python"


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 只认定位/断言里的文案：name= / text= / has_text= / to_contain_text( / get_by_text(
_UI_TEXT_RE = re.compile(
    r"""\bname\s*[:=]\s*['"]([^'"]+)['"]"""
    r"""|\bhas_text\s*=\s*['"]([^'"]+)['"]"""
    r"""|get_by_text\(\s*['"]([^'"]+)['"]"""
    r"""|to_contain_text\(\s*['"]([^'"]+)['"]""")



def _strip_noncode(content: str) -> str:
    '''去掉三引号块和行注释 —— 只有真代码才算引用。

    活体验证时撞到：脚本 docstring 里写了用法示例 t("键", "中文原文")，
    扫描器把「键」当成真引用，报「词典里没有这个键」。**在文档里解释怎么用，
    反被门禁警告**，那种提示看两次就没人信了。
    只用于软警告那两条扫描；写死地址/凭据的硬拦截仍扫全文（注释里贴凭据也不行）。
    '''
    out = re.sub(r'(?s)""".*?"""', '', content)
    out = re.sub(r"(?s)'''.*?'''", '', out)
    return re.sub(r'(?m)^[ \t]*#.*$', '', out)

from app.services.ui_text_render import REF_RE as _PH_RE, text_key as _text_key  # noqa: E402

# 三种写法都要认出来，并分清"带没带中文兜底"：
#   TEXT["键"]                  没带 → 查不到会拿键名去匹配，必然找不到元素
#   TEXT.get("键", "中文原文")   带了 → 查不到退回中文，不挂但测不出英文
#   t("键"[, "中文"])           i18next 那套老写法，库里还有脚本在用
# 第一支直接用渲染那边的正则 —— **别再各写一份**：门禁这份原来不认命名空间键
# （`subscription:stats.x`），于是 CC 照规范写的占位在门禁眼里根本不存在，
# 一句警告都没有，而执行时也没替换掉（同一个漏洞两处一起漏）。
_T_REF_RE = re.compile(
    _PH_RE.pattern                                                       # ${键|中文}
    + r"""|TEXT\.get\(\s*['"]([^'"]+)['"]\s*(,\s*['"][^'"]*['"])?"""
    + r"""|TEXT\[\s*['"]([^'"]+)['"]\s*\]"""
    + r"""|\bt\(\s*['"]([^'"]+)['"]\s*(,\s*['"][^'"]*['"])?""")


def _t_refs(code: str) -> dict[str, bool]:
    """{键: 有没有带中文兜底}。"""
    out: dict[str, bool] = {}
    for m in _T_REF_RE.finditer(code):
        if m.group(1):                      # ${键|中文}
            ref, has = _text_key(m.group(1)), bool(m.group(2))
            if ref is None:                 # ${BASE_URL} 之类：环境变量，不是文案键
                continue
        elif m.group(3):                    # TEXT.get("键"[, "中文"])
            ref, has = m.group(3), bool(m.group(4))
        elif m.group(5):                    # TEXT["键"]
            ref, has = m.group(5), False
        else:                               # t("键"[, "中文"])
            ref, has = m.group(6), bool(m.group(7))
        out[ref] = out.get(ref, False) or has
    return out


# 中文字面量出现在这些地方是**正当**的，不该报：步骤名/日志/失败信息、变量赋值（造数据）
_SAFE_CN_SINK = re.compile(
    r"""(?:tea_step|print|pytest\.fail|pytest\.skip|fail|skip|log\w*)\(\s*['"]"""
    r"""|^\s*\w+\s*=\s*['"]""", re.M)
_ANY_CN_LIT = re.compile(
    r"""(?P<pre>[^\n]{0,40}?)(?P<q>['"])(?P<t>[^'"\n]*[一-鿿][^'"\n]*)(?P=q)""")


def _stray_cn_literals(code: str) -> list[str]:
    """代码里还剩哪些中文字面量 —— 定位器 API 之外的也要抓。

    **这条是被自己漏改逼出来的**：改造网关那 5 个脚本时，
    `_open_more_menu(page, "发布上线")` 一处没换掉 —— 文案传给的是**自定义函数**，
    按 API 名单扫的规则（name=/get_by_text/filter(has_text=…）看不见它，
    我和平台扫描器一起漏了，直到跑英文才红出来。
    所以反过来判：正文里的中文字面量，除了那几个正当去处（tea_step 步骤名、print/fail
    信息、变量赋值造数据），其余一律提醒 —— 它极可能是拿去定位/断言的。
    """
    out: list[str] = []
    for m in _ANY_CN_LIT.finditer(code):
        txt = m.group("t")
        if txt.startswith("${"):
            continue
        pre = m.group("pre")
        if _SAFE_CN_SINK.search(pre + m.group("q")):
            continue
        if txt not in out:
            out.append(txt)
    return out


def _scan_ui_script(content: str, language: str,
                    known_keys: set[str] | None = None) -> tuple[list[str], list[str]]:
    """返回 (硬错误, 软警告)。规矩跟接口回推一致：外部取值一律走变量，不许写死。"""
    errors: list[str] = []
    warns: list[str] = []
    reader = "process.env" if language == "typescript" else "os.getenv"

    # 引用了词典里没有的键 → 软警告。
    # 词典的定位是**「测试引用到的文案清单」**，不是被测系统 locale 的镜像 ——
    # 全量导进来的 2416 条里只有 31 条真被用到，剩下 2385 条是会过期的重复数据，
    # 已清掉。所以是**按需登记**：CC 引用哪条，那条才该在词典里。
    # 这道门禁就是提醒它去登记，不然英文环境下那几处会静默退回中文。
    code = _strip_noncode(content)          # 注释/文档串里的示例不算引用
    _hint = _t_refs(code)
    if _hint and known_keys is not None:
        # 两种命名空间拼法都算已登记（`ns:a.b` ↔ `ns.a.b`）：词典里存的是点号、
        # 脚本按被测系统写冒号。不认的话门禁把明明有的词报成"没登记"——
        # 实测 5 条里 4 条是这么误报的，而现在这条是**硬拦**，误报就直接卡住回推。
        from app.services.ui_text_render import key_aliases
        _known_all = {a for k in known_keys for a in key_aliases(k)}
        _missing = sorted(set(_hint) - _known_all)
        _naked = [k for k in _missing if not _hint[k]]
        if _naked:
            # **硬拦，不是警告。** 这种脚本平台压根不会跑（executor 那道拦截会拒），
            # 放行只是让人多跑一趟；更要紧的是它坏起来一半是看不见的：
            # 正例红在「找不到元素」，而「不应出现」那类负例**假绿**（占位/键名匹配不到
            # 任何元素，"不该存在"当然成立）。恒真断言不会自己喊疼。
            errors.append(
                f"{len(_naked)} 处文案键词典里没有、又没带中文原文"
                f"（{'、'.join(_naked[:3])}…）—— 平台会**拒绝执行**这个脚本："
                f"占位换不掉时正例红在「找不到元素」上、而「不应出现」那类断言会假绿。"
                f"两条都做：写成 ${{键|中文原文}}，并用 tb_upsert_i18n_terms 登记 key+zh+en。")
        _with_hint = [k for k in _missing if _hint[k]]
        if _with_hint:
            warns.append(
                f"{len(_with_hint)} 处 t() 引用的键词典里没有，但带了中文原文"
                f"（{'、'.join(_with_hint[:3])}…）—— 不会挂（退回中文），"
                f"但**英文环境下测的还是中文**。要真能测英文就把它们登记上。")

    # 硬编码的 UI 中文文案 → 软警告。**不硬拦**：词典总有不全的时候，
    # 硬拦会把人卡死在一条查不到的词上。
    # 只扫定位/断言里的文案（name=/text=/has_text=），不扫注释和普通字符串 ——
    # 脚本头部的说明、变量名里带中文都不算。
    # `${键|中文}` 里的中文**不算硬编码** —— 那正是规范要求的写法。
    # 不排掉的话：照规范写反被门禁骂"有 3 处硬编码中文"，实测第一条自测用例就中招。
    code_no_ph = _PH_RE.sub("PH", code)          # 同一个正则，见 _T_REF_RE 上面那段
    _cn_hits = [m for m in _UI_TEXT_RE.finditer(code_no_ph)
                if _CJK_RE.search(next(g for g in m.groups() if g))]
    # 定位器 API 之外的中文（典型：传给自定义 helper 的文案）—— 见 _stray_cn_literals
    stray = [t for t in _stray_cn_literals(code)
             if t not in [next(g for g in m.groups() if g) for m in _cn_hits]]
    if stray:
        warns.append(
            f"正文里还有 {len(stray)} 处中文字面量不在定位器 API 上（{'、'.join(stray[:3])}…）——"
            f"如果它们最终被拿去定位/断言（比如传给自己写的 helper），"
            f"换英文环境一样会挂。是文案就写成 ${{键|中文}}；是步骤名/日志就不用管。")
    if _cn_hits and "tea_i18n" not in content:
        sample = "、".join(
            next(g for g in m.groups() if g)[:12] for m in _cn_hits[:3])
        warns.append(
            f"脚本里有 {len(_cn_hits)} 处硬编码中文文案（{sample}…），换英文环境会全挂。"
            f"优先用 data-testid 定位；必须用文案时写成占位变量 "
            f"`\"${{services.action.more|更多}}\"` —— 平台按 TEST_LANGUAGE 在执行前替换成"
            f"当前语种，词典缺这个语种就退回竖线后面的中文。"
            f"详见 tb_get_sync_spec(kind='ui_script') 的「文案纪律」。")

    for line in content.splitlines():
        if reader in line:
            continue  # 这一行本身就是在读变量，允许它带默认值
        for m in _URL_LITERAL_RE.finditer(line):
            errors.append(
                f'写死了服务地址 {m.group(1)[:60]} —— 换环境必挂。'
                f'改成从变量取：{_UI_ENV_HINT}，再用 f"{{BASE_URL}}/xxx" 拼。'
            )
        for m in _CRED_LITERAL_RE.finditer(line):
            if _INVALID_CRED_RE.search(m.group(2)):
                continue          # 故意用错的凭据，见 _INVALID_CRED_RE
            errors.append(
                f'写死了凭据 {m.group(1)} —— 凭据只能来自环境变量。'
                f'改成 {reader}("ADMIN_PASSWORD"{"" if language == "typescript" else ", \'\'"})。'
            )

    if language == "python":
        if "def test_" not in content:
            errors.append("没找到 def test_ 开头的测试函数 —— 平台用 pytest 跑它，必须有。")
        if "os" not in content.split("\n")[0] and "import os" not in content:
            warns.append("没 import os，那就没法读环境变量；除非这条用例真的不需要任何外部取值。")
        # 自己 sync_playwright() 起浏览器，在平台上**必挂**：平台用 pytest 跑，
        # 而仓库的 pytest 配置是 asyncio_mode=auto，每个用例都被包进事件循环，
        # 此时调 sync API 会抛 "Playwright Sync API inside the asyncio loop"。
        # 这条错误信息完全看不出该怎么改，所以在入库时就说清楚 —— 不然要等到
        # 执行那一步才发现，中间还隔着一次排队和几十秒（实测踩过）。
        if "sync_playwright(" in content:
            errors.append(
                "别自己 sync_playwright() 起浏览器 —— 平台用 pytest 跑，"
                "每个用例都在事件循环里，自己起 sync API 会抛"
                "「Playwright Sync API inside the asyncio loop」。"
                "改成用 pytest-playwright 的 page fixture："
                "`from playwright.sync_api import Page, expect` + "
                "`def test_xxx(page: Page):`，浏览器由平台管。"
            )
    else:
        if "test(" not in content:
            errors.append("没找到 test(...) 用例 —— 平台用 npx playwright test 跑它，必须有。")

    return errors, warns


def _first_test_func(content: str) -> str | None:
    m = re.search(r"^\s*(?:async\s+)?def\s+(test_\w+)", content, re.M)
    return m.group(1) if m else None


async def sync_ui_script(
    session: AsyncSession,
    case_id: str,
    content: str,
    language: str | None = None,
    file_name: str | None = None,
) -> dict:
    """把你在本地写好并跑通的 Playwright 脚本回推到用例上。"""
    try:
        cid = uuid.UUID(case_id)
    except (ValueError, AttributeError):
        return {"error": f"case_id 不是合法 UUID: {case_id}"}

    content = (content or "").strip()
    if not content:
        return {"error": "content 是空的——要回推的是脚本正文，不是文件路径。"}

    case = await session.get(Case, cid)
    if not case:
        return {"error": f"用例不存在: {case_id}"}

    lang = (language or "").lower() or _detect_language(content, file_name)
    if lang not in ("python", "typescript"):
        return {"error": f"language 只支持 python / typescript，收到 {language}"}

    # 已登记的键，用来判「引用了词典里没有的」。查不到就传 None（不报这条）。
    _known = None
    try:
        from app.models.i18n_message import ProjectI18nMessage
        from app.models.project import Branch as _Br
        _c = await session.get(Case, cid)
        _b = await session.get(_Br, _c.branch_id) if _c else None
        if _b:
            _known = set((await session.execute(
                select(ProjectI18nMessage.key_text)
                .where(ProjectI18nMessage.project_id == _b.project_id)
            )).scalars().all())
    except Exception:  # noqa: BLE001
        _known = None
    errors, warns = _scan_ui_script(content, lang, _known)

    # ── 断言门禁（B5）──
    # 唯一的硬拦截：一条断言都没有。"跑通了但什么都不验证"是最常见的作弊路径，
    # 而且 100% 可判。强度变化只给软警告 —— 强度做不到可靠硬判，误拦会逼你
    # 拆断言凑数，比不拦更糟。
    from app.services import assertion_profile as ap
    profile = ap.build(content)
    if profile["total"] == 0:
        errors.append(
            "整个脚本一条断言都没有 —— 这样它只能证明流程跑完了没报错，"
            "证明不了结果是对的。至少断言一个具体结果（页面文案 / 数量 / 状态码）。"
        )

    if errors:
        return {
            "error": "脚本没通过入库检查，先改掉下面这些再传（这些问题换个环境就会挂）：",
            "problems": errors,
            "spec": "调 tb_get_sync_spec(kind='ui_script') 看完整规矩和可抄的模板。",
        }

    # 和上一版比，把退化说出来。不拦，但让它**可见** —— 看得见就治得住。
    prev = await script_service.get_active_script(session, cid, "ui")
    warns.extend(ap.diff_warnings(prev.assertion_profile if prev else None, profile))

    fname = file_name or ("test_ui.py" if lang == "python" else "ui.spec.ts")
    func = _first_test_func(content) if lang == "python" else None

    script = await script_service.create_script(
        session, case_id=cid, script_type="ui", content=content,
        file_name=fname, func_name=func, language=lang,
        source="cc_synced", created_by=await _active_user_id(session),
    )
    script.assertion_profile = profile

    # 页面的「UI 测试」页签是看 cases.ui_scenario 决定渲不渲染的，脚本存在 scripts 表 ——
    # 两个数据源。只写脚本不建场景，页面会一直显示「还没有 UI 脚本」，
    # 明明已经回推成功、也能跑通。所以这里顺带按手工步骤把场景壳建出来。
    if not case.ui_scenario:
        manual = case.steps or []
        steps = [
            {
                "seq": i + 1,
                "phase": "setup" if i == 0 else ("verify" if i == len(manual) - 1 else "action"),
                "action": (st or {}).get("action", ""),
                "expected": (st or {}).get("expected", ""),
                "uiTarget": "",
            }
            for i, st in enumerate(manual)
        ] or [{"seq": 1, "phase": "action", "action": "见脚本", "expected": "", "uiTarget": ""}]
        case.ui_scenario = {"steps": steps, "variablesUsed": []}
    await session.commit()

    return {
        "status": "ok",
        "scriptId": str(script.id),
        "version": script.version,
        "language": lang,
        "fileName": fname,
        "funcName": func,
        "warnings": warns,
        "message": (
            f"已回推到用例「{case.case_code} {case.title}」的 UI 测试页签（v{script.version}）。"
            f"下一步用 tb_run_ui_script(case_id, env_id) 在目标环境上真跑一遍确认。"
        ),
    }
