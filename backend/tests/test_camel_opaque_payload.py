"""驼峰化不许碰用户数据 —— 这是一条**静默改坏测试内容**的数据损坏路径。

实测经过：TC-FWGL-00001 的接口场景 AT-0011 曾经 19/19 全绿；在页面上打开
「接口测试」页签保存过一次之后，变成 6 通过 13 失败，第一步就 422：

    UNKNOWN_FIELD: 请求体包含该接口不支持的字段: upstreamId

根因不在用例，在响应层：`to_camel_case` 无差别递归，把步骤 `body` 这个
**用户手写的 HTTP 请求体**也一起改了 —— 库里 `upstream_id` 取出来成
`upstreamId`，前端加载后一保存就把驼峰写回库。从此这条场景对被测系统
发的就是驼峰，而库里和页面上看到的都是驼峰，**看不出它被改过**，
只会以为用例本来就写错了。

对照组 AT-0009/0012/0013 没被页面打开保存过，body 至今还是蛇形、跑得通。
"""
from __future__ import annotations

from app.core.middleware import to_camel_case


def test_我们自己的字段照常驼峰():
    out = to_camel_case({"created_at": 1, "case_code": "TC-1"})
    assert out == {"createdAt": 1, "caseCode": "TC-1"}


def test_请求体的键一个都不许动():
    """这就是 AT-0011 被改坏的那一下。"""
    step = {"step_name": "建服务", "body": {
        "upstream_id": "u1", "display_name": "d", "service_type": "api",
        "config": {"routes": [{"forward_path": "/", "isolation_rule_ids": ["i1"]}]},
    }}
    out = to_camel_case(step)
    assert out["stepName"] == "建服务", "我们的字段该驼峰还是要驼峰"
    b = out["body"]
    assert set(b) == {"upstream_id", "display_name", "service_type", "config"}, b
    # 嵌套任意深度都不许动
    assert set(b["config"]["routes"][0]) == {"forward_path", "isolation_rule_ids"}


def test_请求头的键不许动():
    """`X-Trace-Id`、`x_custom` 这类头名被改掉，请求就发错了。"""
    out = to_camel_case({"headers": {"x_custom_header": "v", "Content-Type": "application/json"}})
    assert set(out["headers"]) == {"x_custom_header", "Content-Type"}


def test_提取物的键是变量名不许动():
    """键被改成 myVar，而步骤里引用的还是 ${my_var} —— 变量永远解析不出来。"""
    out = to_camel_case({"variables_extract": {"my_var": "data.id", "service_id": "data.sid"}})
    assert set(out["variablesExtract"]) == {"my_var", "service_id"}


def test_被测系统的响应原文不许动():
    """last_response 里装的是对方返回的 JSON，改它等于伪造证据。"""
    out = to_camel_case({"last_response": {"body": {"lifecycle_status": "active", "total_count": 2}}})
    assert out["lastResponse"]["body"] == {"lifecycle_status": "active", "total_count": 2}


def test_抓包留存的请求响应体不许动():
    out = to_camel_case({"captured_requests": [
        {"url": "/x", "request_body": {"user_name": "a"}, "response_body": {"error_code": "E1"}},
    ]})
    r = out["capturedRequests"][0]
    assert r["request_body"] == {"user_name": "a"}
    assert r["response_body"] == {"error_code": "E1"}


def test_列表里的对象照常驼峰():
    out = to_camel_case([{"case_code": "A"}, {"case_code": "B"}])
    assert out == [{"caseCode": "A"}, {"caseCode": "B"}]


def test_body为null或字符串不炸():
    assert to_camel_case({"body": None})["body"] is None
    assert to_camel_case({"body": "raw text"})["body"] == "raw text"


def test_场景整体往返一次键不变():
    """把一条真实步骤过一遍，确认往返之后发出去的还是原来那个请求。"""
    original = {
        "id": "s1", "sort_order": 4, "name": "制备：建服务", "method": "POST",
        "body": {"name": "${svcName}", "enabled": True, "protocol": "http",
                 "upstream_id": "${upstreamId}", "display_name": "${svcName}",
                 "service_type": "api",
                 "config": {"routes": [{"path": "/v1/x", "methods": ["GET"],
                                        "forward_path": "/", "preserve_host": False,
                                        "isolation_rule_ids": ["${isoId}"]}]}},
    }
    out = to_camel_case(original)
    assert out["sortOrder"] == 4
    assert out["body"] == original["body"], "请求体必须逐字原样"
