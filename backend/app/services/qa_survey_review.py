"""把一趟「活体页面枚举·三边对账」的产物，切成**逐域**的评审结论。

## 为什么要有这一层

对账（`qa_live_survey.reconcile`）产出的是**全局**的五类缺口 g1–g5，每行带一个
`domain` 字段；它没有「某个域评得怎么样」这个结论。而 QA 那边的
`lum_get_qa_review(domain='MCP')` 问的正是逐域的那个结论。

老的逐域评审（`qa_catalog_review`）是**静态**的：读 QA 仓的脚本正文，让模型判
「这条声明覆盖了、脚本正文里到底验没验」。它的判据是脚本里的某一句（`evidence`），
取用方拿去 grep。2026-09 起用**活体**替掉它 —— 判据从「脚本里的一句话」变成
「页面在跑这个端点 / 这个按钮点了没反应」，是真去点出来的，比读脚本强。

**所以这层只做「切分 + 归纳」，不做判断**：判断（缺口怎么算）全在
`qa_coverage_reconcile` 里，这里只是把它按域分堆、给个 verdict、拼成文本。

## 五类缺口怎么映射到「评审」

  · **G3**（域在清单里声明了、页面在调这个端点、脚本没打过它）——
    最接近老的 `scriptGaps`：「你说覆盖了，其实没验到」。判据是**端点 + 页面位置**。
  · **G1 / G2**（页面/路由表里有这个端点，清单没登记它）—— 归 `catalogGaps`。
  · **G4 / G5**（控件点了没请求 / 控件点不动）—— 归 `deadControls`。

G1/G2/G3 的行自带 `domain`；G4/G5 自己没有请求、`domain` 是空的，但带了
`pageDomains`（这一页别的请求归哪些域）—— 按「域码 ∈ pageDomains」把它们挂到域上。
"""
from __future__ import annotations

# verdict 三档 + 一档「这个维度不管这个域」。
# 前三档沿用老评审的语义（QA 那边可能 `== "bad"` 地判），na 是新增的第四值：
# 页面维度对这个域**不适用**（清单里这个域全是非 UI 层），跟「评得差」是两回事。
VERDICT_OK = "ok"
VERDICT_RISKY = "risky"
VERDICT_BAD = "bad"
VERDICT_NA = "na"

_READONLY = ("平台只读了 QA 仓的清单与脚本正文、并在被测环境上真跑了一趟页面枚举，"
             "没有对 QA 仓做任何写操作；这份结论是建议，不是门禁。")


def _ep(row: dict) -> str:
    """`GET /api/x` —— 端点的规范写法，取用方拿它去脚本里 grep。"""
    return f"{(row.get('method') or '').upper()} {row.get('path') or ''}".strip()


def _where(row: dict) -> str:
    """这个端点/控件是在**哪个页面、由哪个控件**观测到的。"""
    page = row.get("pagePath") or ""
    label = row.get("label") or ""
    if page and label:
        return f"{page} · {label}"
    return page or label or ""


def domain_universe(rec: dict) -> list[str]:
    """这趟对账里出现过的所有域码。

    并集三处：适用性表（清单声明的域，权威）+ g1/g2/g3 行上的域
    （G1 的域可能没被清单声明 —— 那正是缺口本身）+ g4/g5 挂靠的 `pageDomains`。
    少了后两处，「清单没登记的那个域」会整个从列表里消失。
    """
    gaps = (rec or {}).get("gaps") or {}
    codes: set[str] = set(((rec or {}).get("applicability") or {}).get("byDomain") or {})
    for key in ("g1", "g2", "g3"):
        for row in gaps.get(key) or []:
            if row.get("domain"):
                codes.add(row["domain"])
    for key in ("g4", "g5"):
        for row in gaps.get(key) or []:
            for code in row.get("pageDomains") or []:
                if code:
                    codes.add(code)
    return sorted(codes)


def domain_gaps(rec: dict, code: str) -> dict:
    """把 g1–g5 过滤到某一个域。

    g1/g2/g3 按行上的 `domain`；g4/g5 按「码 ∈ pageDomains」（它们自己没有域）。
    """
    gaps = (rec or {}).get("gaps") or {}
    out: dict[str, list] = {}
    for key in ("g1", "g2", "g3"):
        out[key] = [r for r in (gaps.get(key) or []) if r.get("domain") == code]
    for key in ("g4", "g5"):
        out[key] = [r for r in (gaps.get(key) or [])
                    if code in (r.get("pageDomains") or [])]
    return out


def applicability_of(rec: dict, code: str) -> dict:
    """这个域的页面适用性：applicable / notApplicable / unknown（+ 原因）。"""
    by = ((rec or {}).get("applicability") or {}).get("byDomain") or {}
    return by.get(code) or {"state": "unknown", "reason": "对账里没有这个域的适用性判定"}


def verdict_of(dg: dict, applic: dict) -> str:
    """一个域的结论。**只看缺口，不看分数** —— 跟老评审同一条纪律。

      · 有 G3（声明覆盖了、页面在跑、脚本没验到）→ bad，这是最实的「假覆盖」。
      · 只有 G1/G2（清单没登记）或 G4（死按钮）或适用性 unknown → risky。
      · 一个缺口都没有、适用性 notApplicable → na（这个维度不管这个域）。
      · 其余（applicable、无缺口）→ ok。
    """
    state = (applic or {}).get("state")
    if dg.get("g3"):
        return VERDICT_BAD
    has_gap = any(dg.get(k) for k in ("g1", "g2", "g4"))
    if has_gap or state == "unknown":
        return VERDICT_RISKY
    if state == "notApplicable":
        return VERDICT_NA
    return VERDICT_OK


def _script_gaps(dg: dict) -> list[dict]:
    """G3 → 「声明覆盖了、页面在跑、脚本没验到」。判据 = 端点 + 页面位置。"""
    return [{
        "endpoint": _ep(r),
        "seenOn": _where(r),
        "origin": r.get("origin") or "control",   # control=点出来的 / page=页面加载
        "note": "页面在调这个端点，清单里这个域声明覆盖了，但你的脚本没打过它",
    } for r in dg.get("g3") or []]


def _catalog_gaps(dg: dict) -> list[dict]:
    """G1 + G2 → 清单没登记的端点。G1 是页面在调、G2 是路由表里有。"""
    rows = []
    for r in dg.get("g1") or []:
        rows.append({"endpoint": _ep(r), "seenOn": _where(r), "kind": "G1",
                     "note": "页面在调这个端点，清单里没有它"})
    for r in dg.get("g2") or []:
        rows.append({"endpoint": _ep(r), "group": r.get("group") or "", "kind": "G2",
                     "note": "路由表里有这个端点，清单没覆盖、页面也没调"})
    return rows


def _dead_controls(dg: dict) -> list[dict]:
    """G4 + G5 → 点了没反应 / 点不动的控件。"""
    rows = []
    for r in dg.get("g4") or []:
        rows.append({"where": _where(r), "controlType": r.get("controlType") or "",
                     "kind": "G4", "note": "点了它，一条请求都没发出去"})
    for r in dg.get("g5") or []:
        rows.append({"where": _where(r), "controlType": r.get("controlType") or "",
                     "kind": "G5", "note": "页面上有这个控件，但当前角色点不动"})
    return rows


def _headline(dg: dict, verdict: str) -> str:
    n3, n12 = len(dg.get("g3") or []), len(dg.get("g1") or []) + len(dg.get("g2") or [])
    n45 = len(dg.get("g4") or []) + len(dg.get("g5") or [])
    if verdict == VERDICT_NA:
        return "页面维度不适用于这个域"
    parts = []
    if n3:
        parts.append(f"{n3} 处声明覆盖了却没验到")
    if n12:
        parts.append(f"{n12} 处清单没登记的端点")
    if n45:
        parts.append(f"{n45} 个点不动/没反应的控件")
    return "、".join(parts) if parts else "没查到缺口"


def domain_summary(rec: dict, code: str) -> dict:
    """列表页要的那一行：域、名、verdict、一句话、各类缺口数。"""
    dg = domain_gaps(rec, code)
    applic = applicability_of(rec, code)
    verdict = verdict_of(dg, applic)
    names = (rec or {}).get("domainNames") or {}
    return {
        "domain": code,
        "domainName": names.get(code, ""),
        "verdict": verdict,
        "headline": _headline(dg, verdict),
        "applicability": applic.get("state"),
        "scriptGapCount": len(dg.get("g3") or []),
        "catalogGapCount": len(dg.get("g1") or []) + len(dg.get("g2") or []),
        "deadControlCount": len(dg.get("g4") or []) + len(dg.get("g5") or []),
    }


def _domain_declarations(rec: dict, code: str) -> list[str]:
    """这一趟「没验到什么」里，跟这个域相关的那几句 + 全局那几句。

    声明里大多不带域码（「本轮无路由表」这种是全趟的），全给它就行 ——
    诚实优先，宁可多说一句「这一维没验」，也别让缺口看着像 0。
    """
    return list((rec or {}).get("declarations") or [])


def one(rec: dict, meta: dict, code: str, fmt: str) -> dict:
    """某个域的完整交付物。`meta` 带这趟爬取的元数据（环境/指纹/时间/状态）。"""
    dg = domain_gaps(rec, code)
    applic = applicability_of(rec, code)
    verdict = verdict_of(dg, applic)
    names = (rec or {}).get("domainNames") or {}
    out = {
        "domain": code,
        "domainName": names.get(code, ""),
        "verdict": verdict,
        "environmentName": meta.get("environmentName") or "",
        "buildFingerprint": (meta.get("buildFingerprint") or "")[:12],
        "surveyStatus": meta.get("status") or "",
        "createdAt": meta.get("createdAt"),
        "applicability": {"state": applic.get("state"), "reason": applic.get("reason") or ""},
        "readOnly": _READONLY,
    }
    if fmt == "json":
        out.update({
            "headline": _headline(dg, verdict),
            "summary": _summary_text(dg, verdict, applic),
            # 键名沿用老评审：QA 那边照 `scriptGaps` / `catalogGaps` 读。
            # **判据形态变了**：不再是脚本里的一句 `evidence`，而是「端点 + 页面位置」。
            # 所以**没有** `evidenceCheck` 这个键 —— 老契约里它的缺席本就有定义
            # （「所有判据都得自己验」），这里自己验 = 拿 endpoint 去脚本里 grep。
            "scriptGaps": _script_gaps(dg),
            "catalogGaps": _catalog_gaps(dg),
            "deadControls": _dead_controls(dg),          # 新增：老评审没有这一类
            "declarations": _domain_declarations(rec, code),
            # 这两个键**故意留空**：老契约里它们在，直接删会让取用方 `res[...]` KeyError。
            "envMissing": [],
            "nextUp": [],
        })
    else:
        md, fn = _markdown(rec, meta, code, dg, verdict, applic)
        out["markdown"] = md
        out["filename"] = fn
    return out


def _summary_text(dg: dict, verdict: str, applic: dict) -> str:
    if verdict == VERDICT_NA:
        return "页面维度不适用于这个域：" + (applic.get("reason") or "")
    return _headline(dg, verdict) + "。判据是「哪个页面在调哪个端点」，去脚本里搜那个端点即可核对。"


def _markdown(rec: dict, meta: dict, code: str, dg: dict, verdict: str,
              applic: dict) -> tuple[str, str]:
    names = (rec or {}).get("domainNames") or {}
    name = names.get(code, "")
    verdict_cn = {VERDICT_OK: "没查到缺口", VERDICT_RISKY: "有风险",
                  VERDICT_BAD: "有假覆盖", VERDICT_NA: "页面维度不适用"}.get(verdict, verdict)
    lines = [
        f"# QA 活体评审 · {code}{('（' + name + '）') if name else ''}",
        "",
        f"- 结论：**{verdict_cn}**",
        f"- 环境：{meta.get('environmentName') or '(未知)'}　构建：{(meta.get('buildFingerprint') or '')[:12]}",
        f"- 适用性：{(applic.get('state') or '')}（{applic.get('reason') or ''}）",
        f"- {_READONLY}",
        "",
    ]

    def _sec(title: str, rows: list[str]):
        lines.append(f"## {title}")
        if rows:
            lines.extend(rows)
        else:
            lines.append("（无）")
        lines.append("")

    _sec("声明覆盖了、页面在跑、脚本没验到（去搜这些端点）",
         [f"- `{g['endpoint']}` —— {g['seenOn']}" for g in _script_gaps(dg)])
    _sec("清单没登记的端点",
         [f"- `{g['endpoint']}`　{g.get('note') or ''}" for g in _catalog_gaps(dg)])
    _sec("点不动 / 点了没反应的控件",
         [f"- {g['where']}　{g.get('note') or ''}" for g in _dead_controls(dg)])
    _sec("这一趟没验到的维度（别当成 0 缺口）",
         [f"- {d}" for d in _domain_declarations(rec, code)])

    return "\n".join(lines), f"qa-live-review-{code}-{(meta.get('buildFingerprint') or '')[:7]}.md"
