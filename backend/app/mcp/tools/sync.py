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

术语澄清（很多人会混淆，这里写死口径）：
  · 「接口测试模块·单接口」= tb_generate_api_test：给接口文档、AI 造一组正/边界/安全场景，
     source_api_ids 关联接口、无 source_case_id。用于**无法活体验证**时。
  · 「用例·编排的接口场景」= 本模块 tb_sync_orchestrated_scenario：与某功能用例绑定
     （source_case_id）、你亲手验证过的多步 E2E 链、共享该用例的场景变量。二者不是一回事。
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_test import ApiTestScenario, ApiTestStep
from app.models.api_test_folder import ApiTestFolder
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
_SECRET_RE = re.compile(r"(PASSWORD|PWD|TOKEN|SECRET|KEY)", re.I)
# 明显是结构值/枚举/路径，不该被当成「写死的业务数据」误报
_STRUCT_ENUM = {
    "true", "false", "null", "none", "yes", "no", "on", "off",
    "get", "post", "put", "delete", "patch", "head", "options",
    "asc", "desc", "string", "number", "boolean", "object", "array",
    "application/json", "text/plain", "multipart/form-data",
}


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


def _looks_hardcoded(value: str) -> bool:
    """疑似写死的业务数据（启发式，宁保守勿滥报——只软警告，不拦截）。"""
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
    return bool(re.search(r"[A-Za-z一-鿿]", s))  # 含字母或中文才像业务数据


async def _active_user_id(session: AsyncSession) -> uuid.UUID | None:
    """MCP 无登录上下文：created_by 取一个真实 active 用户（优先 admin），避免外键失败。"""
    return (
        await session.execute(
            select(User.id).where(User.is_active.is_(True))
            .order_by(User.role.asc(), User.created_at.asc()).limit(1)
        )
    ).scalar_one_or_none()


# ─────────────────────────────────────────────────────────────
# 1. 回推规范
# ─────────────────────────────────────────────────────────────

_SPEC_VARIABLES = """## 变量三层模型（回推纪律的基准，务必分清）

| 层 | 谁定 | 怎么引用 | 什么时候用 |
|---|---|---|---|
| ① 项目级/全局 | 环境管理 / 自动化数据页 | 场景变量 kind=global_ref，值=全局键名；或直接 ${BASE_URL} 这类环境键 | 跨用例稳定共享：BASE_URL、账号、token、长期存在的基础数据 |
| ② 场景变量 | 用例详情·场景变量（本工具 tb_upsert_scenario_variables） | ${名字} 或 ${SV_名字} | 单个用例内、UI+接口共用；每次执行要唯一的名字用 kind=random/template |
| ③ 中间提取物 | 步骤 variables_extract | ${名字} | 一次执行内上一步→下一步传值（如登录拿 token、创建拿 id） |

**铁律：步骤里任何取值都必须来自上面三者之一，禁止写死。**
- ✅ `"name": "${svcName}"`（场景变量，kind=template：svc-{{$string:6}}）
- ✅ `"Authorization": "Bearer ${token}"`（token 来自上一步 variables_extract）
- ✅ `"url": "${BASE_URL}/api/v1/services/${serviceId}"`（BASE_URL 全局、serviceId 提取物）
- ❌ `"name": "test-service-001"`（写死业务名——换环境/重复跑必冲突）
- ❌ `"Authorization": "Bearer eyJhbGci..."`（写死 token）

内置可直接用、不用声明：RANDOM_8、TIMESTAMP、SV_RUN_ID，以及 Authorization 由平台按登录态自动注入（无需手写 token 步骤，除非要测鉴权本身）。"""

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

_SPEC_CASE = """## 步骤用例（tb_create_case，非本模块，但一并说明口径）

活体验证后回写步骤用例：case_type=e2e，步骤是**页面操作**（点按钮/填表单），
按钮名/字段标签/Toast 文案用被测系统真实文案；预期结果必须 UI 可见；禁止模糊词
（操作成功/显示正常/无报错）；每条只验一个点；preconditions 分环境前置+数据前置；
steps 每项含 seq/action/expected；多角色加 [管理员]/[租户] 标记。"""


async def get_sync_spec(kind: str = "all") -> dict:
    """获取回推规范。kind: case(步骤用例) / api_scenario(编排接口场景) / variables(变量纪律) / all。

    回推前先调它对齐口径：怎么选变量层、步骤/断言/提取物 JSON 形状、禁止写死的正反例。"""
    parts = {
        "variables": _SPEC_VARIABLES,
        "api_scenario": _SPEC_API_SCENARIO,
        "case": _SPEC_CASE,
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
        "4. tb_run_api_test 执行，确认变量都被正确解析。\n\n"
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

    与 tb_generate_api_test（单接口 AI 造场景）不是一回事：本工具是你亲手验证过的多步 E2E，
    绑定 source_case_id、共享该用例场景变量。入库前会做悬空引用硬拦截 + 疑似写死软警告。"""
    pid = uuid.UUID(project_id)
    bid = uuid.UUID(branch_id)
    steps = _loads(steps)
    if not isinstance(steps, list) or not steps:
        return {"error": "steps 必须是非空数组"}

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
    extracted: set[str] = set()
    for i, st in enumerate(norm):
        refs = _collect_refs(st.get("url"), st.get("headers"), st.get("body"), st.get("assertions"))
        for name in sorted(refs):
            if name not in allow and name not in extracted:
                dangling.append({"step": i + 1, "name": st.get("name") or f"step{i + 1}", "variable": name})
        # body 里疑似写死的业务数据 → 软警告
        for path, val in _iter_strings(st.get("body")):
            if _looks_hardcoded(val):
                warnings.append({"step": i + 1, "field": f"body.{path}" if path else "body", "value": val[:60]})
        # 本步提取物在其后步骤可用
        extra = st.get("variables_extract")
        if isinstance(extra, dict):
            extracted.update(extra.keys())

    if dangling:
        return {
            "error": "存在悬空变量引用，已拒绝入库（纪律：不允许写死，且引用必须可解析）",
            "dangling": dangling,
            "hint": "每个 ${x} 必须来自：①该用例场景变量（先 tb_upsert_scenario_variables）"
                    "②更早步骤的 variables_extract ③全局/环境键 ④内置(RANDOM_8/TIMESTAMP/SV_RUN_ID)。"
                    "可引用项见 tb_list_global_data / tb_list_scenario_variables。",
            "allowedSample": sorted(allow)[:30],
        }

    # ── 建场景（code = 分支内 AT-#### max+1；created_by 取真实 active 用户）──
    max_code = (await session.execute(
        select(sa_func.max(ApiTestScenario.code)).where(ApiTestScenario.branch_id == bid)
    )).scalar()
    next_num = 1
    if max_code:
        try:
            next_num = int(max_code.split("-")[1]) + 1
        except (IndexError, ValueError):
            pass
    code = f"AT-{next_num:04d}"

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
        ))

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
        "message": f"已回推场景 {code}（{len(norm)} 步）"
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
        "message": f"新增 {len(created)}、更新 {len(updated)} 个场景变量"
                   + (f"，{len(errors)} 个失败" if errors else ""),
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

async def list_global_data(session: AsyncSession, project_id: str) -> dict:
    """汇总项目级**可引用**的全局数据，帮你判断哪些该走 global_ref、哪些不该写死。

    含：全局变量、各环境变量键（凭证类脱敏）、项目自动化共享资源。返回的键名可用于
    场景变量 kind=global_ref（value_template 填该键名），或步骤里 ${键名} 直接引用。"""
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

    resources = [{
        "name": r.name,
        "description": r.description,
        "keep": r.keep,
        "existsCheck": r.exists_check,
    } for r in (await session.execute(
        select(AutomationResource).where(AutomationResource.project_id == pid)
        .order_by(AutomationResource.name)
    )).scalars().all()]

    return {
        "projectId": project_id,
        "globalVariables": global_vars,
        "environments": env_data,
        "automationResources": resources,
        "usage": "键名可用于：场景变量 kind=global_ref(value_template=键名)，或步骤 ${键名}。"
                 "凭证类值已脱敏(***)，运行时由平台按所选环境真实注入。",
    }
