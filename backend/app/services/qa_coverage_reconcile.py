"""三方对账 —— **纯集合运算，不做 IO、不问模型**。

三个账本：
  · **P** 页面枚举（Epic 6 爬出来的控件 → 请求 → 路径模板）
  · **R** 路由表（S7.2，`qa_route_table`）
  · **Q** QA 清单（S7.1，`qa_catalog` 的域码表 + 场景清单）

这个文件只负责把三边的**名字对齐**，好让 S7.4 去做交并差。
对齐这件事全是坑，而且**坑的方向是一致的：对不齐 ⇒ 凭空多出缺口**。
两边其实是同一个组，只因写法不同没对上，报告上就长出一条「这个组没人测过」——
去查的人扑个空，第二次就不看这份报告了。

## 三处坑（清单自己警告过的）

1. **组名的大小写和单复数会变。** 域码表原文写过 2.1.1→2.2.0 改过写法；
   按字面比对会**凭空多出 7 个新组**。
2. **`PUB` 不是按组划定的，是按路径前缀**（`/api/public/v1/*`），
   并且**故意**跟 TEM/PRV/AGT/MCP 重叠 —— 同一个端点既属 PUB 又属 TEM。
3. **`Root` 组同属 SMK / MCP / SEC。**

2 和 3 是同一件事的两个例子：**「组 → 域」天生是一对多。**
写成 `dict[str, str]` 的话后一个域把前一个覆盖掉，一个字都不会报错，
而对账那边从此少算一整个域的缺口 —— **少算的缺口不会红，谁都发现不了。**
所以这里的值一律是 `set`。
"""
import re

from app.services.branch_diff_service import normalize_path

# 归一之后仍然保留原样的尾巴：`status` / `access` 剥成 `statu` / `acces`
# 不只是难看 —— 它会跟别的词撞在一起，把两个真不同的组合并成一个，
# 于是其中一个组的缺口凭空消失。**过度归一和不归一，坏的方向正好相反，都要防。**
_KEEP_TAIL = ("ss", "us", "is")
_IES = re.compile(r"ies$")
_XES = re.compile(r"(?:ses|xes|zes|ches|shes)$")
# 路径前缀的形状：反引号里以 `/` 开头、以 `/*` 或 `*` 收尾的那一段。
# **只认这一种确定形状** —— 第三列里混着中文散文，从散文里"理解"归属规则
# 就是把猜换了个地方放，还更隐蔽，因为它看起来像事实。
_PREFIX_RE = re.compile(r"/(?:[A-Za-z0-9_\-{}]+/)*[A-Za-z0-9_\-{}]*\*")


def norm_group(raw: str | None) -> str:
    """组名 → 可比的键。小写、去分隔符、单复数归一。

    `MCP-Tools` / `MCP Tools` / `mcp_tools` / `MCPTool` 全归到 `mcptool`。
    """
    s = re.sub(r"[^0-9a-z]+", "", (raw or "").strip().lower())
    if not s:
        return ""
    if s.endswith(_KEEP_TAIL):
        return s
    if _IES.search(s) and len(s) > 4:
        return s[:-3] + "y"
    if _XES.search(s) and len(s) > 4:
        return s[:-2]
    # 剥掉复数 s，但**剥完至少还剩 3 个字符** —— `Ops` 剥成 `op` 就开始撞了
    if s.endswith("s") and len(s) - 1 >= 3:
        return s[:-1]
    return s


def _prefixes(raw: str) -> list[str]:
    """从域码表第三列的原文里抠出路径前缀（`/api/public/v1/*`）。"""
    out: list[str] = []
    for m in _PREFIX_RE.finditer(raw or ""):
        p = normalize_path(m.group(0).rstrip("*").rstrip("/"))
        if p and p != "/" and p not in out:
            out.append(p)
    return out


def build_group_index(domains: dict[str, dict]) -> dict:
    """域码表 → 对账用的归属索引。

    返回：
      `byGroup`   `{归一组名: {域码, ...}}` —— **值是集合**，见文件头
      `aliases`   `{归一组名: [清单原文写过的名字, ...]}` —— 归一合并了什么必须留痕，
                  否则「清单把 Tags 改成了 Tag」这个信号被归一化本身吃掉了
      `byPrefix`  `[(路径模板, {域码, ...}), ...]` —— `PUB` 那种按前缀划定的
      `unresolved` `[域码, ...]` —— 第三列有内容但**既没组名也没前缀**，
                  归属规则没读懂。这些域**不能**渲染成「0 缺口」：
                  「没有缺口」和「我根本没法给它归属」在数字上是同一个 0。
    """
    by_group: dict[str, set[str]] = {}
    aliases: dict[str, list[str]] = {}
    by_prefix: list[tuple[str, set[str]]] = []
    unresolved: list[str] = []

    prefix_map: dict[str, set[str]] = {}
    for code, meta in (domains or {}).items():
        groups = meta.get("groups") or []
        raw = meta.get("groupsRaw") or ""
        for g in groups:
            key = norm_group(g)
            if not key:
                continue
            by_group.setdefault(key, set()).add(code)
            names = aliases.setdefault(key, [])
            if g not in names:
                names.append(g)
        prefs = _prefixes(raw)
        for p in prefs:
            prefix_map.setdefault(p, set()).add(code)
        if raw.strip() and not groups and not prefs:
            unresolved.append(code)

    # 长前缀排前面：`/api/public/v1` 比 `/api` 更具体，两条都命中时两个域都算
    for p in sorted(prefix_map, key=lambda x: -len(x)):
        by_prefix.append((p, prefix_map[p]))

    return {"byGroup": by_group, "aliases": aliases,
            "byPrefix": by_prefix, "unresolved": unresolved}


def _under(path: str, prefix: str) -> bool:
    """`/api/public/v1/templates` 在 `/api/public/v1` 底下；`/api/public/v10` 不在。"""
    return path == prefix or path.startswith(prefix + "/")


def domains_for(path: str | None, group: str | None, index: dict) -> set[str]:
    """一个端点 →它属于哪些域。**返回集合，可能是多个，也可能是空。**

    组和前缀两条规则**都走、取并集** —— 清单是故意让它们重叠的
    （`/api/public/v1/templates` 既是 PUB 又是 TEM）。只走一条就会漏掉一个域，
    然后那个域的缺口凭空消失。

    空集合的意思是「这个端点在清单里找不到归属」，**不是**「它没有缺口」——
    调用方（S7.4）必须把它单独记账，不能当成已归属处理。
    """
    out: set[str] = set()
    key = norm_group(group)
    if key:
        out |= (index.get("byGroup") or {}).get(key, set())
    norm = normalize_path(path)
    if norm:
        for prefix, codes in index.get("byPrefix") or []:
            if _under(norm, prefix):
                out |= codes
    return out
