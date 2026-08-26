"""TypeScript Playwright 执行器 — 用 `npx playwright test` 跑 .spec.ts 脚本。

统一 UI 脚本执行入口：生成产物是 TypeScript `.spec.ts`，必须用 Playwright Test CLI 执行，
不能喂给 pytest。此模块被 executor.execute_single_case（lum_run_ui_script / /run）复用，
消除"生成 TS 却用 pytest 跑"的执行器精分。

FIXTURE_SHIM / GLOBAL_SETUP / 错误解析复用 verify_tool，保证生成期 verify 与运行期一致。
node 路径统一在此解析，去掉散落各处的 /home/dreamer/.nvm 硬编码。
"""
from __future__ import annotations

import asyncio
import functools
import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_NODE_MODULES = "/usr/local/lib/node_modules"


@functools.lru_cache(maxsize=1)
def resolve_global_node_modules() -> str:
    """定位全局 node_modules（含 @playwright/test），供临时工程 symlink。

    优先级：env GLOBAL_NODE_MODULES → `npm root -g` → 默认 /usr/local/lib/node_modules。
    去掉原来写死的 /home/dreamer/.nvm/... 机器路径。
    """
    env_path = os.environ.get("GLOBAL_NODE_MODULES", "").strip()
    if env_path and os.path.isdir(env_path):
        return env_path
    try:
        out = subprocess.run(
            ["npm", "root", "-g"], capture_output=True, text=True, timeout=10
        )
        cand = out.stdout.strip()
        if cand and os.path.isdir(cand):
            return cand
    except Exception:
        pass
    return DEFAULT_NODE_MODULES


def resolve_node_path() -> str:
    """NODE_PATH：env 覆盖 → 全局 node_modules。"""
    return os.environ.get("NODE_PATH", "").strip() or resolve_global_node_modules()


def link_node_modules(target_dir: str) -> None:
    """在 target_dir 创建 node_modules 软链到全局，让 Playwright 解析 @playwright/test。"""
    link = os.path.join(target_dir, "node_modules")
    if os.path.exists(link):
        return
    gnm = resolve_global_node_modules()
    try:
        if os.path.isdir(gnm):
            os.symlink(gnm, link)
    except FileExistsError:
        pass
    except Exception as e:
        logger.warning("link_node_modules failed: %s", e)


def is_typescript_script(content: str, file_name: str | None = None) -> bool:
    """判断脚本是否为 TypeScript Playwright Test（判据同 api/scripts.py run-stream）。"""
    name = (file_name or "").lower()
    if name.endswith(".ts") or name.endswith(".spec.ts"):
        return True
    return "from '../fixtures'" in content or "from '@playwright/test'" in content


def _select_credentials(env_vars: dict[str, str]) -> tuple[str, str]:
    """脚本内联登录用 TEST_USER/TEST_PASSWORD；未显式给则回退 ADMIN_*。"""
    user = env_vars.get("TEST_USER") or env_vars.get("ADMIN_USERNAME", "")
    pwd = env_vars.get("TEST_PASSWORD") or env_vars.get("ADMIN_PASSWORD", "")
    return user, pwd


def _collect_screenshots(output_dir: str, max_size_bytes: int = 500_000) -> list[dict]:
    import base64
    results: list[dict] = []
    p = Path(output_dir)
    if not p.exists():
        return results
    for png in sorted(p.rglob("*.png")):
        if png.stat().st_size > max_size_bytes:
            continue
        try:
            b64 = base64.b64encode(png.read_bytes()).decode("ascii")
            results.append({"name": png.name, "base64": b64})
        except Exception:
            logger.warning("读取截图失败: %s", png)
        if len(results) >= 10:
            break
    return results


def run_typescript_playwright(
    script_content: str,
    base_url: str,
    env_vars: dict[str, str] | None = None,
    timeout: int = 120,
    test_user: str = "",
    test_password: str = "",
) -> dict:
    """在临时工程用 npx playwright test 执行 TS 脚本。

    返回与 executor.execute_single_case 兼容的 dict：
    {status, duration_ms, error_summary, stdout, steps, screenshots}
    """
    import time as _time

    from app.services.ai.verify_tool import (
        FIXTURE_SHIM,
        GLOBAL_SETUP,
        _parse_errors_from_report,
    )

    ev = env_vars or {}
    base_url = base_url or ev.get("BASE_URL", "")
    if not test_user or not test_password:
        u, p = _select_credentials(ev)
        test_user = test_user or u
        test_password = test_password or p

    sandbox = None
    try:
        import tempfile
        sandbox = tempfile.mkdtemp(prefix="lum_ts_run_")
        link_node_modules(sandbox)

        tests_dir = Path(sandbox) / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        fixtures_dir = Path(sandbox) / "fixtures"
        fixtures_dir.mkdir(parents=True, exist_ok=True)

        (fixtures_dir / "index.ts").write_text(FIXTURE_SHIM, encoding="utf-8")
        (Path(sandbox) / "global-setup.js").write_text(GLOBAL_SETUP, encoding="utf-8")
        (tests_dir / "test.spec.ts").write_text(script_content, encoding="utf-8")

        output_dir = Path(sandbox) / "test-results"
        config = f"""module.exports = {{
  testDir: './tests',
  timeout: {timeout * 1000},
  retries: 0,
  use: {{
    baseURL: '{base_url}',
    headless: true,
    screenshot: 'on',
    recordHar: {{ path: './test-results/network.har', content: 'embed' }},
    locale: 'zh-CN',
    viewport: {{ width: 1280, height: 720 }},
  }},
  reporter: [['json', {{ outputFile: 'report.json' }}]],
  outputDir: './test-results',
}};
"""
        (Path(sandbox) / "playwright.config.js").write_text(config, encoding="utf-8")

        run_env = {
            **os.environ,
            "CI": "1",
            "NODE_PATH": resolve_node_path(),
            "BASE_URL": base_url,
            "TEST_USER": test_user,
            "TEST_PASSWORD": test_password,
        }
        run_env.update(ev)  # 环境变量覆盖，但保留上面显式设置的 TEST_USER 等
        run_env["TEST_USER"] = test_user
        run_env["TEST_PASSWORD"] = test_password
        run_env["NODE_PATH"] = resolve_node_path()

        start = _time.time()
        try:
            proc = subprocess.run(
                ["npx", "playwright", "test",
                 f"--config={Path(sandbox) / 'playwright.config.js'}"],
                capture_output=True, text=True, cwd=sandbox, env=run_env,
                timeout=timeout + 30,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "duration_ms": int((_time.time() - start) * 1000),
                "error_summary": f"执行超时（{timeout}s）",
                "stdout": "", "steps": [], "screenshots": [],
            }
        duration_ms = int((_time.time() - start) * 1000)

        stdout = (proc.stdout or "") + ("\n--- STDERR ---\n" + proc.stderr if proc.stderr else "")

        if proc.returncode == 0:
            status = "passed"
            error_summary = None
        else:
            status = "failed"
            error_summary = _parse_errors_from_report(str(Path(sandbox) / "report.json"))
            if not error_summary:
                error_summary = (stdout or "")[-2000:]

        from app.engine.har import har_path_for, parse_har
        screenshots = _collect_screenshots(str(output_dir))
        captured_requests = parse_har(har_path_for(output_dir))

        return {
            "status": status,
            "duration_ms": duration_ms,
            "error_summary": error_summary,
            "stdout": (stdout or "")[:10000],
            "steps": [],
            "screenshots": screenshots,
            "captured_requests": captured_requests,
        }
    except FileNotFoundError:
        return {"status": "error", "duration_ms": 0,
                "error_summary": "npx 或 playwright 未找到", "stdout": "", "steps": [], "screenshots": []}
    except Exception as exc:
        logger.error("run_typescript_playwright error", exc_info=True)
        return {"status": "error", "duration_ms": 0,
                "error_summary": f"{type(exc).__name__}: {str(exc)[:500]}",
                "stdout": "", "steps": [], "screenshots": []}
    finally:
        if sandbox:
            shutil.rmtree(sandbox, ignore_errors=True)


async def run_typescript_playwright_async(*args, **kwargs) -> dict:
    """异步包装：在线程池跑同步 subprocess 版本。"""
    import anyio
    return await anyio.to_thread.run_sync(
        functools.partial(run_typescript_playwright, *args, **kwargs)
    )
