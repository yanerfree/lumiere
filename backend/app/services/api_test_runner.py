"""接口测试执行引擎 — 单步/场景/批量执行 + TokenCache + 变量传递"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import string
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_test import ApiTestScenario, ApiTestStep

logger = logging.getLogger(__name__)

_run_semaphore = asyncio.Semaphore(5)


@dataclass
class StepResult:
    step_id: str
    step_name: str
    method: str
    url: str
    status: str  # pass | fail | skip
    status_code: int | None = None
    duration: int = 0
    assertions: list[dict] = field(default_factory=list)
    response_body: dict | None = None
    error: str | None = None
    request_data: dict | None = None  # {method, url, headers, body} — 报告下钻展示


@dataclass
class ScenarioResult:
    scenario_id: str
    scenario_title: str
    scenario_status: str = "draft"  # draft | published | deprecated — 草稿调试不进报告
    folder_id: str | None = None
    steps: list[StepResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(s.status == "pass" or s.status == "skip" for s in self.steps)

    @property
    def pass_count(self) -> int:
        return sum(1 for s in self.steps if s.status == "pass")

    @property
    def fail_count(self) -> int:
        return sum(1 for s in self.steps if s.status == "fail")


@dataclass
class RunEvent:
    type: str  # scenario_start | step_result | scenario_done | run_done | error
    data: dict


class TokenCache:
    """环境级 Token 缓存 — 多角色支持 + 401 被动刷新（ADR-3）。

    凭据来源：env 中的 `{ROLE}_USER` / `{ROLE}_PASS`（默认角色 ADMIN）。
    登录端点：env 中的 `LOGIN_URL`，缺省 `{BASE_URL}/api/auth/login`。
    运行结束随对象销毁。
    """

    def __init__(self, env: dict):
        self._env = env
        self._tokens: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def _login_url(self) -> str | None:
        if self._env.get("LOGIN_URL"):
            return self._env["LOGIN_URL"]
        base = self._env.get("BASE_URL")
        return f"{base.rstrip('/')}/api/auth/login" if base else None

    async def get_token(self, client: httpx.AsyncClient, role: str = "ADMIN") -> str | None:
        async with self._lock:
            if role in self._tokens:
                return self._tokens[role]
            return await self._login(client, role)

    async def refresh_token(self, client: httpx.AsyncClient, role: str = "ADMIN") -> str | None:
        """401 被动刷新 — 丢弃缓存重新登录。"""
        async with self._lock:
            self._tokens.pop(role, None)
            return await self._login(client, role)

    async def _login(self, client: httpx.AsyncClient, role: str) -> str | None:
        url = self._login_url()
        user = self._env.get(f"{role}_USER")
        password = self._env.get(f"{role}_PASS")
        if not url or not user or not password:
            return None
        try:
            resp = await client.post(url, json={"username": user, "password": password}, timeout=15)
            if resp.status_code != 200:
                logger.warning("TokenCache 登录失败 role=%s status=%s", role, resp.status_code)
                return None
            body = resp.json()
            token = None
            for path in (("data", "token"), ("token",), ("access_token",), ("data", "access_token")):
                val = body
                for key in path:
                    val = val.get(key) if isinstance(val, dict) else None
                if isinstance(val, str) and val:
                    token = val
                    break
            if token:
                self._tokens[role] = token
            return token
        except Exception as e:
            logger.warning("TokenCache 登录异常 role=%s: %s", role, e)
            return None


def _inject_runtime_variables(env: dict) -> None:
    """场景级运行时变量 — 同一场景内引用同一个值（便于创建+清理配套使用）。"""
    env.setdefault("RANDOM_8", "".join(random.choices(string.ascii_lowercase + string.digits, k=8)))
    env.setdefault("TIMESTAMP", str(int(time.time())))


def _mask_authorization(headers: dict) -> dict:
    """报告持久化时脱敏 Authorization，避免 token 泄漏。"""
    masked = dict(headers)
    for key in masked:
        if key.lower() == "authorization" and isinstance(masked[key], str) and len(masked[key]) > 24:
            masked[key] = masked[key][:16] + "..." + masked[key][-4:]
    return masked


_SECRET_FIELD_RE = re.compile(r"(password|passwd|pwd|secret|token|api_?key|credential)", re.I)


def _mask_secrets(obj):
    """请求体持久化前脱敏密码类字段。

    Authorization 头早就脱敏了，但请求体里的 password 一直是明文落进
    last_response、在运行结果面板里直接可见（登录步骤尤其明显）。同样该遮。
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SECRET_FIELD_RE.search(k) and isinstance(v, str) and v:
                out[k] = "******"
            else:
                out[k] = _mask_secrets(v)
        return out
    if isinstance(obj, list):
        return [_mask_secrets(v) for v in obj]
    return obj


def _resolve_variables(text, env: dict) -> str:
    if not isinstance(text, str):
        return text
    return re.sub(r'\$\{(\w+)\}', lambda m: str(env.get(m.group(1), m.group(0))), text)


def _resolve_obj(obj, env: dict):
    if isinstance(obj, str):
        return _resolve_variables(obj, env)
    if isinstance(obj, dict):
        return {k: _resolve_obj(v, env) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_obj(v, env) for v in obj]
    return obj


def _unresolved_refs(*objs) -> list[str]:
    """收集解析后仍残留的 ${NAME}——即没有任何来源可解析的变量名。"""
    names: list[str] = []

    def walk(node):
        if isinstance(node, str):
            for n in re.findall(r'\$\{(\w+)\}', node):
                if n not in names:
                    names.append(n)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for o in objs:
        walk(o)
    return names


def _extract_value(body, path: str):
    """按 JSONPath-lite 从响应体取值，支持点号 + 数组下标：
    data.token / data.isolation_rules[0].id / data.items[0] / data[0].name / [0].id
    """
    val = body
    for seg in path.split("."):
        if val is None:
            return None
        m = re.match(r'^([^\[\]]*)((?:\[\d+\])*)$', seg)
        if not m:
            # 段里含无法解析的字符，退化为整段当作字典 key
            if isinstance(val, dict):
                val = val.get(seg)
                continue
            return None
        key, idx_part = m.group(1), m.group(2)
        if key:
            if isinstance(val, dict):
                val = val.get(key)
            else:
                return None
        for idx in re.findall(r'\[(\d+)\]', idx_part):
            i = int(idx)
            if isinstance(val, list) and 0 <= i < len(val):
                val = val[i]
            else:
                return None
    return val


def _expects_status(assertions: list[dict], code: int) -> bool:
    """断言是否预期该状态码（401 重试判断用）。"""
    for a in assertions or []:
        if a.get("type") == "status":
            try:
                if int(a.get("value")) == code:
                    return True
            except (TypeError, ValueError):
                pass
    return False


def _check_assertions(assertions: list[dict], status_code: int, resp_body) -> list[dict]:
    results = []
    for a in assertions:
        passed = False
        a_type = a.get("type")
        operator = a.get("operator", "==")
        expected = a.get("expected", a.get("value"))
        field_path = a.get("field") or (a.get("value") if a.get("expected") is not None or operator == "not_empty" else None)

        if a_type == "status":
            expected = a.get("value")
            try:
                expected = int(expected)
            except (TypeError, ValueError):
                pass
            actual = status_code
            if operator == "==":
                passed = actual == expected
            elif operator == "!=":
                passed = actual != expected
            elif operator == "in":
                passed = actual in (expected if isinstance(expected, list) else [expected])
        elif a_type == "body_contains":
            contain_val = a.get("value", a.get("expected", ""))
            op = operator if operator in ("contains", "not_contains") else "contains"
            hit = str(contain_val) in str(resp_body)
            passed = hit if op == "contains" else not hit
        elif a_type == "body_field":
            actual = _extract_value(resp_body, field_path) if field_path else resp_body
            if operator == "==":
                passed = actual == expected
            elif operator == "!=":
                passed = actual != expected
            elif operator == "not_empty":
                passed = actual is not None and actual != ""
            elif operator == "contains":
                passed = expected is not None and str(expected) in str(actual)
            elif operator == "not_contains":
                passed = expected is not None and str(expected) not in str(actual)

        results.append({**a, "passed": passed})
    return results


async def run_single_step(
    step: ApiTestStep,
    env: dict,
    client: httpx.AsyncClient,
    token_cache: TokenCache | None = None,
) -> StepResult:
    if not step.enabled:
        return StepResult(
            step_id=str(step.id), step_name=step.name,
            method=step.method, url=step.url, status="skip",
        )

    url = _resolve_variables(step.url, env)
    headers = _resolve_obj(step.headers or {}, env)
    body = _resolve_obj(step.body, env)

    # 变量没解析出来时 _resolve_variables 会原样留下 ${NAME}，直接发出去只会得到
    # 一个莫名其妙的 404/422，看不出是"环境/场景变量没配"。这里发之前就拦下来说清楚。
    unresolved = _unresolved_refs(url, headers, body)
    if unresolved:
        names = "、".join(f"${{{n}}}" for n in unresolved)
        return StepResult(
            step_id=str(step.id), step_name=step.name,
            method=step.method, url=step.url, status="fail", duration=0,
            error=(
                f"变量未解析：{names}。请求未发出。\n"
                "请检查：①所选执行环境是否配了这些键（环境管理）"
                "②是否该由更早的步骤 variables_extract 提取（顺序对不对）"
                "③是否该在用例「场景变量」里定义。"
            ),
            request_data={"method": step.method, "url": url,
                          "headers": _mask_authorization(headers), "body": _mask_secrets(body)},
        )

    if "Authorization" not in headers:
        if "AUTH_TOKEN" in env:
            headers["Authorization"] = f"Bearer {env['AUTH_TOKEN']}"
        elif token_cache:
            token = await token_cache.get_token(client)
            if token:
                headers["Authorization"] = f"Bearer {token}"
    if "Content-Type" not in headers and body:
        headers["Content-Type"] = "application/json"

    request_data = {
        "method": step.method, "url": url,
        "headers": _mask_authorization(headers),
        "body": _mask_secrets(body),
    }

    start = time.time()
    try:
        resp = await client.request(method=step.method, url=url, headers=headers, json=body if body else None)

        # 401 被动重试：断言不预期 401 且有 TokenCache → 刷新 token 重试一次
        if resp.status_code == 401 and token_cache and not _expects_status(step.assertions, 401):
            token = await token_cache.refresh_token(client)
            if token:
                headers["Authorization"] = f"Bearer {token}"
                request_data["headers"] = _mask_authorization(headers)
                resp = await client.request(method=step.method, url=url, headers=headers, json=body if body else None)

        duration = int((time.time() - start) * 1000)

        try:
            resp_body = resp.json()
        except Exception:
            resp_body = {"_text": resp.text[:2000]}

        # 断言里的 ${var} 也要解析（如 data.name == ${svcName}），与 url/headers/body 口径一致
        resolved_assertions = _resolve_obj(step.assertions or [], env)
        assertion_results = _check_assertions(resolved_assertions, resp.status_code, resp_body)
        all_pass = all(a["passed"] for a in assertion_results) if assertion_results else True

        if step.variables_extract and resp_body:
            for var_name, path in step.variables_extract.items():
                val = _extract_value(resp_body, path)
                if val is not None:
                    env[var_name] = str(val)

        return StepResult(
            step_id=str(step.id), step_name=step.name,
            method=step.method, url=url,
            status="pass" if all_pass else "fail",
            status_code=resp.status_code, duration=duration,
            assertions=assertion_results,
            response_body=resp_body if isinstance(resp_body, (dict, list)) else {"_text": str(resp_body)},
            request_data=request_data,
        )
    except Exception as e:
        duration = int((time.time() - start) * 1000)
        return StepResult(
            step_id=str(step.id), step_name=step.name,
            method=step.method, url=url,
            status="fail", duration=duration, error=str(e)[:500],
            request_data=request_data,
        )


async def run_scenario(
    scenario: ApiTestScenario,
    steps: list[ApiTestStep],
    session: AsyncSession,
    base_env: dict | None = None,
    token_cache: TokenCache | None = None,
) -> AsyncIterator[RunEvent]:
    async with _run_semaphore:
        # 变量优先级：步骤提取 > 场景变量(SV_*) > 运行时 > 用户选择的环境(base_env) > 场景 env_variables
        env = dict(scenario.env_variables or {})
        env.update(base_env or {})
        _inject_runtime_variables(env)
        # 源用例的场景变量：与 UI 脚本共用同一份定义（random 每次执行唯一）
        # 接口步骤里既可写 ${名字}（与抽屉提示一致），也可写 ${SV_名字}（与 UI 脚本 process.env.SV_x 同名）
        if getattr(scenario, "source_case_id", None):
            try:
                from app.services.scenario_variable_service import resolve_scenario_variables
                sv = await resolve_scenario_variables(session, scenario.source_case_id, global_lookup=env)
                for k, val in sv.items():
                    env[k] = val  # SV_<name> / SV_RUN_ID
                    if k.startswith("SV_") and k != "SV_RUN_ID":
                        env.setdefault(k[3:], val)  # 裸名 ${名字}，不覆盖已有环境变量
            except Exception as e:
                logger.warning("解析源用例场景变量失败 case_id=%s: %s", scenario.source_case_id, e)
        if token_cache is None:
            token_cache = TokenCache(env)

        yield RunEvent(type="scenario_start", data={
            "scenarioId": str(scenario.id),
            "title": scenario.title,
            "stepCount": len(steps),
        })

        results = []
        async with httpx.AsyncClient(timeout=30, verify=False) as client:
            for step in steps:
                result = await run_single_step(step, env, client, token_cache)
                results.append(result)

                step.last_status = result.status
                step.last_response = {
                    "statusCode": result.status_code,
                    "duration": result.duration,
                    "body": result.response_body,
                    "assertions": result.assertions,
                    "request": result.request_data,
                } if not result.error else {"error": result.error, "request": result.request_data}

                yield RunEvent(type="step_result", data={
                    "scenarioId": str(scenario.id),
                    "stepId": result.step_id,
                    "stepName": result.step_name,
                    "method": result.method,
                    "status": result.status,
                    "statusCode": result.status_code,
                    "duration": result.duration,
                })

        await session.commit()

        scenario_result = ScenarioResult(
            scenario_id=str(scenario.id),
            scenario_title=scenario.title,
            scenario_status=scenario.status,
            folder_id=str(scenario.folder_id) if scenario.folder_id else None,
            steps=results,
        )
        yield RunEvent(type="scenario_done", data={
            "scenarioId": str(scenario.id),
            "title": scenario.title,
            "passed": scenario_result.passed,
            "passCount": scenario_result.pass_count,
            "failCount": scenario_result.fail_count,
        })


async def run_batch(
    scenario_ids: list[uuid.UUID],
    session: AsyncSession,
    user_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    report_name: str | None = None,
    base_env: dict | None = None,
    branch_id: uuid.UUID | None = None,
) -> AsyncIterator[RunEvent]:
    all_results: list[ScenarioResult] = []
    # 批量执行共享 TokenCache：同一角色只登录一次（ADR-3）
    shared_env = dict(base_env or {})
    token_cache = TokenCache(shared_env)

    for sid in scenario_ids:
        scenario = await session.get(ApiTestScenario, sid)
        if not scenario:
            continue

        steps_result = await session.execute(
            select(ApiTestStep)
            .where(ApiTestStep.scenario_id == sid)
            .order_by(ApiTestStep.sort_order)
        )
        steps = steps_result.scalars().all()

        scenario_result = None
        async for event in run_scenario(scenario, steps, session, base_env=base_env, token_cache=token_cache):
            yield event
            if event.type == "scenario_done":
                scenario_result = ScenarioResult(
                    scenario_id=str(scenario.id),
                    scenario_title=scenario.title,
                    scenario_status=scenario.status,
                    folder_id=str(scenario.folder_id) if scenario.folder_id else None,
                )
                scenario_result.steps = [
                    StepResult(
                        step_id=str(s.id), step_name=s.name,
                        method=s.method, url=s.url,
                        status=s.last_status or "skip",
                        status_code=s.last_response.get("statusCode") if s.last_response else None,
                        duration=s.last_response.get("duration", 0) if s.last_response else 0,
                        assertions=s.last_response.get("assertions", []) if s.last_response else [],
                        response_body=s.last_response.get("body") if s.last_response else None,
                        error=s.last_response.get("error") if s.last_response else None,
                        request_data=s.last_response.get("request") if s.last_response else None,
                    ) for s in steps
                ]
        if scenario_result:
            all_results.append(scenario_result)

    # 批量执行统一生成报告（不区分草稿/已发布，单步调试走 run-step 不经过这里）
    if all_results and user_id:
        folder_name = await _resolve_common_folder_name(session, all_results)
        report_id = await _create_report(session, all_results, user_id, project_id, report_name, folder_name, branch_id)
        yield RunEvent(type="report_created", data={"reportId": str(report_id)})

    yield RunEvent(type="run_done", data={"totalScenarios": len(scenario_ids)})


async def _resolve_common_folder_name(session: AsyncSession, results: list[ScenarioResult]) -> str | None:
    """所有场景同属一个文件夹时返回文件夹名（报告命名用）。"""
    folder_ids = {r.folder_id for r in results}
    if len(folder_ids) != 1:
        return None
    folder_id = folder_ids.pop()
    if not folder_id:
        return None
    from app.models.api_test_folder import ApiTestFolder
    folder = await session.get(ApiTestFolder, uuid.UUID(folder_id))
    return folder.name if folder else None


async def _create_report(
    session: AsyncSession,
    results: list[ScenarioResult],
    user_id: uuid.UUID,
    project_id: uuid.UUID | None,
    report_name: str | None,
    folder_name: str | None = None,
    branch_id: uuid.UUID | None = None,
) -> uuid.UUID:
    from datetime import datetime, timezone
    from app.models.report import TestReport, TestReportScenario, TestReportStep

    total_pass = sum(r.pass_count for r in results)
    total_fail = sum(r.fail_count for r in results)
    total_steps = sum(len(r.steps) for r in results)
    total_duration = sum(s.duration for r in results for s in r.steps)
    pass_rate = round(total_pass / total_steps * 100, 2) if total_steps > 0 else 0

    now = datetime.now(timezone.utc)
    if not report_name:
        if len(results) == 1:
            report_name = results[0].scenario_title
        elif folder_name:
            report_name = f"{folder_name} {now.strftime('%Y-%m-%d %H:%M')}"
        else:
            report_name = f"接口测试回归 {now.strftime('%Y-%m-%d %H:%M')}"

    report = TestReport(
        report_type="api_test",
        report_name=report_name,
        project_id=project_id,
        branch_id=branch_id,
        executed_by=user_id,
        executed_at=now,
        completed_at=now,
        total_scenarios=len(results),
        passed=sum(1 for r in results if r.passed),
        failed=sum(1 for r in results if not r.passed),
        pass_rate=pass_rate,
        total_duration_ms=total_duration,
    )
    session.add(report)
    await session.flush()

    for i, result in enumerate(results):
        scenario_duration = sum(s.duration for s in result.steps)
        report_scenario = TestReportScenario(
            report_id=report.id,
            scenario_name=result.scenario_title,
            status="passed" if result.passed else "failed",
            execution_type="automated",
            duration_ms=scenario_duration,
            sort_order=i,
        )
        session.add(report_scenario)
        await session.flush()

        for j, step in enumerate(result.steps):
            session.add(TestReportStep(
                scenario_id=report_scenario.id,
                step_name=step.step_name,
                http_method=step.method,
                url=step.url,
                status=step.status,
                status_code=step.status_code,
                duration_ms=step.duration,
                sort_order=j,
                request_data=step.request_data,
                response_data=step.response_body,
                assertions=step.assertions,
                error_summary=step.error,
            ))

    await session.commit()
    logger.info("Created API test report %s: %s", report.id, report_name)
    return report.id
