"""前端调的每条接口，后端都得有 —— 挡住"点了开出个 422"的死按钮。

起因：`/api/llm-mock/logs/export` 被同层通配路由吃掉，页面上「导出日志」点下去
是满屏 422（见 test_route_shadowing）。那一类错单看后端看不出来，
单看前端也看不出来，**只有把两边对起来**才看得见。

这条守的是另一半：路径拼错了、后端改了名没同步、方法用错了。
不连数据库、不起服务 —— 直接读 app 的路由表 + 扫 frontend/src。
"""
import re
from pathlib import Path

import pytest

from app.main import app

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"

# utils/request.js 的 del → DELETE、stream → POST(SSE)
METHOD_MAP = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH",
              "del": "DELETE", "delete": "DELETE", "stream": "POST"}

CALL_RE = re.compile(
    r"""api\.(get|post|put|patch|del|delete|stream)\(\s*(?:`([^`]+)`|'([^']+)'|"([^"]+)")""")
RAW_RE = re.compile(r"""(?:window\.open|fetch)\(\s*(?:`([^`]+)`|'([^']+)'|"([^"]+)")""")
CONST_RE = re.compile(r"""(?:const|let|var)\s+(\w+)\s*=\s*`(/[^`]*)`""")


def _strip_interp(p: str) -> str:
    """${...} → {}，**要能处理嵌套大括号**。

    `/api-nodes${branchId ? `?branch_id=${x}` : ''}` 里嵌了一层；
    用 `\\$\\{[^}]*\\}` 会在第一个 } 就停，切出个半截路径然后误报。
    """
    out, i = [], 0
    while i < len(p):
        if p.startswith("${", i):
            depth, j = 1, i + 2
            while j < len(p) and depth:
                if p[j] == "{":
                    depth += 1
                elif p[j] == "}":
                    depth -= 1
                j += 1
            out.append("{}")
            i = j
        else:
            out.append(p[i])
            i += 1
    return "".join(out)


def _norm(p: str) -> str:
    p = _strip_interp(p).split("?")[0].rstrip("/")
    # 尾巴上贴着的 {} 是查询串不是路径段（`...${x ? "?a=1" : ""}`）
    while p.endswith("{}") and not p.endswith("/{}"):
        p = p[:-2].rstrip("/")
    if p.startswith("/api") and p[4:5] == "{":
        return "/api{}"                       # 整段变量拼的，判不了
    # 必须按 "/api/" 判前缀：`/api-mock/routes` 也以 "/api" 开头，
    # 用字符串前缀判会把它当成已带前缀，整个模块被误判
    if not (p == "/api" or p.startswith("/api/")):
        p = "/api" + (p if p.startswith("/") else "/" + p)
    return re.sub(r"\{[^}]*\}", "{}", p)


def _local_bases(lines):
    """`const base = \\`/projects/...\\`` —— 不展开的话，这类调用点全成了判不了的。"""
    out = {}
    for ln in lines:
        for m in CONST_RE.finditer(ln):
            out[m.group(1)] = m.group(2)
    return out


def _frontend_calls():
    calls = {}
    for f in list(FRONTEND.rglob("*.jsx")) + list(FRONTEND.rglob("*.js")):
        lines = f.read_text(errors="replace").splitlines()
        bases = _local_bases(lines)
        for i, line in enumerate(lines, 1):
            for m in CALL_RE.finditer(line):
                raw = m.group(2) or m.group(3) or m.group(4)
                if not raw or raw.startswith("http"):
                    continue
                mb = re.match(r"\$\{(\w+)\}", raw)
                if mb and mb.group(1) in bases:
                    raw = bases[mb.group(1)] + raw[mb.end():]
                calls.setdefault((METHOD_MAP[m.group(1)], _norm(raw)), []).append(f"{f.name}:{i}")
            for m in RAW_RE.finditer(line):
                raw = m.group(1) or m.group(2) or m.group(3)
                if not raw or not raw.startswith("/api"):
                    continue
                # fetch 的方法写在后面几行的选项对象里，不看就会把 POST 当 GET
                mm = re.search(r"method:\s*['\"](\w+)['\"]", "\n".join(lines[i - 1:i + 4]))
                verb = mm.group(1).upper() if mm else "GET"
                calls.setdefault((verb, _norm(raw)), []).append(f"{f.name}:{i}")
    return calls


def _backend_routes():
    out = set()
    for r in app.routes:
        path, methods = getattr(r, "path", None), getattr(r, "methods", None)
        if path and methods:
            p = re.sub(r"\{[^}]*\}", "{}", path.rstrip("/"))
            for m in methods:
                out.add((m.upper(), p))
    return out


UNJUDGEABLE = ("/api{}", "/api", "/api/")


@pytest.mark.skipif(not FRONTEND.exists(), reason="没有前端源码（只装了后端）")
def test_前端调的路径后端都存在():
    routes = _backend_routes()
    paths = {p for _, p in routes}
    bad = []
    for (verb, path), where in sorted(_frontend_calls().items()):
        if path in UNJUDGEABLE or path.startswith("/api/{}"):
            continue                          # 地址整段是变量拼的，静态判不了
        if path not in paths:
            bad.append(f"  {verb:6s} {path}   ← {', '.join(where[:2])}")
    assert not bad, "前端调了后端没有的路径（点下去是 404/422 的死按钮）：\n" + "\n".join(bad)


@pytest.mark.skipif(not FRONTEND.exists(), reason="没有前端源码（只装了后端）")
def test_前端用的方法后端支持():
    routes = _backend_routes()
    paths = {p for _, p in routes}
    bad = []
    for (verb, path), where in sorted(_frontend_calls().items()):
        if path in UNJUDGEABLE or path.startswith("/api/{}") or path not in paths:
            continue
        if (verb, path) not in routes:
            have = sorted(m for m, p in routes if p == path)
            bad.append(f"  {verb:6s} {path}   后端只有 {have}   ← {', '.join(where[:2])}")
    assert not bad, "方法对不上（一样是死按钮）：\n" + "\n".join(bad)


@pytest.mark.skipif(not FRONTEND.exists(), reason="没有前端源码（只装了后端）")
def test_扫到的调用点数量没有塌掉():
    """防的是扫描器自己坏掉 —— 正则一改没匹配上，上面两条会安静地全绿。

    这一整天踩了两次"验证工具自己坏了、结果看起来一切正常"，所以给扫描器本身设个下限。
    """
    assert len(_frontend_calls()) > 250
