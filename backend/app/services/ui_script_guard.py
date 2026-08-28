"""UI 脚本的「不许写死地址和凭据」门禁 —— 判定本体，纯函数。

**为什么从 `app/mcp/tools/sync.py` 里搬出来**：这条门禁原来只长在 MCP 回推工具上，
于是它管得住「CC 推上来的脚本」，管不住**仓内自己写的脚本**。页面枚举爬虫
（`app/engine/surveys/`）就是后者：它打的是别人的测试环境，写死一个地址的后果
不是"换环境挂了"，是**打到了不该打的那台机器上**，而且脚本照跑不误、没有任何报错。

搬出来之后两个入口共用同一份判定：
- `app/mcp/tools/sync.py` 的 `_scan_ui_script`（回推时挡）
- `backend/tests/test_qa_page_survey_crawl.py`（对仓内爬虫源码直接跑，code review 挡）

判定必须留在纯函数里：一旦跟 MCP 请求上下文缠在一起，仓内那个入口就只能复制一份 ——
而两份判定迟早会分叉，分叉的那天没有任何东西会变红。
"""
from __future__ import annotations

import re

UI_ENV_HINT = 'BASE_URL = os.getenv("BASE_URL", "")'

# 服务地址/凭据写死是硬伤：换环境就全挂，而且挂得很隐蔽（脚本还在跑，只是打了别的系统）。
URL_LITERAL_RE = re.compile(r"""["'`](https?://[^"'`\s]+)["'`]""")
CRED_LITERAL_RE = re.compile(
    r"""(password|passwd|pwd|token|secret|api_?key)\s*[:=]\s*["'`]([^"'`\s]{4,})["'`]""",
    re.I,
)
# **合法写法：故意用错的凭据。** 「用错密码登录应失败」这条用例里，
# 那个密码就该是字面量 —— 它不是配置，是本次要验的输入。
# 原来一律硬拦，等于逼人把"错密码"也搬进环境变量（那才是真的乱）。
INVALID_CRED_RE = re.compile(
    r"wrong|invalid|bad[-_]?|expired|revoked|fake|dummy|nonexist|notexist|"
    r"xxx+|placeholder|错误|无效|过期",
    re.I,
)


def env_reader(language: str) -> str:
    return "process.env" if (language or "").lower() == "typescript" else "os.getenv"


def scan_hardcoded_endpoint_or_secret(content: str, language: str = "python") -> list[str]:
    """扫出写死的服务地址和凭据。返回硬错误列表（空 = 干净）。

    逐行扫、**跳过读环境变量那一行**：`os.getenv("BASE_URL", "http://localhost:3000")`
    里的字面量是兜底默认值，不是写死 —— 把它也拦下来，人就只能把默认值删掉，
    于是脚本在没配变量时从「打了本机」变成「拼出 `None/xxx`」，更难查。
    """
    reader = env_reader(language)
    errors: list[str] = []
    for line in content.splitlines():
        if reader in line:
            continue  # 这一行本身就是在读变量，允许它带默认值
        for m in URL_LITERAL_RE.finditer(line):
            errors.append(
                f'写死了服务地址 {m.group(1)[:60]} —— 换环境必挂。'
                f'改成从变量取：{UI_ENV_HINT}，再用 f"{{BASE_URL}}/xxx" 拼。'
            )
        for m in CRED_LITERAL_RE.finditer(line):
            if INVALID_CRED_RE.search(m.group(2)):
                continue          # 故意用错的凭据，见 INVALID_CRED_RE
            errors.append(
                f'写死了凭据 {m.group(1)} —— 凭据只能来自环境变量。'
                f'改成 {reader}("ADMIN_PASSWORD"{"" if language == "typescript" else ", \'\'"})。'
            )
    return errors


def assert_no_hardcoded_endpoint_or_secret(content: str, language: str = "python",
                                           where: str = "") -> None:
    """同上，扫到就抛 —— 给仓内脚本的封样测试用（那里没有"返回一串警告"的出口）。"""
    errors = scan_hardcoded_endpoint_or_secret(content, language)
    if errors:
        head = f"{where}：" if where else ""
        raise ValueError(head + "\n".join(errors))
