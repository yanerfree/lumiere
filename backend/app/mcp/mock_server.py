"""MCP Mock Server — 独立的 MCP 协议端点（/mcp-mock-server/），返回可配置的模拟数据。

与真实 MCP Server（/mcp/）完全解耦：
- /mcp/            → 真实数据（查 DB）
- /mcp-mock-server/ → 模拟数据（工具配置页可配 成功/失败/自定义）
"""
from __future__ import annotations

from fastmcp import FastMCP

from app.api.mcp_mock import DEFAULT_SUCCESS, TOOL_DESCRIPTIONS, get_mock_response_always

mock_mcp = FastMCP(
    name="Lumiere-mock",
    instructions="Lumiere MCP Mock Server。返回可配置的模拟数据，用于外部 MCP 客户端联调测试，不访问真实数据库。",
)


def _mock_result(tool_name: str):
    resp = get_mock_response_always(tool_name)
    if isinstance(resp, dict) and resp.get("code") in ("MOCK_ERROR", "MOCK_CUSTOM_ERROR"):
        raise RuntimeError(resp.get("error", "Mock error"))
    return resp if resp is not None else DEFAULT_SUCCESS.get(tool_name, {"result": "ok"})


@mock_mcp.tool(name="lum_list_cases")
async def lum_list_cases(branch_id: str = "", page: int = 1, page_size: int = 50,
                        keyword: str = "", folder_id: str = "", priority: str = "", case_type: str = "") -> dict:
    """[Mock] 列出分支下的测试用例。返回模拟数据。"""
    return _mock_result("lum_list_cases")


@mock_mcp.tool(name="lum_get_case")
async def lum_get_case(case_id: str = "") -> dict:
    """[Mock] 获取单条测试用例详情。返回模拟数据。"""
    return _mock_result("lum_get_case")


@mock_mcp.tool(name="lum_create_case")
async def lum_create_case(branch_id: str = "", title: str = "", module: str = "", case_type: str = "api",
                         submodule: str = "", priority: str = "P2", preconditions: str = "",
                         steps: list | None = None, expected_result: str = "") -> dict:
    """[Mock] 创建测试用例。返回模拟数据，不写入数据库。"""
    return _mock_result("lum_create_case")


@mock_mcp.tool(name="lum_get_folder_tree")
async def lum_get_folder_tree(branch_id: str = "") -> list:
    """[Mock] 获取用例文件夹树。返回模拟数据。"""
    return _mock_result("lum_get_folder_tree")


@mock_mcp.tool(name="lum_list_api_tree")
async def lum_list_api_tree(project_id: str = "") -> list:
    """[Mock] 获取 API 接口树。返回模拟数据。"""
    return _mock_result("lum_list_api_tree")


@mock_mcp.tool(name="lum_get_api_node")
async def lum_get_api_node(node_id: str = "") -> dict:
    """[Mock] 获取 API 节点详情。返回模拟数据。"""
    return _mock_result("lum_get_api_node")


@mock_mcp.tool(name="lum_list_environments")
async def lum_list_environments() -> list:
    """[Mock] 列出测试环境。返回模拟数据。"""
    return _mock_result("lum_list_environments")


@mock_mcp.tool(name="lum_get_merged_variables")
async def lum_get_merged_variables(env_id: str = "") -> dict:
    """[Mock] 获取合并变量。返回模拟数据。"""
    return _mock_result("lum_get_merged_variables")
