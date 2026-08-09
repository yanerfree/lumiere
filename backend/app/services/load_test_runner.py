"""压力测试执行引擎 — asyncio + httpx 并发"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx

from app.deps.db import async_session_factory
from app.services import load_test_service as svc

logger = logging.getLogger("load_test")

_active_runners: dict[uuid.UUID, LoadTestRunner] = {}


def get_runner(run_id: uuid.UUID) -> LoadTestRunner | None:
    return _active_runners.get(run_id)


class MetricsCollector:
    def __init__(self):
        self._lock = asyncio.Lock()
        self.total = 0
        self.success = 0
        self.error = 0
        self._latencies: list[float] = []
        self._reservoir_size = 10000
        self._error_breakdown: dict[str, int] = defaultdict(int)
        self._step_metrics: dict[str, dict] = defaultdict(lambda: {
            "total": 0, "success": 0, "error": 0, "latencies": [],
        })
        self._window: list[tuple[float, bool]] = []
        self._window_size = 10.0

    async def record(
        self, step_name: str, latency_ms: float, status_code: int, success: bool, error_msg: str | None = None,
    ):
        async with self._lock:
            self.total += 1
            if success:
                self.success += 1
            else:
                self.error += 1
                # 有 error_msg 就用它当分类键。之前只要拿到了状态码就用状态码顶包，
                # 于是"断言没过"会被记成 `200`——页面上「错误分布: 200 × N」，
                # 读起来像"服务器返回 200 也算错"，真实原因被丢掉了，根本没法查。
                key = (error_msg or f"HTTP {status_code}" or "unknown")[:80]
                self._error_breakdown[key] += 1

            if len(self._latencies) < self._reservoir_size:
                self._latencies.append(latency_ms)
            else:
                j = random.randint(0, self.total - 1)
                if j < self._reservoir_size:
                    self._latencies[j] = latency_ms

            self._window.append((time.monotonic(), success))

            sm = self._step_metrics[step_name]
            sm["total"] += 1
            if success:
                sm["success"] += 1
            else:
                sm["error"] += 1
            if len(sm["latencies"]) < 2000:
                sm["latencies"].append(latency_ms)
            else:
                j = random.randint(0, sm["total"] - 1)
                if j < 2000:
                    sm["latencies"][j] = latency_ms

    def _percentile(self, data: list[float], p: float) -> float:
        if not data:
            return 0
        s = sorted(data)
        idx = int(len(s) * p / 100)
        return round(s[min(idx, len(s) - 1)], 2)

    def _calc_qps(self) -> float:
        now = time.monotonic()
        cutoff = now - self._window_size
        self._window = [(t, s) for t, s in self._window if t >= cutoff]
        if not self._window or len(self._window) < 2:
            return 0
        span = now - self._window[0][0]
        return round(len(self._window) / max(span, 0.001), 1)

    def snapshot(self) -> dict:
        return {
            "totalRequests": self.total,
            "successCount": self.success,
            "errorCount": self.error,
            "successRate": round(self.success / max(self.total, 1) * 100, 1),
            "errorRate": round(self.error / max(self.total, 1) * 100, 1),
            "qps": self._calc_qps(),
            "avgLatencyMs": round(sum(self._latencies) / max(len(self._latencies), 1), 2),
            "minLatencyMs": round(min(self._latencies), 2) if self._latencies else 0,
            "maxLatencyMs": round(max(self._latencies), 2) if self._latencies else 0,
            "p50Ms": self._percentile(self._latencies, 50),
            "p95Ms": self._percentile(self._latencies, 95),
            "p99Ms": self._percentile(self._latencies, 99),
            "errorBreakdown": dict(self._error_breakdown),
            "stepSummaries": {
                name: {
                    "total": m["total"],
                    "success": m["success"],
                    "error": m["error"],
                    "avgLatencyMs": round(sum(m["latencies"]) / max(len(m["latencies"]), 1), 2),
                    "p50Ms": self._percentile(m["latencies"], 50),
                    "p95Ms": self._percentile(m["latencies"], 95),
                }
                for name, m in self._step_metrics.items()
            },
        }


class LoadTestRunner:
    def __init__(self, config: dict, run_id: uuid.UUID):
        self.config = config
        self.run_id = run_id
        self.metrics = MetricsCollector()
        self._cancelled = False
        self._started_at = time.monotonic()

    def cancel(self):
        self._cancelled = True

    async def execute(self) -> AsyncIterator[dict]:
        _active_runners[self.run_id] = self
        try:
            async for event in self._run():
                yield event
        finally:
            _active_runners.pop(self.run_id, None)

    async def _run(self) -> AsyncIterator[dict]:
        steps = self.config.get("steps", [])
        if not steps:
            yield {"type": "error", "data": {"message": "No steps defined"}}
            return

        concurrent = self.config.get("concurrent_users", 1)
        ramp_up = self.config.get("ramp_up_seconds", 0)
        total_iter = self.config.get("total_iterations")
        duration = self.config.get("duration_seconds")
        variables = self.config.get("variables") or []

        if not total_iter and not duration:
            total_iter = concurrent

        semaphore = asyncio.Semaphore(concurrent)
        tasks: list[asyncio.Task] = []
        metrics_task = asyncio.create_task(self._emit_metrics_loop())
        iter_counter = {"count": 0}

        try:
            if total_iter:
                for i in range(total_iter):
                    if self._cancelled:
                        break
                    if ramp_up and i < concurrent:
                        await asyncio.sleep(ramp_up / concurrent)
                    user_vars = self._pick_variables(variables, i)
                    task = asyncio.create_task(
                        self._run_iteration(steps, semaphore, user_vars, iter_counter)
                    )
                    tasks.append(task)
            elif duration:
                end_time = time.monotonic() + duration
                i = 0
                while time.monotonic() < end_time and not self._cancelled:
                    if ramp_up and i < concurrent:
                        await asyncio.sleep(ramp_up / concurrent)
                    user_vars = self._pick_variables(variables, i)
                    task = asyncio.create_task(
                        self._run_iteration(steps, semaphore, user_vars, iter_counter)
                    )
                    tasks.append(task)
                    i += 1
                    if len(tasks) - sum(1 for t in tasks if t.done()) >= concurrent:
                        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        tasks = [t for t in tasks if not t.done()]

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        finally:
            self._cancelled = True
            metrics_task.cancel()
            try:
                await metrics_task
            except asyncio.CancelledError:
                pass

        yield {"type": "metrics", "data": self.metrics.snapshot()}
        yield {"type": "done", "data": self.metrics.snapshot()}

    async def _emit_metrics_loop(self):
        while not self._cancelled:
            await asyncio.sleep(1)
            # Yielding from a separate task isn't possible, so metrics are polled via snapshot

    async def _run_iteration(
        self, steps: list[dict], semaphore: asyncio.Semaphore, user_vars: dict, counter: dict,
    ):
        async with semaphore:
            if self._cancelled:
                return
            counter["count"] += 1
            env = dict(user_vars)

            async with httpx.AsyncClient(timeout=30, verify=False, follow_redirects=True) as client:
                for step in steps:
                    if self._cancelled:
                        return
                    await self._execute_step(step, client, env)

    async def _execute_step(self, step: dict, client: httpx.AsyncClient, env: dict):
        step_name = step.get("name") or f"{step['method']} {step['url']}"
        url = self._resolve_vars(step["url"], env)
        method = step.get("method", "GET")
        headers = {}
        for h in (step.get("headers") or []):
            if h.get("key"):
                headers[h["key"]] = self._resolve_vars(str(h.get("value", "")), env)

        body = None
        if step.get("body") and step.get("body_type", "none") != "none":
            body = self._resolve_vars(step["body"], env)

        t0 = time.perf_counter()
        success = True
        status_code = 0
        error_msg = None
        response_text = ""

        try:
            resp = await client.request(method, url, headers=headers, content=body)
            status_code = resp.status_code
            response_text = resp.text
            success = status_code < 400
            if not success:
                error_msg = f"HTTP {status_code}"
        except Exception as e:
            success = False
            error_msg = str(e)[:200]

        latency_ms = (time.perf_counter() - t0) * 1000

        for ext in (step.get("extractions") or []):
            var_name = ext.get("variable_name") or ext.get("variableName")
            jsonpath = ext.get("jsonpath")
            if var_name and jsonpath and response_text:
                val = self._extract_jsonpath(response_text, jsonpath)
                if val is not None:
                    env[var_name] = str(val)

        if step.get("assertions"):
            for a in step["assertions"]:
                passed, why = self._check_assertion(a, status_code, response_text)
                if not passed:
                    success = False
                    error_msg = why or f"断言未过：{a.get('type', '')}"
                    break

        await self.metrics.record(step_name, latency_ms, status_code, success, error_msg)

    def _resolve_vars(self, template: str, env: dict) -> str:
        if not env or "${" not in template:
            return template
        result = template
        for k, v in env.items():
            result = result.replace(f"${{{k}}}", str(v))
        return result

    def _pick_variables(self, variables: list, index: int) -> dict:
        env = {}
        for v in variables:
            name = v.get("name", "")
            values = v.get("values", [])
            if name and values:
                env[name] = values[index % len(values)]
        return env

    def _extract_jsonpath(self, text: str, path: str) -> str | None:
        try:
            data = json.loads(text)
            parts = path.strip("$.").split(".")
            current = data
            for p in parts:
                if isinstance(current, dict):
                    current = current.get(p)
                elif isinstance(current, list):
                    current = current[int(p)]
                else:
                    return None
            return str(current) if current is not None else None
        except Exception:
            return None

    @staticmethod
    def _assertion_value(assertion: dict) -> str | None:
        """取断言的比较值。None = 这条断言根本没给值，是配置坏了。

        平台里断言值有两种写法：压测页面写 `value`，接口场景/回推那边写 `expected`
        （见 tb_sync_orchestrated_scenario 的 steps.assertions）。两边都认，
        否则从那边搬过来的场景会**每一发都判失败**，而失败原因还显示成状态码，没法查。
        """
        for k in ("value", "expected"):
            if assertion.get(k) is not None and str(assertion[k]) != "":
                return str(assertion[k])
        return None

    def _check_assertion(self, assertion: dict, status_code: int, body: str) -> tuple[bool, str | None]:
        """返回 (是否通过, 失败原因)。原因要能直接读懂 —— 它会进错误分布当分类键。"""
        a_type = assertion.get("type", "")
        a_value = self._assertion_value(assertion)

        if a_type not in ("status", "body_contains", "body_regex"):
            # 不认识的断言类型以前直接返回 True（静默放过）。放过比误判好，但要说出来。
            return True, None
        if a_value is None:
            # 以前这里拿空串去比，结果是**每一发都失败**，而且原因显示成状态码。
            # 配置坏了就直说是配置坏了，别让人以为是被测系统的问题。
            return False, f"断言配置有问题：{a_type} 没有给比较值（value / expected 都是空的）"

        if a_type == "status":
            return (str(status_code) == a_value,
                    None if str(status_code) == a_value else f"断言未过：期望状态码 {a_value}，实际 {status_code}")
        if a_type == "body_contains":
            return (a_value in body,
                    None if a_value in body else f"断言未过：响应体里没有「{a_value[:40]}」")
        try:
            hit = bool(re.search(a_value, body))
        except re.error:
            return False, f"断言配置有问题：正则写错了「{a_value[:40]}」"
        return hit, None if hit else f"断言未过：响应体不匹配正则「{a_value[:40]}」"


async def start_run(scenario_id: uuid.UUID, overrides: dict | None = None) -> uuid.UUID:
    async with async_session_factory() as session:
        scenario = await svc.get_scenario(session, scenario_id)
        if not scenario:
            raise ValueError("Scenario not found")

        steps = await svc.list_steps(session, scenario_id)
        if not steps:
            raise ValueError("No steps defined")

        config = {
            "concurrent_users": scenario.concurrent_users,
            "ramp_up_seconds": scenario.ramp_up_seconds,
            "total_iterations": scenario.total_iterations,
            "duration_seconds": scenario.duration_seconds,
            "variables": scenario.variables or [],
            "steps": [
                {
                    "name": s.name,
                    "method": s.method,
                    "url": s.url,
                    "headers": s.headers,
                    "body": s.body,
                    "body_type": s.body_type,
                    "extractions": s.extractions,
                    "assertions": s.assertions,
                }
                for s in steps
            ],
        }

        if overrides:
            for k in ("concurrent_users", "ramp_up_seconds", "total_iterations", "duration_seconds"):
                if overrides.get(k) is not None:
                    config[k] = overrides[k]

        run = await svc.create_run(session, {
            "scenario_id": scenario_id,
            "status": "pending",
            "config_snapshot": config,
        })
        await session.commit()
        return run.id


async def execute_run(run_id: uuid.UUID) -> AsyncIterator[dict]:
    async with async_session_factory() as session:
        run = await svc.get_run(session, run_id)
        if not run:
            raise ValueError("Run not found")

        await svc.update_run(session, run_id, {
            "status": "running",
            "started_at": datetime.now(timezone.utc),
        })
        await session.commit()

    runner = LoadTestRunner(run.config_snapshot, run_id)

    final_summary = None
    try:
        async for event in runner.execute():
            if event["type"] == "done":
                final_summary = event["data"]
            yield event
    except Exception as e:
        logger.exception("Load test run error")
        yield {"type": "error", "data": {"message": str(e)}}
        final_summary = runner.metrics.snapshot()
    finally:
        status = "completed" if final_summary else "error"
        if runner._cancelled and not final_summary:
            status = "cancelled"
            final_summary = runner.metrics.snapshot()

        async with async_session_factory() as session:
            await svc.update_run(session, run_id, {
                "status": status,
                "completed_at": datetime.now(timezone.utc),
                "summary": final_summary,
            })
            await session.commit()
