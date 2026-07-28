"""接口测试场景生成 — 读取 API 定义 → AI 生成场景+步骤 → 存储"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import AsyncIterator

from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_test import ApiTestScenario, ApiTestStep
from app.models.api_test_folder import ApiTestFolder
from app.services.ai import llm_client
from app.services.ai_config_resolver import ResolvedAIConfig

logger = logging.getLogger(__name__)


@dataclass
class GenEvent:
    type: str
    data: dict


async def generate_api_test(
    *,
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    api_info: str,
    api_ids: list[str] | None,
    env_variables: dict | None,
    folder_id: uuid.UUID | None = None,
    case_id: uuid.UUID | None = None,
    ai_config: ResolvedAIConfig,
    session: AsyncSession,
    user_id: uuid.UUID,
) -> AsyncIterator[GenEvent]:
    """生成接口测试场景。"""

    yield GenEvent(type="step_start", data={"step": 1, "title": "读取接口定义和环境变量"})

    # 如果前端没传环境变量，从项目第一个环境自动读取
    if not env_variables:
        try:
            from app.mcp.tools import environments
            envs = await environments.list_environments(session=session)
            if envs and len(envs) > 0:
                first_env = envs[0]
                merged = await environments.get_merged_variables(session=session, env_id=str(first_env["id"]))
                if merged:
                    env_variables = {v["key"]: v["value"] for v in merged if v.get("key")}
        except Exception as e:
            logger.warning("Auto-load env vars failed: %s", e)

    # 从 API 节点读取详情（如果有 api_ids）
    full_api_info = api_info or ""
    if api_ids:
        from app.mcp.tools import api_endpoints
        for aid in api_ids:
            try:
                node = await api_endpoints.get_api_node(session, aid)
                if node:
                    parts = [f"### {node.get('method', 'GET')} {node.get('url', '')} — {node.get('name', '')}"]
                    if node.get("description"):
                        parts.append(f"描述: {node['description']}")
                    if node.get("params"):
                        parts.append(f"参数 Schema: {json.dumps(node['params'], ensure_ascii=False)}")
                    if node.get("headers"):
                        parts.append(f"Headers: {json.dumps(node['headers'], ensure_ascii=False)}")
                    if node.get("body"):
                        parts.append(f"Body: {node['body'][:500]}")
                    full_api_info += "\n\n" + "\n".join(parts)
            except Exception as e:
                logger.warning("Failed to load api node %s: %s", aid, e)

    if not full_api_info.strip():
        yield GenEvent(type="error", data={"message": "没有接口信息，请选择接口或手动输入"})
        return

    yield GenEvent(type="step_done", data={"step": 1, "summary": f"接口信息 {len(full_api_info)} 字符"})

    # Step 2: AI 生成
    yield GenEvent(type="step_start", data={"step": 2, "title": "AI 生成测试场景"})

    from pathlib import Path
    skill_path = Path(__file__).resolve().parent.parent.parent / "skills" / "preset" / "tb-api-case-generate" / "SKILL.md"
    skill_content = ""
    if skill_path.exists():
        raw = skill_path.read_text(encoding="utf-8")
        # 去掉 frontmatter
        if raw.startswith("---"):
            end = raw.find("---", 3)
            if end > 0:
                raw = raw[end + 3:].strip()
        skill_content = raw

    env_str = ""
    if env_variables:
        env_str = "\n".join(f"- ${{{k}}} = {v}" for k, v in env_variables.items())

    # 源用例的场景变量：提示 AI 用 ${名字} 引用，保证 UI/接口共用同一份、造数唯一
    sv_str = ""
    if case_id:
        try:
            from app.models.scenario_variable import ScenarioVariable
            sv_rows = (
                await session.execute(select(ScenarioVariable).where(ScenarioVariable.case_id == case_id))
            ).scalars().all()
            if sv_rows:
                lines = []
                for v in sv_rows:
                    hint = {"random": "每次执行唯一值(造数用)", "global_ref": "引用全局数据", "literal": "固定值"}.get(v.kind, v.kind)
                    lines.append(f"- ${{{v.name}}} — {v.description or hint}")
                sv_str = "\n".join(lines)
        except Exception as e:
            logger.warning("加载源用例场景变量失败 case_id=%s: %s", case_id, e)

    messages = [
        {"role": "system", "content": f"""你是资深 QA 工程师。严格按照以下规范生成接口测试场景。

{skill_content}

{f'当前项目环境变量：\n{env_str}' if env_str else ''}
{f'''本场景可用的场景变量（造数/唯一数据请优先用这些，格式 ${{名字}}，与 UI 测试共用同一份）：
{sv_str}''' if sv_str else ''}

直接输出 JSON，不要用 ```json 包裹。"""},
        {"role": "user", "content": f"""请根据以下接口定义生成测试场景：

{full_api_info}"""},
    ]

    # 带重试的生成：haiku 等模型偶发「输出超 max_tokens 被截断 → JSON 不完整/无法解析」，
    # 直接表现为「编排后接口测试没数据」或静默丢场景。对策：
    #  1) max_tokens 拉高到 16000（网关允许 >8192），给完整 JSON 留足空间；
    #  2) 解析失败或 finish_reason=length（截断）就重试，最多 3 次，彻底消除「无数据」。
    GEN_MAX_TOKENS = 16000
    MAX_ATTEMPTS = 3
    full_content = ""
    parsed: list[dict] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        full_content = ""
        finish_reason = None
        try:
            async for chunk in llm_client.stream(messages, config=ai_config, max_tokens=GEN_MAX_TOKENS):
                if chunk.delta:
                    full_content += chunk.delta
                    yield GenEvent(type="step_progress", data={"step": 2, "chunk": chunk.delta})
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
        except Exception as e:
            if attempt < MAX_ATTEMPTS:
                logger.warning("接口场景生成第 %d 次 LLM 调用异常，重试: %s", attempt, e)
                continue
            yield GenEvent(type="error", data={"message": f"AI 生成失败: {str(e)[:200]}"})
            return

        parsed = _parse_scenarios(full_content)
        truncated = finish_reason == "length"
        # 只有「解析出场景 且 未被截断」才算干净成功；截断即便侥幸解析出部分也重试，保证完整不丢场景
        if parsed and not truncated:
            break
        if attempt < MAX_ATTEMPTS:
            logger.warning(
                "接口场景生成第 %d 次结果不可靠(parsed=%d truncated=%s len=%d)，重试",
                attempt, len(parsed), truncated, len(full_content),
            )
            yield GenEvent(type="step_progress", data={"step": 2, "chunk": f"\n[结果不完整，正在第 {attempt + 1} 次重试…]\n"})

    yield GenEvent(type="step_done", data={"step": 2, "summary": f"生成完成 {len(full_content)} 字符"})

    # Step 3: 解析 + 入库
    yield GenEvent(type="step_start", data={"step": 3, "title": "解析结果并入库"})

    if not parsed:
        yield GenEvent(type="error", data={"message": "无法解析 AI 返回的 JSON（已重试多次仍失败，请重试或减少所选接口数量）"})
        return

    # 获取当前最大编号
    max_code_result = await session.execute(
        select(sa_func.count()).where(
            ApiTestScenario.project_id == project_id,
            ApiTestScenario.branch_id == branch_id,
        )
    )
    code_seq = (max_code_result.scalar() or 0) + 1

    created_ids = []
    auto_folders: dict[str, uuid.UUID] = {}

    # 去写死：入库前把步骤 body 里 *_id/*_ids 的真实 UUID 回写成 ${VAR} 引用，
    # 真实值沉淀为 literal 场景变量（仅当有来源用例、能存场景变量时）。
    reserved_vars = set((env_variables or {}).keys()) | {
        "BASE_URL", "LOGIN_URL", "ADMIN_USERNAME", "ADMIN_PASSWORD",
        "TENANT_USERNAME", "TENANT_PASSWORD", "ADMIN_USER", "ADMIN_PASS", "AUTH_TOKEN",
    }
    preassigned_vars: dict[str, dict] = {}
    if case_id:
        try:
            preassigned_vars = _dehardcode_uuid_bodies(parsed, reserved_vars)
        except Exception as e:
            logger.warning("回写 UUID 变量为引用失败: %s", e)

    for sc in parsed:
        sc_folder_id = folder_id
        if not sc_folder_id:
            module_name = _guess_module_name(sc.get("title", ""), full_api_info)
            if module_name:
                if module_name in auto_folders:
                    sc_folder_id = auto_folders[module_name]
                else:
                    existing = await session.execute(
                        select(ApiTestFolder).where(
                            ApiTestFolder.branch_id == branch_id,
                            ApiTestFolder.name == module_name,
                        )
                    )
                    folder = existing.scalars().first()
                    if not folder:
                        folder = ApiTestFolder(branch_id=branch_id, name=module_name)
                        session.add(folder)
                        await session.flush()
                    sc_folder_id = folder.id
                    auto_folders[module_name] = folder.id

        scenario = ApiTestScenario(
            project_id=project_id,
            branch_id=branch_id,
            code=f"AT-{code_seq:04d}",
            title=sc.get("title", "未命名场景"),
            priority=sc.get("priority", "P1"),
            description=sc.get("description", ""),
            status="draft",
            source_api_ids=api_ids,
            source_case_id=case_id,
            env_variables=env_variables,
            folder_id=sc_folder_id,
            created_by=user_id,
        )
        session.add(scenario)
        await session.flush()

        for i, step in enumerate(sc.get("steps", [])):
            assertions = _normalize_assertions(step.get("assertions", []))
            session.add(ApiTestStep(
                scenario_id=scenario.id,
                sort_order=i,
                group_name=step.get("group"),
                name=step.get("name", f"步骤{i+1}"),
                method=step.get("method", "GET"),
                url=step.get("url", ""),
                headers=step.get("headers"),
                body=step.get("body"),
                assertions=assertions,
                variables_extract=step.get("variables_extract"),
            ))

        created_ids.append(str(scenario.id))
        code_seq += 1

        await session.commit()

        yield GenEvent(type="scenario_created", data={
            "id": str(scenario.id),
            "code": scenario.code,
            "title": scenario.title,
            "priority": scenario.priority,
            "stepCount": len(sc.get("steps", [])),
        })

    yield GenEvent(type="step_done", data={
        "step": 3,
        "summary": f"创建 {len(created_ids)} 个场景",
    })

    # 自动抽取用例级场景变量（UI / 接口测试共用一份）——仅当来源用例存在时回写。
    # 解决「场景变量没有被自动提取保存」：把编排流量里引用的既有资源 ID、以及步骤里
    # 悬空的 ${VAR} 引用，沉淀成用例场景变量，后续生成会自动 ${引用} 复用同一份。
    extracted_names: list[str] = []
    if case_id and parsed:
        try:
            from app.models.scenario_variable import ScenarioVariable
            case_uuid = case_id if isinstance(case_id, uuid.UUID) else uuid.UUID(str(case_id))
            existing_names = {
                r.name for r in (await session.execute(
                    select(ScenarioVariable).where(ScenarioVariable.case_id == case_uuid)
                )).scalars().all()
            }
            # 已回写为引用的 UUID 变量(真实值) + 其余步骤里的悬空引用/未回写字面量。
            # reserved 并入 preassigned 名，避免被当成悬空引用又建一个空占位。
            candidates = {
                **preassigned_vars,
                **_extract_case_variables(parsed, reserved_vars | set(preassigned_vars)),
            }
            for name, info in candidates.items():
                if name in existing_names:
                    continue
                session.add(ScenarioVariable(
                    case_id=case_uuid, name=name, kind=info["kind"],
                    value_template=info["value"], var_type="string",
                    description=info["desc"],
                ))
                extracted_names.append(name)
            if extracted_names:
                await session.commit()
        except Exception as e:
            logger.warning("自动抽取场景变量失败 case_id=%s: %s", case_id, e)
        if extracted_names:
            yield GenEvent(type="variables_extracted", data={
                "count": len(extracted_names),
                "names": extracted_names,
            })

    yield GenEvent(type="done", data={
        "scenarioIds": created_ids,
        "totalScenarios": len(created_ids),
        "extractedVariables": extracted_names,
    })


_UUID_RE = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
_VAR_REF_RE = re.compile(r'\$\{(\w+)\}')
# 运行时/系统内建变量：由执行引擎注入，不建场景变量
_RUNTIME_VARS = {"TIMESTAMP", "RANDOM_8", "RANDOM", "RANDOM_STR", "RUN_ID", "UUID", "NOW", "DATE"}


def _is_uuid(v) -> bool:
    return isinstance(v, str) and bool(_UUID_RE.match(v.strip()))


def _extract_case_variables(scenarios: list[dict], reserved: set[str]) -> dict[str, dict]:
    """从生成的场景步骤里自动抽取「用例级场景变量」(UI/接口共用一份)：

      1) 请求体里 *_id / *_ids 字段的真实 UUID 值 → literal 变量(存真实值,直接可复用)；
      2) 步骤里引用了 ${VAR}，但既不是环境变量、也不是步骤链提取、也不是运行时内建的
         「悬空引用」→ 建占位 literal 变量(空值 + ⚠ 描述,提示补真实值)。

    步骤链中间值(variables_extract 产物)、环境全局、TIMESTAMP/RANDOM 等运行时变量不在此列。
    返回 {变量名: {value, kind, desc}}。
    """
    # 步骤链提取名：「上一步提取→下一步用」的中间值,不提成场景变量
    chain_names: set[str] = set()
    for sc in scenarios:
        for step in sc.get("steps", []):
            ve = step.get("variables_extract") or {}
            if isinstance(ve, dict):
                chain_names.update(ve.keys())
    exclude = {n.upper() for n in reserved} | {n.upper() for n in chain_names} | _RUNTIME_VARS

    found: dict[str, dict] = {}

    def visit_body(node):
        if isinstance(node, dict):
            for k, val in node.items():
                kl = str(k).lower()
                if kl.endswith("_id") and _is_uuid(val):
                    found.setdefault(k.upper(), {
                        "value": val.strip(), "kind": "literal",
                        "desc": f"自动提取自编排流量：字段 {k}",
                    })
                elif kl.endswith("_ids") and isinstance(val, list):
                    uuids = [x for x in val if _is_uuid(x)]
                    if uuids:
                        note = "" if len(uuids) == 1 else f"（数组共 {len(uuids)} 个，取第一个）"
                        found.setdefault(k[:-1].upper(), {  # *_ids -> *_ID
                            "value": uuids[0].strip(), "kind": "literal",
                            "desc": f"自动提取自编排流量：字段 {k}{note}",
                        })
                        for x in val:
                            visit_body(x)
                else:
                    visit_body(val)
        elif isinstance(node, list):
            for x in node:
                visit_body(x)

    refs: set[str] = set()

    def collect_refs(node):
        if isinstance(node, str):
            refs.update(_VAR_REF_RE.findall(node))
        elif isinstance(node, dict):
            for v in node.values():
                collect_refs(v)
        elif isinstance(node, list):
            for v in node:
                collect_refs(v)

    for sc in scenarios:
        for step in sc.get("steps", []):
            body = step.get("body")
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except Exception:
                    body = step.get("body")
            visit_body(body)
            collect_refs(step.get("url"))
            collect_refs(step.get("headers"))
            collect_refs(body)

    # 悬空引用 → 占位变量(空值,提示补真实值)
    for name in sorted(refs):
        nu = name.upper()
        if nu in exclude or nu in found:
            continue
        found[name] = {
            "value": "", "kind": "literal",
            "desc": "⚠ 步骤引用了该变量但未定义，请填写真实值（UI/接口测试共用一份）",
        }

    return found


def _dehardcode_uuid_bodies(scenarios: list[dict], reserved: set[str]) -> dict[str, dict]:
    """去写死：把步骤 body 里 *_id / *_ids 字段的真实 UUID 字面量**回写成 `${VAR}` 引用**，
    并返回 {变量名: {value, kind, desc}}（literal，存真实值）。

    这样脚本里不出现写死的资源 ID：字面量换成变量引用，真实值沉淀为 literal 场景变量，
    运行时由场景变量解析回真实值发送。就地修改 scenarios 里各 step 的 body。
      - `xxx_id`: 单个 UUID → 变量名 `XXX_ID`；
      - `xxx_ids`: 仅当数组里**恰好一个** UUID 时才回写（多个不合并，避免语义错乱）。
    """
    reserved_u = {n.upper() for n in reserved}
    found: dict[str, dict] = {}

    def rewrite(node):
        if isinstance(node, dict):
            for k, val in list(node.items()):
                kl = str(k).lower()
                if kl.endswith("_id") and _is_uuid(val):
                    name = k.upper()
                    if name not in reserved_u:
                        found.setdefault(name, {
                            "value": val.strip(), "kind": "literal",
                            "desc": f"自动提取自编排流量：字段 {k}（已回写为变量引用，去写死）",
                        })
                        node[k] = f"${{{name}}}"
                elif kl.endswith("_ids") and isinstance(val, list):
                    uuids = [x for x in val if _is_uuid(x)]
                    name = k[:-1].upper()  # *_ids -> *_ID
                    if len(uuids) == 1 and name not in reserved_u:
                        found.setdefault(name, {
                            "value": uuids[0].strip(), "kind": "literal",
                            "desc": f"自动提取自编排流量：字段 {k}（已回写为变量引用，去写死）",
                        })
                        node[k] = [f"${{{name}}}" if _is_uuid(x) else x for x in val]
                    else:
                        for x in val:
                            rewrite(x)
                else:
                    rewrite(val)
        elif isinstance(node, list):
            for x in node:
                rewrite(x)

    for sc in scenarios:
        for step in sc.get("steps", []):
            body = step.get("body")
            is_str = isinstance(body, str)
            parsed_body = body
            if is_str:
                try:
                    parsed_body = json.loads(body)
                except Exception:
                    continue
            if not isinstance(parsed_body, (dict, list)):
                continue
            rewrite(parsed_body)
            step["body"] = json.dumps(parsed_body, ensure_ascii=False) if is_str else parsed_body
    return found


def _parse_scenarios(content: str) -> list[dict]:
    """解析 AI 返回的 JSON，提取 scenarios 数组。"""
    # 尝试从 ```json 块提取
    json_match = re.search(r"```json\s*\n(.*?)\n```", content, re.DOTALL)
    text = json_match.group(1) if json_match else content

    # 尝试找 { 开头的 JSON
    brace = text.find("{")
    if brace < 0:
        logger.warning("No '{' found in AI output, len=%d", len(content))
        return []
    text = text[brace:]

    try:
        data = json.loads(text)
        return data.get("scenarios", [data] if "title" in data else [])
    except json.JSONDecodeError as e:
        logger.warning("JSON parse failed: %s, trying truncation fix", e)
        # 截断修复：逐步缩短找到可解析的 JSON
        for end_pos in range(len(text), max(len(text) - 500, 0), -1):
            if text[end_pos - 1] not in '}]':
                continue
            for suffix in ['', ']', ']}', ']}]', ']}]}']:
                try:
                    data = json.loads(text[:end_pos] + suffix)
                    scenarios = data.get("scenarios", [data] if "title" in data else [])
                    if scenarios:
                        logger.info("Truncation fix succeeded with suffix='%s'", suffix)
                        return scenarios
                except json.JSONDecodeError:
                    continue

    logger.warning("All parse attempts failed, content[:200]=%s", content[:200])
    return []


def _normalize_assertions(assertions: list[dict]) -> list[dict]:
    """标准化 AI 生成的断言格式。
    AI 有时把字段路径放在 value 而不是 field 里，这里统一修正。
    """
    result = []
    for a in assertions:
        a = dict(a)
        if a.get("type") == "body_field":
            if "field" not in a and "value" in a:
                if a.get("expected") is not None:
                    a["field"] = a.pop("value")
                elif a.get("operator") == "not_empty":
                    a["field"] = a.pop("value")
        result.append(a)
    return result


# URL 路径 → 模块名映射
_URL_MODULE_MAP = {
    "user": "用户管理",
    "auth": "认证",
    "project": "项目管理",
    "plan": "测试计划",
    "report": "测试报告",
    "case": "用例管理",
    "env": "环境管理",
    "config": "配置管理",
}


def _guess_module_name(title: str, api_info: str) -> str | None:
    """根据场景标题和接口信息推断模块名。"""
    urls = re.findall(r'/api/(\w+)', api_info)
    if urls:
        segment = urls[0].lower().rstrip('s')
        for key, name in _URL_MODULE_MAP.items():
            if key in segment:
                return name
        return urls[0].replace('_', ' ').title()

    for key, name in _URL_MODULE_MAP.items():
        if key in title.lower():
            return name

    parts = title.split('-')
    if len(parts) >= 2:
        return parts[0]

    return None
