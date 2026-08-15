"""构建 pytest 命令行参数（纯函数）。"""
import sys
from pathlib import Path


def build_pytest_command(
    sandbox_dir: str,
    script_ref_file: str,
    script_ref_func: str | None = None,
    junit_xml_path: str | None = None,
    capture_plugin: bool = False,
    playwright_output_dir: str | None = None,
) -> list[str]:
    """
    构建 pytest 命令。

    返回: [sys.executable, "-m", "pytest", "tests/...", ...]
    """
    test_target = str(Path(sandbox_dir) / script_ref_file)
    if script_ref_func:
        test_target += f"::{script_ref_func}"

    # `-s`（不捕获 stdout）是**步骤实时进度的前提**：埋点靠 print `##STEP_START##`
    # 标记，SSE 那条路逐行读 stdout 转发出去。pytest 默认把 print 收进自己的缓冲区、
    # 只在失败时才吐，于是标记永远流不出来 —— 跑的时候面板上一直是「0 步完成」。
    cmd = [sys.executable, "-m", "pytest", test_target, "-v", "--tb=long", "-s"]

    if junit_xml_path:
        cmd.append(f"--junit-xml={junit_xml_path}")

    if capture_plugin:
        cmd.extend(["-p", "tea_capture"])

    if playwright_output_dir:
        cmd.extend([
            f"--output={playwright_output_dir}",
            "--screenshot=only-on-failure",
        ])

    return cmd


def is_playwright_script(content: str) -> bool:
    """检测脚本内容是否为 Playwright 测试"""
    return "playwright" in content.lower() and ("page" in content or "Page" in content)


def check_script_exists(sandbox_dir: str, script_ref_file: str) -> bool:
    """检查脚本文件是否存在。"""
    return (Path(sandbox_dir) / script_ref_file).exists()
