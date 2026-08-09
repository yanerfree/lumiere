"""用例展开主循环（ft S4.1 / FR13-FR17 / ADR-1/5）

按 item（一测试点一行）逐条展开 → 静态校验 → 落 Case → 档案事件 → SSE。
单条失败不阻塞后续。
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case
from app.models.scenario_gen import (
    CaseGenEvent,
    GenerationItem,
    GenerationTask,
    ScenarioModel,
)
from app.services.scenario_gen import pipeline
from app.services.scenario_gen.llm_structured import StructuredOutputError, llm_structured
from app.services.scenario_gen.settings import get_settings
from app.services.scenario_gen.static_validator import validate_cases


class ExpandedCase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    priority: str = Field(default="P1", pattern=r"^P[0-3]$")
    preconditions: str = ""
    steps: list[dict] = Field(min_length=1)
    expected_result: str = Field(min_length=1)
    test_method: str = ""
    test_data: str = ""


class ExpandResult(BaseModel):
    case: ExpandedCase


EXPAND_SYSTEM_PROMPT = """你是一位资深测试用例设计师。根据提供的测试点信息，生成一条完整的功能测试场景用例。

用例必须包含：
- title: 用例标题（中文，明确描述测试目的）
- priority: **原样沿用输入里给定的优先级**，不要自己重新判断（那是人已经确认过的）
- preconditions: 前置条件（具体的数据准备和环境要求）
- steps: 步骤数组 [{action: "操作描述", expected: "单步预期结果"}]，步骤 ≤8 个
- expected_result: 整体预期结果（含可验证关键词，不用"操作成功/显示正常"等模糊词）
- test_method: 测试设计方法（场景法/等价类/边界值/状态迁移/判定表/错误推测）
- test_data: 具体测试数据（用 ${变量} 引用环境变量，生成规则+本次示例值）

规则：
- 步骤中的操作必须含元素锚点（可见文字/占位符/标签）
- 预期结果必须含可验证关键词（Toast内容为/返回值包含/页面跳转到/字段显示为）
- 一步一动作，不合并多个操作到一步
- 事实性内容（接口路径/错误文案/字段名）只能来自输入材料，缺失的标注"待确认"

输出严格 JSON：{"case": {...}}"""


async def expand_single_test_point(
    config,
    task: GenerationTask,
    item: GenerationItem,
    point_snapshot: dict,
    session: AsyncSession,
    context_text: str = "",
) -> Case | None:
    """展开单个测试点为用例。成功返回 Case，失败返回 None（item 已标 failed）。"""
    user_content = f"测试点：{point_snapshot.get('title', '')}\n维度：{point_snapshot.get('dimension', '')}\n优先级：{point_snapshot.get('priority', 'P1')}\n需求点：{point_snapshot.get('requirement_point_code', '')}"
    if context_text:
        user_content = f"{context_text}\n\n{user_content}"

    messages = [
        {"role": "system", "content": EXPAND_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        result = await llm_structured(
            config, messages, ExpandResult,
            session=session, project_id=task.project_id, skill_name="scenario-expand",
            max_tokens=4096,
        )
    except (StructuredOutputError, Exception) as e:
        item.status = "failed"
        item.error_step = "expand"
        item.error_message = str(e)[:500]
        await session.flush()
        return None

    case_data = result.case
    cases_validated, warnings = validate_cases([case_data.model_dump()])
    case_dict = cases_validated[0]

    # 优先级以**人在「确认场景模型」那一步确认过的**为准，不看模型这次怎么判。
    # 实测：模型里 5 个 P0 展开完 P0 归零、全成 P1 —— 人确认的东西在这一步被
    # 悄悄改掉了，那一步的确认就等于白做。优先级是已确认的事实，不是待推理的问题。
    confirmed_priority = point_snapshot.get("priority")
    if confirmed_priority in ("P0", "P1", "P2", "P3"):
        case_dict["priority"] = confirmed_priority

    # 取号 + 归类文件夹
    module = task.settings.get("module", "GEN") if task.settings else "GEN"
    from app.services.scenario_gen.context_builder import get_or_create_folder
    folder_id = await get_or_create_folder(session, task.branch_id, module)

    # 取号走和别处同一份实现（MAX+1 + advisory lock），别在这儿自己算。
    # 这里原来用 count(*) 当"最大序号"——删过任何一条用例，count 就小于真实 MAX，
    # 下一条直接撞 uq_case_branch_code。实测这条分支的 TC-API- 缺 00015/00017 两号，
    # 再往 API 模块生成就会挂。而且中文模块名在这儿会被 upper() 原样带进编号，
    # 和 _next_case_code 的拼音首字母规则也对不上，同一个模块能出两种前缀。
    from app.services.import_service import _next_case_code
    case_code = await _next_case_code(session, task.branch_id, module)

    rp_code = point_snapshot.get("requirement_point_code", "")

    case = Case(
        branch_id=task.branch_id,
        case_code=case_code,
        title=case_dict["title"][:200],
        type="api",
        folder_id=folder_id,
        priority=case_dict["priority"],
        preconditions=case_dict.get("preconditions", ""),
        steps=case_dict["steps"],
        expected_result=case_dict["expected_result"],
        source="ai",
        review_status="pending_review",
        generation_task_id=task.id,
        requirement_point_ids=[rp_code] if rp_code else [],
        quality_score={"warnings": warnings} if warnings else None,
        version=1,
    )
    session.add(case)
    await session.flush()

    # 生成档案事件
    session.add(CaseGenEvent(
        case_id=case.id,
        event_type="generated",
        payload={
            "task_id": str(task.id),
            "test_point_ref": item.test_point_ref,
            "dimension": point_snapshot.get("dimension"),
            "test_method": case_dict.get("test_method", ""),
        },
        actor="ai",
    ))

    item.status = "succeeded"
    item.case_id = case.id
    await session.flush()
    return case
