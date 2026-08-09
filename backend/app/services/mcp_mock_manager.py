"""MCP Mock 服务管理器 — 管理独立大端口 28300 的 MCP Mock 服务（避开 ELK 等常用端口段）"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("mcp_mock")

_STATE_FILE = Path(__file__).resolve().parent.parent.parent / ".mock_state" / "mcp_mock.json"
_TOOLS_FILE = Path(__file__).resolve().parent.parent.parent / ".mock_state" / "mcp_mock_tools.json"

# ── 默认工具 ──────────────────────────────────

DEFAULT_TOOLS = [
    {
        "name": "tb_list_cases",
        "description": "列出测试用例",
        "params": {"branch_id": "string", "page": "integer", "page_size": "integer", "keyword": "string", "folder_id": "string", "priority": "string", "case_type": "string"},
        "mode": "success",
        "enabled": True,
        "customData": None,
        "customIsError": False,
        "successData": {
            "cases": [
                {"id": "mock-001", "caseCode": "TC-DEMO-00001", "title": "用户登录-正常流程", "type": "api", "priority": "P0"},
                {"id": "mock-002", "caseCode": "TC-DEMO-00002", "title": "用户登录-密码错误", "type": "api", "priority": "P1"},
            ],
            "total": 2, "page": 1, "pageSize": 50,
        },
    },
    {
        "name": "tb_get_case",
        "description": "获取用例详情",
        "params": {"case_id": "string"},
        "mode": "success",
        "enabled": True,
        "customData": None,
        "customIsError": False,
        "successData": {"id": "mock-001", "caseCode": "TC-DEMO-00001", "title": "用户登录-正常流程", "type": "api", "priority": "P0", "steps": [{"action": "POST /api/auth/login", "expected": "返回 200"}]},
    },
    {
        "name": "tb_create_case",
        "description": "创建测试用例",
        "params": {"branch_id": "string", "title": "string", "module": "string", "case_type": "string", "priority": "string", "preconditions": "string", "steps": "array", "expected_result": "string"},
        "mode": "success",
        "enabled": True,
        "customData": None,
        "customIsError": False,
        "successData": {"id": "mock-new", "caseCode": "TC-MOCK-00001", "title": "(mock) 新建的用例", "type": "api", "priority": "P2"},
    },
    {
        "name": "tb_get_folder_tree",
        "description": "获取文件夹树",
        "params": {"branch_id": "string"},
        "mode": "success",
        "enabled": True,
        "customData": None,
        "customIsError": False,
        "successData": [
            {"id": "folder-1", "name": "用户管理", "depth": 1, "caseCount": 5, "children": [{"id": "folder-2", "name": "登录", "depth": 2, "caseCount": 3, "children": []}]},
            {"id": "folder-3", "name": "项目管理", "depth": 1, "caseCount": 8, "children": []},
        ],
    },
    {
        "name": "tb_list_api_tree",
        "description": "获取 API 接口树",
        "params": {"project_id": "string"},
        "mode": "success",
        "enabled": True,
        "customData": None,
        "customIsError": False,
        "successData": [
            {"id": "api-1", "type": "folder", "name": "用户模块", "method": None, "url": None},
            {"id": "api-2", "type": "endpoint", "name": "用户登录", "method": "POST", "url": "/api/auth/login"},
            {"id": "api-3", "type": "endpoint", "name": "获取用户列表", "method": "GET", "url": "/api/users"},
        ],
    },
    {
        "name": "tb_get_api_node",
        "description": "获取 API 节点详情",
        "params": {"node_id": "string"},
        "mode": "success",
        "enabled": True,
        "customData": None,
        "customIsError": False,
        "successData": {"id": "api-2", "type": "endpoint", "name": "用户登录", "method": "POST", "url": "/api/auth/login", "headers": {"Content-Type": "application/json"}, "body": {"username": "string", "password": "string"}},
    },
    {
        "name": "tb_list_environments",
        "description": "列出测试环境",
        "params": {},
        "mode": "success",
        "enabled": True,
        "customData": None,
        "customIsError": False,
        "successData": [{"id": "env-1", "name": "development", "description": "开发环境"}, {"id": "env-2", "name": "staging", "description": "预发布环境"}],
    },
    {
        "name": "tb_get_merged_variables",
        "description": "获取合并变量",
        "params": {"env_id": "string"},
        "mode": "success",
        "enabled": True,
        "customData": None,
        "customIsError": False,
        "successData": {"BASE_URL": "http://localhost:8000", "AUTH_TOKEN": "mock-jwt-xxx", "DB_HOST": "localhost"},
    },
]

DEFAULT_ERROR = {"error": "Mock error: tool call failed", "code": "MOCK_ERROR"}


class McpMockServerManager:
    def __init__(self):
        self.port: int = 28300
        self.host: str = "0.0.0.0"
        self.transport: str = "streamable-http"
        self._server = None
        self._task: asyncio.Task | None = None
        self._tools: list[dict] = []
        self._call_logs: deque[dict] = deque(maxlen=500)
        self._load_tools()

    # ── 工具管理 ──

    def _load_tools(self):
        try:
            if _TOOLS_FILE.exists():
                self._tools = json.loads(_TOOLS_FILE.read_text())
                return
        except Exception:
            pass
        self._tools = copy.deepcopy(DEFAULT_TOOLS)
        self._save_tools()

    def _save_tools(self):
        try:
            _TOOLS_FILE.parent.mkdir(parents=True, exist_ok=True)
            _TOOLS_FILE.write_text(json.dumps(self._tools, ensure_ascii=False, indent=2))
        except Exception:
            pass

    def get_tools(self) -> list[dict]:
        return self._tools

    def get_tool(self, name: str) -> dict | None:
        return next((t for t in self._tools if t["name"] == name), None)

    def add_tool(self, tool_data: dict) -> dict:
        tool = {
            "name": tool_data["name"],
            "description": tool_data.get("description", ""),
            # 不填参数时传进来的是 None，得落成 {} —— 否则起服务时迭代 None 会炸
            "params": tool_data.get("params") or {},
            "mode": "success",
            "enabled": True,
            "locked": False,
            "customData": None,
            "customIsError": False,
            "successData": tool_data.get("successData", {"result": "ok"}),
        }
        self._tools.append(tool)
        self._save_tools()
        return tool

    def update_tool(self, name: str, data: dict) -> dict | None:
        tool = self.get_tool(name)
        if not tool:
            return None
        for k in ("description", "mode", "enabled", "locked", "customData", "customIsError", "successData", "params"):
            if k in data:
                tool[k] = data[k]
        self._save_tools()
        return tool

    def toggle_lock(self, name: str) -> dict | None:
        tool = self.get_tool(name)
        if not tool:
            return None
        # 老的 mcp_mock_tools.json 里没有 locked 键，取默认值再翻转
        tool["locked"] = not tool.get("locked", False)
        self._save_tools()
        return tool

    def delete_tool(self, name: str) -> bool:
        idx = next((i for i, t in enumerate(self._tools) if t["name"] == name), None)
        if idx is None:
            return False
        self._tools.pop(idx)
        self._save_tools()
        return True

    # ── Mock 响应 ──

    def compute_response(self, tool_name: str):
        tool = self.get_tool(tool_name)
        if not tool:
            return None
        mode = tool["mode"]
        if mode == "success":
            return tool.get("successData", {"result": "ok"})
        elif mode == "error":
            return copy.deepcopy(DEFAULT_ERROR)
        elif mode == "custom":
            data = tool.get("customData") or tool.get("successData", {"result": "ok"})
            if tool.get("customIsError"):
                msg = data.get("error", str(data)) if isinstance(data, dict) else str(data)
                return {"error": msg, "code": "MOCK_CUSTOM_ERROR"}
            return data
        return None

    # ── 日志 ──

    def log_call(self, tool_name: str, arguments: dict, response, source: str, mode: str, is_error: bool, t0: float):
        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        resp_str = json.dumps(response, ensure_ascii=False, default=str)
        self._call_logs.appendleft({
            "id": str(uuid.uuid4())[:8],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "arguments": arguments,
            "response": resp_str[:5000],
            "source": source,
            "mode": mode,
            "isError": is_error,
            "elapsedMs": elapsed,
        })

    def get_logs(self, tool: str | None = None, status: str | None = None, limit: int = 50, offset: int = 0):
        logs = list(self._call_logs)
        if tool:
            logs = [l for l in logs if l["tool"] == tool]
        if status == "ok":
            logs = [l for l in logs if not l["isError"]]
        elif status == "error":
            logs = [l for l in logs if l["isError"]]
        return logs[offset:offset + limit], len(logs)

    def clear_logs(self) -> int:
        count = len(self._call_logs)
        self._call_logs.clear()
        return count

    # ── 服务管理 ──

    def _save_state(self, running: bool):
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(json.dumps({"running": running, "port": self.port, "transport": self.transport}))
        except Exception:
            pass

    def _load_state(self) -> bool:
        try:
            data = json.loads(_STATE_FILE.read_text())
            self.transport = data.get("transport", "streamable-http")
            self.port = data.get("port", self.port)
            return data.get("running", False)
        except Exception:
            return False

    @property
    def running(self) -> bool:
        if self._server is None:
            return False
        if self._task is not None and self._task.done():
            logger.warning("MCP Mock 服务 task 已意外退出，清理状态")
            self._server = None
            self._task = None
            return False
        return getattr(self._server, 'started', False)

    async def start(self) -> None:
        if self.running:
            return
        app = self._create_app()
        import uvicorn
        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="warning")
        server = uvicorn.Server(config)
        from app.services._mock_server_util import guarded_serve
        task = asyncio.create_task(guarded_serve(server, "MCP Mock"))
        self._task = task
        task.add_done_callback(self._on_task_done)
        self._server = server
        for _ in range(50):
            if server.started:
                break
            if task.done():
                self._server = None
                self._task = None
                raise RuntimeError(f"MCP Mock 启动失败，端口 {self.port} 可能被占用")
            await asyncio.sleep(0.1)
        logger.info("MCP Mock 服务已启动 %s:%d", self.host, self.port)
        self._save_state(True)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
            if self._task:
                try:
                    await asyncio.wait_for(self._task, timeout=5)
                except (asyncio.TimeoutError, Exception):
                    pass
            self._server = None
            self._task = None
            logger.info("MCP Mock 服务已停止")
            self._save_state(False)

    async def _port_free(self) -> bool:
        """端口上还有没有人在听。连得上 = 旧实例还没收完尾。"""
        try:
            _, w = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", self.port), timeout=0.5
            )
            w.close()
            return False
        except Exception:  # noqa: BLE001
            return True

    async def reload(self) -> dict:
        """工具增删改之后，让**已经跑着的**服务重新加载一遍。

        FastMCP 的工具是在 `_create_app()` 里一次性注册进去的，改 `self._tools`
        不会影响已经跑起来的那个实例。实测：页面上加一个工具，对面 CC 的
        `tools/list` 里根本没有 —— 人看到的现象是"工具建了但用不了"，
        而页面没有任何地方提示要重启。

        重载 = 停 + 起。**停完立刻起会撞上端口还没释放**（实测踩过：一次重载之后
        服务再也没起来，页面显示已停止）。所以重试几次；真起不回来要如实报出去，
        不能悄悄留下一个停掉的服务 —— 那比不重载更糟。

        返回 {"reloaded": bool, "reloadError": str|None}。键名不叫 error —— Mock 这一族
        用 `{"error": "..."}` 表示**这次操作失败了**，而重载失败时改动其实已经存下了，
        混用会让前端把"删成功了但没重载"报成"删除失败"。
        """
        if not self.running:
            return {"reloaded": False, "reloadError": None}
        await self.stop()
        # stop() 返回时旧监听 socket 未必已经彻底放开：SO_REUSEADDR 会让新的 bind
        # 直接成功，而新连接仍被路由到正在收尾的旧实例 —— 表现是 initialize 打过去
        # 连接被对端掐断（incomplete chunked read）。等端口真的空出来再起。
        for _ in range(20):
            if await self._port_free():
                break
            await asyncio.sleep(0.15)
        last = ""
        for _ in range(6):
            try:
                await self.start()
                return {"reloaded": True, "reloadError": None}
            except RuntimeError as e:
                last = str(e)
                await asyncio.sleep(0.4)
        logger.error("MCP Mock 重载失败，服务当前是停的: %s", last)
        return {"reloaded": False, "reloadError": f"改动已保存，但 Mock 服务没能重启（{last}）。请手动启动。"}

    def _on_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error("MCP Mock 服务异常退出: %s", exc)
        self._server = None
        self._task = None

    def _create_app(self):
        from fastmcp import FastMCP

        mcp = FastMCP(
            name="testBench-mock",
            instructions="testBench MCP Mock Server — 返回可配置的模拟数据，用于 MCP 客户端联调测试。",
        )

        mgr = self

        def _dispatch(tn):
            t0 = time.perf_counter()
            resp = mgr.compute_response(tn)
            if resp is None:
                resp = {"result": "ok"}
            is_error = isinstance(resp, dict) and resp.get("code") in ("MOCK_ERROR", "MOCK_CUSTOM_ERROR")
            tool = mgr.get_tool(tn)
            mode = tool["mode"] if tool else "success"
            mgr.log_call(tn, {}, resp, "mock-server", mode, is_error, t0)
            if is_error:
                raise RuntimeError(resp.get("error", "Mock error"))
            return resp

        import keyword
        for tool_cfg in self._tools:
            if not tool_cfg.get("enabled", True):
                continue
            tool_name = tool_cfg["name"]
            tool_desc = f"[Mock] {tool_cfg.get('description', tool_name)}"
            # `.get("params", {})` 不够：建工具时不填参数会**把 params 存成 None**
            # （键在、值是 None，默认值用不上），迭代 None 直接 TypeError，
            # 于是整个 MCP Mock 再也起不来 —— 页面上只显示"已停止"，没人知道为什么。
            # 实测踩过：建一个不带参数的工具，服务就此永久起不来。
            tool_params = tool_cfg.get("params") or {}

            # 安全：函数 def 名/参数名只允许合法标识符（非法则降级为占位名/剔除），
            # 避免带连字符等的工具名产生 SyntaxError 把整个 MCP Mock 启动永久打死，
            # 也杜绝代码注入；真实工具名始终经 mcp.tool(name=) 传入、经 ns 传给 _dispatch。
            safe_name = tool_name if isinstance(tool_name, str) and tool_name.isidentifier() else "_mock_tool"
            valid_params = [k for k in tool_params if isinstance(k, str) and k.isidentifier() and not keyword.iskeyword(k)]
            param_str = ", ".join(f'{k}: str = ""' for k in valid_params)
            func_code = f"async def {safe_name}({param_str}):\n    return _dispatch(_tn)\n"
            ns = {"_dispatch": _dispatch, "_tn": tool_name}
            try:
                exec(func_code, ns)
                fn = ns[safe_name]
                fn.__doc__ = tool_desc
                mcp.tool(name=tool_name, description=tool_desc)(fn)
            except Exception:  # noqa: BLE001
                # 上面已经把已知的两种脏数据挡掉了，但兜底仍然要有：
                # **一个工具配坏了，代价应该是这一个用不了，不是整个 Mock 服务起不来**。
                logger.exception("MCP Mock 工具 %s 注册失败，跳过它继续起服务", tool_name)

        return mcp.http_app(path="/", transport=self.transport)


mcp_mock_server = McpMockServerManager()
