"""路由遮挡的全局封样 —— 固定路径被同层的通配路径吃掉。

实测踩到的：`/api/llm-mock/logs/export` 一调就 422，因为
`/api/llm-mock/logs/{log_id}` 注册在它前面，FastAPI 按顺序匹配，
"export" 被当成 log_id 去解析 UUID。页面上「导出日志」点下去只开出一个 422。

隔壁 `api_mock.py` 的顺序恰好是对的 —— 两个几乎一样的页面表现不同，
所以这个 bug 活了很久没人发现。这类错**看代码基本看不出来**（两个装饰器
隔着几十行），只能靠遍历真实路由表。所以钉在这里，全仓一起管。
"""
import re

import pytest

from app.main import app

_PARAM = re.compile(r"\{[^}]+\}")


def _routes():
    out = []
    for r in app.routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None)
        if path and methods:
            out.append((path, methods))
    return out


def _shadowed() -> list[tuple[str, str, str]]:
    """返回 (方法, 被遮挡的固定路径, 遮挡它的通配路径)。"""
    bad = []
    routes = _routes()
    for i, (path, methods) in enumerate(routes):
        if _PARAM.search(path):
            continue                      # 只关心固定路径会不会被吃掉
        segs = path.strip("/").split("/")
        for j in range(i):                # 只有**注册在前面**的才会遮挡
            other, other_methods = routes[j]
            if not (methods & other_methods):
                continue
            osegs = other.strip("/").split("/")
            if len(osegs) != len(segs):
                continue
            # 逐段比：固定段必须相同，通配段视为能吃掉任何东西
            wildcards = 0
            for a, b in zip(segs, osegs):
                if _PARAM.fullmatch(b):
                    wildcards += 1
                elif a != b:
                    break
            else:
                if wildcards:
                    bad.append((sorted(methods)[0], path, other))
                    break
    return bad


def test_没有固定路径被同层通配路径遮挡():
    """新增路由时如果把 /x/{id} 写在 /x/literal 前面，这条会红。

    修法：把固定路径的装饰器挪到通配的**前面**（不是改名、不是加前缀）。
    """
    bad = _shadowed()
    assert not bad, "以下固定路径被前面注册的通配路径吃掉了：\n" + "\n".join(
        f"  {m} {p}  ←被  {o}  遮挡" for m, p, o in bad
    )


@pytest.mark.parametrize("path", [
    "/api/llm-mock/logs/export",
    "/api/api-mock/logs/export",
])
def test_两个mock的导出日志都要可达(path):
    """这俩是同一份代码抄出来的，顺序却不一样 —— 一个能用一个 422。"""
    hit = [p for p, _ in _routes() if p == path]
    assert hit, f"{path} 根本没注册"
    assert not any(p == path for p, _, _ in [(b[1], b[0], b[2]) for b in _shadowed()]), \
        f"{path} 被通配路由遮挡了"
