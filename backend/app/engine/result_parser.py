"""解析 pytest 执行结果 — JUnit XML + 步骤级 JSON。"""
import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_junit_xml(xml_path: str) -> list[dict]:
    """
    解析 JUnit XML 文件。

    返回: [{"name": str, "status": "passed"|"failed"|"error"|"skipped"|"xfail", ...}]
    """
    results = []
    path = Path(xml_path)
    if not path.exists():
        logger.warning("JUnit XML not found: %s", xml_path)
        return results

    try:
        tree = ET.parse(str(path))
        root = tree.getroot()

        for testcase in root.iter("testcase"):
            name = testcase.get("name", "unknown")
            duration = float(testcase.get("time", "0"))

            failure = testcase.find("failure")
            error = testcase.find("error")
            skipped = testcase.find("skipped")

            if failure is not None:
                status = "failed"
                message = failure.get("message", "") or failure.text or ""
            elif error is not None:
                status = "error"
                message = error.get("message", "") or error.text or ""
            elif skipped is not None:
                # pytest 的预期失败在 junit 里也是 <skipped>，靠 type 区分：
                #   type="pytest.xfail" → 它**跑了**而且按预期失败，不是"没跑"
                # 都不进通过率分母，但报告上不能都写成"跳过"
                kind = (skipped.get("type") or "").lower()
                status = "xfail" if "xfail" in kind else "skipped"
                message = skipped.get("message", "")
            else:
                status = "passed"
                message = None

            results.append({
                "name": name,
                "status": status,
                "duration_s": duration,
                "message": message[:2000] if message else None,
            })
    except ET.ParseError:
        logger.exception("Failed to parse JUnit XML: %s", xml_path)

    return results


def _parse_tea_step_format(data: list[dict]) -> list[dict]:
    """解析 tea_step 输出的双层格式（业务步骤 + 子请求）。"""
    steps = []
    for i, step in enumerate(data):
        requests = step.get("requests", [])
        first_req = requests[0] if requests else {}

        steps.append({
            "step_name": step.get("action", step.get("step", f"Step {i+1}")),
            "step_label": step.get("action"),
            "step_phase": step.get("phase"),
            "status": step.get("status", "passed"),
            "duration_ms": step.get("duration_ms"),
            "http_method": first_req.get("method"),
            "url": first_req.get("url"),
            "status_code": first_req.get("status_code"),
            "request_data": first_req.get("request"),
            "response_data": first_req.get("response"),
            "assertions": step.get("assertions"),
            "error_summary": step.get("error"),
            "requests": requests if len(requests) > 1 else None,
        })
    return steps


def _parse_http_capture_format(data: list[dict]) -> list[dict]:
    """解析 tea_capture 原有的 HTTP 级平铺格式。"""
    steps = []
    for i, step in enumerate(data):
        steps.append({
            "step_name": step.get("step", step.get("name", f"Step {i+1}")),
            "step_label": None,
            "step_phase": None,
            "status": step.get("status", "passed"),
            "duration_ms": step.get("duration_ms", step.get("durationMs")),
            "http_method": step.get("method", step.get("http_method")),
            "url": step.get("url"),
            "status_code": step.get("status_code", step.get("statusCode")),
            "request_data": step.get("request"),
            "response_data": step.get("response"),
            "assertions": step.get("assertions"),
            "error_summary": step.get("error", step.get("error_summary")),
        })
    return steps


def parse_step_json(json_path: str) -> list[dict]:
    """
    解析步骤级 JSON 文件。

    自动检测格式：
    - tea_step 格式：包含 action + phase + requests 嵌套
    - tea_capture 格式：HTTP 级平铺（向后兼容）

    返回: [{"step_name", "step_label", "step_phase", "status", "duration_ms",
            "http_method", "url", "status_code", "request_data", "response_data",
            "assertions", "error_summary"}]
    """
    path = Path(json_path)
    if not path.exists():
        return []

    try:
        with open(path) as f:
            data = json.load(f)

        if not isinstance(data, list):
            data = [data]

        if not data:
            return []

        first = data[0]
        is_tea_step_format = "action" in first and "phase" in first

        if is_tea_step_format:
            return _parse_tea_step_format(data)
        else:
            return _parse_http_capture_format(data)

    except (json.JSONDecodeError, KeyError):
        logger.exception("Failed to parse step JSON: %s", json_path)
        return []
