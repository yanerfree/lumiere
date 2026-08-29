"""S3 的验收集：手工验过的 30 条，钉住 evidence 判据。

PRD 的 S3 验收方法写的是「拿手工验过的 30 条当回归集」，一直欠着 ——
此前记的「假警报率 0%」说的是"这几趟碰巧一条没误报"，每跑一趟数就变，
不是一个能复现的数。

这 30 条取自 MCP / TEM / AUT 三个域最新那趟**真评审**的 `scriptGaps`：
模型写的判据引文 + 够它判的那段脚本正文 + 我逐条对着 QA 仓原文核过的期望状态。
`qa_evidence_check` 是纯函数、零 IO、不碰模型 —— 正因如此这份集子钉一次就能永远重放。

取样刻意把不通过的和非 verbatim 的**一条不漏**排在最前，verbatim 只用来补满 30。
全挑 verbatim 的回归集等于挑软柿子：它会给出一个漂亮的数，比没有还坏。

── 出处（fixture 里那些正文是别人仓库的，写清楚它从哪来）──
QA 仓 `origin/main` @ `173af7a`，2026-08-29 只读取出（`git show`，见 `qa_catalog._show`）。
**正文是抄进来的，不是运行时读的** —— 那个仓在别人手上、随时在动，
而且 CI 上根本没有它。运行时读会变成「仓库不在就静默跳过」，
那正好是本模块要禁的那个形状：没跑过和全过在结果里长得一模一样。
抄进来的代价是这份 fixture 94 KB，且 QA 仓改了它不会自动跟 —— 这是故意的：
回归集就该钉在一个不动的输入上，要换输入得有人显式重建它。
已核过里面没有凭证 / 内网地址 / token，只有一条脚本注释里的 GitLab MR 链接。
"""
import collections
import json
import pathlib

from app.services import qa_evidence_check as ec

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "qa_evidence_30.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))

# 建集当时的分档。**改这份 JSON 必须同步改这里** —— 否则"重新取一批样"
# 可以在没人察觉的情况下把不通过的那几条换掉，剩一集子软柿子。
EXPECT_DIST = {"unmatched": 3, "wrong-path": 1, "stitched": 6, "reflowed": 2, "verbatim": 18}


def _judge(case):
    gaps = [{"path": case["path"], "evidence": case["evidence"]}]
    ec.check_evidence(gaps, case["scripts"])
    return gaps[0]


class TestS3三十条回归集:
    def test_逐条状态跟人工核过的一致(self):
        wrong = []
        for c in CASES:
            got = _judge(c)["evidenceCheck"]
            if got != c["expected"]:
                wrong.append(f"{c['path']}：判成 {got}，人工核的是 {c['expected']}\n"
                             f"    判据：{c['evidence'][:120]}")
        assert not wrong, "判据在这些条上变了：\n  " + "\n  ".join(wrong)

    def test_分档条数一条不差(self):
        got = dict(collections.Counter(_judge(c)["evidenceCheck"] for c in CASES))
        assert got == EXPECT_DIST

    def test_分层匹配在这三十条上零假警报(self):
        """假警报 = 引文是真的原文，判据却说它没对上。这是这个模块最贵的一种错：
        它会把模型**没犯**的错写进页面，读的人照着去查，查不到，然后不再信这一列。"""
        bad = [c["path"] for c in CASES
               if c["expected"] in ec.PASS_STATES
               and _judge(c)["evidenceCheck"] not in ec.PASS_STATES]
        assert bad == []

    def test_分层匹配也没把缩写放过去(self):
        """反方向同样要钉：假放行比假警报更毒 —— 页面会说"引文已核对"，而它是编的。"""
        bad = [c["path"] for c in CASES
               if c["expected"] not in ec.PASS_STATES
               and _judge(c)["evidenceCheck"] in ec.PASS_STATES]
        assert bad == []

    def test_朴素exact匹配在同一份集子上误报八条(self):
        """PRD 记的基线 27%，就是这么来的：整段引文直接 `in` 正文。

        8 条被误判 —— 全是 stitched / reflowed，即模型引的是**真原文**，
        只是跨了行或空白对不齐。分层匹配把这 8 条全救回来了（上面那条测试）。
        """
        miss = [c for c in CASES
                if not any(c["evidence"] in s["content"] for s in c["scripts"])]
        false_alarm = [c for c in miss if c["expected"] in ec.PASS_STATES]
        assert len(miss) == 11
        assert len(false_alarm) == 8
        assert round(len(false_alarm) / len(CASES) * 100, 1) == 26.7

    def test_不通过的那几条存的是脚本全文(self):
        """**不许剪干草堆。**

        把「这句话在这份文件里根本没有」的样本裁成一个窗口，断言照样绿 ——
        但它绿的理由变了：不是模型缩写了，是我们把正文剪掉了。
        读集子的人分不出这两件事，于是这几条最贵的样本会悄悄退化成恒真。
        所以被指认的那份文件一律从第一行存起。
        （wrong-path 的「其实在这儿」那份是摘录 —— 它只需要证明引文**在**，不需要全文。）
        """
        for c in CASES:
            if c["expected"] not in ("unmatched", "wrong-path"):
                continue
            head = c["scripts"][0]["content"]
            assert c["scripts"][0]["path"] == c["path"]
            assert head.startswith(("#!", "// @scenario")), \
                f"{c['path']} 被指认的这份不是从文件头存的，干草堆被剪过"

    def test_集子里不许只剩软柿子(self):
        """对这份 JSON 自身的结构封样：真正考判据的是不通过的和非 verbatim 的那些。"""
        dist = collections.Counter(c["expected"] for c in CASES)
        assert len(CASES) == 30
        assert dist["unmatched"] >= 3
        assert sum(v for k, v in dist.items() if k != "verbatim") >= 10
