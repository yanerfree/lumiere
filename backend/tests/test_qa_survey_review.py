"""把一趟活体枚举的对账产物切成逐域评审 —— 纯函数，不连库。

盯的是三件容易塌的事：
  · G4/G5 自己没有域，必须靠 `pageDomains` 挂上去，不能因此从某个域里消失；
  · verdict 只看缺口不看分（有 G3=假覆盖=bad）；
  · 判据形态变了（端点+页面位置），所以**不能**再有 `evidenceCheck` 这个键。
"""
from app.services import qa_survey_review as sr

REC = {
    "available": True,
    "domainNames": {"POL": "策略", "GW": "网关", "PAY": "支付"},
    "declarations": ["本轮无路由表，G2 未验证"],
    "applicability": {"byDomain": {
        "POL": {"state": "applicable", "tiers": ["ui"], "reason": "清单里这个域有 UI 层场景"},
        "NFR": {"state": "notApplicable", "tiers": ["nfr"], "reason": "全是非 UI 层：nfr"},
    }},
    "gaps": {
        "g1": [{"domain": "PAY", "method": "get", "path": "/api/pay/x",
                "pagePath": "/pay", "label": "刷新", "origin": "control"}],
        "g2": [],
        "g3": [{"domain": "POL", "method": "post", "path": "/api/pol/apply",
                "pagePath": "/policy", "label": "应用", "origin": "control"}],
        "g4": [{"pagePath": "/policy", "label": "导出", "controlType": "button",
                "pageDomains": ["POL"]}],
        "g5": [{"pagePath": "/gw", "label": "禁用", "controlType": "button",
                "pageDomains": ["GW"]}],
    },
}
META = {"surveyId": "s1", "environmentName": "staging",
        "buildFingerprint": "abc123def456", "status": "done", "createdAt": "2026-09-08T00:00:00"}


def test_域全集_含未声明的域和g4g5挂靠的域():
    # POL/NFR 来自适用性；PAY 来自 G1（清单没声明）；GW 来自 G5 的 pageDomains
    assert sr.domain_universe(REC) == ["GW", "NFR", "PAY", "POL"]


def test_g4g5按pageDomains挂到域上():
    dg = sr.domain_gaps(REC, "POL")
    assert len(dg["g3"]) == 1 and len(dg["g4"]) == 1
    assert dg["g1"] == [] and dg["g5"] == []
    assert sr.domain_gaps(REC, "GW")["g5"] and not sr.domain_gaps(REC, "GW")["g4"]


def test_verdict_有g3就是bad():
    assert sr.verdict_of(sr.domain_gaps(REC, "POL"),
                         sr.applicability_of(REC, "POL")) == sr.VERDICT_BAD


def test_verdict_只有清单缺口是risky():
    assert sr.verdict_of(sr.domain_gaps(REC, "PAY"),
                         sr.applicability_of(REC, "PAY")) == sr.VERDICT_RISKY


def test_verdict_无缺口且不适用是na():
    assert sr.verdict_of(sr.domain_gaps(REC, "NFR"),
                         sr.applicability_of(REC, "NFR")) == sr.VERDICT_NA


def test_json_形态_端点可grep_且没有evidenceCheck():
    out = sr.one(REC, META, "POL", "json")
    assert out["verdict"] == "bad"
    assert out["domainName"] == "策略"
    assert out["scriptGaps"][0]["endpoint"] == "POST /api/pol/apply"
    assert "/policy" in out["scriptGaps"][0]["seenOn"]
    assert len(out["deadControls"]) == 1           # G4
    assert "evidenceCheck" not in out              # 判据形态变了，这个键必须不在
    assert out["envMissing"] == [] and out["nextUp"] == []   # 留空占位，别 KeyError


def test_md_形态_带端点和文件名():
    out = sr.one(REC, META, "POL", "md")
    assert "POST /api/pol/apply" in out["markdown"]
    assert out["filename"].startswith("qa-live-review-POL")


def test_列表行():
    row = sr.domain_summary(REC, "POL")
    assert row["verdict"] == "bad" and row["scriptGapCount"] == 1
    assert row["deadControlCount"] == 1
