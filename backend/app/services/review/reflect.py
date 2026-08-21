"""场景级反问 —— 回推当场问四句，**平台把事实填进问题里**。

为什么要有这一步：三档规则全是步骤级的（这条断言恒真、那步没验效果），
而「这个场景验证点合不合理」「有没有相关场景没覆盖」「场景清不清晰」规则判不了 ——
只有 CC 答得上，它手上有需求和代码。平台能做的是**把它自己糊不过去的事实摊出来**。

口径（用户拍的）：**照常入库，不拦**。理由是入库了才能跑，而变异验证/断言咬合
这些最硬的证据只能从真跑里来；拦得住"没答"也拦不住"乱答"，而乱答比不答更糟。
不答的代价放在后面：交付门禁不放行、评审按"自证不全"扣分。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

# 一个模块常见的、容易漏的几类场景。判"有没有"只看邻居标题里的关键词 ——
# 判不准也无所谓：这是**反问**，让它自己说"不适用"或"还没写"，不是平台下结论。
_GAP_KINDS = {
    "状态切回来（禁用→启用、下线→上线）": r"启用|恢复|重新上线|切回|回滚|重新激活",
    "越权 / 跨租户": r"越权|无权|非成员|跨租户|其他租户|403|权限",
    "边界与限额": r"边界|上限|限额|最大|超长|为空|重复提交",
    "幂等 / 重复操作": r"幂等|重复|再次|两次",
    "删除后残留": r"删除后|删完|残留|回收站|彻底删",
}
_TWO_THINGS = re.compile(r"且|并且|同时|以及")


def _title_shape(title: str) -> tuple[str, str | None]:
    m = re.search(r"\s*[-—–:：]\s*", title or "")
    return ((title or "")[:m.start()], (title or "")[m.end():]) if m else (title or "", None)


def build(case, scenario: dict | None, neighbors: list[dict],
          script: dict | None = None) -> list[dict]:
    """生成四问。每问带 key（CC 按 key 回答）、facts（平台数出来的）、question。"""
    steps = (scenario or {}).get("steps") or []
    asserts = sum(len(s.get("assertions") or []) for s in steps)
    reads = sum(1 for s in steps if (s.get("method") or "GET").upper() == "GET")
    prefixed = sum(1 for s in steps
                   if re.match(r"^\s*(前置|制备|准备|操作|动作|验证|校验|清理|收尾)\s*[:：]",
                               s.get("name") or ""))
    head, tail = _title_shape(case.title or "")

    nb_titles = [n["title"] for n in neighbors]
    missing = [label for label, pat in _GAP_KINDS.items()
               if not any(re.search(pat, t) for t in nb_titles + [case.title or ""])]

    out = [{
        "key": "verificationPoints",
        "facts": {"标题承诺": tail or head, "接口步骤": len(steps), "断言": asserts,
                  "其中读操作步": reads, "UI 脚本": bool(script)},
        "question": f"你的标题承诺了「{tail or head}」。"
                    f"**哪几条断言在验它？** 逐件指出步骤号 —— "
                    f"承诺里有几件事就要指出几处。指不出来的那件，说明这条没验到。",
    }, {
        "key": "clarity",
        "facts": {"标题前段": f"{head}（{len(head)} 字）",
                  "分成两段了吗": bool(tail),
                  "步骤角色前缀": f"{prefixed}/{len(steps)}" if steps else "无接口场景"},
        "question": "**这条是不是只验一件事？**"
                    + ("（标题里有「且/同时」这类词，可能塞了两个功能）"
                       if _TWO_THINGS.search(case.title or "") else "")
                    + "是一件事的前后两阶段（配下去→真生效）就说一句；"
                      "是两个互不依赖的功能就拆开。",
    }, {
        "key": "coverage",
        "facts": {"同模块已有": nb_titles[:8] or "这是本模块第一条",
                  "本模块还没人写的常见类别": missing or "上面几类都有人写了"},
        "question": "**你这条和邻居不重复在哪？** 另外列出来的这几类，"
                    "哪些是**不适用**（说一句理由）、哪些是**还没写**"
                    "（自己补，或说清为什么先不做）。",
    }, {
        "key": "expectationSource",
        "facts": {"预期已确认落款": bool(getattr(case, "expected_confirmed_note", None))},
        "question": "预期是**按需求**写的还是按实测写的？不一致的地方怎么处理的？"
                    "（照实现抄 = 把 bug 固化成预期，这是最贵的一种错）",
    }]
    return out


def normalize(answers: dict | None, actor: str | None = None) -> dict | None:
    """收下答案。**不校验内容**（那是评审的活），只做长度和形状。"""
    if not answers or not isinstance(answers, dict):
        return None
    keys = ("verificationPoints", "clarity", "coverage", "expectationSource")
    out = {k: str(answers[k])[:1500] for k in keys if str(answers.get(k) or "").strip()}
    if not out:
        return None
    out["answeredAt"] = datetime.now(timezone.utc).isoformat()
    out["by"] = (actor or "cc")[:100]
    return out


def pending(case) -> bool:
    """还没答（或答得不全）。三问里少一问就算没答完 ——
    clarity 允许空（平台自己能算出大半）。"""
    r = getattr(case, "reflections", None) or {}
    return not all(str(r.get(k) or "").strip()
                   for k in ("verificationPoints", "coverage", "expectationSource"))
