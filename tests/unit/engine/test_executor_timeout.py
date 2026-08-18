"""Unit 测试 — executor.py 的超时分支

补这条的原因：超时控制从 pytest 参数搬到了 executor 的 subprocess.run(timeout=...)，
但搬过去之后没人测过。原来那条 test_with_timeout 测的是已经删掉的 pytest 参数，
看着像有覆盖，实际上真正干活的分支一条断言都没有。

不真起一个慢进程去等超时（timeout+10 秒，白等），而是让 subprocess.run 直接抛
TimeoutExpired —— 要验的是「超时之后回什么」，不是操作系统会不会杀进程。
"""
import subprocess

import pytest

from app.engine import executor


class TestExecutorTimeout:

    @pytest.mark.unit
    def test_timeout_returns_error_status_not_exception(self, tmp_path, monkeypatch):
        """超时要转成结构化结果，不能把异常抛给调用方。

        上层（计划执行、报告写入）按 dict 的 status 分流。这里漏出异常就会变成
        整个计划挂掉，而不是这一条用例记一次超时。
        """
        script = tmp_path / "test_slow.py"
        script.write_text("def test_slow():\n    pass\n")

        def _boom(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="pytest", timeout=kw.get("timeout", 0))

        monkeypatch.setattr(executor.subprocess, "run", _boom)

        result = executor.execute_single_case(
            sandbox_dir=str(tmp_path), script_ref_file="test_slow.py", timeout=7
        )

        assert result["status"] == "error"
        # 秒数要落在文案里 —— 人看报告时得知道是卡了多久，不能只写「执行超时」
        assert "7" in result["error_summary"]
        assert "超时" in result["error_summary"]

    @pytest.mark.unit
    def test_timeout_result_has_fields_upstream_reads(self, tmp_path, monkeypatch):
        """超时分支返回的 dict 要跟正常分支同构。

        上层无脑取 duration_ms / stdout / steps。超时分支少给一个键，
        报告页就 KeyError —— 而超时恰恰是最需要看到报告的时候。
        """
        script = tmp_path / "test_slow.py"
        script.write_text("def test_slow():\n    pass\n")
        monkeypatch.setattr(
            executor.subprocess, "run",
            lambda *a, **kw: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(cmd="pytest", timeout=1)
            ),
        )

        result = executor.execute_single_case(
            sandbox_dir=str(tmp_path), script_ref_file="test_slow.py", timeout=1
        )

        for key in ("status", "duration_ms", "error_summary", "stdout", "steps"):
            assert key in result, f"超时分支少了 {key}，上层会 KeyError"
        assert isinstance(result["duration_ms"], int)
        assert result["steps"] == []

    @pytest.mark.unit
    def test_subprocess_gets_grace_over_declared_timeout(self, tmp_path, monkeypatch):
        """传给 subprocess 的超时要比声明的大一点。

        pytest 自己收尾（写 junit xml、落 playwright 产物）也要时间。掐得一样紧
        会在正常收尾途中被杀，结果变成「超时」而不是真实的成功/失败。
        """
        script = tmp_path / "test_x.py"
        script.write_text("def test_x():\n    pass\n")
        seen = {}

        def _capture(*a, **kw):
            seen["timeout"] = kw.get("timeout")
            raise subprocess.TimeoutExpired(cmd="pytest", timeout=kw.get("timeout", 0))

        monkeypatch.setattr(executor.subprocess, "run", _capture)
        executor.execute_single_case(
            sandbox_dir=str(tmp_path), script_ref_file="test_x.py", timeout=30
        )

        assert seen["timeout"] > 30, "没留收尾余量，正常收尾会被误杀成超时"
