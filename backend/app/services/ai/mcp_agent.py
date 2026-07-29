"""MCP Agent — 用 LangGraph ReAct Agent 驱动 Playwright MCP 生成 UI 测试脚本。

照搬 ThemisAI 的 agents/deep/__init__.py 架构：
- LangGraph create_react_agent 替代 deepagents
- LangChain ChatOpenAI 调用 LLM
- Playwright MCP Bridge 提供浏览器工具
- submit_tool + verify_tool 提交验证脚本
- SKILL.md 注入 system prompt
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import logging

from app.config import settings
from app.services.ai.mcp_bridge import PlaywrightMCPBridge

logger = logging.getLogger(__name__)

SKILLS_DIR = str(Path(__file__).parent / "skills")
ARTIFACTS_BASE = tempfile.gettempdir()


def _load_skills(skills_dirs: list[str]) -> str:
    """加载 skills 目录下所有 SKILL.md / *.md 文件，拼接为 system prompt。"""
    parts = []
    for d in skills_dirs:
        if not os.path.isdir(d):
            continue
        for root, dirs, files in os.walk(d):
            for f in sorted(files):
                if f.endswith(".md"):
                    fp = os.path.join(root, f)
                    try:
                        content = Path(fp).read_text(encoding="utf-8")
                        parts.append(content)
                    except Exception:
                        pass
    return "\n\n---\n\n".join(parts)


def _build_task_prompt(
    test_case_title: str,
    test_case_steps: list[dict],
    expected_result: str | None,
    base_url: str,
    preconditions: str | None = None,
    test_user: str = "admin",
    test_password: str = "admin123",
) -> str:
    steps_text = ""
    for i, step in enumerate(test_case_steps, 1):
        desc = step.get("description", step.get("action", str(step)))
        expected = step.get("expected", step.get("expected_result", ""))
        steps_text += f"  步骤 {i}: {desc}"
        if expected:
            steps_text += f" → 预期: {expected}"
        steps_text += "\n"

    preconditions_text = preconditions or "无"

    return f"""请基于以下功能用例生成 UI 自动化测试脚本：

用例标题: {test_case_title}

前置条件: {preconditions_text}

测试步骤:
{steps_text}
整体预期结果: {expected_result or '无'}

被测应用地址: {base_url}

## 严格按三阶段执行

### 阶段一：浏览器探索
仅执行测试步骤描述的操作，不要探索步骤之外的功能。

0. **调用 `browser_close` 关闭任何已有浏览器会话**（清除残留 context，必须作为第一步执行）
1. 如果需要登录：顺序执行（不要并行填表单）
   - browser_navigate → {base_url}（应用会自动跳转到登录页，不要手动拼接 /login）
   - browser_snapshot 获取元素引用（先看实际页面结构再操作）
   - browser_fill 用户名字段（按 snapshot 中的实际 ref 和 accessible name 定位），填入 '{test_user}'（必须用 fill 不是 type）
   - browser_fill 密码字段（按 snapshot 中的实际 ref 和 accessible name 定位），填入 '{test_password}'
   - browser_click 点击登录/Sign In 按钮
   - 等待登录完成（URL 不再是登录页）
2. 按测试步骤逐步操作浏览器，每步：
   - 执行操作
   - browser_snapshot 记录选择器
   - 验证预期结果

### 阶段二：生成脚本
根据阶段一记录的选择器，按 script-spec.md 格式生成脚本。
- 必须 import from '../fixtures'
- 使用 `page` fixture（不要用 authenticatedPage，登录在 test body 中显式写）
- 使用相对路径（不要硬编码 URL）
- **阶段一每一步操作都必须在脚本中体现**：如果阶段一中点击按钮后弹出了下拉菜单并选择了选项，脚本中必须包含 click 按钮 + click 菜单选项两个动作，不能跳过中间步骤
- **数据清理（按需）**：只有在数据**真正被持久化**（提交成功）后才注册 cleanup。验证类用例（空提交报错、格式校验等）不需要 cleanup。

### 阶段三：提交并验证
1. submit_script 提交脚本
2. verify_script 验证
3. 如果失败，修复后重新提交+验证，最多 3 轮

## 关键注意事项
- 表单输入必须用 browser_fill（清空后填入），不要用 browser_type（会追加）
- 多个表单字段必须顺序填写，不要并行填写
- 只探索测试步骤要求的操作，不要自行测试额外功能
- 禁止注释掉步骤、try/catch 吞错、永真断言、硬编码完整 URL
"""


@dataclass
class SSEEvent:
    event: str
    data: dict[str, Any]


async def stream_mcp_agent(
    test_case_title: str,
    test_case_steps: list[dict],
    expected_result: str | None,
    preconditions: str,
    base_url: str,
    test_user: str = "admin",
    test_password: str = "admin123",
    model_name: str | None = None,
) -> AsyncGenerator[SSEEvent, None]:
    """运行 MCP Agent，流式产出 SSE 事件。"""
    from langchain_openai import ChatOpenAI
    from langgraph.prebuilt import create_react_agent

    from app.services.ai.submit_tool import create_submit_tool
    from app.services.ai.verify_tool import create_verify_tool

    execution_id = f"gen-{os.urandom(4).hex()}"
    artifacts_dir = os.path.join(ARTIFACTS_BASE, "tb-ui-artifacts", execution_id)
    os.makedirs(artifacts_dir, exist_ok=True)

    bridge = PlaywrightMCPBridge(
        headless=not bool(os.environ.get("DISPLAY")),
        mcp_url=settings.playwright_mcp_url or None,
    )
    shared_state: dict[str, Any] = {"script_content": "", "version": 0}

    try:
        yield SSEEvent("status", {"content": "正在连接 Playwright MCP..."})
        await bridge.connect()
        tools = await bridge.as_langchain_tools()

        tools.append(create_submit_tool(shared_state))
        tools.append(create_verify_tool(
            base_url=base_url, artifacts_dir=artifacts_dir, shared_state=shared_state,
            test_user=test_user, test_password=test_password,
        ))

        yield SSEEvent("status", {"content": f"已连接，加载了 {len(tools)} 个工具"})

        # 构建 LLM — UI 生成专用强模型（ai_ui_model），回退 ai_model
        _model = model_name or settings.ai_ui_model or settings.ai_model or "claude-sonnet-5"
        model = ChatOpenAI(
            model=_model,
            api_key=settings.ai_auth_token or settings.ai_api_key or "none",
            base_url=settings.ai_ui_base_url or settings.ai_base_url,
            temperature=0.0,
            max_tokens=settings.ai_ui_max_tokens or settings.ai_max_tokens,
            streaming=True,
            max_retries=5,  # 网关瞬时限流(GW-2006)退避重试
            # claude-proxy 每次 spawn 真 CLI，首字块可能 >120s（默认 chunk 超时太紧）→ 关掉，靠 timeout 兜底
            stream_chunk_timeout=None,
            model_kwargs={"stream_options": {"include_usage": True}} if "claude" not in _model.lower() else {},
            default_headers={"User-Agent": "claude-cli/1.0"},
            timeout=600,
        )

        # 加载 SKILL.md 作为 system prompt
        skills_prompt = _load_skills([SKILLS_DIR])
        system_prompt = (
            "你是 UI 自动化测试专家。通过 Playwright MCP 工具操控真实浏览器，"
            "探索目标页面，生成可执行的 Playwright Test 脚本（TypeScript .spec.ts）。\n\n"
            + skills_prompt
        )

        agent = create_react_agent(
            model=model,
            tools=tools,
            prompt=system_prompt,
        )

        task_prompt = _build_task_prompt(
            test_case_title=test_case_title,
            test_case_steps=test_case_steps,
            expected_result=expected_result,
            base_url=base_url,
            preconditions=preconditions,
            test_user=test_user,
            test_password=test_password,
        )

        yield SSEEvent("status", {"content": "Agent 开始执行..."})

        step_seq = 0
        try:
            async for event in agent.astream_events(
                {"messages": [("human", task_prompt)]},
                version="v2",
                config={"recursion_limit": 500},
            ):
                kind = event.get("event", "")

                if kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        content = chunk.content
                        if isinstance(content, str):
                            yield SSEEvent("token", {"content": content})
                        elif isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    yield SSEEvent("token", {"content": block["text"]})

                elif kind == "on_tool_start":
                    tool_name = event.get("name", "unknown")
                    tool_input = event.get("data", {}).get("input", {})

                    if tool_name == "submit_script":
                        yield SSEEvent("status", {"content": "正在提交脚本..."})
                    elif tool_name == "verify_script":
                        yield SSEEvent("status", {"content": "正在验证脚本..."})
                    else:
                        step_seq += 1
                        friendly = _friendly_tool_label(tool_name, tool_input)
                        yield SSEEvent("step_start", {"seq": step_seq, "action": friendly, "phase": "action"})

                elif kind == "on_tool_end":
                    tool_name = event.get("name", "unknown")
                    output = event.get("data", {}).get("output", "")
                    output_str = str(output)

                    if tool_name == "submit_script":
                        yield SSEEvent("status", {"content": f"脚本已提交 (v{shared_state.get('version', '?')})"})
                    elif tool_name == "verify_script":
                        passed = "VERIFICATION PASSED" in output_str
                        shared_state["verified"] = passed
                        v_status = "passed" if passed else "failed"
                        yield SSEEvent("verification", {"status": v_status, "output": output_str[:2000]})
                    else:
                        yield SSEEvent("step_done", {
                            "seq": step_seq, "action": _friendly_tool_label(tool_name, {}),
                            "status": "passed" if "error" not in output_str.lower()[:50] else "failed",
                        })

        except Exception as stream_exc:
            exc_name = type(stream_exc).__name__
            if "ClosedResource" in exc_name or "ClosedResource" in str(stream_exc):
                logger.info("mcp_agent_stream_closed_normally")
            else:
                logger.error("mcp_agent_stream_error", exc_info=True)
                yield SSEEvent("error", {"content": f"Agent 异常: {exc_name}: {stream_exc}"})
                return

        script_content = shared_state.get("script_content", "")
        if script_content:
            yield SSEEvent("done", {
                "script_content": script_content,
                "all_passed": bool(shared_state.get("verified", False)),
            })
        else:
            yield SSEEvent("error", {"content": "Agent 未调用 submit_script，脚本未生成。"})

    except Exception as exc:
        logger.error("mcp_agent_failed", exc_info=True)
        if shared_state.get("script_content"):
            yield SSEEvent("done", {"script_content": shared_state["script_content"], "all_passed": False})
        else:
            yield SSEEvent("error", {"content": f"{type(exc).__name__}: {str(exc)[:500]}"})
    finally:
        try:
            await bridge.close()
        except Exception:
            pass


def _friendly_tool_label(name: str, args: dict) -> str:
    labels = {
        "browser_navigate": lambda a: f"导航到 {a.get('url', '')}",
        "browser_click": lambda a: f"点击 {a.get('element', a.get('ref', ''))}",
        "browser_fill": lambda a: f"填写 {a.get('element', a.get('ref', ''))} = {str(a.get('value', ''))[:30]}",
        "browser_type": lambda a: f"输入 {a.get('element', a.get('ref', ''))}",
        "browser_fill_form": lambda a: "填写表单",
        "browser_select_option": lambda a: f"选择 {a.get('element', a.get('ref', ''))}",
        "browser_snapshot": lambda _: "获取页面快照",
        "browser_take_screenshot": lambda _: "截图",
        "browser_close": lambda _: "关闭浏览器",
        "browser_wait_for": lambda a: f"等待 {a.get('text', a.get('time', ''))}",
        "browser_press_key": lambda a: f"按键 {a.get('key', '')}",
        "browser_hover": lambda a: f"悬停 {a.get('element', a.get('ref', ''))}",
        "browser_find": lambda a: f"搜索 {a.get('text', '')}",
        "browser_evaluate": lambda _: "执行 JS",
        "browser_run_code_unsafe": lambda _: "执行 Playwright 代码",
        "browser_network_requests": lambda _: "查看网络请求",
        "browser_console_messages": lambda _: "查看控制台",
        "browser_tabs": lambda _: "管理标签页",
    }
    fn = labels.get(name)
    if fn:
        try:
            return fn(args)
        except Exception:
            pass
    return name
