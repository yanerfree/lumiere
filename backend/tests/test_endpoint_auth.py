"""每个端点都得要鉴权 —— 封样，防止再漏一族。

实测踩到的：不带 Authorization 把全部端点打了一遍，**74 个未认证可达**，
全集中在 Mock 和测试工具那一族（平台主体都老老实实 401，只有这族当初漏了）。
按严重程度：

  · `POST /api/toolbox/http-request`、`POST /api/http-client/send` —— **SSRF**：
    不用登录就能让服务器去请求任意地址（实测让它打了自己的 /api/healthz 拿到 200）。
    平台监听 0.0.0.0:8756，同一局域网里谁都能拿它当跳板。
  · `PUT /api/proxy-probe/config`、`POST /api/*-mock/...` —— 改代理配置、增删 Mock 路由。
  · `DELETE /api/*/logs` —— 清空各类 Mock 请求日志（**扫描过程中真被删掉了几批**）。

这类漏洞逐个函数去加必然再漏，所以鉴权挂在 `include_router(dependencies=...)` 上，
新加的路由自动带上。这条用例守的就是"别再出现下一个漏网的族"。
"""
import re

import pytest
from fastapi.routing import APIRoute

from app.main import app

# 本来就该公开的：登录、刷新 token、健康检查、OpenAPI 文档
PUBLIC = re.compile(
    r"^/api/auth/(login|refresh)$|^/api/(healthz|readyz)$|^/(openapi.json|docs|redoc)$|^/docs/"
)

# 必须公开、且每条都写清为什么 —— 白名单是最容易被用来"让红变绿"的地方，
# 所以下面那条反向用例会检查这个字典没有被随手扩充。
PUBLIC_BY_DESIGN = {
    # 文档/报告里的截图是 <img src="/api/screenshots/files/..."> 渲染的，
    # 而 <img> 请求**带不了 Authorization 头**。加鉴权 = 所有文档的图片全裂。
    # 安全性靠路径里的 uuid（capability URL）。
    "/api/screenshots/files/{path:path}": "img 标签发不出 Authorization 头，加鉴权会让所有文档图片裂掉",
    # 原来这里还有一条 /api/projects/{id}/documents/tasks/{task_id}（平台发给外部 CC
    # 的一次性取任务 URL）。「文档管理」2026-08-27 整个下线、路由已删，而下面那条
    # 反向用例会 assert 白名单里的路径真的存在 —— 留着就是一条必红的死条目。
}

# 按**定义所在模块**判，不按函数名判：`require_project_role(...)` 返回的是个闭包，
# 名字是内部的 `_check` 之类，按名字匹配会把一大批真的有鉴权的路由误判成裸奔
# （第一版就是这么写的，直接报了 100 多条假问题）。
AUTH_MODULE = "app.deps.auth"


def _has_auth(route: APIRoute) -> bool:
    """递归看这条路由的依赖树里有没有来自鉴权模块的依赖。

    要递归：`require_project_role` 内部又依赖 `get_current_user`，
    而挂载级依赖（include_router(dependencies=...)）也会出现在这棵树上。
    """
    seen = set()

    def walk(dep) -> bool:
        for sub in dep.dependencies:
            call = getattr(sub, "call", None)
            if call is not None:
                if id(call) in seen:
                    continue
                seen.add(id(call))
                if (getattr(call, "__module__", "") or "").startswith(AUTH_MODULE):
                    return True
            if walk(sub):
                return True
        return False

    return walk(route.dependant)


def _guarded_routes():
    return [r for r in app.routes
            if isinstance(r, APIRoute) and r.path.startswith("/api/") and not PUBLIC.match(r.path)]


def test_每个api端点都要求登录():
    naked = sorted({f"{sorted(r.methods)[0]:6s} {r.path}"
                    for r in _guarded_routes()
                    if not _has_auth(r) and r.path not in PUBLIC_BY_DESIGN})
    assert not naked, (
        "以下端点不带 token 就能调（Mock/工具那一族曾经整族漏掉，含 SSRF）：\n"
        + "\n".join("  " + x for x in naked)
    )


def test_故意公开的那几条必须写明理由且没被扩充():
    """白名单是最容易被拿来"让红变绿"的地方，所以钉死条数和内容。

    真要新增，得先在这里写清"为什么它不能加鉴权"，改不动就说明该加鉴权。
    """
    assert set(PUBLIC_BY_DESIGN) == {
        "/api/screenshots/files/{path:path}",
    }, sorted(PUBLIC_BY_DESIGN)
    for path, why in PUBLIC_BY_DESIGN.items():
        assert len(why) > 15, f"{path} 的理由写得太敷衍"
        # 白名单里的路径得真的存在，别留一条早就删掉的
        assert any(isinstance(r, APIRoute) and r.path == path for r in app.routes), path


@pytest.mark.parametrize("path", [
    "/api/toolbox/http-request",      # SSRF：让服务器请求任意地址
    "/api/http-client/send",          # 同上
    "/api/proxy-probe/config",        # 改代理行为
    "/api/api-mock/routes",           # 增删 Mock 路由
    "/api/llm-mock/logs",             # 清空日志
    "/api/system/services",           # 暴露内部服务与端口
])
def test_几个高危端点必须有鉴权(path):
    """单独点名 —— 这几个即使将来重构挪了地方，也不许裸奔。"""
    hits = [r for r in app.routes if isinstance(r, APIRoute) and r.path == path]
    assert hits, f"{path} 不见了，用例要跟着更新"
    for r in hits:
        assert _has_auth(r), f"{sorted(r.methods)} {path} 没有鉴权"


def test_公开端点确实只有那几个():
    """反向守一手：别为了让上面那条变绿，把东西塞进 PUBLIC 白名单。"""
    public = sorted({r.path for r in app.routes
                     if isinstance(r, APIRoute) and r.path.startswith("/api/") and PUBLIC.match(r.path)})
    assert public == ["/api/auth/login", "/api/auth/refresh", "/api/healthz", "/api/readyz"], public
