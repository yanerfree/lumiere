"""MCP Mock 预置服务的封样。

盯的是两件**会安静退化**的事：

  ① **一个服务的工具跑题了。** 2026-09-29 之前每个预置服务里都塞着
     `echo` / `list_users` / `get_server_time` 这种凑数工具 —— 现实里没有
     任何一个 MCP 服务长这样，拿它演示接入，对面第一眼就知道是假的。
     这类退化**不报错**（页面照常显示、调用照常返回），只能靠封样拦。

  ② **某一档接入场景没人占了。** 传输/认证/校验/异常四个维度，改预置时
     顺手把最后一个 SSE 服务改成 HTTP，那一类接入场景就**悄悄地再也测不到**，
     页面上完全看不出来。
"""
import hashlib
import json

import pytest

from app.services.mcp_mock_presets import (
    PRESET_REV,
    PRESET_SERVERS,
    PRESET_SLUGS,
    _SERVER_SYNC_FIELDS,
)
from app.services.mcp_mock_service import slug_error
from app.services.mcp_mock_validate import PARAM_TYPES, VALIDATE_MODES

# 那批「凑数工具」的名字。**别把它们加回来** —— 理由见模块头。
_FILLER_TOOL_NAMES = {
    "echo", "ping", "whoami", "list_users", "get_server_time",
    "always_fail", "slow_response", "empty_result",
    "search_docs", "get_quota", "query_metrics", "submit_order",
}


def _tools(spec: dict) -> list[dict]:
    return spec.get("tools") or []


# ── ① 得像个真服务 ──────────────────────────────────────────────


def test_没有凑数工具():
    bad = []
    for spec in PRESET_SERVERS:
        for t in _tools(spec):
            if t["name"] in _FILLER_TOOL_NAMES:
                bad.append(f"{spec['slug']}.{t['name']}")
    assert not bad, (
        f"这些工具名是当年凑数用的、跟服务主题无关：{bad}。"
        "预置服务要照着一个真实存在的服务写，工具全围着同一件事转。"
    )


def test_每个服务至少三个工具且都写了说明():
    for spec in PRESET_SERVERS:
        tools = _tools(spec)
        assert len(tools) >= 3, f"{spec['slug']} 只有 {len(tools)} 个工具，撑不起一个真服务的样子"
        for t in tools:
            assert t.get("description"), f"{spec['slug']}.{t['name']} 没写说明，客户端那边就是一片空白"


def test_服务自己有名字和说明():
    for spec in PRESET_SERVERS:
        assert spec.get("name"), f"{spec['slug']} 没名字"
        assert spec.get("description"), f"{spec['slug']} 没说明 —— 页面左边那一列就只剩个代号"
        assert spec.get("instructions"), f"{spec['slug']} 没 instructions —— 客户端 initialize 时读不到用途"


# ── ② 四个维度每一档都得有人占着 ────────────────────────────────


def test_传输方式两种都有():
    kinds = {s.get("transport", "streamable-http") for s in PRESET_SERVERS}
    assert kinds == {"streamable-http", "sse"}, f"传输方式只剩 {kinds}，少的那种接入场景没人测了"


def test_认证方式三种都有():
    kinds = {s.get("auth_type", "none") for s in PRESET_SERVERS}
    assert kinds == {"none", "bearer", "apikey"}, f"认证方式只剩 {kinds}"


def test_校验松紧三档都有():
    kinds = {s.get("validate_mode", "strict") for s in PRESET_SERVERS}
    assert kinds == set(VALIDATE_MODES), f"校验档位只剩 {kinds}"


def test_三种异常场景都有工具占着():
    has_error = has_delay = has_empty = False
    for spec in PRESET_SERVERS:
        for t in _tools(spec):
            if t.get("mode") == "error":
                has_error = True
            if int(t.get("delay_ms") or 0) >= 1000:
                has_delay = True
            if t.get("mode", "success") == "success" and t.get("success_data") == {}:
                has_empty = True
    assert has_error, "没有一个工具是必定报错的 —— 客户端的错误处理没法验"
    assert has_delay, "没有一个工具是慢响应的 —— 客户端的超时设置没法验"
    assert has_empty, "没有一个工具返回空 —— 客户端对空结果的处理没法验"


# ── ③ 结构上得立得住（不然启动时静默失败）──────────────────────


def test_slug合法且不重复():
    assert len(PRESET_SLUGS) == len(PRESET_SERVERS), "有 slug 撞车了，后挂的会把前一个整个盖掉"
    for spec in PRESET_SERVERS:
        assert slug_error(spec["slug"]) is None, f"{spec['slug']}: {slug_error(spec['slug'])}"


def test_工具名在服务内不重复():
    for spec in PRESET_SERVERS:
        names = [t["name"] for t in _tools(spec)]
        assert len(set(names)) == len(names), f"{spec['slug']} 里有重名工具，插库会撞唯一约束"


def test_认证配置配齐了():
    for spec in PRESET_SERVERS:
        auth = spec.get("auth_type", "none")
        cfg = spec.get("auth_config") or {}
        if auth == "bearer":
            assert cfg.get("token"), f"{spec['slug']} 是 bearer 却没给 token —— 谁都连不上"
        elif auth == "apikey":
            assert cfg.get("headerName"), f"{spec['slug']} 是 apikey 却没给请求头名"
            assert cfg.get("apiKey"), f"{spec['slug']} 是 apikey 却没给 key"
        else:
            assert not cfg, f"{spec['slug']} 不认证却带着 auth_config"


def test_参数定义立得住():
    for spec in PRESET_SERVERS:
        for t in _tools(spec):
            seen = set()
            for p in t.get("params") or []:
                where = f"{spec['slug']}.{t['name']}.{p.get('name')}"
                assert p.get("name"), f"{spec['slug']}.{t['name']} 有个参数没名字"
                assert p["name"] not in seen, f"{where} 重名"
                seen.add(p["name"])
                assert p.get("type", "string") in PARAM_TYPES, f"{where} 类型 {p.get('type')} 页面上选不出来"
                if p.get("type") == "array":
                    assert p.get("itemsType") in PARAM_TYPES, f"{where} 是数组却没说元素类型"
                if p.get("enum") is not None:
                    assert isinstance(p["enum"], list) and p["enum"], f"{where} 的 enum 得是非空列表"
                    if p.get("default") is not None:
                        assert p["default"] in p["enum"], f"{where} 的默认值不在枚举里，一选就是非法值"


def test_返回样例能序列化():
    for spec in PRESET_SERVERS:
        for t in _tools(spec):
            for key in ("success_data", "custom_data"):
                val = t.get(key)
                if val is None:
                    continue
                json.dumps(val, ensure_ascii=False)  # 序列化不了就在这里炸


def test_报错模式得写清报错信息():
    for spec in PRESET_SERVERS:
        for t in _tools(spec):
            if t.get("mode") == "error":
                assert t.get("error_message"), f"{spec['slug']}.{t['name']} 是报错模式却没写原因"


# ── ④ 刷新机制本身 ─────────────────────────────────────────────


def test_启停不跟着平台刷():
    """`enabled` 绝不能进同步字段。

    停用一个预置服务**不需要解锁**（页面上锁着也能关），所以它是「人的决定」。
    把它刷回去的表现是「我关掉的服务，重启一次自己又开了」—— 而且不报错。
    """
    assert "enabled" not in _SERVER_SYNC_FIELDS
    assert "locked" not in _SERVER_SYNC_FIELDS
    assert "builtin" not in _SERVER_SYNC_FIELDS
    assert "slug" not in _SERVER_SYNC_FIELDS, "slug 是地址的一部分，改了等于换 URL，不许跟着刷"


# 预置内容的指纹。**改了上面任何一个服务/工具，这一条会红** ——
# 红了就去把 PRESET_REV 往后挪一格，再把这里的指纹更新成新的。
# 不挪版本号的话，库里已经建出来的行一个字都不会刷新：代码里改了、页面上没变，
# 而且不报错 —— 这正是最难查的那一类。
_CONTENT_FINGERPRINT = "478bf1db414f6fb0d7a2113eba0db7c7"


def _fingerprint() -> str:
    blob = json.dumps(PRESET_SERVERS, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(blob.encode()).hexdigest()


def test_改了预置内容就得挪版本号():
    assert PRESET_REV, "PRESET_REV 不能为空，否则刷新逻辑判不了"
    assert _fingerprint() == _CONTENT_FINGERPRINT, (
        "预置内容变了。请：① 把 mcp_mock_presets.py 的 PRESET_REV 往后挪一格；"
        f"② 把本文件的 _CONTENT_FINGERPRINT 改成 {_fingerprint()}。"
        "不挪版本号的话，库里已有的预置服务不会刷新 —— 代码改了、页面没变，还不报错。"
    )
