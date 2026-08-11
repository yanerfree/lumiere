"""Skill 执行器 — 解析 SKILL.md → 收集上下文 → LLM 生成 → 工具调用 → SSE 推送"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp.tools import test_cases, api_endpoints
from app.mcp.tools import test_reports as report_tools
from app.services.ai import llm_client
from app.services.ai_config_resolver import ResolvedAIConfig

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills" / "preset"


@dataclass
class SkillMeta:
    name: str
    description: str
    version: int = 1
    tools: list[str] = field(default_factory=list)


@dataclass
class SkillStep:
    number: int
    title: str
    content: str


@dataclass
class SSEEvent:
    type: str  # step_start, step_progress, step_done, case_generated, error, done
    data: dict


def load_skill(name: str) -> tuple[SkillMeta, list[SkillStep]]:
    skill_path = SKILLS_DIR / name / "SKILL.md"
    if not skill_path.exists():
        raise FileNotFoundError(f"Skill '{name}' not found at {skill_path}")

    text = skill_path.read_text(encoding="utf-8")

    fm_match = re.match(r"^---\s*\n(.+?)\n---\s*\n", text, re.DOTALL)
    if not fm_match:
        raise ValueError(f"Skill '{name}' missing YAML frontmatter")

    meta_dict = yaml.safe_load(fm_match.group(1))
    meta = SkillMeta(
        name=meta_dict["name"],
        description=meta_dict.get("description", ""),
        version=meta_dict.get("version", 1),
        tools=meta_dict.get("tools", []),
    )

    body = text[fm_match.end():]
    steps = []
    step_pattern = re.compile(r"^## Step (\d+)\s*[—–-]\s*(.+)$", re.MULTILINE)
    matches = list(step_pattern.finditer(body))

    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content = body[start:end].strip()
        content_end = re.search(r"^## 质量红线", content, re.MULTILINE)
        if content_end:
            content = content[:content_end.start()].strip()
        steps.append(SkillStep(number=int(m.group(1)), title=m.group(2).strip(), content=content))

    return meta, steps


async def execute_case_generate(
    *,
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    interface_info: str,
    business_rules: list[str],
    module: str,
    submodule: str | None,
    ai_config: ResolvedAIConfig,
    session: AsyncSession,
) -> AsyncIterator[SSEEvent]:
    """执行 tb-case-generate Skill，逐步 yield SSE 事件。"""

    meta, steps = load_skill("tb-case-generate")
    yield SSEEvent(type="skill_start", data={
        "skill": meta.name,
        "description": meta.description,
        "totalSteps": len(steps),
    })

    # ── Step 1: 上下文收集 ──
    yield SSEEvent(type="step_start", data={"step": 1, "title": "上下文收集"})

    api_tree = await api_endpoints.list_api_tree(session, str(project_id))
    yield SSEEvent(type="step_progress", data={"step": 1, "message": f"加载了 {len(api_tree)} 个 API 节点"})

    existing_cases_result = await test_cases.list_cases(session, str(branch_id), page_size=100)
    existing_cases = existing_cases_result["cases"]
    existing_titles = {c["title"] for c in existing_cases}
    yield SSEEvent(type="step_progress", data={"step": 1, "message": f"发现 {len(existing_cases)} 条已有用例"})

    folder_tree = await test_cases.get_folder_tree(session, str(branch_id))

    # 读取项目知识库
    from sqlalchemy import select as sa_select
    from app.models.knowledge import KnowledgeEntry
    knowledge_result = await session.execute(
        sa_select(KnowledgeEntry)
        .where(KnowledgeEntry.project_id == project_id)
        .order_by(KnowledgeEntry.created_at.desc())
        .limit(10)
    )
    knowledge_entries = knowledge_result.scalars().all()
    knowledge_text = ""
    if knowledge_entries:
        knowledge_text = "\n".join(f"- [{e.category}] {e.title}: {e.content[:100]}" for e in knowledge_entries)
        yield SSEEvent(type="step_progress", data={"step": 1, "message": f"加载了 {len(knowledge_entries)} 条知识库条目"})

    yield SSEEvent(type="step_done", data={"step": 1, "summary": f"API {len(api_tree)} 个, 已有用例 {len(existing_cases)} 条, 知识 {len(knowledge_entries)} 条"})

    # ── Step 2-4: LLM 生成 ──
    yield SSEEvent(type="step_start", data={"step": 2, "title": "维度规划 + 用例生成"})

    api_context = _build_api_context(api_tree, interface_info)
    existing_context = _build_existing_context(existing_cases)

    system_prompt = _build_system_prompt(meta, steps)
    user_prompt = _build_user_prompt(
        interface_info=interface_info,
        business_rules=business_rules,
        module=module,
        submodule=submodule,
        api_context=api_context,
        existing_context=existing_context,
    )

    if knowledge_text:
        user_prompt += f"\n\n## 项目知识库（AI 评审/诊断的历史经验）\n{knowledge_text}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    full_content = ""
    try:
        async for chunk in llm_client.stream(messages, config=ai_config):
            if chunk.delta:
                full_content += chunk.delta
                yield SSEEvent(type="step_progress", data={"step": 2, "chunk": chunk.delta})
    except Exception as e:
        logger.error("Skill LLM call failed: %s", e)
        yield SSEEvent(type="error", data={"message": f"AI 生成失败: {str(e)[:200]}"})
        return

    yield SSEEvent(type="step_done", data={"step": 2, "summary": "LLM 生成完成"})

    # ── Step 5: 解析 + 入库 ──
    yield SSEEvent(type="step_start", data={"step": 3, "title": "解析结果 + 入库"})

    cases = _parse_cases(full_content)
    if not cases:
        yield SSEEvent(type="error", data={"message": "无法解析 AI 返回的用例数据"})
        return

    imported = 0
    skipped = 0
    for c in cases:
        title = c.get("title", "").strip()
        if not title or title in existing_titles:
            skipped += 1
            continue

        try:
            preconditions = c.get("preconditions")
            if isinstance(preconditions, list):
                preconditions = "\n".join(f"- {p}" for p in preconditions)

            result = await test_cases.create_case(
                session,
                str(branch_id),
                title=title,
                module=c.get("module", module),
                case_type=c.get("type", "api"),
                submodule=c.get("submodule", submodule),
                priority=c.get("priority", "P2"),
                preconditions=preconditions,
                steps=c.get("steps", []),
                expected_result=c.get("expected_result"),
            )
            imported += 1
            existing_titles.add(title)
            yield SSEEvent(type="case_generated", data={
                "caseCode": result.get("caseCode", ""),
                "title": title,
                "priority": c.get("priority", "P2"),
                "index": imported,
            })
        except Exception as e:
            logger.warning("Failed to create case '%s': %s", title, e)
            skipped += 1

    # 写入用量记录
    from app.models.case_file import AIUsageLog
    session.add(AIUsageLog(
        project_id=project_id,
        skill_name="tb-case-generate",
        model=ai_config.model,
        total_tokens=len(full_content) * 2,
    ))

    await session.commit()

    yield SSEEvent(type="step_done", data={
        "step": 3,
        "summary": f"入库 {imported} 条，跳过 {skipped} 条",
    })

    priority_counts = {}
    for c in cases:
        p = c.get("priority", "P2")
        priority_counts[p] = priority_counts.get(p, 0) + 1

    yield SSEEvent(type="done", data={
        "imported": imported,
        "skipped": skipped,
        "total": len(cases),
        "priorities": priority_counts,
    })


def _build_api_context(api_tree: list[dict], interface_info: str) -> str:
    if not api_tree:
        return "（项目未录入 API 接口）"
    endpoints = [n for n in api_tree if n.get("type") == "endpoint"]
    if not endpoints:
        return "（项目未录入 API 端点）"
    lines = []
    for ep in endpoints[:20]:
        method = ep.get("method", "GET")
        url = ep.get("url", "")
        name = ep.get("name", "")
        lines.append(f"- {method} {url}  ({name})")
    return "\n".join(lines)


def _build_existing_context(cases: list[dict]) -> str:
    if not cases:
        return "（暂无已有用例）"
    lines = [f"- [{c['priority']}] {c['title']}" for c in cases[:30]]
    return "\n".join(lines)


def _build_system_prompt(meta: SkillMeta, steps: list[SkillStep]) -> str:
    quality_rules = ""
    skill_path = SKILLS_DIR / meta.name / "SKILL.md"
    if skill_path.exists():
        text = skill_path.read_text(encoding="utf-8")
        m = re.search(r"## 质量红线\s*\n(.+)", text, re.DOTALL)
        if m:
            quality_rules = m.group(1).strip()

    return f"""你是一位资深 QA 测试工程师，正在执行 Skill "{meta.name}"。

## 任务
{meta.description}

## 输出要求
- 严格输出 JSON 数组，每个元素是一个测试用例对象
- 不要输出 JSON 以外的文本
- 用 ```json 包裹
- 每个用例包含：title, type("api"), priority("P0"-"P3"), preconditions, steps([{{action,expected}}]), expected_result, module, submodule, tags([])

## 质量红线
{quality_rules}"""


def _build_user_prompt(
    *,
    interface_info: str,
    business_rules: list[str],
    module: str,
    submodule: str | None,
    api_context: str,
    existing_context: str,
) -> str:
    rules_text = "\n".join(f"- {r}" for r in business_rules) if business_rules else "（无特殊规则）"
    sub = f"/{submodule}" if submodule else ""
    return f"""## 目标模块
{module}{sub}

## 接口信息
{interface_info}

## 业务规则
{rules_text}

## 项目已有 API 端点（参考上下文）
{api_context}

## 同模块已有用例（避免重复）
{existing_context}

请按照 Skill 定义的维度（正向/参数验证/业务规则/边界值/异常/安全）生成测试用例。
每个维度 2-4 条，P0 不超过 15%，和已有用例去重。输出 JSON 数组。"""


def _parse_cases(content: str) -> list[dict]:
    content = content.strip()
    json_match = re.search(r"```json\s*\n(.*?)(?:\n```|$)", content, re.DOTALL)
    if json_match:
        content = json_match.group(1).strip()
    else:
        bracket_match = re.search(r"\[.*", content, re.DOTALL)
        if bracket_match:
            content = bracket_match.group(0)

    # 尝试直接解析
    try:
        data = json.loads(content)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # JSON 被截断：尝试修复（找最后一个完整的 } 然后关闭数组）
    last_brace = content.rfind("}")
    if last_brace > 0:
        truncated = content[:last_brace + 1].rstrip().rstrip(",") + "\n]"
        try:
            data = json.loads(truncated)
            if isinstance(data, list):
                logger.info("Recovered %d cases from truncated JSON", len(data))
                return data
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse AI output as JSON, length=%d", len(content))
    return []


# ── 质量评审 Skill ──────────────────────────────────

async def execute_quality_review(
    *,
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    folder_id: str | None,
    module: str | None,
    ai_config: ResolvedAIConfig,
    session: AsyncSession,
) -> AsyncIterator[SSEEvent]:
    """执行 tb-quality-review Skill。"""

    yield SSEEvent(type="skill_start", data={"skill": "tb-quality-review", "totalSteps": 3})

    # Step 1: 收集用例
    yield SSEEvent(type="step_start", data={"step": 1, "title": "收集用例和接口"})

    # list_cases 内部把 page_size 压到 100（min(page_size, 100)），传 200 也只回 100。
    # 报告顶上却按拿到的条数报「共 N 条」—— 105 条的分支上会显示 100，
    # 那 5 条既没参与评审也没人知道被漏了。分页取完。
    cases = []
    page = 1
    while True:
        chunk = (await test_cases.list_cases(
            session, str(branch_id), page=page, page_size=100, folder_id=folder_id,
        ))["cases"]
        cases.extend(chunk)
        if len(chunk) < 100 or len(cases) >= 2000:
            break
        page += 1
    if not cases:
        yield SSEEvent(type="error", data={"message": "该模块下没有用例，无法评审"})
        return

    api_tree = await api_endpoints.list_api_tree(session, str(project_id))
    endpoints = [n for n in api_tree if n.get("type") == "endpoint"]

    priority_dist = {}
    for c in cases:
        p = c.get("priority", "P2")
        priority_dist[p] = priority_dist.get(p, 0) + 1

    yield SSEEvent(type="step_done", data={
        "step": 1,
        "summary": f"用例 {len(cases)} 条, API 端点 {len(endpoints)} 个, 优先级分布: {priority_dist}",
    })

    # Step 2: LLM 评审
    yield SSEEvent(type="step_start", data={"step": 2, "title": "AI 四维度评审"})

    # 统计由**平台**算，不许 LLM 自己数。
    #
    # 实测它编得有鼻子有眼：报告说「105 条里 50 条没有前置条件、50 条没有预期结果、
    # P0 只有 3 条」，库里真实是 6 / 5 / 11。原因很直白 —— 送进去的只有
    # 「[优先级] 标题 (N步)」，它**从没看见过** preconditions 和 expected_result 这两个字段，
    # 于是按"看起来合理"编了一组整数。人照这个报告去改用例会被带偏，
    # 比不给报告更糟。
    SAMPLE = 50
    facts = {
        "totalCases": len(cases),
        "sampledCases": min(SAMPLE, len(cases)),
        "priorityDist": priority_dist,
        "casesWithoutPrecondition": sum(
            1 for c in cases if not (c.get("preconditions") or "").strip()),
        "casesWithoutExpectedResult": sum(
            1 for c in cases if not (c.get("expectedResult") or c.get("expected_result") or "").strip()),
        "avgStepsPerCase": round(
            sum(len(c.get("steps") or []) for c in cases) / max(len(cases), 1), 1),
        "apisTotal": len(endpoints),
    }

    cases_text = "\n".join(
        # 把它要判断的东西真的给它看：没有前置条件/预期结果的，明写出来。
        f"- [{c['priority']}] {c['title']}（{len(c.get('steps', []))} 步"
        f"{'，无前置条件' if not (c.get('preconditions') or '').strip() else ''}"
        f"{'，无预期结果' if not (c.get('expectedResult') or c.get('expected_result') or '').strip() else ''}）"
        for c in cases[:SAMPLE]
    )
    api_text = "\n".join(f"- {ep.get('method','GET')} {ep.get('url','')} ({ep.get('name','')})" for ep in endpoints[:20])

    messages = [
        {"role": "system", "content": """你是一位资深 QA 质量评审专家。请对以下测试用例进行四维度评审。

输出严格 JSON 格式（用 ```json 包裹）：
{
  "score": 85,
  "dimensions": {
    "completeness": {"score": 80, "weight": 30, "issues": ["缺少安全测试场景"]},
    "accuracy": {"score": 90, "weight": 25, "issues": []},
    "effectiveness": {"score": 85, "weight": 25, "issues": ["存在2条重复用例"]},
    "executability": {"score": 88, "weight": 20, "issues": ["部分前置条件描述不够具体"]}
  },
  "issues": [
    {"dimension": "completeness", "severity": "high", "case": "用例标题", "description": "具体问题"},
  ],
  "suggestions": ["建议1", "建议2"],
  "coverage": {"apisCovered": 3, "missingApis": ["GET /api/xxx"]}
}

评分标准：90-100 优秀 / 75-89 良好 / 60-74 一般 / <60 不合格

**不要输出任何统计数字**（总数、各优先级条数、缺前置条件的条数等）——
那些由平台查库给出，见下方「事实」。你自己数会数错，而人会当真。
missingApis 只能从下方真实给出的 API 列表里挑；列表为空就返回空数组，不要凭印象编。"""},
        {"role": "user", "content": f"""## 事实（平台查库得出，以此为准，不要另行估算）
{json.dumps(facts, ensure_ascii=False, indent=2)}

## 待评审用例（共 {facts["totalCases"]} 条，下面列出前 {facts["sampledCases"]} 条）
{cases_text}

## 项目 API 端点（{len(endpoints)} 个）
{api_text}

请从完整性(30%)、准确性(25%)、有效性(25%)、可执行性(20%)四个维度评审，输出 JSON。"""},
    ]

    full_content = ""
    try:
        async for chunk in llm_client.stream(messages, config=ai_config):
            if chunk.delta:
                full_content += chunk.delta
                yield SSEEvent(type="step_progress", data={"step": 2, "chunk": chunk.delta})
    except Exception as e:
        logger.error("Quality review LLM call failed: %s", e)
        yield SSEEvent(type="error", data={"message": f"AI 评审失败: {str(e)[:200]}"})
        return

    yield SSEEvent(type="step_done", data={"step": 2, "summary": "AI 评审完成"})

    # Step 3: 解析报告
    yield SSEEvent(type="step_start", data={"step": 3, "title": "解析评审报告"})

    report = _parse_json_object(full_content)
    if not report:
        yield SSEEvent(type="error", data={"message": "无法解析 AI 评审结果"})
        return

    # LLM 还是可能自作主张塞 statistics —— 一律用平台算的覆盖掉，
    # 它编的数字一个都不许出现在界面上。
    report["statistics"] = facts
    cov = report.get("coverage")
    if isinstance(cov, dict):
        cov["apisTotal"] = facts["apisTotal"]
        if not facts["apisTotal"]:
            # 一个端点都没有的时候还列出「缺失的 API」，那是凭空想的。
            cov["missingApis"] = []
            cov["note"] = "项目还没有登记 API 端点，无法做覆盖率分析"
    if facts["sampledCases"] < facts["totalCases"]:
        report.setdefault("suggestions", []).insert(
            0, f"本次只抽样评审了前 {facts['sampledCases']} 条（共 {facts['totalCases']} 条），"
               "结论仅代表抽样部分")

    score = report.get("score", 0)
    level = "优秀" if score >= 90 else "良好" if score >= 75 else "一般" if score >= 60 else "不合格"

    yield SSEEvent(type="step_done", data={"step": 3, "summary": f"评审完成: {score} 分 ({level})"})

    from app.models.case_file import AIUsageLog
    session.add(AIUsageLog(project_id=project_id, skill_name="tb-quality-review", model=ai_config.model, total_tokens=len(full_content) * 2))

    from app.api.knowledge import add_knowledge_from_review
    await add_knowledge_from_review(session, project_id, report)

    await session.commit()

    yield SSEEvent(type="done", data={
        "report": report,
        "score": score,
        "level": level,
        "caseCount": facts["totalCases"],
        "sampledCount": facts["sampledCases"],
        "apiCount": facts["apisTotal"],
    })


def _parse_json_object(content: str) -> dict | None:
    content = content.strip()
    json_match = re.search(r"```json\s*\n(.*?)(?:\n```|$)", content, re.DOTALL)
    if json_match:
        content = json_match.group(1).strip()
    else:
        brace_match = re.search(r"\{.*", content, re.DOTALL)
        if brace_match:
            content = brace_match.group(0)

    try:
        data = json.loads(content)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        last_brace = content.rfind("}")
        if last_brace > 0:
            try:
                return json.loads(content[:last_brace + 1])
            except json.JSONDecodeError:
                pass

    logger.warning("Failed to parse review JSON, length=%d", len(content))
    return None


# ── 失败诊断 Skill ──────────────────────────────────

async def execute_diagnose(
    *,
    project_id: uuid.UUID,
    branch_id: uuid.UUID,
    plan_id: str,
    report_id: str | None,
    case_ids: list[str] | None,
    ai_config: ResolvedAIConfig,
    session: AsyncSession,
) -> AsyncIterator[SSEEvent]:
    """执行 tb-diagnose Skill — 分析失败用例的根因。"""

    yield SSEEvent(type="skill_start", data={"skill": "tb-diagnose", "totalSteps": 3})

    # Step 1: 收集失败信息
    yield SSEEvent(type="step_start", data={"step": 1, "title": "收集失败信息"})

    failed = await report_tools.get_failed_scenarios(session, plan_id, report_id)
    if case_ids:
        failed = [f for f in failed if f["caseId"] in case_ids]

    if not failed:
        yield SSEEvent(type="error", data={"message": "未找到失败的用例"})
        return

    yield SSEEvent(type="step_done", data={"step": 1, "summary": f"找到 {len(failed)} 条失败用例"})

    # Step 2: LLM 诊断
    yield SSEEvent(type="step_start", data={"step": 2, "title": "AI 分析失败原因"})

    failures_text = ""
    for i, f in enumerate(failed[:10]):
        failures_text += f"\n### 失败 #{i+1}: {f['caseTitle']}\n"
        failures_text += f"状态: {f['status']}\n"
        if f.get("remark"):
            failures_text += f"错误信息: {f['remark']}\n"
        if f.get("preconditions"):
            failures_text += f"前置条件: {f['preconditions']}\n"
        if f.get("expectedResult"):
            failures_text += f"预期结果: {f['expectedResult']}\n"
        if f.get("steps"):
            failures_text += f"步骤: {json.dumps(f['steps'], ensure_ascii=False)}\n"
        if f.get("scriptFile"):
            failures_text += f"脚本: {f['scriptFile']}\n"

    messages = [
        {"role": "system", "content": """你是一位资深测试诊断专家。分析测试失败原因，进行三分类仲裁。

对每个失败用例判断根因：
- script_bug: 脚本自身 Bug（定位器/断言/数据）→ 给出修复代码
- system_bug: 被测系统的真实 Bug → 给出 Bug 报告
- env_issue: 环境/配置问题 → 给出检查清单

输出严格 JSON（用 ```json 包裹）：
{
  "diagnoses": [
    {
      "caseTitle": "标题",
      "verdict": "script_bug|system_bug|env_issue",
      "confidence": 0.85,
      "summary": "一句话",
      "evidence": ["证据"],
      "fixSuggestion": "修复建议"
    }
  ],
  "summary": {"total": N, "scriptBug": N, "systemBug": N, "envIssue": N}
}"""},
        {"role": "user", "content": f"以下是 {len(failed)} 条失败的测试用例，请逐一诊断：\n{failures_text}"},
    ]

    full_content = ""
    try:
        async for chunk in llm_client.stream(messages, config=ai_config):
            if chunk.delta:
                full_content += chunk.delta
                yield SSEEvent(type="step_progress", data={"step": 2, "chunk": chunk.delta})
    except Exception as e:
        yield SSEEvent(type="error", data={"message": f"AI 诊断失败: {str(e)[:200]}"})
        return

    yield SSEEvent(type="step_done", data={"step": 2, "summary": "AI 分析完成"})

    # Step 3: 解析报告
    yield SSEEvent(type="step_start", data={"step": 3, "title": "解析诊断报告"})

    report = _parse_json_object(full_content)
    if not report:
        yield SSEEvent(type="error", data={"message": "无法解析 AI 诊断结果"})
        return

    summary = report.get("summary", {})
    yield SSEEvent(type="step_done", data={
        "step": 3,
        "summary": f"脚本Bug {summary.get('scriptBug',0)} · 系统Bug {summary.get('systemBug',0)} · 环境问题 {summary.get('envIssue',0)}",
    })

    yield SSEEvent(type="done", data={
        "report": report,
        "failedCount": len(failed),
    })
