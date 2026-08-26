"""CLI Agent — 用真实 Claude CLI 直驱 Playwright MCP 生成 UI 测试脚本。

替代 LangGraph+claude-proxy（每步冷启 CLI，复杂用例极慢）方案：
- 一个 `claude --print` 会话内完成"探索浏览器→生成脚本"，原生 tool_use、不冷启、不 429（真 claude-cli 客户端走网关正常配额）。
- Lumiere 侧负责 verify（npx playwright test）；失败则 `--resume` 同一会话喂错误自愈，≤3 轮。

需要长驻 Playwright MCP（SSE，settings.playwright_mcp_url）与网关 token（settings.ai_auth_token）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import logging

from app.config import settings

logger = logging.getLogger(__name__)

SKILLS_DIR = str(Path(__file__).parent / "skills")
GATEWAY_BASE = "http://192.168.51.10:8080"  # 公司网关（真 CLI 走这里，不带 /v1）
MAX_HEAL_ROUNDS = 5
CLI_TURN_TIMEOUT = 900  # 单次 claude 调用上限（探索+生成）


@dataclass
class SSEEvent:
    event: str
    data: dict[str, Any]


def _load_skill() -> str:
    parts = []
    for root, _dirs, files in os.walk(SKILLS_DIR):
        for f in sorted(files):
            if f.endswith(".md"):
                try:
                    parts.append(Path(root, f).read_text(encoding="utf-8"))
                except Exception:
                    pass
    return "\n\n---\n\n".join(parts)


def _mcp_config_file() -> str:
    """写一个把长驻 Playwright MCP(SSE) 挂给 CLI 的 mcp-config。"""
    url = settings.playwright_mcp_url or "http://localhost:38931/sse"
    cfg = {"mcpServers": {"playwright": {"type": "sse", "url": url}}}
    fd, path = tempfile.mkstemp(prefix="cli-mcp-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f)
    return path


def _build_task_prompt(title, steps, expected, base_url, preconditions, user, password, context_block: str = "") -> str:
    steps_text = ""
    for i, step in enumerate(steps or [], 1):
        desc = step.get("description", step.get("action", str(step)))
        exp = step.get("expected", step.get("expected_result", ""))
        steps_text += f"  步骤 {i}: {desc}" + (f" → 预期: {exp}" if exp else "") + "\n"
    return f"""请基于以下功能用例生成 Playwright Test 脚本（TypeScript .spec.ts）。

用例标题: {title}
前置条件: {preconditions or '无'}
测试步骤:
{steps_text}整体预期: {expected or '无'}
被测应用地址: {base_url}
登录账号: {user}
登录密码: {password}

## 执行方式（严格）
1. 用 playwright MCP 工具（browser_navigate/browser_snapshot/browser_click/browser_fill 等）在真实浏览器里**逐步走完上面的测试步骤**，边走边用 browser_snapshot 记录真实选择器。若需登录，先登录（用上面账号密码，按 snapshot 的真实元素定位，顺序填不要并行）。
2. 走完后，**只输出一个完整的 TypeScript Playwright Test 脚本**，用 ```typescript 代码块包裹。要求：
   - `import {{ test, expect }} from '../fixtures'`（禁止从 '@playwright/test' 导入）
   - 用 `page` fixture，登录写在 test body 顶部，凭据用 process.env.TEST_USER! / process.env.TEST_PASSWORD!
   - **page.goto 必须用相对路径**（如 `page.goto('/')`、`page.goto('/services')`）；**绝对禁止**硬编码 `http://localhost:xxxx` 或任何绝对 URL——baseURL 由执行环境注入。**只操作被测应用地址({base_url})对应的页面，绝不导航到其它端口/其它应用**。
   - 若被测地址打不开/探索不到目标页面，**如实停止并说明"被测地址不可达"，不要去试别的端口、不要拿其它应用的页面凑数**（那样会生成假通过脚本）。
   - page.goto 用相对路径；每步至少一条断言；用 expect(...).toBeVisible() 等，禁止 waitForTimeout 死等
   - 选择器优先 getByRole/getByText/getByLabel；同名多个用 .first() 或 exact 规避 strict mode。**登录按钮尤其注意**：页面常同时有「登录」提交按钮和「企业 SSO 登录 / LDAP / OIDC」等按钮，`name: /登录/` 会命中多个 → 用 `getByRole('button', {{ name: '登录', exact: true }})` 或 `page.locator('button[type=submit]')` 精确点提交按钮。
   - 创建了数据就用 cleanup fixture 注册清理（cleanup.add(async () => {{...}})）
3. 只探索用例要求的操作，不要顺手测别的。最终回复里除了 ```typescript 脚本块，不要贴多余内容。

## 前置数据处理（重要 — 区分全局 vs 场景，保证脚本自包含、可反复执行）
前置条件里的"业务数据前置"必须分两类处理：
- **场景级数据**（本用例专属、可随意增删的，如"已有一个运行中的服务/一条XX记录"）：脚本里**自己创建**（探索时你会看到创建接口，在 test body 开头用 `page.request.post(...)` 或走 UI 创建）+ **唯一命名**，并在 `cleanup.add(async () => {{ ... }})` 里**自己删除**。做到不依赖环境已有数据、可反复跑。
  - **唯一命名务必优先用场景变量**：若下方「自动化上下文」列出了场景变量（`process.env.SV_<名>`），**必须直接引用它们作为数据的名字/值**（random 类已自动带唯一后缀，可据此识别本脚本造的数据）。**严禁再自己拼 `` `xxx_${{Date.now()}}` `` 或任何随机串**——那样会导致 UI 与接口测试造出的名字对不上、且无法识别本脚本造的数据。**只有当某项数据在场景变量里确实没有对应项时**，才退回用 `` `xxx_${{Date.now()}}` `` 生成唯一名。
- **全局级数据**（系统级共享、不该被测试删除的，如"系统已有默认租户/默认网关"、具名共享资源）：脚本里**先查是否存在**（GET/列表接口或 UI 确认）——存在就用、**绝不创建也不删除**；若不存在，在最终回复里注明"缺全局前置数据 <名称>，需先在环境准备"，不要盲目创建全局资源。
- 判断：措辞含"一个/某条/新建/任意" → 场景级（自建自删）；含"系统/平台/默认/全局/具名共享" → 全局级（查存在、只用不删）。拿不准按场景级处理。
- **API 造数/清理必须带鉴权**（否则 401）：`page.request` 不会自动带登录态。**优先用注入的 `process.env.TEST_TOKEN`**（见下方「自动化上下文」，若有）：在每个 `page.request.post/delete` 里加 `headers: {{ Authorization: `Bearer ${{process.env.TEST_TOKEN}}` }}`。若上下文未提供 token，再退而登录后从 localStorage 取（`browser_evaluate` 确认 token 存哪个 key）或用 `page.evaluate(async () => await fetch(url, {{ method, body, headers, credentials: 'include' }}))` 借会话 cookie。
- **造数/清理的每个 `page.request` 调用后必须校验响应、失败时抛出响应体**（关键——否则失败只剩 `status` 数字，无法知道缺哪些字段，自愈只能瞎猜）：
  `const _r = await page.request.post(url, {{ headers, data }}); if (!_r.ok()) throw new Error(`setup失败 ${{_r.status()}}: ${{await _r.text()}}`);`
  这样 422/400 的字段校验详情（如"缺 Name/ServiceType/Protocol"）会进错误信息，下一轮才能精准补字段。**造数的请求体字段务必来自探索时观察到的真实创建请求**（在 UI 里真实创建一次、看它发出的 POST body 有哪些字段），不要凭空只填 name。
{context_block}
## 效率要求（重要，直接影响生成速度）
- **不要用 browser_take_screenshot**：生成脚本用不到截图，纯浪费轮次。
- **每个页面状态只 browser_snapshot 一次**：用这一次快照提取本页需要的所有选择器；页面没跳转/结构没大变，就复用已有快照，**不要每个动作后都重新 snapshot**。
- **不要调 browser_network_requests / browser_console_messages 等 devtools 工具**（本任务用不到）。
- 目标：用尽量少的工具调用走完流程——探索步数越少，生成越快。
"""


def _mask(element: str, value: str) -> str:
    """密码类字段脱敏，其余显示实际值（截断）。"""
    if not value:
        return ""
    el = str(element or "").lower()
    if any(k in el for k in ("密码", "password", "pwd", "passwd")):
        return "******"
    v = str(value)
    return v if len(v) <= 40 else v[:40] + "…"


_TOOL_LABELS = {
    "browser_navigate": lambda a: f"导航到 {a.get('url', '')}",
    "browser_click": lambda a: f"点击 {a.get('element', a.get('ref', ''))}",
    "browser_fill": lambda a: (f"填写 {a.get('element', a.get('ref', ''))}"
                               + (f" = {_mask(a.get('element'), a.get('value') or a.get('text'))}" if (a.get('value') or a.get('text')) else "")),
    "browser_fill_form": lambda a: "填写表单" + (
        " (" + ", ".join(f"{f.get('name', '')}={_mask(f.get('name'), f.get('value'))}" for f in (a.get('fields') or [])[:4]) + ")"
        if a.get('fields') else ""),
    "browser_type": lambda a: (f"输入 {a.get('element', a.get('ref', ''))}"
                               + (f" = {_mask(a.get('element'), a.get('text') or a.get('value'))}" if (a.get('text') or a.get('value')) else "")),
    "browser_select_option": lambda a: (f"选择 {a.get('element', a.get('ref', ''))}"
                                        + (f" = {_mask(a.get('element'), (a.get('values') or a.get('value')))}" if (a.get('values') or a.get('value')) else "")),
    "browser_snapshot": lambda _: "获取页面快照",
    "browser_take_screenshot": lambda _: "截图",
    "browser_close": lambda _: "关闭浏览器",
    "browser_wait_for": lambda a: f"等待 {a.get('text', a.get('time', ''))}",
    "browser_press_key": lambda a: f"按键 {a.get('key', '')}",
    "browser_hover": lambda a: f"悬停 {a.get('element', a.get('ref', ''))}",
    "browser_evaluate": lambda _: "执行 JS",
}


def _friendly(tool_name: str, args: dict) -> str:
    short = tool_name.split("__")[-1]  # mcp__playwright__browser_navigate -> browser_navigate
    fn = _TOOL_LABELS.get(short)
    if fn:
        try:
            return fn(args)
        except Exception:
            pass
    return short


def _extract_script(text: str) -> str:
    m = re.search(r"```(?:typescript|ts)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""


_SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-auth-token", "x-api-key", "token"}


def _headers_to_dict(hlist: list | None) -> dict:
    """HAR headers[] → dict；敏感头脱敏值但保留 key（结构完整 + 不泄密）。"""
    out = {}
    for h in (hlist or []):
        name = h.get("name", "")
        val = h.get("value", "")
        if name.lower() in _SENSITIVE_HEADERS:
            val = "***"
        out[name] = val
    return out


def _parse_har(har_path: str) -> list[dict]:
    """从执行期录制的 HAR 提取被测应用的**全部** API 请求（供接口视图/接口测试编排）。
    不去重、字段完整（method/url/query/请求头/请求体/响应头/响应体/status）；
    只滤掉静态资源（保留 /api/ 或 json 响应）；敏感头脱敏值保留 key。"""
    if not os.path.exists(har_path):
        return []
    try:
        data = json.loads(Path(har_path).read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return []
    out: list[dict] = []
    for e in data.get("log", {}).get("entries", []):
        req = e.get("request", {}) or {}
        resp = e.get("response", {}) or {}
        url = req.get("url", "")
        ct = ((resp.get("content", {}) or {}).get("mimeType") or "")
        # 只丢静态资源；接口请求（/api/ 或 json 响应）全部保留，不去重
        if "/api/" not in url and "json" not in ct.lower():
            continue
        post = req.get("postData") or {}
        out.append({
            "method": req.get("method", ""),
            "url": url,
            "queryParams": {q.get("name"): q.get("value") for q in (req.get("queryString") or [])},
            "requestHeaders": _headers_to_dict(req.get("headers")),
            "requestContentType": post.get("mimeType", ""),
            "requestBody": (post.get("text") or "")[:8000],
            "status": resp.get("status"),
            "responseHeaders": _headers_to_dict(resp.get("headers")),
            "responseContentType": ct,
            "responseBody": ((resp.get("content", {}) or {}).get("text") or "")[:8000],
            "startedDateTime": e.get("startedDateTime", ""),
        })
        if len(out) >= 300:  # 仅安全上限，正常远不会到
            break
    return out


def _cli_env() -> dict:
    token = settings.ai_auth_token or settings.ai_api_key or ""
    return {
        **os.environ,
        "ANTHROPIC_BASE_URL": GATEWAY_BASE,
        "ANTHROPIC_AUTH_TOKEN": token,
        "ANTHROPIC_API_KEY": token,
    }


async def _run_cli(prompt: str, mcp_cfg: str, model: str, resume_sid: str | None):
    """跑一次 claude --print，返回 (async 事件生成器需在外层消费)。这里返回 proc。"""
    args = [
        "claude", "--print", "--output-format", "stream-json", "--verbose",
        "--model", model,
        "--mcp-config", mcp_cfg,
        "--strict-mcp-config",  # 只用上面的 playwright MCP，忽略项目/全局 .mcp.json（防止误加载 Lumiere 自身工具）
        "--dangerously-skip-permissions",
        "--disallowedTools", "Bash,Read,Write,Edit,NotebookEdit,WebFetch,WebSearch",
        "--max-turns", "80",
    ]
    if resume_sid:
        args += ["--resume", resume_sid]
    args.append(prompt)
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_cli_env(),
    )
    return proc


async def stream_cli_agent(
    test_case_title: str,
    test_case_steps: list[dict],
    expected_result: str | None,
    preconditions: str,
    base_url: str,
    test_user: str = "admin",
    test_password: str = "admin123",
    model_name: str | None = None,
    context_block: str = "",
    verify_env: dict | None = None,
) -> AsyncGenerator[SSEEvent, None]:
    """CLI 引擎主流程：探索+生成 → verify → 失败 resume 自愈（≤3 轮）。"""
    from app.services.ai.verify_tool import _run_playwright_verify

    model = model_name or settings.ai_ui_model or settings.ai_model or "claude-sonnet-5"
    mcp_cfg = _mcp_config_file()
    artifacts_dir = tempfile.mkdtemp(prefix="tb-cli-ui-")
    skill = _load_skill()

    task = _build_task_prompt(
        test_case_title, test_case_steps, expected_result, base_url,
        preconditions, test_user, test_password, context_block,
    )
    # SKILL 作为 system 追加（保留 claude 基础能力），任务作为 prompt
    first_prompt = f"# 生成规范（务必遵守）\n{skill}\n\n# 任务\n{task}"

    session_id: str | None = None
    script_content = ""
    all_passed = False
    captured_requests: list[dict] = []
    step_seq = 0

    try:
        yield SSEEvent("status", {"content": "启动 Claude CLI（原生驱动 Playwright MCP）..."})

        for round_no in range(1, MAX_HEAL_ROUNDS + 1):
            if round_no == 1:
                prompt = first_prompt
                resume = None
            else:
                prompt = (
                    f"上一版脚本 verify 未通过，错误如下：\n{last_error}\n\n"
                    "请修复脚本（必要时用 browser 工具重新确认真实选择器），"
                    "然后只输出修正后的完整 ```typescript 脚本块。"
                )
                resume = session_id

            yield SSEEvent("status", {"content": f"第 {round_no} 轮：探索并生成脚本..."})
            proc = await _run_cli(prompt, mcp_cfg, model, resume)

            final_text = ""
            buf = b""
            try:
                while True:
                    # 用 read(chunk) 手动分行，避免 readline() 的 64KB 上限（大页面 snapshot 会超）
                    chunk = await asyncio.wait_for(proc.stdout.read(65536), timeout=CLI_TURN_TIMEOUT)
                    if chunk:
                        buf += chunk
                        raw_lines = []
                        while b"\n" in buf:
                            ln, buf = buf.split(b"\n", 1)
                            raw_lines.append(ln)
                    else:
                        raw_lines = [buf] if buf.strip() else []
                        buf = b""
                    for line in raw_lines:
                        s = line.decode("utf-8", errors="ignore").strip()
                        if not s:
                            continue
                        try:
                            ev = json.loads(s)
                        except Exception:
                            continue
                        etype = ev.get("type")
                        if etype == "system" and ev.get("subtype") == "init":
                            session_id = ev.get("session_id") or session_id
                        elif etype == "assistant":
                            for block in ev.get("message", {}).get("content", []):
                                if block.get("type") == "tool_use":
                                    step_seq += 1
                                    _act = _friendly(block.get("name", ""), block.get("input", {}))
                                    # 兜底脱敏：字段名判不出时，标签里出现真实密码值也替换掉
                                    if test_password and test_password in _act:
                                        _act = _act.replace(test_password, "******")
                                    yield SSEEvent("step_start", {
                                        "seq": step_seq,
                                        "action": _act,
                                        "phase": "action",
                                    })
                                elif block.get("type") == "text" and block.get("text"):
                                    yield SSEEvent("token", {"content": block["text"]})
                        elif etype == "user":
                            if step_seq:
                                yield SSEEvent("step_done", {"seq": step_seq, "status": "passed"})
                        elif etype == "result":
                            final_text = ev.get("result", "") or final_text
                            if not session_id:
                                session_id = ev.get("session_id") or session_id
                    if not chunk:
                        break
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                last_error = f"CLI 第 {round_no} 轮超时（{CLI_TURN_TIMEOUT}s）"
                yield SSEEvent("status", {"content": last_error})
                continue
            finally:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=10)
                except Exception:
                    proc.kill()

            candidate = _extract_script(final_text)
            if not candidate:
                last_error = "CLI 未输出 ```typescript 脚本块"
                yield SSEEvent("status", {"content": f"第 {round_no} 轮未产出脚本，重试..."})
                continue

            # 守卫：page.goto 硬编码绝对 URL = 测错应用（如撞到别的端口/Lumiere 自身）→ 判无效，宁可失败不存假通过
            if re.search(r"\.goto\(\s*['\"]https?://", candidate):
                bad = re.search(r"\.goto\(\s*['\"](https?://[^'\"]+)", candidate)
                last_error = (f"脚本硬编码了绝对 URL（{bad.group(1) if bad else '?'}），"
                              "必须用相对路径 page.goto('/...')；这通常意味着被测地址不可达、agent 探索到了错误应用。")
                yield SSEEvent("status", {"content": f"第 {round_no} 轮脚本硬编码绝对 URL（疑似测错应用），重试..."})
                continue

            script_content = candidate
            yield SSEEvent("status", {"content": "正在验证脚本（npx playwright test）..."})
            result = await _run_playwright_verify(
                script_content, base_url, artifacts_dir,
                test_user=test_user, test_password=test_password,
                extra_env=verify_env,
            )
            passed = "VERIFICATION PASSED" in result
            yield SSEEvent("verification", {"status": "passed" if passed else "failed", "output": result[:2000]})
            if passed:
                all_passed = True
                captured_requests = _parse_har(os.path.join(artifacts_dir, "verify", "api-trace.har"))
                break
            last_error = result[:1500]

        if script_content.strip():
            yield SSEEvent("done", {"script_content": script_content, "all_passed": all_passed,
                                    "captured_requests": captured_requests})
        else:
            yield SSEEvent("error", {"content": f"CLI 引擎未生成有效脚本（{MAX_HEAL_ROUNDS} 轮）"})

    except Exception as exc:
        logger.error("cli_agent_failed", exc_info=True)
        if script_content.strip():
            yield SSEEvent("done", {"script_content": script_content, "all_passed": all_passed,
                                    "captured_requests": captured_requests})
        else:
            yield SSEEvent("error", {"content": f"{type(exc).__name__}: {str(exc)[:400]}"})
    finally:
        try:
            os.unlink(mcp_cfg)
        except Exception:
            pass
