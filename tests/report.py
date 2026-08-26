"""测试报告生成器 — 运行 pytest 后输出 Excel 友好的 TSV 报告到 tests/report.txt"""
import ast
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
REPORT_PATH = PROJECT_ROOT / "tests" / "report.txt"


def extract_test_info(file_path: str) -> dict[str, dict]:
    """从测试文件中提取每个测试方法的断言描述。"""
    source = Path(file_path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    info = {}

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            lines = source.split("\n")[node.lineno - 1 : node.end_lineno]
            comments = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("# "):
                    comments.append(stripped[2:])
                elif "assert " in stripped and not stripped.startswith("#"):
                    assertion = stripped.replace("assert ", "").split("#")[0].strip()
                    if len(assertion) > 100:
                        assertion = assertion[:97] + "..."
                    comments.append(f"断言: {assertion}")

            desc = " → ".join(comments) if comments else node.name.replace("_", " ")
            info[node.name] = {"desc": desc}

    return info


def run_and_report():
    """运行 pytest 并生成报告文件。"""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=no", "-q", "--no-header"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "backend")},
    )

    # 解析 pytest 输出（格式: tests/path.py::Class::func PASSED [ xx%]）
    import re
    lines = result.stdout.strip().split("\n")
    tests = []
    for line in lines:
        m = re.match(r'^(tests/\S+::\S+)\s+(PASSED|FAILED|ERROR)', line)
        if m:
            tests.append((m.group(1), m.group(2)))

    # 按模块分组
    modules = {}
    for test_id, status in tests:
        parts = test_id.split("::")
        file_path = parts[0]
        class_name = parts[1] if len(parts) > 1 else ""
        func_name = parts[2] if len(parts) > 2 else parts[1] if len(parts) > 1 else ""

        path_parts = file_path.replace("tests/", "").replace(".py", "").split("/")
        # 提取测试级别: tests/api/... → API, tests/unit/... → UNIT, tests/atdd/... → ATDD, tests/e2e/... → E2E
        level = path_parts[0].upper() if path_parts else "OTHER"
        module_key = path_parts[1] if len(path_parts) >= 2 else path_parts[0]

        if module_key not in modules:
            modules[module_key] = []
        modules[module_key].append({
            "file": file_path, "class": class_name, "func": func_name, "status": status, "level": level,
        })

    # 从源码提取描述
    file_cache = {}
    for module_tests in modules.values():
        for t in module_tests:
            fp = t["file"]
            if fp not in file_cache:
                try:
                    file_cache[fp] = extract_test_info(str(PROJECT_ROOT / fp))
                except Exception:
                    file_cache[fp] = {}
            t["desc"] = file_cache[fp].get(t["func"], {}).get("desc", t["func"].replace("_", " "))

    # 统计
    total = len(tests)
    passed = sum(1 for _, s in tests if s == "PASSED")
    failed = sum(1 for _, s in tests if s == "FAILED")

    # 按级别统计
    level_counts = {}
    for module_tests in modules.values():
        for t in module_tests:
            level_counts[t["level"]] = level_counts.get(t["level"], 0) + 1

    # 中文数字
    cn_nums = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
               "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十"]

    # 生成报告
    out = []
    out.append(f"Lumiere 自动化测试报告")
    out.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    out.append(f"总计: {total} 个 | 通过: {passed} | 失败: {failed}")
    level_summary = " | ".join(f"{k}: {v}" for k, v in sorted(level_counts.items()))
    out.append(f"按类型: {level_summary}")
    out.append("")
    out.append(f"序号\t类型\t测试模块\t测试场景\t步骤（断言描述）\t结果")

    seq = 0
    for idx, module_key in enumerate(sorted(modules.keys())):
        module_tests = modules[module_key]
        module_display = module_key.replace("test_", "").replace("_", " ").title()
        cn = cn_nums[idx] if idx < len(cn_nums) else str(idx + 1)
        out.append(f"{cn}、{module_display}（{len(module_tests)} 个）")

        for t in module_tests:
            seq += 1
            scenario = t["func"].replace("test_", "").replace("_", " ")
            out.append(f"{seq}\t{t['level']}\t{t['class']}\t{scenario}\t{t['desc']}\t{t['status']}")

    report_text = "\n".join(out)

    # 写文件
    REPORT_PATH.write_text(report_text, encoding="utf-8")
    print(f"报告已生成: {REPORT_PATH}")
    print(f"总计: {total} 个 | 通过: {passed} | 失败: {failed}")

    # 同时打印到终端
    print("\n" + report_text)

    return result.returncode


if __name__ == "__main__":
    sys.exit(run_and_report())
