"""MCP 工具 — 测试用例 + 文件夹"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import case_service
from app.services.folder_service import list_folder_tree


def _case_to_dict(c) -> dict:
    return {
        "id": str(c.id),
        "caseCode": c.case_code,
        "title": c.title,
        "type": c.type,
        "priority": c.priority,
        "folderId": str(c.folder_id) if c.folder_id else None,
        "preconditions": c.preconditions,
        "steps": c.steps,
        "expectedResult": c.expected_result,
        "automationStatus": c.automation_status,
        "source": c.source,
    }


async def list_cases(
    session: AsyncSession,
    branch_id: str,
    page: int = 1,
    page_size: int = 50,
    keyword: str | None = None,
    folder_id: str | None = None,
    priority: str | None = None,
    case_type: str | None = None,
) -> dict:
    """列出分支下的测试用例，支持分页和筛选。"""
    cases, total = await case_service.list_cases(
        session,
        uuid.UUID(branch_id),
        page=page,
        page_size=min(page_size, 100),
        keyword=keyword,
        folder_id=uuid.UUID(folder_id) if folder_id else None,
        priority=priority,
        case_type=case_type,
    )
    return {
        "cases": [_case_to_dict(c) for c in cases],
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


async def get_case(session: AsyncSession, case_id: str) -> dict | None:
    """获取单条用例详情。"""
    case = await case_service.get_case(session, uuid.UUID(case_id))
    if not case:
        return None
    return _case_to_dict(case)


async def create_case(
    session: AsyncSession,
    branch_id: str,
    title: str,
    module: str,
    case_type: str = "e2e",
    submodule: str | None = None,
    priority: str = "P2",
    preconditions: str | None = None,
    steps: list | None = None,
    expected_result: str | None = None,
) -> dict:
    """创建一条测试用例。自动生成 case_code 和目录。创建前会做质量校验，不合格时返回 warnings。"""

    warnings = _validate_case_quality(title, module, priority, preconditions, steps, expected_result)

    if steps:
        # 自动拆分粒度过粗的步骤（"一步一动作"规范）
        steps = _split_coarse_steps(steps)
        for i, s in enumerate(steps):
            if not s.get("seq"):
                s["seq"] = i + 1

    if preconditions:
        import re
        preconditions = preconditions.replace("\\n", "\n")
        preconditions = re.sub(r'；\s*(\d+)\.\s*', r'\n\1. ', preconditions)
        preconditions = re.sub(r';\s*(\d+)\.\s*', r'\n\1. ', preconditions)

    from app.schemas.case import CreateCaseRequest
    data = CreateCaseRequest(
        title=title,
        type=case_type,
        module=module,
        submodule=submodule,
        priority=priority,
        preconditions=preconditions,
        steps=steps or [],
        expected_result=expected_result,
    )
    case = await case_service.create_case(session, uuid.UUID(branch_id), data, source="ai")
    result = _case_to_dict(case)
    if warnings:
        result["_qualityWarnings"] = warnings
    return result


_FUZZY_WORDS = ["操作成功", "显示正常", "无报错", "符合预期", "正确显示", "成功返回", "正常运行", "有效数据", "合法数据"]
_API_PATTERNS = ["POST /", "GET /", "PUT /", "DELETE /", "PATCH /", "返回 2", "返回 4", "返回 5", "HTTP ", "curl "]


def _validate_case_quality(title, module, priority, preconditions, steps, expected_result) -> list[str]:
    warnings = []

    if not module or module.strip() == "-":
        warnings.append("module 为空：用例必须归属到具体模块（如'服务管理/创建服务'）")

    if not preconditions or len(preconditions.strip()) < 5:
        warnings.append("preconditions 为空或过短：必须声明前置条件（登录状态、测试数据等）")

    if "/" in title and "—" not in title:
        warnings.append(f"标题含 '/' 可能混合了多个场景：'{title}'。建议拆分为独立用例")

    if steps:
        for i, s in enumerate(steps):
            action = s.get("action", "")
            expected = s.get("expected", "")

            for pat in _API_PATTERNS:
                if pat in action:
                    warnings.append(f"步骤 {i+1} 使用了接口调用风格（'{pat}...'），应改为页面操作描述")
                    break

            for fw in _FUZZY_WORDS:
                if fw in expected:
                    warnings.append(f"步骤 {i+1} 预期含模糊词'{fw}'，应改为具体可验证描述")
                    break

    if expected_result:
        for fw in _FUZZY_WORDS:
            if fw in expected_result:
                warnings.append(f"expected_result 含模糊词'{fw}'")

    return warnings


# 动作连接词——表示一个步骤包含多个独立动作
_SPLIT_PATTERNS = ["，点击", "，配置", "，填写", "，输入", "，确认", "，勾选",
                   ",点击", ",配置", ",填写", ",输入", ",确认", ",勾选"]


def _split_coarse_steps(steps: list[dict]) -> list[dict]:
    """自动拆分粒度过粗的步骤——一个 action 含多个独立动作时拆成多步"""
    import re
    result = []
    for s in steps:
        action = s.get("action", "")
        expected = s.get("expected", "")

        # 检测是否有多个动作
        # 模式：中文逗号/英文逗号 + 动作动词（点击/填写/选择/配置/输入/确认）
        split_points = []
        for pat in _SPLIT_PATTERNS:
            idx = action.find(pat)
            while idx > 0:
                split_points.append(idx)
                idx = action.find(pat, idx + 1)

        if not split_points:
            result.append(s)
            continue

        # 去重排序
        split_points = sorted(set(split_points))

        # 拆分
        parts = []
        prev = 0
        for sp in split_points:
            part = action[prev:sp].strip().rstrip("，,")
            if part:
                parts.append(part)
            prev = sp + 1  # 跳过逗号
        last = action[prev:].strip()
        if last:
            parts.append(last)

        if len(parts) <= 1:
            result.append(s)
            continue

        # 生成拆分后的步骤
        for j, part in enumerate(parts):
            new_step = {
                "seq": len(result) + 1,
                "action": part,
                "expected": expected if j == len(parts) - 1 else f"操作完成，页面状态更新",
            }
            result.append(new_step)

    # 重新编号
    for i, s in enumerate(result):
        s["seq"] = i + 1

    return result


async def get_folder_tree(session: AsyncSession, branch_id: str) -> list[dict]:
    """获取用例文件夹树形结构，含每层用例数量。"""
    return await list_folder_tree(session, uuid.UUID(branch_id))
