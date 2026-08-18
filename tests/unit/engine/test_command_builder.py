"""Unit 测试 — command_builder.py

这里原来有三条断言错了位置：命令头已经从 `["pytest", target, ...]` 改成
`[sys.executable, "-m", "pytest", target, ...]`（用当前解释器跑，不依赖 PATH 上
恰好有 pytest），所以 target 在 cmd[3] 不在 cmd[1]。断言写 cmd[1] 的两条会红，
而 `assert "::" not in cmd[1]` 那条更糟 —— cmd[1] 是 "-m"，它恒为真、一直假通过，
本来想守的「没指定函数时不该出现 ::」根本没在守。
"""
import sys

import pytest

from app.engine.command_builder import build_pytest_command, check_script_exists

# 命令固定前缀，target 紧跟其后
_PREFIX = [sys.executable, "-m", "pytest"]
_TARGET_IDX = len(_PREFIX)


class TestBuildPytestCommand:

    @pytest.mark.unit
    def test_basic_command(self):
        cmd = build_pytest_command("/sandbox", "tests/test_login.py")
        assert cmd[:_TARGET_IDX] == _PREFIX
        assert cmd[_TARGET_IDX] == "/sandbox/tests/test_login.py"
        assert "--tb=long" in cmd
        # -s 是步骤实时进度的前提：埋点靠 print ##STEP_START##，pytest 默认会把它
        # 收进缓冲区只在失败时吐，标记就流不到 SSE，面板上一直显示「0 步完成」
        assert "-s" in cmd

    @pytest.mark.unit
    def test_with_function(self):
        cmd = build_pytest_command("/sandbox", "tests/test_login.py", script_ref_func="test_success")
        assert cmd[_TARGET_IDX] == "/sandbox/tests/test_login.py::test_success"

    @pytest.mark.unit
    def test_with_junit_xml(self):
        cmd = build_pytest_command("/sandbox", "tests/t.py", junit_xml_path="/tmp/result.xml")
        assert "--junit-xml=/tmp/result.xml" in cmd

    @pytest.mark.unit
    def test_command_carries_no_timeout_flag(self):
        """超时不走 pytest 参数 —— 这条守的是那个设计决定。

        原来这里是 `build_pytest_command(..., timeout=60)` 断言出现 `--timeout=60`。
        那个参数已经没了：超时改由 executor 用 subprocess.run(timeout=...) 在进程级别
        控（executor.py 的 TimeoutExpired 分支），不再依赖 pytest-timeout 插件 ——
        插件得装在沙箱里才生效，而沙箱依赖不由我们控。
        谁把 --timeout 加回命令里，这条会红，逼他先想清楚沙箱里到底有没有那个插件。
        """
        cmd = build_pytest_command("/sandbox", "tests/t.py")
        assert not [a for a in cmd if a.startswith("--timeout")]

    @pytest.mark.unit
    def test_no_function_no_double_colon(self):
        cmd = build_pytest_command("/sandbox", "tests/t.py")
        # 断 target 那一格，不是 cmd[1]（那是 "-m"，恒为真）
        assert "::" not in cmd[_TARGET_IDX]
