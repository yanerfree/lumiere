"""路径归属校验的封样 —— 挡住"路径写自己的项目、id 填别人的"。

实测（造了个只属于 A 项目的 tester）曾经能做到：
    GET  /projects/{A}/branches/{A的分支}/cases/{B的用例id}  → 200，读到 B 的用例正文
    GET  /projects/{A}/branches/{B的分支id}/cases            → 200，列出 B 的整个用例列表
    PUT  /projects/{A}/branches/{A的分支}/cases/{B的用例id}  → 200，**改掉了 B 的用例标题**

第三条是真改了数据（靠审计日志才还原）。`require_project_role` 只回答
"你是不是路径里那个 project_id 的成员"，不管后面那些 id 属不属于这个项目。

这里钉两件事：① 该挂校验的路由器一个都不能漏；② 校验本身认得出不匹配。
"""
import re
import uuid

import pytest
from fastapi.routing import APIRoute

from app.deps.scope import verify_path_scope
from app.main import app


def _dep_names(route: APIRoute) -> set[str]:
    out, seen = set(), set()

    def walk(dep):
        for sub in dep.dependencies:
            call = getattr(sub, "call", None)
            if call is not None and id(call) not in seen:
                seen.add(id(call))
                out.add(getattr(call, "__name__", ""))
            walk(sub)
    walk(route.dependant)
    return out


def _scoped_paths():
    """所有路径里带 {branch_id} 或 {case_id} 的路由。"""
    return [r for r in app.routes
            if isinstance(r, APIRoute)
            and ("{branch_id}" in r.path or "{case_id}" in r.path)]


def test_带branch或case的路由都挂了归属校验():
    """新加一个 branches/{branch_id}/... 的路由器而忘了挂校验，这条会红。"""
    # 两种形状：路径里有 project_id 的走链路校验；只有 case_id 的走反查校验
    ok = {"verify_path_scope", "verify_case_access"}
    naked = sorted({f"{sorted(r.methods)[0]:6s} {r.path}"
                    for r in _scoped_paths()
                    if not (ok & _dep_names(r))})
    assert not naked, "以下路由没有做路径归属校验（可越权读写别的项目）：\n" + "\n".join("  " + x for x in naked)


def test_确实覆盖到了足够多的路由():
    """防的是选择器坏掉 —— 匹配不到任何路由时，上面那条会安静地全绿。"""
    assert len(_scoped_paths()) > 40, len(_scoped_paths())


@pytest.mark.asyncio
async def test_分支不属于该项目时报不存在():
    """故意返回 404 而不是 403 —— 403 等于告诉对方"这个 id 是存在的，只是你没权限"。"""
    from app.core.exceptions import NotFoundError

    class _Req:
        path_params = {"project_id": str(uuid.uuid4()), "branch_id": str(uuid.uuid4())}

    class _Result:
        @staticmethod
        def scalar_one_or_none():
            return uuid.uuid4()          # 分支存在，但属于别的项目

    class _Session:
        async def execute(self, *a, **k):
            return _Result()

    with pytest.raises(NotFoundError) as e:
        await verify_path_scope(_Req(), _Session())
    assert "不存在" in str(e.value.message)


@pytest.mark.asyncio
async def test_归属对得上就放行():
    same = uuid.uuid4()

    class _Req:
        path_params = {"project_id": str(same), "branch_id": str(uuid.uuid4())}

    class _Result:
        @staticmethod
        def scalar_one_or_none():
            return same

    class _Session:
        async def execute(self, *a, **k):
            return _Result()

    assert await verify_path_scope(_Req(), _Session()) is None


@pytest.mark.asyncio
async def test_路径里没有这些段就跳过():
    """挂在路由器上，同一个依赖会遇到各种路径，缺哪段就不验哪段。"""
    class _Req:
        path_params = {"project_id": str(uuid.uuid4())}

    class _Session:
        async def execute(self, *a, **k):       # 不该被调用
            raise AssertionError("路径里没有 branch_id，不该去查库")

    assert await verify_path_scope(_Req(), _Session()) is None
