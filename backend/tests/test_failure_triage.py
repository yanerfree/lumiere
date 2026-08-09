"""失败分类器的封样验收（A4）。

10 条封样：1~8 是设计时想到的，9~10 是 dogfood 跑真实回归时捞出来的形态 ——
Playwright 的 expect() 失败文本里**同时**含 AssertionError 和 "waiting for locator"
的 call log，1~8 里两类恰好是分开的两种写法，所以覆盖不到，实测误判成 element_not_found。

判据（评审裁定，见 docs/cc-platform-loop-spec.md §5）：
- **封样**：期望标签写死在这里，改分类器不许改这些期望值
- **不允许跨类错**：应判 A 判成 B 一律失败
- **判成 unknown 算保守，不算错**——除了 7、8 两条负样本，它们判成任何具体
  类别都算错。那两条测的是「它知不知道自己不知道」
- 这 8 条是**常驻 fixture**，不是一次性验收。不进常驻的话，下次改规则会静默退化

反面教材：一个「HAR 里见到任何 5xx 就判 http_5xx」的规则能轻松拿 6/8，但真实场景里
后台轮询报一个 5xx 就会把所有失败都误判成系统问题。样本 6 和 8 专门拦这个。
"""
from __future__ import annotations

import pytest

from app.services.failure_triage import (
    ASSERTION_MISMATCH,
    DEPENDENCY_UNRESOLVED,
    ELEMENT_NOT_FOUND,
    HTTP_5XX,
    SCRIPT_ERROR,
    TIMEOUT,
    UNKNOWN,
    classify,
)

BASE = "http://192.168.51.108:5173"


def _req(url, status, started="2026-08-08T10:00:10+00:00", method="GET"):
    return {"method": method, "url": url, "status": status, "startedAt": started}


# 正常流量背景：登录 + 列表，都是 2xx，和失败无关
_OK_TRAFFIC = [
    _req(f"{BASE}/api/auth/login", 200, "2026-08-08T10:00:00+00:00", "POST"),
    _req(f"{BASE}/api/projects", 200, "2026-08-08T10:00:01+00:00"),
]

# ── 封样：8 条，期望标签在此固化 ──────────────────────────────
SEALED_SAMPLES = [
    (
        "1-改掉按钮id",
        dict(status="failed",
             error_summary='TimeoutError: Locator.click: Timeout 10000ms exceeded.\n'
                           'Call log:\n  - waiting for locator("#submit-btn")',
             captured_requests=_OK_TRAFFIC),
        ELEMENT_NOT_FOUND,
    ),
    (
        "2-接口返回500",
        dict(status="failed",
             error_summary='TimeoutError: Locator.click: Timeout 10000ms exceeded.',
             captured_requests=_OK_TRAFFIC + [
                 _req(f"{BASE}/api/projects", 500, "2026-08-08T10:00:12+00:00", "POST")]),
        HTTP_5XX,
    ),
    (
        "3-断言期望值改错",
        dict(status="failed",
             error_summary='AssertionError: assert "已完成" == "进行中"',
             captured_requests=_OK_TRAFFIC),
        ASSERTION_MISMATCH,
    ),
    (
        "4-脚本里制造NameError",
        dict(status="error",
             error_summary="NameError: name 'undefined_var' is not defined",
             captured_requests=_OK_TRAFFIC),
        SCRIPT_ERROR,
    ),
    (
        "5-引用未登记的变量",
        dict(status="error",
             error_summary="变量未解析：${upstreamId} —— 前置资源 defaultUpstream 未在当前环境找到",
             captured_requests=[]),
        DEPENDENCY_UNRESOLVED,
    ),
    (
        "6-页面加载整体超时",
        dict(status="error",
             error_summary="执行超时（120s）",
             captured_requests=[]),
        TIMEOUT,
    ),
    # ── 负样本：判成任何具体类别都算错 ──
    (
        "7-负样本·没见过的错误形态",
        dict(status="error",
             error_summary="Error: net::ERR_CONNECTION_REFUSED at http://10.0.0.9:9999",
             captured_requests=[]),
        UNKNOWN,
    ),
    (
        "8-负样本·有无关5xx噪音但错误信息给不出线索",
        dict(status="failed",
             error_summary="",
             # 后台轮询在很早的时候报过一个 5xx，离失败时刻很远 —— 不该据此归因
             captured_requests=_OK_TRAFFIC + [
                 _req(f"{BASE}/api/notifications/poll", 503, "2026-08-08T10:00:02+00:00"),
                 _req(f"{BASE}/api/projects", 200, "2026-08-08T10:00:30+00:00"),
             ]),
        UNKNOWN,
    ),
    # ── 9/10：dogfood 实测捞出来的真实形态。原来 1~8 覆盖不到 ──
    (
        "9-真实Playwright expect失败(元素找到了但值不对)",
        dict(status="failed",
             error_summary=(
                 "AssertionError: Locator expected to have text '我的项目'\n"
                 "Actual value: 项目列表 \n"
                 "Call log:\n"
                 '  - Expect "to_have_text" with timeout 8000ms\n'
                 '  - waiting for locator("h2, .page-title, h1").first\n'
                 "    20 × locator resolved to <h2>项目列表</h2>\n"
                 '       - unexpected value "项目列表"\n'
             ),
             captured_requests=_OK_TRAFFIC),
        ASSERTION_MISMATCH,
    ),
    (
        "10-真的没找到元素(从没 resolved 过)",
        dict(status="failed",
             error_summary=(
                 "playwright._impl._errors.TimeoutError: Locator.click: Timeout 5000ms exceeded.\n"
                 "Call log:\n"
                 '  - waiting for locator("#never-exists")\n'
             ),
             captured_requests=_OK_TRAFFIC),
        ELEMENT_NOT_FOUND,
    ),
    # ── 11：dogfood2 捞出来的。样本 9 的修复自己引入的反向误判 ──
    # to_be_visible() 失败时文本长这样：既有 AssertionError，又有 "Actual value: None"。
    # 样本 9 的判据是"出现 Actual value: 就说明元素找到了"，于是把这条判成了
    # assertion_mismatch —— 而 None + "element(s) not found" 恰恰说明**没找到**。
    # 9 和 11 方向相反，必须同时钉住，任何一边的修法都不能把另一边弄挂。
    (
        "11-真实 to_be_visible 失败(元素根本不存在)",
        dict(status="failed",
             error_summary=(
                 "AssertionError: Locator expected to be visible\n"
                 "Actual value: None\n"
                 "Error: element(s) not found \n"
                 "Call log:\n"
                 '  - Expect "to_be_visible" with timeout 5000ms\n'
                 '  - waiting for get_by_placeholder("用户名")\n'
             ),
             captured_requests=_OK_TRAFFIC),
        ELEMENT_NOT_FOUND,
    ),
]


@pytest.mark.parametrize("name,kwargs,expected", SEALED_SAMPLES, ids=[s[0] for s in SEALED_SAMPLES])
def test_sealed_sample(name, kwargs, expected):
    got = classify(base_url=BASE, **kwargs)["phenomenon"]
    if got == expected:
        return
    # 判成 unknown 算保守，不算错（负样本除外——它们的期望就是 unknown）
    if got == UNKNOWN and expected != UNKNOWN:
        pytest.skip(f"{name}: 保守判成 unknown（期望 {expected}），不算跨类错")
    pytest.fail(f"{name}: 跨类错 —— 期望 {expected}，实际 {got}")


def test_passed_not_classified():
    """通过的执行不该被分类。"""
    assert classify(status="passed", error_summary=None)["phenomenon"] is None


def test_5xx_must_be_same_origin():
    """第三方 CDN/埋点挂了不是被测系统的问题。"""
    r = classify(
        status="failed",
        error_summary='AssertionError: assert 1 == 2',
        captured_requests=_OK_TRAFFIC + [
            _req("https://cdn.thirdparty.com/track", 500, "2026-08-08T10:00:12+00:00")],
        base_url=BASE,
    )
    assert r["phenomenon"] == ASSERTION_MISMATCH, r


def test_5xx_must_be_in_failure_window():
    """离失败时刻很远的 5xx 不算数 —— 这条拦的是「见 5xx 就归因」那类规则。"""
    r = classify(
        status="failed",
        error_summary='waiting for locator("#x")',
        captured_requests=[
            _req(f"{BASE}/api/early", 500, "2026-08-08T10:00:00+00:00"),
            _req(f"{BASE}/api/later", 200, "2026-08-08T10:01:00+00:00"),
        ],
        base_url=BASE,
    )
    assert r["phenomenon"] == ELEMENT_NOT_FOUND, r
