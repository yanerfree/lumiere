"""覆盖缺口的归并 —— 「这个模块缺哪一类用例」按**话题**合并，不按字面。

原来的键是「去掉前缀后的前 12 个字」。LLM 每轮措辞都不一样，于是同一件事
（越权访问）会被拆成三条各自 1× 的缺口 —— 而这一页存在的理由就是那个 count 列：
被提到得多说明这个模块真的缺这类用例。1× 一片等于这页没有信息。

**判据放在代码里，不问 LLM。** 这一页每次打开都要算，塞一次模型调用既慢又不稳，
而且"合并结果每次不一样"比不合并更糟。所以走关键词 → 话题标签：同一话题的
不同说法（越权/权限/鉴权/unauthorized/403）归到一个桶。命中不了的退回
「实词签名」（去掉停用词后取前几个实词排序），至少把纯改语序的重复合掉。
"""
from __future__ import annotations

import re

# 话题标签 → 命中这个话题的词。**顺序有意义**：从左到右第一个命中的赢，
# 所以把更具体的话题放前面（"越权"比"权限"具体，"并发冲突"比"异常"具体）。
_TOPICS: list[tuple[str, tuple[str, ...]]] = [
    ("权限与越权", ("越权", "权限", "鉴权", "授权", "未授权", "无权", "不属于自己",
                    "他人", "跨租户", "跨项目", "unauthorized", "forbidden",
                    "403", "401", "rbac", "role")),
    ("并发与竞态", ("并发", "竞态", "同时", "抢占", "锁", "race", "冲突")),
    ("幂等与重复提交", ("幂等", "重复提交", "重复创建", "重复调用", "重试", "idempot")),
    ("边界与长度", ("边界", "超长", "最大", "最小", "长度", "上限", "越界", "空字符串",
                    "特殊字符", "非法字符", "boundary", "max", "min")),
    ("异常与错误码", ("异常", "错误码", "报错", "失败路径", "负例", "不存在", "404",
                      "422", "500", "校验失败", "参数校验")),
    ("空数据与初始态", ("空数据", "空列表", "无数据", "首次", "初始", "零条", "empty")),
    ("分页与排序", ("分页", "排序", "翻页", "page", "sort", "limit", "offset")),
    ("清理与残留", ("清理", "残留", "脏数据", "回滚", "删除后", "级联", "cleanup")),
    ("多语言与文案", ("多语言", "国际化", "i18n", "语种", "英文环境", "文案")),
    ("性能与超时", ("性能", "超时", "耗时", "慢", "大数据量", "timeout")),
]

_PREFIXES = ("模块级缺口：", "模块级：", "覆盖缺口：", "缺口：")
# 实词签名要去掉的虚词/套话 —— 留着它们，签名就全靠这些词撞在一起了。
_STOP = re.compile(r"(没有|缺少|缺失|建议|补充|覆盖|场景|用例|测试|相关|以及|这个|一个|"
                   r"的|了|和|与|或|等|对|在|是|有|要|需要|应该|可以|目前|当前|"
                   r"missing|coverage|case|test|scenario|should|need)")


def _strip_prefix(text: str) -> str:
    for p in _PREFIXES:
        if text.startswith(p):
            return text[len(p):]
    return text


def topic_of(gap: str) -> str:
    """这条缺口讲的是哪一类。命中不了就退回实词签名（不是原文，别再按字面比）。"""
    body = _strip_prefix(str(gap or "").strip())
    low = body.lower()
    for label, words in _TOPICS:
        if any(w in low for w in words):
            return label
    words = sorted(set(w for w in re.split(r"[^\w一-鿿]+", _STOP.sub("", low)) if len(w) > 1))
    return "其他：" + "-".join(words[:4]) if words else "其他"


def merge(gaps_with_case: list[tuple[str, str]], top: int = 8) -> tuple[list[dict], int]:
    """把 (缺口原文, 用例编号) 归并成按话题的桶。

    返回 (前 top 个桶, 桶总数)。**总数要回出去** —— 只回 top 的话，
    "就这几类"和"被砍了"在页面上长得一模一样。
    """
    buckets: dict[str, dict] = {}
    for gap, case_code in gaps_with_case:
        key = topic_of(gap)
        b = buckets.setdefault(key, {"topic": key, "gap": str(gap)[:200],
                                     "count": 0, "cases": [], "phrasings": []})
        b["count"] += 1
        if case_code and case_code not in b["cases"]:
            b["cases"].append(case_code)
        # 各条的原话都留着（去重）—— 合并后还得能看出"这三条到底是不是一件事"，
        # 不然归并本身就变成一层看不穿的黑箱。
        one = str(gap)[:200]
        if one not in b["phrasings"]:
            b["phrasings"].append(one)
    out = sorted(buckets.values(), key=lambda x: (-x["count"], x["topic"]))
    for b in out:
        b["phrasings"] = b["phrasings"][:6]
        # 实词签名是**归并用的键**，不是给人看的标签。直接显示会变成
        # 「其他：expectedconfirmednote-false-matchkeychanged-path」这种噪声，
        # 比不归并还难读。命中话题的显示话题，没命中的就显示原话。
        b["display"] = b["topic"] if not b["topic"].startswith("其他") else b["gap"]
        b["matchedTopic"] = not b["topic"].startswith("其他")
    return out[:top], len(out)
