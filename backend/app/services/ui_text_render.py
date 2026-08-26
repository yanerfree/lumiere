"""UI 脚本里的文案占位：`${services.list.searchPlaceholder|搜索服务名 / 路由…}`。

**为什么用 `${}` 而不是函数调用。** 脚本里别的取值都是变量形态
（`os.getenv("SV_svcName")`、接口断言里的 `${svcName}`），只有文案曾经是 `t("键")` ——
同一份脚本两套取法，读的人看不出 t() 也是"平台注入的数据"。现在两边同形：
接口断言写 `${T:键|中文}`，UI 脚本写 `${键|中文}`，都是"取一个平台给的值"。

替换发生在**执行前的源码文本**上（和替换 `os.getenv("X", "默认")` 默认值同一处），
所以：
  · 本地直接 pytest 跑，`${}` 是字面量 —— 要跑就先渲染一遍（见 lum_get_sync_spec 里
    那段 3 行本地渲染；平台跑的是同一套替换）
  · 替换进去的文案要转义：反斜杠和两种引号都转，Python 里 `\\'` 在双引号串里、
    `\\"` 在单引号串里都合法，所以不用知道外面是哪种引号

判据：**带点号或命名空间冒号的才当文案键**（`services.list.x`、
`subscription:stats.pendingApproval`）。不带的 `${BASE_URL}` 是环境变量，
UI 脚本里本来就该走 os.getenv，不碰它 —— 见 text_key()。
"""
from __future__ import annotations

import re

# ${键}  或  ${键|中文原文}；也认历史写法 ${T:键[|中文]}。
#
# 键**可以带 i18next 的命名空间**：`subscription:stats.pendingApproval`。
# 少了冒号这一段就是这个模块最贵的一个 bug —— 被测系统的键全是 `ns:a.b` 形态，
# 一条都匹配不上，占位原样进了断言，而 stat 三个桶全是空的（连门禁都没得警告）。
# 实测（CC 活体回推 v4）：正例红在「找不到元素」，同一趟里两条「不应出现」的负例
# **全绿** —— 匹配不到任何元素，"不该存在"当然成立。见 unresolved()。
_KEY = r"[A-Za-z_][\w.\-]*(?::[\w.\-]+)*"
REF_RE = re.compile(rf"\$\{{({_KEY})(?:\|([^}}]*))?\}}")
_REF_RE = REF_RE          # 老名字还有引用


def text_key(ref: str) -> str | None:
    """`${}` 里那串东西是文案键吗？是就返回规范化后的键（剥掉历史的 `T:` 前缀）。

    判据：**带点号、或带命名空间冒号的才是文案键**（`services.list.x`、
    `subscription:stats.x`）。不带的 `${BASE_URL}` 是环境变量，UI 脚本里本来就该走
    os.getenv，不碰它。

    门禁和渲染共用这一个判据 —— 各写一份正则的代价已经付过一次了。
    """
    if ref.startswith("T:") and _looks_key(ref[2:]):
        ref = ref[2:]                  # 历史写法；命名空间真叫 T 的话另说，没有这种
    return ref if _looks_key(ref) else None


def _looks_key(ref: str) -> bool:
    return "." in ref or ":" in ref


def key_aliases(key: str) -> list[str]:
    """同一条词的两种拼法互认：i18next 的 `ns:a.b` ↔ 全点号 `ns.a.b`。

    被测系统里键长这样 `t('subscription:manage.rejectBtn')`（namespace 用冒号分隔），
    而平台词典早期是**全点号**收进来的 —— 同一条词两种拼法，查不到就静默退回中文，
    **英文环境下测的其实是中文**，而且没有任何红。
    实测（CC 活体回推）：报"5 个键没登记"，其中 4 个词典里明明有，只是写成了点号。

    只换**第一个**分隔符 —— 命名空间只有一层，后面的点是键路径。
    """
    out = [key]
    if ":" in key:
        out.append(key.replace(":", ".", 1))
    elif "." in key:
        ns, rest = key.split(".", 1)
        out.append(f"{ns}:{rest}")
    return out


def with_aliases(table: dict[str, dict]) -> dict[str, dict]:
    """把词典补上另一种拼法的别名（原键优先，不覆盖已有的）。"""
    out = dict(table)
    for key, row in table.items():
        for alias in key_aliases(key)[1:]:
            out.setdefault(alias, row)
    return out


def unresolved(content: str) -> list[str]:
    """正文里还剩哪些**没解析的文案占位** —— 拦在执行之前用。

    渲染之后正文里不该再有文案占位：能查到的换成了译文，查不到但带了 `|中文` 的
    退回了中文，剩下的就是"既没登记、又没写中文原文"。这种**必须拦死，不许开跑**：

      · 正例（断言文案出现）会红在「找不到元素」上 —— 看得见，还能查
      · 负例（断言"不应出现"）会**假绿** —— 占位匹配不到任何元素，
        "不该存在"当然成立。这就是恒真断言：坏了不喊疼，跑一万次都是绿的

    所以这里不看渲染统计、直接扫最终正文 —— 顺带把"某条执行路径压根忘了渲染"
    也一起拦住（这库已经栽过一次：词典只在一条路注入，另一条静默跑字面量）。
    """
    out: list[str] = []
    for m in REF_RE.finditer(content):
        key = text_key(m.group(1))
        if key and key not in out:
            out.append(key)
    return out


def unresolved_hint(content: str) -> str:
    """未解析的占位该怎么修 —— 区分「少登记」和「这条路忘了渲染」。

    带了 `|中文原文` 的占位，渲染过就一定会退回中文、不可能还留在正文里。
    所以它还在 = **这条执行路径压根没调 render()**，是管道漏了渲染。
    此前一律回「占位里补上 ${键|中文原文}」，而人写的本来就是 ${键|中文} ——
    建议解决不了问题，还把注意力从真因（漏渲染）上引开。实测：计划执行路径
    未渲染，报错叫人去补一个已经写着的东西。
    """
    with_fallback = [m.group(1) for m in REF_RE.finditer(content)
                     if text_key(m.group(1)) and (m.group(2) or "").strip()]
    if with_fallback:
        return (f"其中 {len(with_fallback)} 处**已经写了 `|中文原文`**"
                f"（如 ${{{with_fallback[0]}|…}}）—— 渲染过的话它会退回中文、"
                f"不会留在正文里。所以真因是**这条执行路径没做文案渲染**"
                f"（没调 ui_text_render.render），不是你少写占位。"
                f"要改的是那条路，别改脚本。")
    return ("两条任选：lum_upsert_i18n_terms 登记 key+zh+en，"
            "或占位里补上 ${键|中文原文}。")


def _escape(text: str) -> str:
    """替换进源码字符串字面量里 —— 反斜杠和两种引号都得转。"""
    return (text.replace("\\", "\\\\").replace('"', '\\"')
                .replace("'", "\\'").replace("\n", "\\n").replace("\r", ""))


def render(content: str, table: dict[str, dict], locale: str) -> tuple[str, dict]:
    """把脚本里的 `${键|中文}` 换成当前语种那句话。

    返回 (渲染后的脚本, {"resolved": [...], "fellBack": [...], "missing": [...]})
      · resolved  词典命中
      · fellBack  词典没有、退回了中文（英文环境下测的还是中文）
      · missing   词典没有、也没给中文 → **原样留着那串 `${}`**，选择器必然匹配不上。
        故意不静默：让它红在"找不到元素"上之前，回推门禁已经先警告过一次。
    """
    stat = {"resolved": [], "fellBack": [], "missing": []}

    def one(m: re.Match) -> str:
        hint = m.group(2)
        ref = text_key(m.group(1))
        if ref is None:                       # 不是文案键（${BASE_URL} 之类），不碰
            return m.group(0)
        row = table.get(ref) or {}
        val = row.get(locale)
        if not val:
            pre = locale.split("-")[0]
            val = next((v for k, v in row.items() if k.split("-")[0] == pre and v), None)
        if val:
            stat["resolved"].append(ref)
            return _escape(val)
        # 词典里有这条、但缺目标语种 → 退回它自己的中文（比留下 ${} 好得多：
        # 那样一定红在"找不到元素"上，而退回中文至少是在测中文那一版）
        zh = row.get("zh-CN") or row.get("zh")
        if zh:
            stat["fellBack"].append(ref)
            return _escape(zh)
        if hint:                              # 空的 ${键|} 不算给了中文原文
            stat["fellBack"].append(ref)
            return _escape(hint)
        stat["missing"].append(ref)
        return m.group(0)

    return _REF_RE.sub(one, content), stat


def locale_of(env: dict) -> str:
    """这次执行按哪个语种渲染 —— 和 pw_conftest 同一套判据。"""
    explicit = str(env.get("PLAYWRIGHT_LOCALE") or "").strip()
    if explicit:
        return explicit
    lang = str(env.get("TEST_LANGUAGE") or "").strip().lower()
    return {"zh": "zh-CN", "en": "en-US"}.get(lang, "zh-CN")


# `os.getenv("KEY", "默认")` / `os.getenv("KEY")` —— 按**括号里的键**匹配，不管左边叫什么。
_GETENV_RE = re.compile(
    r"""os\.getenv\(\s*(['"])(?P<key>[A-Za-z_][\w.]*)\1\s*(?:,\s*(?P<def>'[^']*'|"[^"]*")\s*)?\)""")


def bake_env_defaults(content: str, env: dict, skip=()) -> tuple[str, list[str]]:
    """把脚本里 `os.getenv("KEY", ...)` 的默认值换成该环境的真值。

    **按键匹配，不按左边的变量名。** 原来三处（MCP 跑、页面跑、本地渲染）各写一份正则，
    都要求 `NAME = os.getenv("NAME", ...)` 左右同名 —— 于是
    `PROJECT_ID = os.getenv("SV_projectId", "")` 这种再自然不过的写法一个都替换不到。
    平台跑时真环境变量在进程里，`os.getenv` 运行时照样取得到，**这个漏洞被藏住了**；
    本地渲染成一个文件跑才暴露：那一行拿到空串，页面地址拼成 `/projects//cases`。

    返回 (新正文, 真的替换掉的键)。skip 里的键跳过（凭据默认不烧进本地文件）。
    """
    baked: list[str] = []

    def one(m: re.Match) -> str:
        key = m.group("key")
        if key in skip or key not in env:
            return m.group(0)
        val = str(env[key]).replace("\\", "\\\\").replace('"', '\\"')
        baked.append(key)
        return f'os.getenv("{key}", "{val}")'

    return _GETENV_RE.sub(one, content), baked
