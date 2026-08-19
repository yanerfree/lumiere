"""自动登录的地址：LOGIN_URL 是**路径**时必须拼上 BASE_URL。

这条不修，一整条能力是死的而且没人发现：步骤里写 `${BASE_URL}${LOGIN_URL}` 拼起来用，
而 TokenCache 直接拿 LOGIN_URL 当完整 URL 发请求 → httpx 抛「missing protocol」→
异常被吞成一行 warning。后果：
  · 共享资源探测一律 401 → state=unknown → `${资源名}` 永远注入不进来，
    于是每条链只好自己写一步「按名字查上游」并硬断言，一个底座缺失就放大成一批全红
  · 401 被动刷新也拿不到新 token
实测这个项目 4 个共享资源全部 unknown —— 链子自带登录步骤照样跑得绿，
只有共享资源这条路悄悄死了。
"""
from __future__ import annotations

from app.services.api_test_runner import TokenCache


def _u(env):
    return TokenCache(env)._login_url()


def test_路径要拼上BASE_URL():
    assert _u({"BASE_URL": "http://h:5176", "LOGIN_URL": "/api/auth/login"}) \
        == "http://h:5176/api/auth/login"


def test_不带前导斜杠也拼对():
    assert _u({"BASE_URL": "http://h:5176/", "LOGIN_URL": "api/auth/login"}) \
        == "http://h:5176/api/auth/login"


def test_已经是完整URL就不动():
    assert _u({"BASE_URL": "http://h", "LOGIN_URL": "https://sso.x/login"}) == "https://sso.x/login"


def test_没配LOGIN_URL用默认路径():
    assert _u({"BASE_URL": "http://h"}) == "http://h/api/auth/login"


def test_连BASE_URL都没有就别发请求():
    assert _u({"LOGIN_URL": "/api/auth/login"}) is None
    assert _u({}) is None
