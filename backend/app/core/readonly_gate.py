"""游客只读闸门 —— 纯函数，无 FastAPI、无 DB。

**为什么是"方法闸门"而不是"权限点"**

`deps/permissions.require_permission` 按权限点挡，语义更精确，但它的 `_check` 签名
靠路径里的 `{project_id}` 取项目语境。实测 `/api` 下共 **264 条写方法路由，其中 129 条
一个角色守卫都没有**，且**都不含 `{project_id}`** ——

    protocol-mock 44 / api-mock 14 / llm-mock 13 / mcp-mock 10 / load-test 10 /
    oauth2-mock 9 / http-client 6 / proxy-probe 5 / auth 4 / toolbox 4 /
    mcp-keys 3 / ai-providers 2 / assistant 2 / projects 1 / debug 1 / screenshots 1

这些路由上**挂不上** `require_permission`。所以「把守卫逐条换成权限点」即便做完，
游客照样能打这 129 条。方法闸门是唯一覆盖得全的形状：一个收口点，加新端点时
不可能忘记（默认拒绝），而白名单是显式的、每条带理由、有反向封样测试盯着。

**白名单纪律**：只放"形状是写、实质不是写"的端点。判据是**它会不会改变
被测/本系统的持久状态**，不是"它看起来危不危险"。新增条目必须同时写理由，
`tests/test_authz_seal.py` 会断言白名单不长胖。
"""
from __future__ import annotations

# HTTP 语义上的安全方法 —— 不改变服务端状态。
# 注意 HEAD/OPTIONS 也在内：它们是 GET 的元信息形态。
SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})

# 游客可以打的非安全方法端点 —— 键是**路径模板**（不是原始 URL，避免 id 参与比较）。
# 值是理由，会被封样测试要求非空。
GUEST_WRITE_ALLOWLIST: dict[str, str] = {
    # ── 自身身份，不碰任何业务数据 ──
    "/api/auth/login": "登录本身；此时还没有 current_user，闸门实际不会走到这里，列出是为自洽",
    "/api/auth/refresh": "换 token，只读自己的身份",
    "/api/auth/logout": "登出，只作废自己的 token",
    "/api/auth/change-password": "改自己的密码 —— 拿不到这条，游客账号就无法自行改密",
    # ── 形状是 POST，实质是纯函数 / 只产出提案 ──
    "/api/assistant/chat": (
        "只产出提案、从不落库（api/assistant.py 里零 commit/session.add）；"
        "真正执行的 /api/assistant/execute **故意不在白名单**"
    ),
    "/api/projects/{project_id}/branches/{branch_id}/cases/{case_id}/scenario-variables/preview": (
        "把模板展开一次做样例预览，纯函数，处理器连 session 都不注入"
    ),
}

# 明确记下"考虑过但不放"的，免得下次有人当遗漏补进来。
# /api/assistant/execute  —— 真的会落库
# /api/toolbox/http-request —— 代本系统向外发请求，是真副作用
# /api/toolbox/generate-regex —— 打 LLM，烧额度
# /api/toolbox/{jwt,hmac}-sign —— 是纯函数，但游客拿不到 system.tools.use、进不了工具页，
#                                 放进来只会让白名单虚胖


def blocks_guest(method: str, path_template: str) -> bool:
    """这条（方法, 路径模板）是否应当对游客关闭。

    纯函数，便于封样测试离线遍历整张路由表。
    """
    if method.upper() in SAFE_METHODS:
        return False
    return path_template not in GUEST_WRITE_ALLOWLIST
