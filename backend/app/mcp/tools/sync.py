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


def _typo_assertions(seq: int, st: dict) -> list[dict]:
    """断言的期望值被写成字符串，而响应里是布尔 —— **必然假红**。

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
        exp = a.get("expected") if a.get("expected") is not None else a.get("value")
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
2. **没有就你自己调接口造出来**（活体验证时顺手造），造完 **不要清理** —— 它要留给后续场景复用；
3. 造好后（或本来就有）用 `tb_upsert_automation_resource` 登记 `exists_check`：
   写明「怎么按名字/条件找到它 + 从响应里抽哪个字段当 id」。之后每次跑，平台会在
   第一个步骤之前自动探一次并注入 `${资源名}`，换环境也能找到那个环境里的对应资源。

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
- `status`：字段用 `value`（状态码），operator ∈ ==/!=/in
- `body_field`：`field`=JSONPath（点号+下标，如 data.items[0].id），operator ∈ ==/!=/not_empty/contains/not_contains，比较值放 `expected`
- `body_contains`：`value`=子串，operator ∈ contains/not_contains

variables_extract：`{变量名: JSONPath}`，把响应里的值抽成变量供**后续步骤** ${变量名} 用（典型：登录抽 token、创建抽 id、清理按 id 删）。

强烈建议传 source_case_id 关联对应功能用例——这样运行时会自动注入该用例的场景变量，UI 与接口共用一份定义。"""

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


_SPEC_CASE = """## 步骤用例（tb_create_case，非本模块，但一并说明口径）

活体验证后回写步骤用例：case_type=e2e，步骤是**页面操作**（点按钮/填表单），
按钮名/字段标签/Toast 文案用被测系统真实文案；预期结果必须 UI 可见；禁止模糊词
（操作成功/显示正常/无报错）；每条只验一个点；preconditions 分环境前置+数据前置；
steps 每项含 seq/action/expected；多角色加 [管理员]/[租户] 标记。"""


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

### 流程

1. 本地写脚本，**先自己跑通**（别回推没验证过的东西）
### 文案纪律：优先 testid，退回文案时用 `t()`

**数据不许写死已经有硬拦截，文案是同一件事的另一半。**
脚本里 `name="更多"` 这种硬编码中文，换英文环境全挂 ——
实测 9 个脚本 57 处写死中文、只有 5 处用 testid。

按这个顺序选定位方式：

  1. **`data-testid`**（`page.get_by_test_id("sync-status-bar")`）—— 最稳，
     文案改了、语种换了都不受影响。被测系统有就用它
  2. **结构 + 角色**（`get_by_role("button")` + 位置/父级），不带 name
  3. **文案** —— 只有前两条都不行才用，而且**必须走 `t()`**：

         from tea_i18n import t
         page.get_by_role("button", name=t("更多")).click()
         expect(page.get_by_test_id("sync-status-bar")).to_contain_text(t("草稿"))

`t()` 由平台注入沙箱（tea_i18n.py），按环境变量 **`TEST_LANGUAGE=zh|en`** 取译文
（不配就是中文）；
**查不到就原样返回中文**，所以词典没收录的词也不会让脚本挂掉。
本地写的时候自己 stub 一个 `def t(s): return s` 就行。

回推时会扫硬编码中文给**软警告**（不硬拦 —— 词典总有不全的时候）。

2. `tb_sync_ui_script(case_id, content)` 入库
3. `tb_run_ui_script(case_id, env_id)` 在目标环境上再跑一遍——平台跑通了才算通
4. 失败看 `tb_get_ui_script_result(case_id)`：状态、耗时、错误摘要、截图数
"""


async def get_sync_spec(kind: str = "all") -> dict:
    """获取回推规范。kind: case(步骤用例) / api_scenario(编排接口场景) / ui_script(UI 脚本) / variables(变量纪律) / all。

    回推前先调它对齐口径：怎么选变量层、步骤/断言/提取物 JSON 形状、禁止写死的正反例。"""
    parts = {
        "variables": _SPEC_VARIABLES,
        "api_scenario": _SPEC_API_SCENARIO,
        "ui_script": _SPEC_UI_SCRIPT,
        "case": _SPEC_CASE,
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
) -> dict:
    """把活体验证过的接口链显式写入「用例·编排的接口场景」。

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
    for i, raw in enumerate(steps):
        st = _loads(raw)
        if not isinstance(st, dict):
            return {"error": f"第 {i + 1} 个 step 不是对象"}
        st = dict(st)
        for f in ("headers", "body", "assertions", "variables_extract"):
            if f in st:
                st[f] = _loads(st[f])
        norm.append(st)

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
        # 异步下发的断言没开重试 → 软警告。见 _needs_retry。
        r = _needs_retry(i + 1, st)
        if r:
            warnings.append(r)
        # 本步提取物在其后步骤可用
        extra = st.get("variables_extract")
        if isinstance(extra, dict):
            extracted.update(extra.keys())

    if bad_types:
        return {
            "error": "断言的期望值类型写错了，已拒绝入库 —— 这类错误**必然假红**，"
                     "而且报错长得像平台在说胡话（「期望 true｜实际 True」差一个大小写）。",
            "badAssertions": bad_types,
            "hint": "JSON 里 true/false 是布尔、123 是数字，加引号就变成字符串，"
                    "和响应里的真值严格比较必挂。判定不会替你放松："
                    "「期望 true、实际 1」如果算相等，那是另一种假绿。"
                    "把引号去掉即可（expected: true，不是 \"true\"）。",
        }

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

    for i, st in enumerate(norm):
        session.add(ApiTestStep(
            scenario_id=scenario.id,
            sort_order=i,
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
        "sourceCaseId": str(scid) if scid else None,
        "scenarioVariablesLinked": scenario_var_names,
        "hardcodeWarnings": warnings,
        "replacedExisting": replaced,
        "message": (f"已覆盖同名场景 {code}" if replaced else f"已新建场景 {code}")
                   + f"（{len(norm)} 步）"
                   + (f"，⚠ {len(warnings)} 处疑似写死（仅提醒，已入库）" if warnings else "，无写死告警"),
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

    created, updated, errors = [], [], []
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
            kind = "literal"

        val = str(item.get("value_template") or "")
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
        "antipatterns": antipatterns,
        "message": f"新增 {len(created)}、更新 {len(updated)} 个场景变量"
                   + (f"，{len(errors)} 个失败" if errors else "")
                   + (f"。⚠ {len(antipatterns)} 处反模式（见 antipatterns，已入库但建议改）"
                      if antipatterns else ""),
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
        select(GlobalVariable).order_by(GlobalVariable.sort_order, GlobalVariable.key)
    )).scalars().all()]

    # 环境变量（Environment 是全局的，非项目隔离）——列出键名，凭证值脱敏
    envs = (await session.execute(select(Environment).order_by(Environment.name))).scalars().all()
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
    ⚠ 探不到时不会自动补建（create_def 暂只登记备查、不执行），只会让引用它的步骤报
      「变量未解析」，并在运行结果顶部提示缺哪个。所以第 2 步不能省。

    exists_check 形如 {"method":"GET","url":"${BASE_URL}/api/v1/upstreams?page_size=100",
                      "match":{"field":"name","equals":"autotest-default-upstream"},
                      "extract":{"upstreamId":"id"}}
    create_def   形如 {"method":"POST","url":"${BASE_URL}/api/v1/upstreams","body":{...}}
                 （登记备查，说明这资源当初是怎么造的；平台暂不自动执行）
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
                   " ⚠ 探不到时不会自动补建（create_def 暂只登记不执行），"
                   "只会让引用它的步骤报「变量未解析」——请确认该资源在目标环境确实存在。",
    }


# ── UI 脚本回推 ──────────────────────────────────────────────────────────────

_UI_ENV_HINT = 'BASE_URL = os.getenv("BASE_URL", "")'

# 服务地址/凭据写死是硬伤：换环境就全挂，而且挂得很隐蔽（脚本还在跑，只是打了别的系统）。
_URL_LITERAL_RE = re.compile(r"""["'`](https?://[^"'`\s]+)["'`]""")
_CRED_LITERAL_RE = re.compile(
    r"""(password|passwd|pwd|token|secret|api_?key)\s*[:=]\s*["'`]([^"'`\s]{4,})["'`]""",
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


def _scan_ui_script(content: str, language: str) -> tuple[list[str], list[str]]:
    """返回 (硬错误, 软警告)。规矩跟接口回推一致：外部取值一律走变量，不许写死。"""
    errors: list[str] = []
    warns: list[str] = []
    reader = "process.env" if language == "typescript" else "os.getenv"

    # 硬编码的 UI 中文文案 → 软警告。**不硬拦**：词典总有不全的时候，
    # 硬拦会把人卡死在一条查不到的词上。
    # 只扫定位/断言里的文案（name=/text=/has_text=），不扫注释和普通字符串 ——
    # 脚本头部的说明、变量名里带中文都不算。
    _cn_hits = [m for m in _UI_TEXT_RE.finditer(content)
                if _CJK_RE.search(next(g for g in m.groups() if g))]
    if _cn_hits and "tea_i18n" not in content:
        sample = "、".join(
            next(g for g in m.groups() if g)[:12] for m in _cn_hits[:3])
        warns.append(
            f"脚本里有 {len(_cn_hits)} 处硬编码中文文案（{sample}…），换英文环境会全挂。"
            f"优先用 data-testid 定位；必须用文案时走 `from tea_i18n import t` + "
            f"`t(\"更多\")`，平台按 PLAYWRIGHT_LOCALE 注入译文，查不到会原样返回中文。"
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

    errors, warns = _scan_ui_script(content, lang)

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
