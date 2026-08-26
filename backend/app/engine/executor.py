"""
执行器 — 在沙箱中调用 pytest 执行单条用例。

仅包含同步函数，在 arq Worker 的线程中通过 anyio.to_thread.run_sync 调用。
"""
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from app.engine.command_builder import build_pytest_command, check_script_exists, is_playwright_script
from app.engine.result_parser import parse_junit_xml, parse_step_json

logger = logging.getLogger(__name__)


def execute_single_case(
    sandbox_dir: str,
    script_ref_file: str,
    script_ref_func: str | None = None,
    env_vars: dict[str, str] | None = None,
    timeout: int = 300,
    i18n: dict[str, dict] | None = None,
) -> dict:
    """
    在沙箱中执行单条 pytest 用例。

    返回: {
        "status": "passed"|"failed"|"error"|"skipped"|"xfail",
        "duration_ms": int,
        "error_summary": str|None,
        "stdout": str,
        "steps": list[dict],
    }
    """
    # 1. 检查脚本存在
    if not check_script_exists(sandbox_dir, script_ref_file):
        return {
            "status": "skipped",
            "duration_ms": 0,
            "error_summary": f"脚本文件不存在: {script_ref_file}",
            "stdout": "",
            "steps": [],
        }

    # 2. 构建命令
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False, prefix="junit_") as f:
        junit_xml_path = f.name

    # 检测是否为 Playwright 脚本
    script_content = Path(sandbox_dir, script_ref_file).read_text(encoding="utf-8", errors="ignore")

    # TypeScript Playwright(.spec.ts) 必须用 npx playwright test 执行，不能喂 pytest。
    # 统一走 ts_runner，消除"生成 TS 却用 pytest 跑"的执行器精分。
    from app.engine.ts_runner import is_typescript_script, run_typescript_playwright
    if is_typescript_script(script_content, script_ref_file):
        try:
            os.unlink(junit_xml_path)
        except OSError:
            pass
        base_url = (env_vars or {}).get("BASE_URL", "")
        return run_typescript_playwright(
            script_content=script_content,
            base_url=base_url,
            env_vars=env_vars or {},
            timeout=timeout,
        )

    # 未解析的文案占位 → **不许开跑**。
    # 占位坏掉时正例红在「找不到元素」上（看得见），而「不应出现」这类负例**假绿** ——
    # 匹配不到任何元素，"不该存在"当然成立。恒真断言不会自己喊疼，只能拦在执行前。
    # 实测（CC 活体回推 v4）：同一趟里第 10、12 步两条负例全绿，真相是占位压根没被替换。
    # 放在这里是因为四条执行路径都过这个函数 —— 连"某条路忘了渲染文案"也一起拦住。
    from app.services.ui_text_render import unresolved as _unresolved_text
    from app.services.ui_text_render import unresolved_hint as _unresolved_hint
    _left = _unresolved_text(script_content)
    if _left:
        try:
            os.unlink(junit_xml_path)
        except OSError:
            pass
        return {
            "status": "error",
            "duration_ms": 0,
            "error_summary": (
                f"{len(_left)} 处文案占位没解析出来，拒绝执行："
                f"{'、'.join(_left[:5])}"
                + ("…" if len(_left) > 5 else "")
                + "。不拦的话「不应出现」那类断言会假绿（占位匹配不到任何元素，"
                  "'不该存在'当然成立），跑绿了也证明不了任何事。"
                + _unresolved_hint(script_content)
            ),
            "stdout": "",
            "steps": [],
        }

    pw_output_dir = None
    if is_playwright_script(script_content):
        pw_output_dir = str(Path(sandbox_dir) / ".pw_results")
        Path(pw_output_dir).mkdir(parents=True, exist_ok=True)
        from app.engine.har import har_path_for
        from app.engine.pw_conftest import write_playwright_conftest
        # 文案词典由调用方查好传进来 —— 这个函数是同步的、跑在线程里，
        # 拿不到 session。传空也没关系：t() 查不到就原样返回中文。
        write_playwright_conftest(sandbox_dir, env_vars,
                                  har_path=har_path_for(pw_output_dir), i18n=i18n)

    # 注入 HTTP 捕获插件 + 步骤标记器
    plugin_src = Path(__file__).parent / "plugins" / "tea_capture.py"
    step_src = Path(__file__).parent / "plugins" / "tea_step.py"
    autolog_src = Path(__file__).parent / "plugins" / "tea_autolog.py"
    tea_plugins_dir = Path(sandbox_dir) / ".tea_plugins"
    tea_results_dir = Path(sandbox_dir) / ".tea_results"
    has_capture_plugin = plugin_src.exists()
    if has_capture_plugin:
        tea_plugins_dir.mkdir(parents=True, exist_ok=True)
        tea_results_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(plugin_src), str(tea_plugins_dir / "tea_capture.py"))
        if step_src.exists():
            shutil.copy2(str(step_src), str(tea_plugins_dir / "tea_step.py"))
        # 自动埋点插件。两处沙箱（这里和 api/scripts.py 的 SSE 路径）都得复制 ——
        # 漏一处那条路径就静默走 conftest 的 except 分支，执行历史又只剩 pytest 一行。
        if autolog_src.exists():
            shutil.copy2(str(autolog_src), str(tea_plugins_dir / "tea_autolog.py"))

    # 注入平台 conftest.py（始终覆盖，确保环境变量注入逻辑可用）
    conftest_src = Path(__file__).parent / "plugins" / "conftest_platform.py"
    sandbox_conftest = Path(sandbox_dir) / "tests" / "conftest.py"
    if conftest_src.exists():
        sandbox_conftest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(conftest_src), str(sandbox_conftest))

    cmd = build_pytest_command(
        sandbox_dir=sandbox_dir,
        script_ref_file=script_ref_file,
        script_ref_func=script_ref_func,
        junit_xml_path=junit_xml_path,
        capture_plugin=has_capture_plugin,
        playwright_output_dir=pw_output_dir,
    )

    # 3. 构建环境变量
    run_env = os.environ.copy()
    if env_vars:
        run_env.update(env_vars)
    if has_capture_plugin:
        run_env["PYTHONPATH"] = str(tea_plugins_dir) + ":" + run_env.get("PYTHONPATH", "")
        run_env["TEA_CAPTURE_DIR"] = str(tea_results_dir)

    # 4. 执行
    start_time = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=sandbox_dir,
            env=run_env,
            timeout=timeout + 10,
        )
        stdout = result.stdout
        stderr = result.stderr
        returncode = result.returncode
    except subprocess.TimeoutExpired:
        duration_ms = int((time.time() - start_time) * 1000)
        return {
            "status": "error",
            "duration_ms": duration_ms,
            "error_summary": f"执行超时（{timeout}s）",
            "stdout": "",
            "steps": [],
        }

    duration_ms = int((time.time() - start_time) * 1000)

    # 5. 解析 JUnit XML
    junit_results = parse_junit_xml(junit_xml_path)

    # 清理临时文件
    try:
        os.unlink(junit_xml_path)
    except OSError:
        pass

    # 6. 确定整体状态
    if not junit_results:
        status = "error" if returncode != 0 else "passed"
        error_summary = stderr[:2000] if returncode != 0 else None
    else:
        statuses = [r["status"] for r in junit_results]
        if "error" in statuses:
            status = "error"
        elif "failed" in statuses:
            status = "failed"
        elif all(s == "skipped" for s in statuses):
            status = "skipped"
        elif all(s in ("xfail", "skipped") for s in statuses):
            # 全是预期失败/跳过：跑了但没有一条真通过，不能记成 passed
            status = "xfail" if "xfail" in statuses else "skipped"
        else:
            status = "passed"

        error_msgs = [r["message"] for r in junit_results if r["message"]]
        error_summary = "; ".join(error_msgs)[:2000] if error_msgs else None

    # 7. 解析步骤级 JSON
    steps = []
    tea_results_dir = Path(sandbox_dir) / ".tea_results"
    if script_ref_func:
        step_json_path = str(tea_results_dir / f"{script_ref_func}.json")
        steps = parse_step_json(step_json_path)
        if not steps:
            step_json_path = str(Path(sandbox_dir) / f"{script_ref_func}.json")
            steps = parse_step_json(step_json_path)
    if not steps and tea_results_dir.exists():
        for json_file in sorted(tea_results_dir.glob("*.json")):
            steps = parse_step_json(str(json_file))
            if steps:
                break
            steps = parse_step_json(step_json_path)

    # 8. 采集 Playwright 截图 + 网络流量（可选）
    # 必须在这里采，不能等回到调用方 —— 四条调用方（/run、lum_run_ui_script、
    # 计划执行、adhoc 批量）都在 finally 里 rmtree(sandbox)，跟着 sandbox 走的一律拿不到。
    from app.engine.har import parse_har_dir
    screenshots = _collect_screenshots(pw_output_dir) if pw_output_dir else []
    # **读整个目录，不是单个 network.har** —— 多角色脚本一次开好几个 context，
    # conftest 给每个分了 network-N.har（共用一个路径的话后关的会把先关的覆盖成空）。
    captured_requests = parse_har_dir(pw_output_dir) if pw_output_dir else []

    return {
        "status": status,
        "duration_ms": duration_ms,
        "error_summary": error_summary,
        "stdout": ((stdout or "") + ("\n--- STDERR ---\n" + stderr if stderr else ""))[:10000],
        "steps": steps,
        "screenshots": screenshots,
        "captured_requests": captured_requests,
    }


def _collect_screenshots(output_dir: str | None, max_size_bytes: int = 500_000) -> list[dict]:
    """扫描 Playwright 输出目录，收集截图文件转为 base64"""
    if not output_dir:
        return []
    import base64
    results = []
    output_path = Path(output_dir)
    if not output_path.exists():
        return []
    for png in sorted(output_path.rglob("*.png")):
        if png.stat().st_size > max_size_bytes:
            continue
        try:
            b64 = base64.b64encode(png.read_bytes()).decode("ascii")
            results.append({
                "name": png.name,
                "base64": b64,
            })
        except Exception:
            logger.warning("读取截图失败: %s", png)
        if len(results) >= 10:
            break
    return results
