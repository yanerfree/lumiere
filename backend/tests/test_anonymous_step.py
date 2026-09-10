"""显式留空 Authorization = 故意匿名，平台不许偷偷替它补 token。

外部 CC 反馈：想写「匿名访问该 401/403」「越权该 403」这类负例，做法是把步骤的
Authorization 头留空。可平台的自动补 token 逻辑会：① 没有 Authorization 头时用
环境里的 AUTH_TOKEN 补上；② 一遇 401 就刷新 token 重放一次。于是匿名负例永远被
平台登录成功，「该被拒」的断言恒绿 —— 一种很隐蔽的假绿（用例看着在测鉴权，
实际每次都带着合法 token 打）。

修法：Authorization 头**存在但为空串** = 故意匿名 —— 删掉它、且 401 不再补 token 重放。
这跟"根本没设 Authorization 头"（走自动补 token）是两回事，区别就在那对空引号。

这里用源码盯住三处接线，任意一处被回退，匿名负例就又会被悄悄洗成鉴权通过。
"""
from __future__ import annotations

import inspect

from app.services import api_test_runner


def _src() -> str:
    return inspect.getsource(api_test_runner.run_single_step)


def test_空Authorization头被删掉而不是补token():
    """存在但空 → 认成匿名、把头删掉。不删的话下面照样自动补 token。"""
    src = _src()
    assert 'if not str(headers["Authorization"]).strip():' in src, "缺少「空串即匿名」这条判断"
    assert 'headers.pop("Authorization")' in src, "空头没被删，会被后面的自动补 token 逻辑接管"
    assert "anonymous = True" in src


def test_匿名步骤401不刷新token重放():
    """否则匿名负例一遇 401 就被平台登录后重放，哪怕断的是 403 也会被洗成鉴权通过。"""
    src = _src()
    # 401 重试的那道条件里必须带上 `not anonymous`
    assert "not anonymous" in src, "401 重试没排除匿名步骤 —— 匿名负例会被偷偷补 token 重放"


def test_匿名和自动补token是互斥的两条路():
    """区别只在那对空引号：头不存在才走自动补 token，头存在但空走匿名。"""
    src = _src()
    # 自动补 token 仍然保留（普通步骤要用）
    assert 'headers["Authorization"] = f"Bearer {env[\'AUTH_TOKEN\']}"' in src \
        or "AUTH_TOKEN" in src, "普通步骤的自动补 token 被误删了"
    # 匿名分支要留下可读的归因，报告里能看出这步是故意不带鉴权
    assert "故意匿名" in src
